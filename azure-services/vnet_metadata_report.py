import os
#!/usr/bin/env python3
"""
Azure Virtual Network Metadata Report
Collects VNets, subnets, NSGs (with rules), private endpoints, VNet peerings,
and service endpoints across all resource groups. Flags data-exfil risk indicators.

Usage:
  python3 vnet_metadata_report.py
"""

import json
import subprocess
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests

# ── config ─────────────────────────────────────────────────────────────────────

SUBSCRIPTIONS = [
    "57493fde-eff8-432f-8574-4f1281bd2ce3",  # ECAE IDOH Production
    "5d3a4b9c-0e31-477c-9122-bb3be662e2a9",  # ECAE Shared Production
]
MGMT     = "https://management.azure.com"
API      = "2023-09-01"
OUT_FILE = "/home/thedavidporter/vnet_metadata_report.html"

# Risk rule thresholds
RISK_WIDE_OPEN_SOURCES = {"*", "Internet", "Any", "0.0.0.0/0"}
HIGH_RISK_PORTS        = {"22", "3389", "445", "1433", "3306", "5432", "27017", "*"}

# ── auth ───────────────────────────────────────────────────────────────────────

def get_token():
    r = subprocess.run(
        ["az", "account", "get-access-token", "--resource", "https://management.azure.com/"],
        capture_output=True, text=True, check=True
    )
    return json.loads(r.stdout)["accessToken"]

def hdrs(token):
    return {"Authorization": f"Bearer {token}"}

# ── REST helpers ───────────────────────────────────────────────────────────────

def get(url, token, params=None):
    p = {"api-version": API}
    if params:
        p.update(params)
    r = requests.get(url, headers=hdrs(token), params=p)
    if r.status_code in (400, 403, 404):
        return {}
    r.raise_for_status()
    if not r.text.strip():
        return {}
    return r.json()

def get_all(url, token):
    results, params = [], {"api-version": API}
    while url:
        r = requests.get(url, headers=hdrs(token), params=params)
        if r.status_code in (400, 403, 404):
            break
        r.raise_for_status()
        data = r.json()
        results.extend(data.get("value", []))
        url    = data.get("nextLink")
        params = {}
    return results

# ── helpers ────────────────────────────────────────────────────────────────────

def rg_from_id(rid):
    parts = (rid or "").split("/")
    try:
        return parts[parts.index("resourceGroups") + 1]
    except (ValueError, IndexError):
        return ""

def res_name(rid):
    return (rid or "").split("/")[-1]

def res_type(rid):
    parts = (rid or "").split("/")
    try:
        ns_idx  = next(i for i, p in enumerate(parts) if "." in p)
        return f"{parts[ns_idx]}/{parts[ns_idx+1]}"
    except (StopIteration, IndexError):
        return ""

def cidr_size(prefix):
    try:
        bits = int(prefix.split("/")[1])
        return 2 ** (32 - bits)
    except Exception:
        return 0

# ── data collection ─────────────────────────────────────────────────────────────

def fetch_vnets_all(sub, token):
    url = f"{MGMT}/subscriptions/{sub}/providers/Microsoft.Network/virtualNetworks"
    return get_all(url, token)

def fetch_nsgs_all(sub, token):
    url = f"{MGMT}/subscriptions/{sub}/providers/Microsoft.Network/networkSecurityGroups"
    return get_all(url, token)

def fetch_private_endpoints(sub, token):
    url = f"{MGMT}/subscriptions/{sub}/providers/Microsoft.Network/privateEndpoints"
    return get_all(url, token)

def fetch_public_ips_all(sub, token):
    url = f"{MGMT}/subscriptions/{sub}/providers/Microsoft.Network/publicIPAddresses"
    return get_all(url, token)

def fetch_nics_all(sub, token):
    url = f"{MGMT}/subscriptions/{sub}/providers/Microsoft.Network/networkInterfaces"
    return get_all(url, token)

# ── cleaners ───────────────────────────────────────────────────────────────────

def clean_rule(r):
    p = r.get("properties", {})
    return {
        "name":        r.get("name", ""),
        "priority":    p.get("priority"),
        "direction":   p.get("direction", ""),
        "access":      p.get("access", ""),
        "protocol":    p.get("protocol", "*"),
        "src_prefix":  p.get("sourceAddressPrefix") or ", ".join(p.get("sourceAddressPrefixes", [])),
        "src_port":    p.get("sourcePortRange") or ", ".join(p.get("sourcePortRanges", [])),
        "dst_prefix":  p.get("destinationAddressPrefix") or ", ".join(p.get("destinationAddressPrefixes", [])),
        "dst_port":    p.get("destinationPortRange") or ", ".join(p.get("destinationPortRanges", [])),
        "description": p.get("description", ""),
        "is_default":  r.get("name", "").startswith("Microsoft.Databricks") or
                       r.get("name", "").startswith("Allow") and p.get("priority", 0) >= 65000,
    }

def risk_rule(rule):
    """Return (severity, reason) if the rule is a risk indicator, else None."""
    if rule["access"] != "Allow":
        return None
    src  = rule["src_prefix"]
    port = rule["dst_port"]
    if rule["direction"] == "Inbound":
        if src in RISK_WIDE_OPEN_SOURCES:
            if port in HIGH_RISK_PORTS or port == "*":
                return ("high",   f"Inbound Allow from {src} to port {port}")
            return ("medium", f"Inbound Allow from {src} to port {port}")
    if rule["direction"] == "Outbound":
        if rule["dst_prefix"] in RISK_WIDE_OPEN_SOURCES and port == "*":
            return ("medium", f"Outbound Allow-All to {rule['dst_prefix']}")
    return None

def clean_nsg(n, sub_id="", sub_name=""):
    p     = n.get("properties", {})
    custom_rules  = [clean_rule(r) for r in p.get("securityRules", [])]
    default_rules = [clean_rule(r) for r in p.get("defaultSecurityRules", [])]
    subnets = [s.get("id", "").split("/")[-1] for s in p.get("subnets", [])]
    nics    = [ni.get("id", "").split("/")[-1] for ni in p.get("networkInterfaces", [])]
    rg      = rg_from_id(n.get("id", ""))

    risks = []
    for r in custom_rules:
        rv = risk_rule(r)
        if rv:
            risks.append({"rule": r["name"], "severity": rv[0], "reason": rv[1]})

    return {
        "id":              n.get("id", ""),
        "name":            n.get("name", ""),
        "rg":              rg,
        "location":        n.get("location", ""),
        "custom_rules":    custom_rules,
        "default_rules":   default_rules,
        "subnets":         subnets,
        "nics":            nics,
        "risks":           risks,
        "orphaned":        not subnets and not nics,
        "subscription_id":   sub_id,
        "subscription_name": sub_name,
        "tags":              n.get("tags") or {},
    }

def clean_subnet(s, nsg_map):
    p         = s.get("properties", {})
    nsg_id    = (p.get("networkSecurityGroup") or {}).get("id", "")
    nsg_name  = nsg_id.split("/")[-1] if nsg_id else ""
    svc_eps   = [ep.get("service", "") for ep in p.get("serviceEndpoints", [])]
    pe_ids    = [pe.get("id", "") for pe in p.get("privateEndpoints", [])]
    delegs    = [d.get("serviceName", "") for d in p.get("delegations", [])]
    prefix    = p.get("addressPrefix") or p.get("addressPrefixes", ["?"])[0]

    risks = []
    if not nsg_name:
        if pe_ids:
            risks.append({"severity": "info", "reason": "PE subnet — no NSG needed"})
        else:
            risks.append({"severity": "medium", "reason": "No NSG attached"})
    if svc_eps and not nsg_name:
        risks.append({"severity": "medium", "reason": f"Service endpoints {svc_eps} without NSG"})

    return {
        "name":       s.get("name", ""),
        "prefix":     prefix,
        "nsg":        nsg_name,
        "nsg_id":     nsg_id,
        "svc_eps":    svc_eps,
        "pe_count":   len(pe_ids),
        "delegations": delegs,
        "risks":      risks,
    }

def clean_vnet(v, token, sub_id="", sub_name=""):
    p        = v.get("properties", {})
    rg       = rg_from_id(v.get("id", ""))
    name     = v.get("name", "")
    location = v.get("location", "")
    addr     = p.get("addressSpace", {}).get("addressPrefixes", [])
    dns      = p.get("dhcpOptions", {}).get("dnsServers", [])

    raw_subnets  = p.get("subnets", [])
    raw_peerings = p.get("virtualNetworkPeerings", [])

    subnets = [clean_subnet(s, {}) for s in raw_subnets]

    peerings = []
    for pr in raw_peerings:
        pp  = pr.get("properties", {})
        remote_id = pp.get("remoteVirtualNetwork", {}).get("id", "")
        peerings.append({
            "name":             pr.get("name", ""),
            "remote_vnet":      res_name(remote_id),
            "remote_vnet_id":   remote_id,
            "state":            pp.get("peeringState", ""),
            "sync_level":       pp.get("peeringSyncLevel", ""),
            "allow_fwd":        pp.get("allowForwardedTraffic", False),
            "allow_gw_transit": pp.get("allowGatewayTransit", False),
            "use_remote_gw":    pp.get("useRemoteGateways", False),
            "allow_vnet_access":pp.get("allowVirtualNetworkAccess", True),
        })

    # Risk flags at VNet level
    risks = []
    for pr in peerings:
        if pr["use_remote_gw"]:
            risks.append({
                "severity": "medium",
                "reason":   f"Peering '{pr['remote_vnet']}' uses remote gateway (traffic exits via peer)"
            })
        if pr["allow_fwd"] and pr["state"] == "Connected":
            risks.append({
                "severity": "info",
                "reason":   f"Peering '{pr['remote_vnet']}' allows forwarded traffic"
            })

    total_ips = sum(cidr_size(a) for a in addr)

    return {
        "id":          v.get("id", ""),
        "name":        name,
        "rg":          rg,
        "location":    location,
        "address_prefixes": addr,
        "total_ips":   total_ips,
        "dns_servers": dns,
        "subnets":     subnets,
        "peerings":    peerings,
        "risks":       risks,
        "subnet_count":      len(subnets),
        "peering_count":     len(peerings),
        "subscription_id":   sub_id,
        "subscription_name": sub_name,
        "tags":              v.get("tags") or {},
    }

def clean_pe(pe, sub_id="", sub_name=""):
    p    = pe.get("properties", {})
    rg   = rg_from_id(pe.get("id", ""))
    conns = (p.get("privateLinkServiceConnections") or
             p.get("manualPrivateLinkServiceConnections") or [])

    connections = []
    for c in conns:
        cp = c.get("properties", {})
        target_id  = cp.get("privateLinkServiceId", "")
        state      = cp.get("privateLinkServiceConnectionState", {})
        connections.append({
            "target_resource": res_name(target_id),
            "target_type":     res_type(target_id),
            "target_id":       target_id,
            "group_ids":       cp.get("groupIds", []),
            "status":          state.get("status", ""),
            "description":     state.get("description", ""),
        })

    dns_cfgs = p.get("customDnsConfigs", [])
    dns = [{
        "fqdn": d.get("fqdn", ""),
        "ips":  d.get("ipAddresses", []),
    } for d in dns_cfgs]

    subnet_id  = p.get("subnet", {}).get("id", "")
    nic_id     = (p.get("networkInterfaces") or [{}])[0].get("id", "")

    # Risk: pending/rejected connections
    risks = []
    for c in connections:
        if c["status"] == "Pending":
            risks.append({"severity": "medium", "reason": f"Connection to {c['target_resource']} is Pending approval"})
        elif c["status"] == "Rejected":
            risks.append({"severity": "high", "reason": f"Connection to {c['target_resource']} was Rejected but endpoint still exists"})

    return {
        "id":          pe.get("id", ""),
        "name":        pe.get("name", ""),
        "rg":          rg,
        "location":    pe.get("location", ""),
        "subnet":      res_name(subnet_id),
        "subnet_vnet": subnet_id.split("/")[-5] if "/subnets/" in subnet_id else "",
        "nic":         res_name(nic_id),
        "connections": connections,
        "dns":         dns,
        "risks":             risks,
        "subscription_id":   sub_id,
        "subscription_name": sub_name,
        "tags":              pe.get("tags") or {},
    }

def clean_public_ip(pip, sub_id="", sub_name=""):
    p  = pip.get("properties", {})
    rg = rg_from_id(pip.get("id", ""))
    assoc_id = (p.get("ipConfiguration") or {}).get("id", "")
    return {
        "name":        pip.get("name", ""),
        "rg":          rg,
        "ip":          p.get("ipAddress", ""),
        "allocation":  p.get("publicIPAllocationMethod", ""),
        "sku":         pip.get("sku", {}).get("name", ""),
        "attached_to": res_name(assoc_id) if assoc_id else "Unattached",
        "attached_type": res_type(assoc_id),
        "dns_label":   (p.get("dnsSettings") or {}).get("domainNameLabel", ""),
        "fqdn":        (p.get("dnsSettings") or {}).get("fqdn", ""),
        "risk":              "high" if p.get("ipAddress") and assoc_id else
                             ("medium" if p.get("ipAddress") else "info"),
        "subscription_id":   sub_id,
        "subscription_name": sub_name,
        "tags":              pip.get("tags") or {},
    }

def clean_nic(nic, sub_id="", sub_name=""):
    p       = nic.get("properties", {})
    rg      = rg_from_id(nic.get("id", ""))
    name    = nic.get("name", "")
    vm_id   = (p.get("virtualMachine") or {}).get("id", "")
    pe_id   = (p.get("privateEndpoint") or {}).get("id", "")
    nsg_id  = (p.get("networkSecurityGroup") or {}).get("id", "")

    ip_cfgs = []
    has_public_ip = False
    for ipc in p.get("ipConfigurations", []):
        ipcp    = ipc.get("properties", {})
        pip_id  = (ipcp.get("publicIPAddress") or {}).get("id", "")
        priv_ip = ipcp.get("privateIPAddress", "")
        subnet  = res_name((ipcp.get("subnet") or {}).get("id", ""))
        if pip_id:
            has_public_ip = True
        ip_cfgs.append({
            "name":       ipc.get("name", ""),
            "private_ip": priv_ip,
            "public_ip":  res_name(pip_id) if pip_id else "",
            "subnet":     subnet,
        })

    # Detect Databricks-managed NICs by naming convention
    is_dbrk = ("privateNIC" in name or "publicNIC" in name or
                "databricks" in rg.lower())
    # "publicNIC" in Databricks is a naming convention — the NIC connects to
    # the databricks-hosts subnet, NOT to an actual internet-facing IP.

    attached_to = ""
    attached_type = ""
    if vm_id:
        attached_to   = res_name(vm_id)
        attached_type = "VirtualMachine"
    elif pe_id:
        attached_to   = res_name(pe_id)
        attached_type = "PrivateEndpoint"

    private_ips = [c["private_ip"] for c in ip_cfgs if c["private_ip"]]
    first_subnet = ip_cfgs[0]["subnet"] if ip_cfgs else ""
    first_pub_ip = next((c["public_ip"] for c in ip_cfgs if c["public_ip"]), "")

    return {
        "name":            name,
        "rg":              rg,
        "attached_to":     attached_to,
        "attached_type":   attached_type,
        "nsg":             res_name(nsg_id),
        "nic_nsg":         res_name(nsg_id),
        "ip_configs":      ip_cfgs,
        "private_ips":     private_ips,
        "subnet":          first_subnet,
        "public_ip":       first_pub_ip,
        "has_public_ip":     has_public_ip,
        "is_databricks":     is_dbrk,
        "is_pe_nic":         bool(pe_id),
        "is_vm_nic":         bool(vm_id),
        "subscription_id":   sub_id,
        "subscription_name": sub_name,
        "tags":              nic.get("tags") or {},
    }

# ── overall risk summary ───────────────────────────────────────────────────────

def compute_risk_summary(vnets, nsgs, pes, pips, nics):
    findings = []

    # Subnets without NSG (excluding PE-only subnets)
    for v in vnets:
        for s in v["subnets"]:
            for r in s["risks"]:
                if r["severity"] != "info":
                    findings.append({
                        "severity": r["severity"],
                        "category": "Subnet",
                        "resource": f"{v['name']} / {s['name']}",
                        "detail":   r["reason"],
                        "subscription_id": v.get("subscription_id", ""),
                    })

    # NSG high-risk rules
    for n in nsgs:
        if n["orphaned"]:
            findings.append({
                "severity": "info",
                "category": "NSG",
                "resource": n["name"],
                "detail":   "NSG is not attached to any subnet or NIC",
                "subscription_id": n.get("subscription_id", ""),
            })
        for r in n["risks"]:
            findings.append({
                "severity": r["severity"],
                "category": "NSG Rule",
                "resource": f"{n['name']} / {r['rule']}",
                "detail":   r["reason"],
                "subscription_id": n.get("subscription_id", ""),
            })

    # VNet-level peering risks
    for v in vnets:
        for r in v["risks"]:
            findings.append({
                "severity": r["severity"],
                "category": "VNet Peering",
                "resource": v["name"],
                "detail":   r["reason"],
                "subscription_id": v.get("subscription_id", ""),
            })

    # PE risks
    for pe in pes:
        for r in pe["risks"]:
            findings.append({
                "severity": r["severity"],
                "category": "Private Endpoint",
                "resource": pe["name"],
                "detail":   r["reason"],
                "subscription_id": pe.get("subscription_id", ""),
            })

    # Public IP resources
    for pip in pips:
        sev = pip["risk"] if pip["ip"] else "info"
        findings.append({
            "severity": sev,
            "category": "Public IP",
            "resource": f"{pip['name']} ({pip['ip'] or 'unallocated'})",
            "detail":   f"Attached to {pip['attached_to']} [{pip['attached_type']}]"
                        if pip["attached_to"] != "Unattached"
                        else "Unattached public IP resource (still billed)",
            "subscription_id": pip.get("subscription_id", ""),
        })

    # NICs with actual public IPs attached
    for nic in nics:
        if nic["has_public_ip"]:
            for cfg in nic["ip_configs"]:
                if cfg["public_ip"]:
                    findings.append({
                        "severity": "high",
                        "category": "NIC Public IP",
                        "resource": f"{nic['name']} ({nic['rg']})",
                        "detail":   f"Public IP '{cfg['public_ip']}' attached to NIC"
                                    + (f" on VM {nic['attached_to']}" if nic["attached_to"] else ""),
                        "subscription_id": nic.get("subscription_id", ""),
                    })

    # Databricks publicNIC naming — info only (not a real public IP)
    dbrk_public_nics = [n for n in nics if "publicNIC" in n["name"] and not n["has_public_ip"]]
    if dbrk_public_nics:
        findings.append({
            "severity": "info",
            "category": "Databricks NICs",
            "resource": f"{len(dbrk_public_nics)} 'publicNIC' interfaces found",
            "detail":   "Databricks naming convention for the 'hosts' subnet NIC — "
                        "no actual internet-facing IP attached. Traffic uses service endpoints.",
            "subscription_id": "",
        })

    # VM NICs without NSG
    for nic in nics:
        if nic["is_vm_nic"] and not nic["nsg"] and not nic["is_databricks"]:
            findings.append({
                "severity": "medium",
                "category": "VM NIC",
                "resource": f"{nic['name']} → {nic['attached_to']}",
                "detail":   "VM NIC has no NSG — relies on subnet-level NSG only",
                "subscription_id": nic.get("subscription_id", ""),
            })

    # Sort: high first
    order = {"high": 0, "medium": 1, "info": 2}
    return sorted(findings, key=lambda f: order.get(f["severity"], 3))

# ── collect ─────────────────────────────────────────────────────────────────────

def get_subscription_name(sub_id):
    try:
        out = subprocess.check_output(
            ["az", "account", "show", "--subscription", sub_id, "--query", "name", "-o", "tsv"],
            stderr=subprocess.DEVNULL
        )
        return out.decode().strip()
    except Exception:
        return sub_id

# Short display labels for subscription badges in the report
SUB_SHORT = {
    "57493fde-eff8-432f-8574-4f1281bd2ce3": "IDOH Prod",
    "5d3a4b9c-0e31-477c-9122-bb3be662e2a9": "Shared Prod",
}

def collect(token):
    print(f"\n=== VNet Metadata — Multi-Subscription ===")

    # Resolve subscription names
    sub_info = {}
    for sub_id in SUBSCRIPTIONS:
        name = get_subscription_name(sub_id)
        sub_info[sub_id] = {"name": name, "short": SUB_SHORT.get(sub_id, sub_id[:8])}
        print(f"  Subscription: {name} ({sub_id})")

    all_vnets, all_nsgs, all_pips, all_nics, all_pes = [], [], [], [], []

    for sub_id in SUBSCRIPTIONS:
        sub_name = sub_info[sub_id]["name"]
        print(f"\n--- {sub_name} ---")

        print(f"  Fetching VNets (subscription-wide)…", end="", flush=True)
        raw_vnets = fetch_vnets_all(sub_id, token)
        print(f" {len(raw_vnets)}")
        for v in raw_vnets:
            all_vnets.append(clean_vnet(v, token, sub_id, sub_name))

        print(f"  Fetching NSGs (subscription-wide)…", end="", flush=True)
        raw_nsgs = fetch_nsgs_all(sub_id, token)
        print(f" {len(raw_nsgs)}")
        for n in raw_nsgs:
            all_nsgs.append(clean_nsg(n, sub_id, sub_name))

        print(f"  Fetching public IPs (subscription-wide)…", end="", flush=True)
        raw_pips = fetch_public_ips_all(sub_id, token)
        print(f" {len(raw_pips)}")
        all_pips.extend([clean_public_ip(p, sub_id, sub_name) for p in raw_pips])

        print(f"  Fetching NICs (subscription-wide)…", end="", flush=True)
        raw_nics = fetch_nics_all(sub_id, token)
        print(f" {len(raw_nics)}")
        all_nics.extend([clean_nic(n, sub_id, sub_name) for n in raw_nics])

        print(f"  Fetching private endpoints (subscription-wide)…", end="", flush=True)
        raw_pes = fetch_private_endpoints(sub_id, token)
        print(f" {len(raw_pes)}")
        all_pes.extend([clean_pe(pe, sub_id, sub_name) for pe in raw_pes])

    risk_findings = compute_risk_summary(all_vnets, all_nsgs, all_pes, all_pips, all_nics)

    total_subnets  = sum(len(v["subnets"]) for v in all_vnets)
    total_peerings = sum(len(v["peerings"]) for v in all_vnets)
    nics_with_pip  = [n for n in all_nics if n["has_public_ip"]]

    return {
        "vnets":             all_vnets,
        "nsgs":              all_nsgs,
        "private_endpoints": all_pes,
        "public_ips":        all_pips,
        "nics":              all_nics,
        "risk_findings":     risk_findings,
        "subscriptions":     sub_info,
        "summary": {
            "vnet_count":    len(all_vnets),
            "subnet_count":  total_subnets,
            "nsg_count":     len(all_nsgs),
            "pe_count":      len(all_pes),
            "peering_count": total_peerings,
            "pip_count":     len(all_pips),
            "nic_count":     len(all_nics),
            "nics_with_pip": len(nics_with_pip),
            "risk_high":     sum(1 for f in risk_findings if f["severity"] == "high"),
            "risk_medium":   sum(1 for f in risk_findings if f["severity"] == "medium"),
            "risk_info":     sum(1 for f in risk_findings if f["severity"] == "info"),
        },
    }

# ── CSS ─────────────────────────────────────────────────────────────────────────

CSS = """
:root{
  --bg:#0f1117;--sur:#1a1d27;--sur2:#252836;--brd:#2e3245;
  --txt:#e2e8f0;--mut:#8892a4;--acc:#6c8eff;--grn:#4ade80;
  --red:#f87171;--yel:#fbbf24;--pur:#c084fc;--cyn:#22d3ee;--org:#fb923c;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;overflow:hidden}
body{background:var(--bg);color:var(--txt);font:13px/1.5 'Segoe UI',system-ui,sans-serif}
.layout{display:flex;height:100vh}

/* sidebar */
.sidebar{width:250px;background:var(--sur);border-right:1px solid var(--brd);
  display:flex;flex-direction:column;overflow:hidden;flex-shrink:0}
.sb-hdr{padding:12px 14px;border-bottom:1px solid var(--brd);font-weight:700;font-size:14px}
.sb-hdr small{display:block;color:var(--mut);font-weight:400;font-size:10px;margin-top:2px}
.sb-body{overflow-y:auto;flex:1;padding:6px 0}
.sb-section{font-size:10px;font-weight:700;color:var(--mut);padding:8px 14px 3px;
  text-transform:uppercase;letter-spacing:.05em}
.sb-item{padding:5px 14px;font-size:11px;cursor:pointer;display:flex;align-items:center;
  gap:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sb-item:hover{background:var(--sur2)}
.sb-item.active{background:var(--sur2);border-left:3px solid var(--acc);padding-left:11px}
.sb-badge{margin-left:auto;flex-shrink:0;font-size:9px;background:var(--sur2);
  border:1px solid var(--brd);border-radius:3px;padding:1px 4px;color:var(--mut)}

/* main */
.main{flex:1;overflow:hidden;display:flex;flex-direction:column;min-width:0}
.main-hdr{padding:16px 24px 0;flex-shrink:0}
h1{font-size:19px;font-weight:700;margin-bottom:2px}
.sub{color:var(--mut);font-size:11px;margin-bottom:12px}

/* stats */
.stats{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:12px}
.sc{background:var(--sur);border:1px solid var(--brd);border-radius:8px;
  padding:10px 14px;min-width:92px;cursor:pointer;transition:border-color .15s}
.sc:hover{border-color:var(--acc)}
.sc.active-card{border-color:var(--acc);background:var(--sur2)}
.sc-n{font-size:20px;font-weight:700;line-height:1.1}
.sc-l{font-size:10px;color:var(--mut);margin-top:2px}

/* tabs */
.tabs{display:flex;gap:2px;border-bottom:2px solid var(--brd);padding:0 24px;
  flex-shrink:0;flex-wrap:wrap;background:var(--bg)}
.tab{padding:6px 12px;cursor:pointer;border-radius:6px 6px 0 0;font-size:12px;
  font-weight:600;color:var(--mut);border:1px solid transparent;
  border-bottom:none;margin-bottom:-2px;user-select:none;white-space:nowrap}
.tab:hover{color:var(--txt);background:var(--sur)}
.tab.active{background:var(--sur);border-color:var(--brd);border-bottom-color:var(--sur);color:var(--txt)}

/* content */
.content{flex:1;overflow-y:auto;padding:14px 24px}
.panel{display:none}.panel.active{display:block}
h2{font-size:13px;font-weight:700;margin:14px 0 8px;padding-bottom:4px;
  border-bottom:1px solid var(--brd)}

/* filter */
.filter-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
.filter-row input,.filter-row select{padding:5px 10px;background:var(--sur);
  border:1px solid var(--brd);border-radius:5px;color:var(--txt);font-size:12px;outline:none}
.filter-row input:focus,.filter-row select:focus{border-color:var(--acc)}
.filter-row input{width:240px}

/* tables */
table{width:100%;border-collapse:collapse;font-size:12px}
th{background:var(--sur2);padding:6px 10px;text-align:left;font-weight:700;
  border-bottom:2px solid var(--brd);white-space:nowrap;position:sticky;top:0;z-index:2}
td{padding:5px 10px;border-bottom:1px solid var(--brd);vertical-align:top}
tr:hover td{background:var(--sur)}
.hidden{display:none!important}
.mono{font-family:monospace;font-size:11px}
.mut{color:var(--mut);font-size:11px}
.trunc{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.clickrow{cursor:pointer}.clickrow:hover td{background:#1e2535}

/* chips */
.chip{display:inline-block;font-size:10px;padding:2px 7px;border-radius:3px;font-weight:700;white-space:nowrap}
.chip-allow{background:#1a3a2a;color:#4ade80}
.chip-deny {background:#3a1a1a;color:#f87171}
.chip-in   {background:#1e2a4a;color:#6c8eff}
.chip-out  {background:#2a1a4a;color:#c084fc}
.chip-ok   {background:#1a3a2a;color:#4ade80}
.chip-warn {background:#2a2a0a;color:#fbbf24}
.chip-err  {background:#3a1a1a;color:#f87171}
.chip-info  {background:var(--sur2);color:var(--mut)}
.chip-active{background:#1a3a2a;color:#4ade80}
.chip-dbrk  {background:#2a1e4a;color:#c084fc}
.tag-pill{display:inline-block;font-size:10px;padding:1px 6px;border-radius:3px;
  background:var(--sur2);border:1px solid var(--brd);color:var(--mut);margin:1px 2px;white-space:nowrap}
.tag-pill b{color:var(--txt);font-weight:600}
.chip-rg   {font-size:10px;padding:2px 7px;border-radius:3px;
  background:#1e2a4a;color:var(--acc);font-weight:700}

/* risk severity */
.sev-high  {color:var(--red);font-weight:700;font-size:11px}
.sev-medium{color:var(--yel);font-weight:700;font-size:11px}
.sev-info  {color:var(--mut);font-size:11px}
.risk-row-high   td{border-left:3px solid var(--red)}
.risk-row-medium td{border-left:3px solid var(--yel)}
.risk-row-info   td{border-left:3px solid var(--brd)}

/* overview cards */
.ov-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:9px;margin-bottom:16px}
.ov-card{background:var(--sur);border:1px solid var(--brd);border-radius:8px;
  padding:12px 14px;cursor:pointer;transition:border-color .15s}
.ov-card:hover{border-color:var(--acc)}
.ov-card h3{font-size:12px;color:var(--acc);margin-bottom:5px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ov-card .kv{font-size:11px;color:var(--mut);line-height:1.7}
.ov-card .kv b{color:var(--txt)}
.cidr-pill{display:inline-block;font-family:monospace;font-size:10px;
  padding:2px 7px;border-radius:3px;background:#1e2a4a;color:var(--cyn);
  border:1px solid #2a3a5a;margin-right:3px;margin-bottom:2px}
.svc-ep{display:inline-block;font-size:10px;padding:1px 5px;border-radius:3px;
  background:#2a1a4a;color:var(--pur);margin-right:3px;margin-bottom:2px}
.peer-row{display:flex;align-items:center;gap:6px;font-size:11px;
  padding:4px 0;border-bottom:1px solid var(--brd)}
.peer-row:last-child{border:none}
.flag-pill{font-size:9px;padding:1px 5px;border-radius:3px;font-weight:700}
.flag-on {background:#1a3a2a;color:#4ade80}
.flag-off{background:var(--sur2);color:var(--mut)}
.flag-warn{background:#3a2a0a;color:#fbbf24}

/* modal */
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.65);
  z-index:100;align-items:center;justify-content:center}
.modal-overlay.open{display:flex}
.modal{background:var(--sur);border:1px solid var(--brd);border-radius:10px;
  width:min(900px,96vw);max-height:90vh;overflow-y:auto;padding:22px 26px}
.modal h2{font-size:15px;font-weight:700;margin-bottom:14px;
  display:flex;justify-content:space-between;align-items:center}
.modal-close{cursor:pointer;color:var(--mut);font-size:18px;padding:4px 8px;
  border-radius:4px;background:var(--sur2)}
.modal-close:hover{color:var(--txt)}
.kv-table{width:100%;font-size:12px;border-collapse:collapse}
.kv-table td{padding:5px 10px;border-bottom:1px solid var(--brd)}
.kv-table td:first-child{color:var(--mut);width:160px;font-weight:700;white-space:nowrap}
.slabel{font-size:11px;font-weight:700;color:var(--mut);margin:12px 0 6px;
  text-transform:uppercase;letter-spacing:.05em}

/* topology */
.topo-line{display:flex;align-items:center;gap:8px;padding:5px 0;
  border-bottom:1px solid var(--brd);font-size:11px}
.topo-line:last-child{border:none}
.topo-icon{font-size:14px;flex-shrink:0;width:22px;text-align:center}
.arrow{color:var(--mut);font-size:12px}
"""

# ── JavaScript ──────────────────────────────────────────────────────────────────

JS = r"""
function esc(s){ return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

// ── tab switching ──────────────────────────────────────────────────────────────
const panelInited={};
function showTab(id){
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.sc').forEach(c=>c.classList.remove('active-card'));
  const pan=document.getElementById('p-'+id);
  const tab=document.getElementById('tab-'+id);
  const card=document.getElementById('card-'+id);
  if(pan)  pan.classList.add('active');
  if(tab)  tab.classList.add('active');
  if(card) card.classList.add('active-card');
  if(!panelInited[id]){ panelInited[id]=true; renderPanel(id); }
}

// ── sidebar filter ─────────────────────────────────────────────────────────────
let activeVNet='__all__';
let activeSub='__all__';
function sbSelect(name, el, sub){
  document.querySelectorAll('.sb-item').forEach(e=>e.classList.remove('active'));
  if(el) el.classList.add('active');
  activeVNet=name;
  activeSub=sub||'__all__';
  const active=document.querySelector('.panel.active');
  if(active){ renderPanel(active.id.replace('p-','')); }
}

function matchVNet(item){
  if(activeSub!=='__all__' && item.subscription_id!==activeSub) return false;
  if(activeVNet==='__all__') return true;
  return item.name===activeVNet || item.vnet===activeVNet || item.subnet_vnet===activeVNet;
}

// ── subscription badge ─────────────────────────────────────────────────────────
const SUB_STYLE={
  '57493fde-eff8-432f-8574-4f1281bd2ce3':{label:'IDOH Prod',  color:'var(--acc)'},
  '5d3a4b9c-0e31-477c-9122-bb3be662e2a9':{label:'Shared Prod',color:'var(--cyn)'},
};
function subBadge(sid){
  const i=SUB_STYLE[sid]||{label:(sid||'').slice(0,8),color:'var(--mut)'};
  return `<span class="chip" style="border:1px solid ${i.color}44;color:${i.color};background:${i.color}18">${i.label}</span>`;
}
function subFilter(panelId){
  const sel=document.getElementById('sub-sel-'+panelId);
  return sel?sel.value:'';
}

// ── tags ───────────────────────────────────────────────────────────────────────
function tagPills(tags){
  if(!tags) return '';
  const entries=Object.entries(tags).filter(([k])=>k.trim());
  if(!entries.length) return '';
  return '<div style="margin-top:4px">'+entries.map(([k,v])=>
    `<span class="tag-pill"><b>${esc(k.trim())}</b>: ${esc(v)}</span>`
  ).join('')+'</div>';
}
function tagsMatch(tags, q){
  if(!tags||!q) return false;
  return Object.entries(tags).some(([k,v])=>
    k.toLowerCase().includes(q)||String(v).toLowerCase().includes(q)
  );
}

// ── chips ──────────────────────────────────────────────────────────────────────
function accessChip(a){ return a==='Allow'?'<span class="chip chip-allow">Allow</span>':'<span class="chip chip-deny">Deny</span>'; }
function dirChip(d)   { return d==='Inbound'?'<span class="chip chip-in">Inbound</span>':'<span class="chip chip-out">Outbound</span>'; }
function sevChip(s){
  if(s==='high')   return '<span class="chip chip-err">HIGH</span>';
  if(s==='medium') return '<span class="chip chip-warn">MEDIUM</span>';
  return '<span class="chip chip-info">INFO</span>';
}
function stateChip(s){
  if(s==='Connected') return '<span class="chip chip-ok">Connected</span>';
  if(s==='Initiated') return '<span class="chip chip-warn">Initiated</span>';
  return `<span class="chip chip-info">${esc(s)}</span>`;
}
function flagPill(val, warnIfTrue){
  const on=!!val;
  const cl= on&&warnIfTrue ? 'flag-warn' : on ? 'flag-on' : 'flag-off';
  return `<span class="flag-pill ${cl}">${on?'Yes':'No'}</span>`;
}

// ── render panels ──────────────────────────────────────────────────────────────
function renderPanel(id){
  if(id==='overview')  renderOverview();
  if(id==='vnets')     renderVNets();
  if(id==='nsgs')      renderNSGs();
  if(id==='endpoints') renderEndpoints();
  if(id==='peerings')  renderPeerings();
  if(id==='nics')      renderNICs();
  if(id==='pips')      renderPublicIPs();
  if(id==='risk')      renderRisk();
}

// ── OVERVIEW ──────────────────────────────────────────────────────────────────
function renderOverview(){
  const vnets=DATA.vnets.filter(matchVNet);
  let cards=vnets.map(v=>{
    const cidrs=v.address_prefixes.map(a=>`<span class="cidr-pill">${esc(a)}</span>`).join('');
    const svcEPs=[...new Set(v.subnets.flatMap(s=>s.svc_eps))];
    const epStr=svcEPs.map(e=>`<span class="svc-ep">${esc(e.replace('Microsoft.',''))}</span>`).join('');
    const peTotal=v.subnets.reduce((a,s)=>a+s.pe_count,0);
    const noNsgSubs=v.subnets.filter(s=>!s.nsg&&s.pe_count===0).length;
    const ownerTag=v.tags?.['Resource-Owner']||v.tags?.['resource-owner']||'';
    const envTag=v.tags?.['Environment-Tier']||v.tags?.['Environment']||'';
    const agencyTag=v.tags?.['Agency-Name']||'';
    return `<div class="ov-card" onclick="openVNetModal('${esc(v.name)}')">
      <h3>🌐 ${esc(v.name)}</h3>
      <div class="kv">
        <div>${subBadge(v.subscription_id)}&nbsp;<span class="chip-rg">${esc(v.rg)}</span></div>
        <div>${cidrs}</div>
        <div>Subnets: <b>${v.subnet_count}</b> &nbsp;·&nbsp; Peerings: <b>${v.peering_count}</b></div>
        <div>Private Endpoints: <b>${peTotal}</b></div>
        ${agencyTag?`<div class="mut">Agency: <b>${esc(agencyTag)}</b></div>`:''}
        ${envTag?`<div class="mut">Env: <b>${esc(envTag)}</b></div>`:''}
        ${ownerTag?`<div class="mut" style="font-size:10px">Owner: ${esc(ownerTag)}</div>`:''}
        ${svcEPs.length?`<div>Service Endpoints: ${epStr}</div>`:''}
        ${noNsgSubs?`<div style="color:var(--yel)">⚠ ${noNsgSubs} subnet(s) without NSG</div>`:''}
        ${v.risks.some(r=>r.severity==='medium')?`<div style="color:var(--yel)">⚠ ${v.risks.filter(r=>r.severity==='medium').length} peering risk(s)</div>`:''}
      </div></div>`;
  }).join('');
  document.getElementById('ov-vnets').innerHTML=cards||'<p class="mut">No VNets.</p>';
}

// ── VNets & SUBNETS ───────────────────────────────────────────────────────────
function renderVNets(){
  const subF=subFilter('vnets');
  const vnets=DATA.vnets.filter(matchVNet).filter(v=>!subF||v.subscription_id===subF);
  const q=(document.getElementById('vnet-search')?.value||'').toLowerCase();
  const filtered=vnets.filter(v=>!q||v.name.toLowerCase().includes(q)||v.rg.toLowerCase().includes(q)||tagsMatch(v.tags,q));
  document.getElementById('vnet-search').oninput=renderVNets;
  document.getElementById('sub-sel-vnets').onchange=renderVNets;

  let html='';
  filtered.forEach(v=>{
    const cidrs=v.address_prefixes.map(a=>`<span class="cidr-pill">${esc(a)}</span>`).join(' ');
    const dns=v.dns_servers.length?v.dns_servers.join(', '):'Azure Default';
    html+=`<tr class="clickrow" onclick="openVNetModal('${esc(v.name)}')" style="background:var(--sur2)">
      <td colspan="8" style="padding:8px 10px">
        <b style="font-size:13px">🌐 ${esc(v.name)}</b>
        &nbsp;${subBadge(v.subscription_id)}
        &nbsp;<span class="chip-rg">${esc(v.rg)}</span>
        &nbsp;${cidrs}
        &nbsp;<span class="mut">DNS: ${esc(dns)}</span>
        ${tagPills(v.tags)}
      </td></tr>`;
    v.subnets.forEach(s=>{
      const nsgCell=s.nsg
        ? `<span style="color:var(--grn)">🛡 ${esc(s.nsg)}</span>`
        : `<span style="color:var(--yel)">⚠ None</span>`;
      const eps=s.svc_eps.map(e=>`<span class="svc-ep">${esc(e.replace('Microsoft.',''))}</span>`).join('');
      const deleg=s.delegations.length?`<span class="mut">${s.delegations.map(esc).join(', ')}</span>`:'';
      const riskIcons=s.risks.filter(r=>r.severity!=='info').map(r=>`<span title="${esc(r.reason)}" style="color:${r.severity==='high'?'var(--red)':'var(--yel)'}">⚠</span>`).join('');
      html+=`<tr>
        <td style="padding-left:24px" class="mono">${esc(s.name)}</td>
        <td class="mono">${esc(s.prefix)}</td>
        <td>${nsgCell}</td>
        <td>${eps}</td>
        <td style="text-align:center">${s.pe_count>0?`<b style="color:var(--grn)">${s.pe_count}</b>`:'<span class="mut">0</span>'}</td>
        <td>${deleg}</td>
        <td>${riskIcons}</td>
      </tr>`;
    });
  });
  document.getElementById('vnet-tbody').innerHTML=html||'<tr><td colspan="8" class="mut" style="padding:12px">No VNets.</td></tr>';
}

// ── NSGs ──────────────────────────────────────────────────────────────────────
function renderNSGs(){
  const subF=subFilter('nsgs');
  let nsgs=DATA.nsgs;
  if(subF) nsgs=nsgs.filter(n=>n.subscription_id===subF);
  const q=(document.getElementById('nsg-search')?.value||'').toLowerCase();
  document.getElementById('nsg-search').oninput=renderNSGs;
  document.getElementById('sub-sel-nsgs').onchange=renderNSGs;
  const filtered=nsgs.filter(n=>!q||n.name.toLowerCase().includes(q)||n.subnets.some(s=>s.toLowerCase().includes(q))||tagsMatch(n.tags,q));

  let html='';
  filtered.forEach(n=>{
    const attached=n.subnets.length?n.subnets.map(esc).join(', '):'<span class="mut">Unattached</span>';
    const riskBadge=n.risks.length
      ?`<span class="chip chip-err">${n.risks.length} risk(s)</span>`
      :(n.orphaned?'<span class="chip chip-warn">Orphaned</span>':'<span class="chip chip-ok">Clean</span>');
    html+=`<tr class="clickrow" onclick="openNSGModal('${esc(n.name)}')" style="background:var(--sur2)">
      <td colspan="8" style="padding:8px 10px">
        <b style="font-size:13px">🛡 ${esc(n.name)}</b>
        &nbsp;${subBadge(n.subscription_id)}
        &nbsp;<span class="chip-rg">${esc(n.rg)}</span>
        &nbsp;${riskBadge}
        &nbsp;<span class="mut">Attached: ${attached}</span>
        ${tagPills(n.tags)}
      </td></tr>`;
    // custom rules
    n.custom_rules.forEach(r=>{
      const rv=riskFlag(r);
      html+=`<tr class="${rv?'risk-row-'+rv:''}">
        <td style="padding-left:24px"><b>${esc(r.name)}</b></td>
        <td>${dirChip(r.direction)}</td>
        <td>${accessChip(r.access)}</td>
        <td class="mono">${esc(r.protocol)}</td>
        <td class="mono">${esc(r.src_prefix)} <span class="mut">:${esc(r.src_port)}</span></td>
        <td class="mono">${esc(r.dst_prefix)} <span class="mut">:${esc(r.dst_port)}</span></td>
        <td>${r.priority??'—'}</td>
        <td>${rv?sevChip(rv):''}</td>
      </tr>`;
    });
    if(!n.custom_rules.length){
      html+=`<tr><td colspan="8" style="padding:5px 24px" class="mut">No custom rules (default rules only)</td></tr>`;
    }
  });
  document.getElementById('nsg-tbody').innerHTML=html||'<tr><td colspan="8" class="mut" style="padding:12px">No NSGs.</td></tr>';
}

function riskFlag(rule){
  if(rule.access!=='Allow') return null;
  const wideOpen=['*','Internet','Any','0.0.0.0/0'];
  const hotPorts=['22','3389','445','1433','3306','5432','27017','*'];
  if(rule.direction==='Inbound' && wideOpen.includes(rule.src_prefix)){
    if(hotPorts.includes(rule.dst_port)||rule.dst_port==='*') return 'high';
    return 'medium';
  }
  if(rule.direction==='Outbound' && wideOpen.includes(rule.dst_prefix) && rule.dst_port==='*') return 'medium';
  return null;
}

// ── PRIVATE ENDPOINTS ─────────────────────────────────────────────────────────
function renderEndpoints(){
  const subF=subFilter('endpoints');
  const q=(document.getElementById('pe-search')?.value||'').toLowerCase();
  document.getElementById('pe-search').oninput=renderEndpoints;
  document.getElementById('sub-sel-endpoints').onchange=renderEndpoints;
  let data=DATA.private_endpoints;
  if(subF) data=data.filter(pe=>pe.subscription_id===subF);
  if(q) data=data.filter(pe=>pe.name.toLowerCase().includes(q)||pe.connections.some(c=>c.target_resource.toLowerCase().includes(q))||tagsMatch(pe.tags,q));

  function row(pe){
    const conn=pe.connections[0]||{};
    const dns=pe.dns.map(d=>`<span class="mono" style="font-size:10px">${esc(d.fqdn)} → ${(d.ips||[]).join(', ')}</span>`).join('<br>');
    const status=conn.status==='Approved'?'<span class="chip chip-ok">Approved</span>':
                 conn.status==='Pending' ?'<span class="chip chip-warn">Pending</span>':
                 conn.status==='Rejected'?'<span class="chip chip-err">Rejected</span>':
                 `<span class="chip chip-info">${esc(conn.status)}</span>`;
    const gids=(conn.group_ids||[]).map(g=>`<span class="svc-ep">${esc(g)}</span>`).join(' ');
    return `<tr>
      <td><b>${esc(pe.name)}</b><br>${subBadge(pe.subscription_id)}${tagPills(pe.tags)}</td>
      <td class="mut">${esc(pe.rg)}</td>
      <td><b>${esc(conn.target_resource||'—')}</b></td>
      <td class="mut" style="font-size:10px">${esc((conn.target_type||'').replace('Microsoft.',''))}</td>
      <td>${gids}</td>
      <td>${status}</td>
      <td class="mono" style="font-size:10px">${esc(pe.subnet)}</td>
      <td>${dns}</td>
    </tr>`;
  }

  document.getElementById('pe-tbody').innerHTML=data.map(row).join('')||
    '<tr><td colspan="8" class="mut" style="padding:12px">No private endpoints.</td></tr>';
  document.getElementById('pe-count').textContent=`${data.length} of ${DATA.private_endpoints.length}`;
}

// ── PEERINGS ──────────────────────────────────────────────────────────────────
function renderPeerings(){
  const subF=subFilter('peerings');
  const q=(document.getElementById('peer-search')?.value||'').toLowerCase();
  document.getElementById('peer-search').oninput=renderPeerings;
  document.getElementById('sub-sel-peerings').onchange=renderPeerings;
  let rows=DATA.vnets.flatMap(v=>v.peerings.map(p=>({...p, local_vnet:v.name, rg:v.rg, subscription_id:v.subscription_id})));
  if(subF) rows=rows.filter(r=>r.subscription_id===subF);
  const filtered=rows.filter(p=>!q||p.local_vnet.toLowerCase().includes(q)||p.remote_vnet.toLowerCase().includes(q));

  function row(p){
    return `<tr>
      <td><b>${esc(p.local_vnet)}</b><br>${subBadge(p.subscription_id)}</td>
      <td class="mut" style="font-size:10px">${esc(p.rg)}</td>
      <td>→</td>
      <td><b>${esc(p.remote_vnet)}</b></td>
      <td>${stateChip(p.state)}</td>
      <td>${flagPill(p.allow_fwd, false)}</td>
      <td>${flagPill(p.allow_gw_transit, false)}</td>
      <td>${flagPill(p.use_remote_gw, true)}</td>
      <td>${flagPill(p.allow_vnet_access, false)}</td>
    </tr>`;
  }
  document.getElementById('peer-tbody').innerHTML=filtered.map(row).join('')||
    '<tr><td colspan="9" class="mut" style="padding:12px">No peerings.</td></tr>';
}

// ── NICs & VMs ────────────────────────────────────────────────────────────────
function renderNICs(){
  const subF=subFilter('nics');
  const q=(document.getElementById('nic-search')?.value||'').toLowerCase();
  const typeF=(document.getElementById('nic-type-sel')?.value||'').toLowerCase();
  document.getElementById('nic-search').oninput=renderNICs;
  document.getElementById('nic-type-sel').onchange=renderNICs;
  document.getElementById('sub-sel-nics').onchange=renderNICs;

  let nics=DATA.nics||[];
  if(subF) nics=nics.filter(n=>n.subscription_id===subF);
  if(q) nics=nics.filter(n=>n.name.toLowerCase().includes(q)||n.rg.toLowerCase().includes(q)||(n.attached_to||'').toLowerCase().includes(q)||(n.subnet||'').toLowerCase().includes(q)||tagsMatch(n.tags,q));
  if(typeF==='vm')   nics=nics.filter(n=>n.is_vm_nic&&!n.is_databricks);
  if(typeF==='pe')   nics=nics.filter(n=>n.is_pe_nic);
  if(typeF==='dbrk') nics=nics.filter(n=>n.is_databricks);

  function typeChip(n){
    if(n.is_databricks) return '<span class="chip chip-dbrk">Databricks</span>';
    if(n.is_pe_nic)     return '<span class="chip chip-info">Private Endpoint</span>';
    if(n.is_vm_nic)     return '<span class="chip chip-active">VM</span>';
    return '<span class="chip">Unknown</span>';
  }
  function pipCell(n){
    if(n.has_public_ip) return `<span class="sev-high">⚠ ${esc(n.public_ip||'Yes')}</span>`;
    return '<span class="mut">None</span>';
  }
  function nsgCell(n){
    if(n.nic_nsg) return `<span style="color:var(--grn)">🛡 ${esc(n.nic_nsg)}</span>`;
    return '<span class="mut">—</span>';
  }

  function row(n){
    return `<tr>
      <td class="mono"><b>${esc(n.name)}</b><br>${subBadge(n.subscription_id)}${tagPills(n.tags)}</td>
      <td class="mut" style="font-size:10px">${esc(n.rg)}</td>
      <td>${esc(n.attached_to||'—')}</td>
      <td>${typeChip(n)}</td>
      <td class="mono">${(n.private_ips||[]).join(', ')||'—'}</td>
      <td>${pipCell(n)}</td>
      <td class="mono mut">${esc(n.subnet||'—')}</td>
      <td>${nsgCell(n)}</td>
    </tr>`;
  }
  document.getElementById('nic-tbody').innerHTML=nics.map(row).join('')||
    '<tr><td colspan="8" class="mut" style="padding:12px">No NICs found.</td></tr>';
}

// ── PUBLIC IPs ────────────────────────────────────────────────────────────────
function renderPublicIPs(){
  const subF=subFilter('pips');
  const q=(document.getElementById('pip-search')?.value||'').toLowerCase();
  document.getElementById('pip-search').oninput=renderPublicIPs;
  document.getElementById('sub-sel-pips').onchange=renderPublicIPs;

  let pips=DATA.public_ips||[];
  if(subF) pips=pips.filter(p=>p.subscription_id===subF);
  if(q) pips=pips.filter(p=>
    p.name.toLowerCase().includes(q)||
    p.rg.toLowerCase().includes(q)||
    (p.ip||'').includes(q)||
    (p.attached_to||'').toLowerCase().includes(q)||
    tagsMatch(p.tags,q)
  );

  function riskCell(p){
    if(p.risk==='high')   return '<span class="chip chip-err">HIGH</span>';
    if(p.risk==='medium') return '<span class="chip chip-warn">MEDIUM</span>';
    return '<span class="chip chip-info">INFO</span>';
  }
  function row(p){
    const dns=[p.dns_label,p.fqdn].filter(Boolean).join(' / ');
    return `<tr class="risk-row-${p.risk}">
      <td><b>${esc(p.name)}</b><br>${subBadge(p.subscription_id)}${tagPills(p.tags)}</td>
      <td class="mut" style="font-size:10px">${esc(p.rg)}</td>
      <td class="mono"><b style="color:var(--red)">${esc(p.ip||'unallocated')}</b></td>
      <td>${esc(p.sku)}</td>
      <td>${esc(p.allocation)}</td>
      <td>${p.attached_to==='Unattached'?'<span class="mut">Unattached</span>':esc(p.attached_to)}</td>
      <td class="mut" style="font-size:10px">${esc((p.attached_type||'').replace('Microsoft.',''))}</td>
      <td class="mono mut" style="font-size:10px">${esc(dns||'—')}</td>
      <td>${riskCell(p)}</td>
    </tr>`;
  }

  document.getElementById('pip-tbody').innerHTML=pips.map(row).join('')||
    '<tr><td colspan="9" class="mut" style="padding:12px">No public IPs found.</td></tr>';
  document.getElementById('pip-count').textContent=`${pips.length} of ${(DATA.public_ips||[]).length}`;
}

function goToRisk(sev){
  showTab('risk');
  const sel=document.getElementById('risk-sev-sel');
  if(sel){ sel.value=sev; }
  renderRisk();
}

// ── RISK SUMMARY ──────────────────────────────────────────────────────────────
function renderRisk(){
  const subF=subFilter('risk');
  const sevF=(document.getElementById('risk-sev-sel')?.value||'').toLowerCase();
  const q=(document.getElementById('risk-search')?.value||'').toLowerCase();
  document.getElementById('risk-search').oninput=renderRisk;
  document.getElementById('risk-sev-sel').onchange=renderRisk;
  document.getElementById('sub-sel-risk').onchange=renderRisk;
  let data=DATA.risk_findings;
  if(subF) data=data.filter(f=>f.subscription_id===subF);
  if(sevF) data=data.filter(f=>f.severity===sevF);
  if(q) data=data.filter(f=>f.resource.toLowerCase().includes(q)||f.detail.toLowerCase().includes(q)||f.category.toLowerCase().includes(q));

  function row(f){
    return `<tr class="risk-row-${f.severity}">
      <td>${sevChip(f.severity)}</td>
      <td><span class="chip chip-info">${esc(f.category)}</span></td>
      <td><b>${esc(f.resource)}</b>${f.subscription_id?'<br>'+subBadge(f.subscription_id):''}</td>
      <td class="mut">${esc(f.detail)}</td>
    </tr>`;
  }
  document.getElementById('risk-tbody').innerHTML=data.map(row).join('')||
    '<tr><td colspan="4" class="mut" style="padding:12px">No risk findings.</td></tr>';
  document.getElementById('risk-count').textContent=`${data.length} finding(s)`;
}

// ── MODALS ─────────────────────────────────────────────────────────────────────
function closeModal(id){ document.getElementById(id).classList.remove('open'); }
document.addEventListener('keydown',e=>{
  if(e.key==='Escape') document.querySelectorAll('.modal-overlay.open').forEach(m=>m.classList.remove('open'));
});

function openVNetModal(name){
  const v=DATA.vnets.find(v=>v.name===name); if(!v) return;
  const pes=DATA.private_endpoints.filter(pe=>pe.subnet_vnet===name);

  const subnetRows=v.subnets.map(s=>{
    const eps=s.svc_eps.map(e=>`<span class="svc-ep">${esc(e.replace('Microsoft.',''))}</span>`).join(' ');
    const riskHtml=s.risks.map(r=>`<span class="${r.severity==='high'?'sev-high':r.severity==='medium'?'sev-medium':'sev-info'}">${esc(r.reason)}</span>`).join('<br>');
    return `<tr>
      <td class="mono"><b>${esc(s.name)}</b></td>
      <td class="mono">${esc(s.prefix)}</td>
      <td>${s.nsg?`<span style="color:var(--grn)">🛡 ${esc(s.nsg)}</span>`:'<span style="color:var(--yel)">⚠ None</span>'}</td>
      <td>${eps}</td>
      <td>${s.pe_count}</td>
      <td>${riskHtml}</td>
    </tr>`;
  }).join('');

  const peerRows=v.peerings.map(p=>`<div class="peer-row">
    <span class="mono" style="color:var(--acc)">${esc(v.name)}</span>
    <span class="arrow">→</span>
    <span class="mono">${esc(p.remote_vnet)}</span>
    &nbsp;${stateChip(p.state)}
    <span class="flag-pill ${p.allow_fwd?'flag-on':'flag-off'}">FwdTraffic:${p.allow_fwd?'Y':'N'}</span>
    <span class="flag-pill ${p.use_remote_gw?'flag-warn':'flag-off'}">RemoteGW:${p.use_remote_gw?'Y':'N'}</span>
    <span class="flag-pill ${p.allow_gw_transit?'flag-on':'flag-off'}">GWTransit:${p.allow_gw_transit?'Y':'N'}</span>
  </div>`).join('');

  const peRows=pes.map(pe=>{
    const conn=pe.connections[0]||{};
    return `<tr>
      <td>${esc(pe.name)}</td>
      <td>${esc(conn.target_resource||'—')}</td>
      <td>${(conn.group_ids||[]).map(g=>`<span class="svc-ep">${esc(g)}</span>`).join(' ')}</td>
      <td>${conn.status==='Approved'?'<span class="chip chip-ok">Approved</span>':`<span class="chip chip-warn">${esc(conn.status)}</span>`}</td>
      <td class="mono" style="font-size:10px">${esc(pe.subnet)}</td>
    </tr>`;
  }).join('');

  document.getElementById('vnet-modal-body').innerHTML=`
    <table class="kv-table">
      <tr><td>Resource Group</td><td><span class="chip-rg">${esc(v.rg)}</span></td></tr>
      <tr><td>Location</td><td>${esc(v.location)}</td></tr>
      <tr><td>Address Space</td><td>${v.address_prefixes.map(a=>`<span class="cidr-pill">${esc(a)}</span>`).join(' ')}</td></tr>
      <tr><td>Total IPs</td><td>${v.total_ips.toLocaleString()}</td></tr>
      <tr><td>DNS Servers</td><td class="mono">${v.dns_servers.length?v.dns_servers.join(', '):'Azure Default'}</td></tr>
    </table>

    <div class="slabel" style="margin-top:14px">Subnets (${v.subnets.length})</div>
    <table><thead><tr><th>Name</th><th>Prefix</th><th>NSG</th><th>Service Endpoints</th><th>PEs</th><th>Risks</th></tr></thead>
    <tbody>${subnetRows}</tbody></table>

    ${v.peerings.length?`
    <div class="slabel" style="margin-top:14px">VNet Peerings (${v.peerings.length})</div>
    <div>${peerRows}</div>`:''}

    ${pes.length?`
    <div class="slabel" style="margin-top:14px">Private Endpoints in this VNet (${pes.length})</div>
    <table><thead><tr><th>Endpoint</th><th>Target</th><th>Sub-resource</th><th>Status</th><th>Subnet</th></tr></thead>
    <tbody>${peRows}</tbody></table>`:''}
  `;
  document.getElementById('vnet-modal-title').textContent=name;
  document.getElementById('vnet-modal').classList.add('open');
}

function openNSGModal(name){
  const n=DATA.nsgs.find(n=>n.name===name); if(!n) return;

  function ruleRows(rules, showRisk){
    return rules.map(r=>{
      const rv=showRisk?riskFlag(r):null;
      return `<tr class="${rv?'risk-row-'+rv:''}">
        <td>${r.priority??'—'}</td>
        <td>${esc(r.name)}</td>
        <td>${dirChip(r.direction)}</td>
        <td>${accessChip(r.access)}</td>
        <td class="mono">${esc(r.protocol)}</td>
        <td class="mono">${esc(r.src_prefix)}<span class="mut">:${esc(r.src_port)}</span></td>
        <td class="mono">${esc(r.dst_prefix)}<span class="mut">:${esc(r.dst_port)}</span></td>
        ${showRisk?`<td>${rv?sevChip(rv):''}</td>`:''}
      </tr>`;
    }).join('');
  }

  const colHead=`<th>Priority</th><th>Name</th><th>Direction</th><th>Access</th><th>Protocol</th><th>Source</th><th>Destination</th>`;
  document.getElementById('nsg-modal-body').innerHTML=`
    <table class="kv-table">
      <tr><td>Resource Group</td><td><span class="chip-rg">${esc(n.rg)}</span></td></tr>
      <tr><td>Attached Subnets</td><td>${n.subnets.map(esc).join(', ')||'<span class="mut">None (orphaned)</span>'}</td></tr>
      <tr><td>Attached NICs</td><td>${n.nics.map(esc).join(', ')||'<span class="mut">None</span>'}</td></tr>
      <tr><td>Custom Rules</td><td>${n.custom_rules.length}</td></tr>
      <tr><td>Risk Findings</td><td>${n.risks.length?n.risks.map(r=>`<span class="${r.severity==='high'?'sev-high':'sev-medium'}">${esc(r.reason)}</span>`).join('<br>'):'<span class="chip chip-ok">None</span>'}</td></tr>
    </table>

    <div class="slabel" style="margin-top:14px">Custom Rules (${n.custom_rules.length})</div>
    ${n.custom_rules.length?`
    <table><thead><tr>${colHead}<th>Risk</th></tr></thead>
    <tbody>${ruleRows(n.custom_rules,true)}</tbody></table>`
    :'<p class="mut" style="padding:6px">No custom rules — using Azure defaults only.</p>'}

    <div class="slabel" style="margin-top:14px">Default Rules</div>
    <table><thead><tr>${colHead}</tr></thead>
    <tbody>${ruleRows(n.default_rules,false)}</tbody></table>
  `;
  document.getElementById('nsg-modal-title').textContent=name;
  document.getElementById('nsg-modal').classList.add('open');
}

document.addEventListener('DOMContentLoaded',()=>{ showTab('overview'); });
"""

# ── HTML builder ────────────────────────────────────────────────────────────────

def esc(s):
    if s is None: return ""
    return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")

def build_html(data, generated):
    s   = data["summary"]
    pes = data["private_endpoints"]
    rfs = data["risk_findings"]

    # sidebar VNet items — grouped by subscription
    sb_vnets = ""
    for sub_id, sub_meta in data["subscriptions"].items():
        sub_name  = sub_meta["name"]
        sub_short = sub_meta["short"]
        sub_vnets = [v for v in data["vnets"] if v["subscription_id"] == sub_id]
        if not sub_vnets:
            continue
        sb_vnets += (
            f'<div class="sb-section" style="cursor:pointer" '
            f'onclick="sbSelect(\'__all__\',this,\'{sub_id}\')">'
            f'{esc(sub_short)}</div>'
        )
        for v in sub_vnets:
            n_risks = sum(1 for r in v["risks"] if r["severity"] in ("high", "medium"))
            badge   = f'<span class="sb-badge" style="color:var(--yel)">⚠{n_risks}</span>' if n_risks else ""
            sb_vnets += (
                f'<div class="sb-item" onclick="sbSelect(\'{v["name"]}\',this,\'{sub_id}\')">'
                f'🌐 {esc(v["name"])}{badge}</div>'
            )

    # subscription option HTML for filter dropdowns
    sub_options = "".join(
        f'<option value="{sub_id}">{esc(meta["short"])}</option>'
        for sub_id, meta in data["subscriptions"].items()
    )
    sub_sel_html = (
        f'<select id="sub-sel-{{panel}}">'
        f'<option value="">All Subscriptions</option>'
        f'{sub_options}'
        f'</select>'
    )

    data_json = json.dumps(data, ensure_ascii=False, default=str)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>VNet Metadata — IDOH</title>
<style>{CSS}.help-fab{{position:fixed;bottom:22px;right:22px;width:38px;height:38px;border-radius:50%;background:var(--acc);color:#fff;font-size:17px;font-weight:700;display:flex;align-items:center;justify-content:center;text-decoration:none;z-index:9999;box-shadow:0 2px 10px rgba(0,0,0,.5);opacity:.8;transition:opacity .15s;line-height:1}}.help-fab:hover{{opacity:1}}</style>
</head>
<body>
<a href="help.html" class="help-fab" title="Help &amp; Guide">?</a>
<div class="layout">

<!-- SIDEBAR -->
<div class="sidebar">
  <div class="sb-hdr">Azure Networking<small>{len(data["subscriptions"])} Subscriptions</small></div>
  <div class="sb-body">
    <div class="sb-section">Filter</div>
    <div class="sb-item" onclick="sbSelect('__all__',this,'__all__')">All VNets</div>
    {sb_vnets}
  </div>
</div>

<!-- MAIN -->
<div class="main">
  <div class="main-hdr">
    <h1>Azure Virtual Networks</h1>
    <p class="sub">{"&nbsp;·&nbsp;".join(esc(m["name"]) for m in data["subscriptions"].values())}</p>
    <p class="sub">Generated: <span id="gen-ts" data-ts="{esc(generated)}">&#x21BB; {esc(generated)}</span><script>(function(){{var s=document.getElementById(\'gen-ts\'),h=(Date.now()-new Date(s.dataset.ts.replace(\' \',\'T\')))/36e5;s.style.color=h<25?'var(--grn)':h<168?'var(--yel)':'var(--red)';s.style.fontWeight='700';}})();</script></p>

    <div class="stats">
      <div class="sc" id="card-overview"    onclick="showTab('overview')"      title="Virtual Networks (VNets) are the private network isolation boundaries in Azure. All resources inside a VNet can communicate with each other by default. VNets are scoped to a region and segmented into subnets.">
        <div class="sc-n">{s["vnet_count"]}</div><div class="sc-l">VNets</div></div>
      <div class="sc" id="card-vnets"       onclick="showTab('vnets')"         title="Subnets divide a VNet's address space into smaller segments, each of which can have its own NSG and route table. Services like Databricks, AKS, and App Gateway require dedicated subnets with specific delegations.">
        <div class="sc-n" style="color:var(--cyn)">{s["subnet_count"]}</div><div class="sc-l">Subnets</div></div>
      <div class="sc" id="card-nsgs"        onclick="showTab('nsgs')"          title="Network Security Groups (NSGs) are stateful firewall rule sets attached to subnets or NICs. They control inbound and outbound traffic using allow/deny rules based on source IP, destination port, and protocol. The first matching rule wins.">
        <div class="sc-n" style="color:var(--pur)">{s["nsg_count"]}</div><div class="sc-l">NSGs</div></div>
      <div class="sc" id="card-endpoints"   onclick="showTab('endpoints')"     title="Private Endpoints give an Azure PaaS service (Storage, SQL, Key Vault, etc.) a private IP address inside your VNet. Traffic to the service never leaves the Microsoft backbone — no public internet exposure. Required for NIST-compliant environments.">
        <div class="sc-n" style="color:var(--grn)">{s["pe_count"]}</div><div class="sc-l">Private Endpoints</div></div>
      <div class="sc" id="card-peerings"    onclick="showTab('peerings')"      title="VNet Peerings connect two VNets so resources in each can communicate using private IP addresses. Peering is non-transitive — if VNet A peers with B and B peers with C, A cannot reach C unless directly peered. Low latency, no bandwidth limit.">
        <div class="sc-n" style="color:var(--acc)">{s["peering_count"]}</div><div class="sc-l">Peerings</div></div>
      <div class="sc" id="card-nics"        onclick="showTab('nics')"          title="Network Interface Cards (NICs) are the attachment point between a VM or service and a subnet. Each NIC holds one or more private IP addresses and optionally a public IP. NICs can also be associated with an NSG independently of the subnet.">
        <div class="sc-n" style="color:var(--org)">{s["nic_count"]}</div><div class="sc-l">NICs</div></div>
      <div class="sc" id="card-pips"        onclick="showTab('pips')"          title="Public IP addresses are internet-routable IPs assigned to resources like load balancers, VPN gateways, or NICs. Every public IP in this environment is a potential attack surface — review this tab to ensure none are unintentionally exposed.">
        <div class="sc-n" style="color:var(--grn)">{s["pip_count"]}</div><div class="sc-l">Public IPs</div></div>
      <div class="sc" id="card-risk-high"   onclick="goToRisk('high')"         title="High-risk findings flagged by the network security analysis — for example: overly permissive NSG rules (Any source, wide port ranges), public IPs on sensitive resources, or subnets without an NSG. Click to jump to the risk section.">
        <div class="sc-n" style="color:var(--red)">{s["risk_high"]}</div><div class="sc-l">High Risks</div></div>
      <div class="sc" id="card-risk-medium" onclick="goToRisk('medium')"       title="Medium-risk findings that warrant review but are not immediately critical — for example: NSG rules allowing broad port ranges from specific IPs, subnets with no private endpoints where they would be expected, or peerings with unrestricted access.">
        <div class="sc-n" style="color:var(--yel)">{s["risk_medium"]}</div><div class="sc-l">Medium Risks</div></div>
    </div>
  </div>

  <div class="tabs">
    <div class="tab" id="tab-overview"  onclick="showTab('overview')">Overview</div>
    <div class="tab" id="tab-vnets"     onclick="showTab('vnets')">VNets &amp; Subnets ({s["subnet_count"]})</div>
    <div class="tab" id="tab-nsgs"      onclick="showTab('nsgs')">NSGs ({s["nsg_count"]})</div>
    <div class="tab" id="tab-endpoints" onclick="showTab('endpoints')">Private Endpoints ({s["pe_count"]})</div>
    <div class="tab" id="tab-peerings"  onclick="showTab('peerings')">Peerings ({s["peering_count"]})</div>
    <div class="tab" id="tab-nics"      onclick="showTab('nics')">NICs &amp; VMs ({s["nic_count"]})</div>
    <div class="tab" id="tab-pips"      onclick="showTab('pips')">Public IPs ({s["pip_count"]})</div>
    <div class="tab" id="tab-risk"      onclick="showTab('risk')">
      Risk Summary ({s["risk_high"] + s["risk_medium"] + s["risk_info"]})</div>
  </div>

  <div class="content">

    <!-- OVERVIEW -->
    <div class="panel" id="p-overview">
      <h2>Virtual Networks</h2>
      <div id="ov-vnets" class="ov-grid"></div>
    </div>

    <!-- VNets & SUBNETS -->
    <div class="panel" id="p-vnets">
      <div class="filter-row">
        <input id="vnet-search" placeholder="Search VNets / subnets…"/>
        {sub_sel_html.replace('{panel}','vnets')}
      </div>
      <table>
        <thead><tr>
          <th>Subnet / VNet</th><th>Address Prefix</th><th>NSG</th>
          <th>Service Endpoints</th><th>Private EPs</th><th>Delegation</th><th>Risk</th>
        </tr></thead>
        <tbody id="vnet-tbody"></tbody>
      </table>
    </div>

    <!-- NSGs -->
    <div class="panel" id="p-nsgs">
      <div class="filter-row">
        <input id="nsg-search" placeholder="Search NSGs…"/>
        {sub_sel_html.replace('{panel}','nsgs')}
      </div>
      <table>
        <thead><tr>
          <th>Name / Rule</th><th>Direction</th><th>Access</th><th>Protocol</th>
          <th>Source</th><th>Destination</th><th>Priority</th><th>Risk</th>
        </tr></thead>
        <tbody id="nsg-tbody"></tbody>
      </table>
    </div>

    <!-- PRIVATE ENDPOINTS -->
    <div class="panel" id="p-endpoints">
      <div class="filter-row">
        <input id="pe-search" placeholder="Search private endpoints…"/>
        {sub_sel_html.replace('{panel}','endpoints')}
        <span id="pe-count" class="mut"></span>
      </div>
      <table>
        <thead><tr>
          <th>Endpoint Name</th><th>Resource Group</th><th>Target Resource</th>
          <th>Resource Type</th><th>Sub-resource</th><th>Status</th>
          <th>Subnet</th><th>DNS / IP</th>
        </tr></thead>
        <tbody id="pe-tbody"></tbody>
      </table>
    </div>

    <!-- PEERINGS -->
    <div class="panel" id="p-peerings">
      <div class="filter-row">
        <input id="peer-search" placeholder="Search peerings…"/>
        {sub_sel_html.replace('{panel}','peerings')}
      </div>
      <p class="mut" style="margin-bottom:8px;font-size:11px">
        <b style="color:var(--yel)">useRemoteGateways=Yes</b> means traffic from this VNet
        can exit through the remote VNet's gateway (ExpressRoute/VPN) — worth reviewing for data-exfil risk.
      </p>
      <table>
        <thead><tr>
          <th>Local VNet</th><th>RG</th><th></th><th>Remote VNet</th><th>State</th>
          <th>Allow Forwarded</th><th>GW Transit</th>
          <th title="Traffic exits via remote gateway">Use Remote GW ⚠</th>
          <th>VNet Access</th>
        </tr></thead>
        <tbody id="peer-tbody"></tbody>
      </table>
    </div>

    <!-- NICs & VMs -->
    <div class="panel" id="p-nics">
      <div class="filter-row">
        <input id="nic-search" placeholder="Search NICs / VMs…"/>
        <select id="nic-type-sel">
          <option value="">All types</option>
          <option value="vm">VM NICs</option>
          <option value="pe">Private Endpoint NICs</option>
          <option value="dbrk">Databricks NICs</option>
        </select>
        {sub_sel_html.replace('{panel}','nics')}
      </div>
      <p class="mut" style="margin-bottom:8px;font-size:11px">
        ⓘ Databricks NICs named <b>publicNIC</b> connect to the <i>databricks-hosts</i> subnet —
        this is a Databricks naming convention, <b>not</b> an internet-facing IP.
        See the Public IPs tab for standalone Azure Public IP Address resources.
      </p>
      <table>
        <thead><tr>
          <th>NIC Name</th><th>Resource Group</th><th>Attached To</th><th>Type</th>
          <th>Private IP</th><th>Public IP</th><th>Subnet</th><th>NIC NSG</th>
        </tr></thead>
        <tbody id="nic-tbody"></tbody>
      </table>
    </div>

    <!-- PUBLIC IPs -->
    <div class="panel" id="p-pips">
      <div class="filter-row">
        <input id="pip-search" placeholder="Search public IPs…"/>
        {sub_sel_html.replace('{panel}','pips')}
        <span id="pip-count" class="mut"></span>
      </div>
      <table>
        <thead><tr>
          <th>Name</th><th>Resource Group</th><th>IP Address</th><th>SKU</th>
          <th>Allocation</th><th>Attached To</th><th>Resource Type</th>
          <th>DNS / FQDN</th><th>Risk</th>
        </tr></thead>
        <tbody id="pip-tbody"></tbody>
      </table>
    </div>

    <!-- RISK SUMMARY -->
    <div class="panel" id="p-risk">
      <div class="filter-row">
        <input id="risk-search" placeholder="Search findings…"/>
        <select id="risk-sev-sel">
          <option value="">All severities</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="info">Info</option>
        </select>
        {sub_sel_html.replace('{panel}','risk')}
        <span id="risk-count" class="mut"></span>
      </div>
      <p class="mut" style="margin-bottom:8px;font-size:11px">
        Automated risk indicators for data-exfil assessment. Review in context — not all findings
        require remediation (e.g. Databricks NSG rules are managed by Microsoft).
      </p>
      <table>
        <thead><tr>
          <th>Severity</th><th>Category</th><th>Resource</th><th>Detail</th>
        </tr></thead>
        <tbody id="risk-tbody"></tbody>
      </table>
    </div>

  </div>
</div>
</div>

<!-- VNet MODAL -->
<div class="modal-overlay" id="vnet-modal" onclick="if(event.target===this) closeModal('vnet-modal')">
  <div class="modal">
    <h2><span id="vnet-modal-title"></span>
      <span class="modal-close" onclick="closeModal('vnet-modal')">✕</span></h2>
    <div id="vnet-modal-body"></div>
  </div>
</div>

<!-- NSG MODAL -->
<div class="modal-overlay" id="nsg-modal" onclick="if(event.target===this) closeModal('nsg-modal')">
  <div class="modal">
    <h2><span id="nsg-modal-title"></span>
      <span class="modal-close" onclick="closeModal('nsg-modal')">✕</span></h2>
    <div id="nsg-modal-body"></div>
  </div>
</div>

<script>
const DATA = {data_json};
{JS}
</script>
</body>
</html>"""

# ── main ────────────────────────────────────────────────────────────────────────

def main():
    print("Authenticating…")
    token = get_token()

    data = collect(token)
    s    = data["summary"]

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    print("\nBuilding HTML…")
    html = build_html(data, generated)

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\nSaved: {OUT_FILE}")
    print(f"  VNets            : {s['vnet_count']}")
    print(f"  Subnets          : {s['subnet_count']}")
    print(f"  NSGs             : {s['nsg_count']}")
    print(f"  Private Endpoints: {s['pe_count']}")
    print(f"  VNet Peerings    : {s['peering_count']}")
    print(f"  Public IPs       : {s['pip_count']}")
    print(f"  Risk — High      : {s['risk_high']}")
    print(f"  Risk — Medium    : {s['risk_medium']}")
    print(f"  Risk — Info      : {s['risk_info']}")


    if not os.environ.get('PUBLISH_RUNNING'):
        try:
            import generate_metadata_index
            generate_metadata_index.main()
            print("  Index updated       : index.html")
        except Exception as exc:
            print(f"  Warning: could not update index.html: {exc}")

if __name__ == "__main__":
    main()
