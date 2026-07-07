#!/usr/bin/env python3
"""
Azure Key Vault Metadata Report
Collects secrets, keys, certificates, and access policies for all Key Vaults
in dev and prd resource groups. Secret VALUES are never collected.

Usage:
  python3 keyvault_metadata_report.py --env dev
  python3 keyvault_metadata_report.py --env prd
"""

import argparse
import json
import subprocess
import requests
from collections import defaultdict
from datetime import datetime, timezone

# ── environment config ─────────────────────────────────────────────────────────

ENVIRONMENTS = {
    "dev": {
        "subscription_id": "57493fde-eff8-432f-8574-4f1281bd2ce3",
        "resource_group":  "zus1-idoh-dev-v2-rg",
        "label": "DEV",
    },
    "prd": {
        "subscription_id": "57493fde-eff8-432f-8574-4f1281bd2ce3",
        "resource_group":  "zus1-idoh-prd-v1-rg",
        "label": "PRD",
    },
}

MGMT_API_VER  = "2022-07-01"
VAULT_API_VER = "7.4"
MGMT          = "https://management.azure.com"
EXPIRY_WARN_DAYS = 30

# ── auth ───────────────────────────────────────────────────────────────────────

def get_token(resource):
    r = subprocess.run(
        ["az", "account", "get-access-token", "--resource", resource],
        capture_output=True, text=True, check=True
    )
    return json.loads(r.stdout)["accessToken"]

def mgmt_hdrs(token):
    return {"Authorization": f"Bearer {token}"}

# ── REST helpers ───────────────────────────────────────────────────────────────

def get_all_mgmt(url, token, api_ver=None):
    params = {"api-version": api_ver or MGMT_API_VER}
    results = []
    while url:
        r = requests.get(url, headers=mgmt_hdrs(token), params=params)
        r.raise_for_status()
        data = r.json()
        results.extend(data.get("value", []))
        url    = data.get("nextLink")
        params = {}
    return results

def get_all_vault(url, vault_token):
    params = {"api-version": VAULT_API_VER}
    results = []
    while url:
        r = requests.get(url, headers=mgmt_hdrs(vault_token), params=params)
        if r.status_code == 403:
            print(f"    Access denied: {url} — check data plane RBAC / access policy")
            return []
        r.raise_for_status()
        data = r.json()
        results.extend(data.get("value", []))
        url    = data.get("nextLink")
        params = {}
    return results

def get_one_vault(url, vault_token):
    r = requests.get(url, headers=mgmt_hdrs(vault_token),
                     params={"api-version": VAULT_API_VER})
    if r.status_code in (403, 404):
        return {}
    r.raise_for_status()
    return r.json()

# ── time helpers ───────────────────────────────────────────────────────────────

def from_epoch(ts):
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc)
    except Exception:
        return None

def fmt_dt(dt):
    if not dt:
        return ""
    return dt.strftime("%Y-%m-%d")

def days_until(dt):
    if not dt:
        return None
    now = datetime.now(tz=timezone.utc)
    return (dt - now).days

def expiry_status(dt):
    """Return (label, css_class) based on expiry date."""
    if not dt:
        return "No Expiry", "exp-none"
    d = days_until(dt)
    if d < 0:
        return f"Expired {abs(d)}d ago", "exp-red"
    if d <= EXPIRY_WARN_DAYS:
        return f"Expires in {d}d", "exp-yellow"
    return fmt_dt(dt), "exp-green"

# ── resolve object IDs ─────────────────────────────────────────────────────────

_id_cache = {}

def resolve_id(object_id):
    if object_id in _id_cache:
        return _id_cache[object_id]
    try:
        r = subprocess.run(
            ["az", "ad", "object", "show", "--id", object_id, "--query", "displayName", "-o", "tsv"],
            capture_output=True, text=True, timeout=8
        )
        name = r.stdout.strip()
        _id_cache[object_id] = name if name else object_id
    except Exception:
        _id_cache[object_id] = object_id
    return _id_cache[object_id]

# ── fetch ──────────────────────────────────────────────────────────────────────

def list_vaults(sub, rg, mgmt_token):
    url = f"{MGMT}/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.KeyVault/vaults"
    vaults = get_all_mgmt(url, mgmt_token)
    return vaults

def get_vault_detail(sub, rg, name, mgmt_token):
    url = f"{MGMT}/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.KeyVault/vaults/{name}"
    r = requests.get(url, headers=mgmt_hdrs(mgmt_token),
                     params={"api-version": MGMT_API_VER})
    if r.status_code == 200:
        return r.json()
    return {}

def fetch_secrets(vault_uri, vault_token):
    items = get_all_vault(f"{vault_uri}/secrets", vault_token)
    out = []
    for s in items:
        sid  = s.get("id", "")
        name = sid.split("/")[-1] if "/" in sid else sid
        attr = s.get("attributes", {})
        exp  = from_epoch(attr.get("expires"))
        out.append({
            "name":         name,
            "enabled":      attr.get("enabled", True),
            "content_type": s.get("contentType", ""),
            "created":      fmt_dt(from_epoch(attr.get("created"))),
            "updated":      fmt_dt(from_epoch(attr.get("updated"))),
            "expires_dt":   exp,
            "expires":      fmt_dt(exp),
            "tags":         ", ".join((s.get("tags") or {}).keys()),
        })
    return out

def fetch_keys(vault_uri, vault_token):
    items = get_all_vault(f"{vault_uri}/keys", vault_token)
    out = []
    for k in items:
        kid  = k.get("kid", "")
        name = kid.split("/")[-2] if "/keys/" in kid else kid.split("/")[-1]
        attr = k.get("attributes", {})
        exp  = from_epoch(attr.get("expires"))
        out.append({
            "name":     name,
            "enabled":  attr.get("enabled", True),
            "key_ops":  ", ".join(k.get("keyOps") or []),
            "created":  fmt_dt(from_epoch(attr.get("created"))),
            "updated":  fmt_dt(from_epoch(attr.get("updated"))),
            "expires_dt": exp,
            "expires":  fmt_dt(exp),
            "tags":     ", ".join((k.get("tags") or {}).keys()),
        })
    return out

def fetch_certificates(vault_uri, vault_token):
    items = get_all_vault(f"{vault_uri}/certificates", vault_token)
    out = []
    for c in items:
        cid  = c.get("id", "")
        name = cid.split("/")[-1] if "/" in cid else cid
        attr = c.get("attributes", {})
        exp  = from_epoch(attr.get("expires"))

        # fetch detail for issuer/subject
        detail = get_one_vault(f"{vault_uri}/certificates/{name}", vault_token)
        policy = detail.get("policy", {}) if detail else {}
        issuer  = (policy.get("issuer") or {}).get("name", "")
        subj    = (policy.get("x509CertificateProperties") or {}).get("subject", "")
        sans    = ", ".join((policy.get("x509CertificateProperties") or {})
                            .get("subjectAlternativeNames", {})
                            .get("dnsNames", []))
        key_type = (policy.get("keyProperties") or {}).get("keyType", "")
        key_size = (policy.get("keyProperties") or {}).get("keySize", "")

        out.append({
            "name":      name,
            "enabled":   attr.get("enabled", True),
            "thumbprint": c.get("x5t", ""),
            "issuer":    issuer,
            "subject":   subj,
            "sans":      sans,
            "key_type":  f"{key_type} {key_size}".strip(),
            "created":   fmt_dt(from_epoch(attr.get("created"))),
            "updated":   fmt_dt(from_epoch(attr.get("updated"))),
            "expires_dt": exp,
            "expires":   fmt_dt(exp),
            "tags":      ", ".join((c.get("tags") or {}).keys()),
        })
    return out

def fetch_access_policies(vault_detail):
    props = vault_detail.get("properties", {})
    policies = props.get("accessPolicies") or []
    out = []
    for p in policies:
        perms = p.get("permissions", {})
        out.append({
            "object_id":    p.get("objectId", ""),
            "tenant_id":    p.get("tenantId", ""),
            "secrets":      ", ".join(perms.get("secrets", [])),
            "keys":         ", ".join(perms.get("keys", [])),
            "certificates": ", ".join(perms.get("certificates", [])),
            "storage":      ", ".join(perms.get("storage", [])),
        })
    return out

# ── helpers ────────────────────────────────────────────────────────────────────

def esc(s):
    if s is None: return ""
    return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")

def js_esc(s):
    if s is None: return ""
    return str(s).replace("\\","\\\\").replace("'","\\'").replace('"','\\"').replace("\n","\\n")

# ── CSS ────────────────────────────────────────────────────────────────────────

CSS = """
:root{--bg:#0f1117;--sur:#1a1d27;--sur2:#252836;--brd:#2e3245;
  --txt:#e2e8f0;--mut:#8892a4;--acc:#6c8eff;--grn:#4ade80;
  --red:#f87171;--yel:#fbbf24;--pur:#c084fc;--cyn:#22d3ee;--org:#fb923c;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font:13px/1.5 'Segoe UI',system-ui,sans-serif;overflow:hidden}
.layout{display:flex;height:100vh}

.sidebar{width:260px;min-width:160px;background:var(--sur);border-right:1px solid var(--brd);
  display:flex;flex-direction:column;overflow:hidden;flex-shrink:0}
.sb-hdr{padding:12px 14px;border-bottom:1px solid var(--brd);font-weight:700;font-size:13px}
.sb-hdr small{display:block;color:var(--mut);font-weight:400;font-size:11px;margin-top:2px}
.sb-body{overflow-y:auto;flex:1;padding:8px 0}
.sb-vault{padding:6px 14px;font-size:12px;font-weight:700;color:var(--acc);
  border-bottom:1px solid var(--brd);cursor:pointer}
.sb-vault:hover{background:var(--sur2)}
.sb-vault.active{background:var(--sur2);border-left:3px solid var(--acc)}
.sb-vault small{display:block;color:var(--mut);font-weight:400;font-size:10px;margin-top:1px}

.main{flex:1;overflow:hidden;display:flex;flex-direction:column}
.main-hdr{padding:18px 26px 0;flex-shrink:0}
h1{font-size:20px;font-weight:700;margin-bottom:2px}
.sub{color:var(--mut);font-size:12px;margin-bottom:14px}

.stats{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.sc{background:var(--sur);border:1px solid var(--brd);border-radius:10px;
  padding:11px 15px;min-width:105px;cursor:pointer;transition:border-color .15s}
.sc:hover{border-color:var(--acc)}
.sc.active-card{border-color:var(--acc);background:var(--sur2)}
.sc-n{font-size:20px;font-weight:700;line-height:1}
.sc-l{font-size:10px;color:var(--mut);margin-top:3px}

.tabs{display:flex;gap:2px;border-bottom:2px solid var(--brd);padding:0 26px;flex-shrink:0;flex-wrap:wrap}
.tab{padding:6px 13px;cursor:pointer;border-radius:6px 6px 0 0;font-size:12px;font-weight:600;
  color:var(--mut);border:1px solid transparent;border-bottom:none;margin-bottom:-2px;user-select:none}
.tab:hover{color:var(--txt);background:var(--sur)}
.tab.active{background:var(--sur);border-color:var(--brd);border-bottom-color:var(--sur);color:var(--txt)}

.content{flex:1;overflow-y:auto;padding:16px 26px}
.panel{display:none}.panel.active{display:block}

.srch{margin-bottom:10px}
.srch input{padding:6px 11px;background:var(--sur);border:1px solid var(--brd);
  border-radius:6px;color:var(--txt);font-size:12px;width:100%;max-width:420px;outline:none}
.srch input:focus{border-color:var(--acc)}

table{width:100%;border-collapse:collapse;font-size:12px}
th{background:var(--sur2);padding:6px 10px;text-align:left;font-weight:700;
  border-bottom:2px solid var(--brd);white-space:nowrap;position:sticky;top:0;z-index:1}
td{padding:5px 10px;border-bottom:1px solid var(--brd);vertical-align:middle}
tr:hover td{background:var(--sur)}
.hidden{display:none!important}
.mono{font-family:monospace;font-size:11px;color:var(--mut);word-break:break-all}
.mut{color:var(--mut);font-size:11px}

.chip{display:inline-block;font-size:10px;padding:1px 6px;border-radius:3px;font-weight:700;white-space:nowrap}
.chip-en{background:#1a3a2a;color:#4ade80}
.chip-dis{background:#3a1a1a;color:#f87171}

/* expiry indicators */
.exp-green{color:var(--grn);font-size:11px}
.exp-yellow{color:var(--yel);font-size:11px;font-weight:700}
.exp-red{color:var(--red);font-size:11px;font-weight:700}
.exp-none{color:var(--mut);font-size:11px}

/* permissions chips */
.perm{display:inline-block;font-size:10px;padding:1px 5px;border-radius:3px;margin:1px;
  background:var(--sur2);color:var(--txt);border:1px solid var(--brd)}
.perm-full{background:#1e2a4a;color:#6c8eff;border-color:#2e3a6a}

/* overview grid */
.ov-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:9px;margin-bottom:20px}
.ov-card{background:var(--sur);border:1px solid var(--brd);border-radius:8px;padding:12px 14px}
.ov-card h3{font-size:12px;color:var(--acc);margin-bottom:6px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ov-card .ct{font-size:11px;color:var(--mut);display:flex;flex-direction:column;gap:3px}
.ov-card .ct strong{color:var(--txt)}
h2{font-size:14px;font-weight:700;margin:14px 0 10px;padding-bottom:4px;border-bottom:1px solid var(--brd)}
.vault-section{margin-bottom:32px}
.vault-banner{background:var(--sur2);border:1px solid var(--brd);border-radius:8px;
  padding:10px 14px;margin-bottom:12px;display:flex;gap:20px;flex-wrap:wrap;align-items:center}
.vault-banner strong{color:var(--acc);font-size:13px}
.vault-banner span{color:var(--mut);font-size:11px}
.vault-banner .kv-uri{font-family:monospace;font-size:11px;color:var(--cyn)}
"""

# ── JavaScript ─────────────────────────────────────────────────────────────────

JS = """
function escH(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

function showTab(id){
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.sc').forEach(c=>c.classList.remove('active-card'));
  document.getElementById('p-'+id).classList.add('active');
  const tab=document.getElementById('tab-'+id);  if(tab) tab.classList.add('active');
  const card=document.getElementById('card-'+id); if(card) card.classList.add('active-card');
}

function ft(tid,q){
  q=(q||'').toLowerCase().trim();
  document.querySelectorAll('#'+tid+' tbody tr').forEach(tr=>{
    tr.classList.toggle('hidden',!!q&&!tr.textContent.toLowerCase().includes(q));
  });
}

function filterByVault(vaultName){
  document.querySelectorAll('.sb-vault').forEach(el=>{
    el.classList.toggle('active', el.dataset.vault===vaultName);
  });
  ['secrets','keys','certs','policies'].forEach(tab=>{
    document.querySelectorAll('#p-'+tab+' tr[data-vault]').forEach(tr=>{
      tr.classList.toggle('hidden', tr.dataset.vault !== vaultName && vaultName !== '__all__');
    });
  });
}

document.addEventListener('DOMContentLoaded',()=>{ showTab('overview'); });
"""

# ── HTML builder ───────────────────────────────────────────────────────────────

def enabled_chip(enabled):
    return ('<span class="chip chip-en">Enabled</span>' if enabled
            else '<span class="chip chip-dis">Disabled</span>')

def build_html(vaults_data, env_cfg, generated):
    label = env_cfg["label"]

    # aggregate stats
    all_secrets = [s for v in vaults_data for s in v["secrets"]]
    all_keys    = [k for v in vaults_data for k in v["keys"]]
    all_certs   = [c for v in vaults_data for c in v["certs"]]
    all_policies= [p for v in vaults_data for p in v["policies"]]

    n_expiring = sum(1 for s in all_secrets
                     if s["expires_dt"] and days_until(s["expires_dt"]) is not None
                     and days_until(s["expires_dt"]) <= EXPIRY_WARN_DAYS)
    n_cert_exp = sum(1 for c in all_certs
                     if c["expires_dt"] and days_until(c["expires_dt"]) is not None
                     and days_until(c["expires_dt"]) <= EXPIRY_WARN_DAYS)

    # ── sidebar ────────────────────────────────────────────────────────────────
    sb = '<div class="sb-vault" data-vault="__all__" onclick="filterByVault(\'__all__\')">All Vaults</div>'
    for v in vaults_data:
        sb += (f'<div class="sb-vault" data-vault="{esc(v["name"])}" '
               f'onclick="filterByVault(\'{js_esc(v["name"])}\')"> '
               f'🔑 {esc(v["name"])}'
               f'<small>{len(v["secrets"])} secrets · {len(v["keys"])} keys · {len(v["certs"])} certs</small>'
               f'</div>')

    # ── overview ───────────────────────────────────────────────────────────────
    ov_cards = ""
    for v in vaults_data:
        props = v["detail"].get("properties", {})
        uri   = props.get("vaultUri", "")
        sku   = v["detail"].get("sku", {}).get("name", "")
        sd    = "Yes" if props.get("enableSoftDelete") else "No"
        pp    = "Yes" if props.get("enablePurgeProtection") else "No"
        rbac  = "RBAC" if props.get("enableRbacAuthorization") else "Access Policies"
        loc   = v["detail"].get("location", "")
        n_exp = sum(1 for s in v["secrets"]
                    if s["expires_dt"] and days_until(s["expires_dt"]) is not None
                    and days_until(s["expires_dt"]) <= EXPIRY_WARN_DAYS)
        ov_cards += (
            f'<div class="ov-card">'
            f'<h3>🔑 {esc(v["name"])}</h3>'
            f'<div class="ct">'
            f'<span class="kv-uri">{esc(uri)}</span>'
            f'<span>SKU: <strong>{esc(sku)}</strong> &nbsp;|&nbsp; Region: <strong>{esc(loc)}</strong></span>'
            f'<span>Auth: <strong>{esc(rbac)}</strong></span>'
            f'<span>Soft Delete: <strong>{sd}</strong> &nbsp;|&nbsp; Purge Protection: <strong>{pp}</strong></span>'
            f'<span><strong>{len(v["secrets"])}</strong> secrets'
            f'{f" &nbsp;<span style=\'color:var(--yel)\'>(⚠ {n_exp} expiring soon)</span>" if n_exp else ""}</span>'
            f'<span><strong>{len(v["keys"])}</strong> keys &nbsp;·&nbsp; '
            f'<strong>{len(v["certs"])}</strong> certificates &nbsp;·&nbsp; '
            f'<strong>{len(v["policies"])}</strong> access policies</span>'
            f'</div></div>'
        )

    # ── secrets table ──────────────────────────────────────────────────────────
    sec_rows = ""
    for v in vaults_data:
        for s in sorted(v["secrets"], key=lambda x: x["name"]):
            lbl, cls = expiry_status(s["expires_dt"])
            sec_rows += (
                f'<tr data-vault="{esc(v["name"])}">'
                f'<td class="mut" style="font-size:10px">{esc(v["name"])}</td>'
                f'<td><strong>{esc(s["name"])}</strong></td>'
                f'<td>{enabled_chip(s["enabled"])}</td>'
                f'<td class="mut">{esc(s["content_type"]) or "—"}</td>'
                f'<td class="mut">{esc(s["created"])}</td>'
                f'<td class="mut">{esc(s["updated"])}</td>'
                f'<td class="{cls}">{esc(lbl)}</td>'
                f'<td class="mut">{esc(s["tags"]) or "—"}</td>'
                f'</tr>'
            )

    # ── keys table ─────────────────────────────────────────────────────────────
    key_rows = ""
    for v in vaults_data:
        for k in sorted(v["keys"], key=lambda x: x["name"]):
            lbl, cls = expiry_status(k["expires_dt"])
            key_rows += (
                f'<tr data-vault="{esc(v["name"])}">'
                f'<td class="mut" style="font-size:10px">{esc(v["name"])}</td>'
                f'<td><strong>{esc(k["name"])}</strong></td>'
                f'<td>{enabled_chip(k["enabled"])}</td>'
                f'<td class="mut">{esc(k["key_ops"]) or "—"}</td>'
                f'<td class="mut">{esc(k["created"])}</td>'
                f'<td class="mut">{esc(k["updated"])}</td>'
                f'<td class="{cls}">{esc(lbl)}</td>'
                f'</tr>'
            )

    # ── certificates table ─────────────────────────────────────────────────────
    cert_rows = ""
    for v in vaults_data:
        for c in sorted(v["certs"], key=lambda x: x["name"]):
            lbl, cls = expiry_status(c["expires_dt"])
            cert_rows += (
                f'<tr data-vault="{esc(v["name"])}">'
                f'<td class="mut" style="font-size:10px">{esc(v["name"])}</td>'
                f'<td><strong>{esc(c["name"])}</strong></td>'
                f'<td>{enabled_chip(c["enabled"])}</td>'
                f'<td class="mut" style="font-size:10px">{esc(c["issuer"]) or "—"}</td>'
                f'<td class="mut" style="font-size:10px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{esc(c["subject"]) or "—"}</td>'
                f'<td class="mut">{esc(c["key_type"]) or "—"}</td>'
                f'<td class="mut">{esc(c["created"])}</td>'
                f'<td class="{cls}">{esc(lbl)}</td>'
                f'</tr>'
            )

    # ── access policies table ──────────────────────────────────────────────────
    def perm_chips(perms_str):
        if not perms_str:
            return '<span class="mut">—</span>'
        perms = [p.strip() for p in perms_str.split(",") if p.strip()]
        full  = {"all", "get", "list", "set", "delete", "backup", "restore",
                 "recover", "purge", "import", "update", "create", "sign",
                 "verify", "encrypt", "decrypt", "wrapKey", "unwrapKey"}
        return "".join(
            f'<span class="perm{"" if p.lower() not in ("all","get","list") else " perm-full"}">{esc(p)}</span>'
            for p in perms
        )

    pol_rows = ""
    for v in vaults_data:
        for p in v["policies"]:
            display = resolve_id(p["object_id"])
            pol_rows += (
                f'<tr data-vault="{esc(v["name"])}">'
                f'<td class="mut" style="font-size:10px">{esc(v["name"])}</td>'
                f'<td><strong>{esc(display)}</strong>'
                f'{"<br><span class=\'mono\' style=\'font-size:10px\'>" + esc(p["object_id"]) + "</span>" if display != p["object_id"] else ""}'
                f'</td>'
                f'<td><div style="display:flex;flex-wrap:wrap;gap:2px">{perm_chips(p["secrets"])}</div></td>'
                f'<td><div style="display:flex;flex-wrap:wrap;gap:2px">{perm_chips(p["keys"])}</div></td>'
                f'<td><div style="display:flex;flex-wrap:wrap;gap:2px">{perm_chips(p["certificates"])}</div></td>'
                f'<td><div style="display:flex;flex-wrap:wrap;gap:2px">{perm_chips(p["storage"])}</div></td>'
                f'</tr>'
            )

    no_rows = '<tr><td colspan="8" class="mut" style="padding:12px">None found.</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>Key Vault Metadata — {label}</title>
<style>{CSS}.help-fab{{position:fixed;bottom:22px;right:22px;width:38px;height:38px;border-radius:50%;background:var(--acc);color:#fff;font-size:17px;font-weight:700;display:flex;align-items:center;justify-content:center;text-decoration:none;z-index:9999;box-shadow:0 2px 10px rgba(0,0,0,.5);opacity:.8;transition:opacity .15s;line-height:1}}.help-fab:hover{{opacity:1}}</style>
</head>
<body>
<a href="help.html" class="help-fab" title="Help &amp; Guide">?</a>
<div class="layout">

<!-- SIDEBAR -->
<div class="sidebar">
  <div class="sb-hdr">Key Vault — {esc(label)}<small>{env_cfg["resource_group"]}</small></div>
  <div class="sb-body">{sb}</div>
</div>

<!-- MAIN -->
<div class="main">
<div class="main-hdr">
  <h1>Azure Key Vault — {esc(label)}</h1>
  <p class="sub">{len(vaults_data)} vault{"s" if len(vaults_data)!=1 else ""} &nbsp;|&nbsp; Generated: {esc(generated)}</p>

  <div class="stats">
    <div class="sc" id="card-overview"  onclick="showTab('overview')"   title="Total number of Key Vaults across all resource groups in this environment. Each vault is a separate security boundary with its own access policies, network rules, and audit log. Vaults should be scoped per application or workload, not shared broadly.">
      <div class="sc-n">{len(vaults_data)}</div><div class="sc-l">Vaults</div></div>
    <div class="sc" id="card-secrets"   onclick="showTab('secrets')"    title="Secrets are named string values stored securely in the vault — typically connection strings, API keys, passwords, or storage account keys. ADF linked services and Databricks reference secrets here instead of embedding credentials in code or config.">
      <div class="sc-n" style="color:var(--acc)">{len(all_secrets)}</div><div class="sc-l">Secrets</div></div>
    <div class="sc"                      onclick="showTab('secrets')"   title="Secrets with an expiration date set that are within 30 days of expiring. Expired secrets cause authentication failures in any service that references them. Click to review and rotate before they expire.">
      <div class="sc-n" style="color:var(--yel)">{n_expiring}</div><div class="sc-l">Expiring Soon</div></div>
    <div class="sc" id="card-keys"      onclick="showTab('keys')"       title="Cryptographic keys used for encryption, signing, or wrapping operations. Unlike secrets (plain strings), keys never leave the vault — operations are performed inside Key Vault. Used for customer-managed encryption keys (CMK) on storage accounts, SQL, and Databricks.">
      <div class="sc-n" style="color:var(--pur)">{len(all_keys)}</div><div class="sc-l">Keys</div></div>
    <div class="sc" id="card-certs"     onclick="showTab('certs')"      title="TLS/SSL certificates managed by Key Vault. Key Vault can auto-renew certificates from DigiCert or Let's Encrypt before expiry. Services like App Gateway and API Management can pull certificates directly from the vault without manual PFX exports.">
      <div class="sc-n" style="color:var(--cyn)">{len(all_certs)}</div><div class="sc-l">Certificates</div></div>
    <div class="sc" id="card-policies"  onclick="showTab('policies')"   title="Access policies grant specific identities (users, service principals, managed identities) permission to get/set/list secrets, keys, or certificates. Prefer managed identities over service principals where possible — no credential to rotate. Azure RBAC is the modern alternative to vault access policies.">
      <div class="sc-n" style="color:var(--grn)">{len(all_policies)}</div><div class="sc-l">Access Policies</div></div>
  </div>
</div>

  <div class="tabs">
    <div class="tab" id="tab-overview" onclick="showTab('overview')">Overview</div>
    <div class="tab" id="tab-secrets"  onclick="showTab('secrets')">Secrets ({len(all_secrets)})</div>
    <div class="tab" id="tab-keys"     onclick="showTab('keys')">Keys ({len(all_keys)})</div>
    <div class="tab" id="tab-certs"    onclick="showTab('certs')">Certificates ({len(all_certs)})</div>
    <div class="tab" id="tab-policies" onclick="showTab('policies')">Access Policies ({len(all_policies)})</div>
  </div>

  <div class="content">

    <!-- OVERVIEW -->
    <div class="panel" id="p-overview">
      <div class="ov-grid">{ov_cards}</div>
    </div>

    <!-- SECRETS -->
    <div class="panel" id="p-secrets">
      <p class="mut" style="margin-bottom:8px">Secret values are never collected. Names and metadata only.</p>
      <div class="srch"><input placeholder="Search secrets…" oninput="ft('sec-tbl',this.value)"/></div>
      <table id="sec-tbl">
        <thead><tr><th>Vault</th><th>Secret Name</th><th>Status</th><th>Content Type</th>
          <th>Created</th><th>Updated</th><th>Expiry</th><th>Tags</th></tr></thead>
        <tbody>{sec_rows or no_rows}</tbody>
      </table>
    </div>

    <!-- KEYS -->
    <div class="panel" id="p-keys">
      <div class="srch"><input placeholder="Search keys…" oninput="ft('key-tbl',this.value)"/></div>
      <table id="key-tbl">
        <thead><tr><th>Vault</th><th>Key Name</th><th>Status</th><th>Allowed Operations</th>
          <th>Created</th><th>Updated</th><th>Expiry</th></tr></thead>
        <tbody>{key_rows or no_rows}</tbody>
      </table>
    </div>

    <!-- CERTIFICATES -->
    <div class="panel" id="p-certs">
      <div class="srch"><input placeholder="Search certificates…" oninput="ft('cert-tbl',this.value)"/></div>
      <table id="cert-tbl">
        <thead><tr><th>Vault</th><th>Certificate Name</th><th>Status</th><th>Issuer</th>
          <th>Subject</th><th>Key Type</th><th>Created</th><th>Expiry</th></tr></thead>
        <tbody>{cert_rows or no_rows}</tbody>
      </table>
    </div>

    <!-- ACCESS POLICIES -->
    <div class="panel" id="p-policies">
      <div class="srch"><input placeholder="Search policies…" oninput="ft('pol-tbl',this.value)"/></div>
      <table id="pol-tbl">
        <thead><tr><th>Vault</th><th>Identity</th><th>Secrets</th>
          <th>Keys</th><th>Certificates</th><th>Storage</th></tr></thead>
        <tbody>{pol_rows or no_rows}</tbody>
      </table>
    </div>

  </div>
</div>
</div>
<script>{JS}
document.addEventListener('DOMContentLoaded',()=>{{ showTab('overview'); }});
</script>
</body>
</html>"""

# ── main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["dev","prd"], required=True)
    args = parser.parse_args()

    env_cfg = ENVIRONMENTS[args.env]
    sub     = env_cfg["subscription_id"]
    rg      = env_cfg["resource_group"]
    label   = env_cfg["label"]

    print(f"\n=== Key Vault Metadata — {label} ===")
    print("Getting tokens…")
    mgmt_token  = get_token("https://management.azure.com/")
    vault_token = get_token("https://vault.azure.net")

    print(f"  Discovering vaults in {rg}…")
    raw_vaults = list_vaults(sub, rg, mgmt_token)
    print(f"  Found {len(raw_vaults)} vault(s): {[v['name'] for v in raw_vaults]}")

    vaults_data = []
    for v in raw_vaults:
        name = v["name"]
        print(f"\n  [{name}]")

        detail = get_vault_detail(sub, rg, name, mgmt_token)
        props  = detail.get("properties", {})
        uri    = props.get("vaultUri", f"https://{name}.vault.azure.net/").rstrip("/")

        print(f"    Fetching secrets…", end="", flush=True)
        secrets = fetch_secrets(uri, vault_token)
        print(f" {len(secrets)}")

        print(f"    Fetching keys…", end="", flush=True)
        keys = fetch_keys(uri, vault_token)
        print(f" {len(keys)}")

        print(f"    Fetching certificates…", end="", flush=True)
        certs = fetch_certificates(uri, vault_token)
        print(f" {len(certs)}")

        policies = fetch_access_policies(detail)
        print(f"    Access policies: {len(policies)}")

        print(f"    Resolving {len(policies)} policy identities…")
        for p in policies:
            resolve_id(p["object_id"])

        vaults_data.append({
            "name":     name,
            "detail":   detail,
            "secrets":  secrets,
            "keys":     keys,
            "certs":    certs,
            "policies": policies,
        })

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    print("\nBuilding HTML…")
    html = build_html(vaults_data, env_cfg, generated)

    out = f"/home/thedavidporter/keyvault_metadata_report_{args.env}.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Saved: {out}")
    all_s = sum(len(v["secrets"])  for v in vaults_data)
    all_k = sum(len(v["keys"])     for v in vaults_data)
    all_c = sum(len(v["certs"])    for v in vaults_data)
    all_p = sum(len(v["policies"]) for v in vaults_data)
    print(f"  Secrets      : {all_s}")
    print(f"  Keys         : {all_k}")
    print(f"  Certificates : {all_c}")
    print(f"  Policies     : {all_p}")


    try:
        import generate_metadata_index
        generate_metadata_index.main()
        print("  Index updated       : index.html")
    except Exception as exc:
        print(f"  Warning: could not update index.html: {exc}")
if __name__ == "__main__":
    main()
