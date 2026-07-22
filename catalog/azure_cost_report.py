#!/usr/bin/env python3
"""
azure_cost_report.py — Executive Azure Cost & Savings Report
Subscriptions: ECAE IDOH Production + ECAE Shared Production
"""
import json
import subprocess
import sys
import calendar
from datetime import datetime, date
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
import requests

EASTERN = ZoneInfo("America/New_York")
OUTPUT  = "/home/thedavidporter/azure_cost_report.html"

IDOH_ID   = "57493fde-eff8-432f-8574-4f1281bd2ce3"
SHARED_ID = "5d3a4b9c-0e31-477c-9122-bb3be662e2a9"
SUBS = {
    IDOH_ID:   "ECAE IDOH Production",
    SHARED_ID: "ECAE Shared Production",
}
SUB_SHORT = {IDOH_ID: "IDOH", SHARED_ID: "Shared"}
SUB_COLOR = {IDOH_ID: "#38bdf8", SHARED_ID: "#a78bfa"}

# Map ServiceTier substrings → purpose label for the VM detail pop-up.
# Add or edit entries here as the VM fleet changes.
VM_PURPOSE_MAP = [
    ("FSv2 Series Windows",  "AVD Session Host"),
    ("Dv3 Series",           "SHIR / DevOps Agent"),
    ("Dsv3 Series",          "SHIR / DevOps Agent"),
    ("Dsv5 Series",          "SHIR / DevOps Agent"),
    ("Dv5 Series",           "SHIR / DevOps Agent"),
    ("Dsv4 Series",          "SHIR / DevOps Agent"),
    ("Dv4 Series",           "SHIR / DevOps Agent"),
    ("Esv5 Series",          "Synapse / Data"),
    ("Ev5 Series",           "Synapse / Data"),
    ("Esv3 Series",          "Synapse / Data"),
    ("Ev3 Series",           "Synapse / Data"),
    ("BS Series",            "Dev / Utility"),
    ("Bsv2 Series",          "Dev / Utility"),
    ("Bas Series",           "Dev / Utility"),
]

def _vm_purpose(service_tier: str) -> str:
    for pattern, label in VM_PURPOSE_MAP:
        if pattern.lower() in service_tier.lower():
            return label
    return "—"


CATEGORY_MAP = {
    "Virtual Machines":              "Compute & Virtual Desktop",
    "Azure Kubernetes Service":      "Compute & Virtual Desktop",
    "Container Instances":           "Compute & Virtual Desktop",
    "Azure Synapse Analytics":       "Analytics & Warehousing",
    "Azure Databricks":              "Developer Platforms",
    "Azure Data Factory v2":         "Data Integration",
    "Logic Apps":                    "Data Integration",
    "Azure Firewall":                "Infrastructure & Networking",
    "Virtual Network":               "Infrastructure & Networking",
    "Application Gateway":           "Infrastructure & Networking",
    "Load Balancer":                 "Infrastructure & Networking",
    "Azure DNS":                     "Infrastructure & Networking",
    "NAT Gateway":                   "Infrastructure & Networking",
    "Bandwidth":                     "Infrastructure & Networking",
    "VPN Gateway":                   "Infrastructure & Networking",
    "Storage":                       "Storage & Data Lake",
    "Microsoft Defender for Cloud":  "Security & Monitoring",
    "Log Analytics":                 "Security & Monitoring",
    "Azure Monitor":                 "Security & Monitoring",
    "Azure DevOps":                  "DevOps & Tooling",
    "Container Registry":            "DevOps & Tooling",
    "Automation":                    "DevOps & Tooling",
    "Key Vault":                     "DevOps & Tooling",
    "Event Grid":                    "DevOps & Tooling",
    "Azure Database for PostgreSQL":  "Databases",
    "Azure API Management":          "DevOps & Tooling",
}

CAT_COLOR = {
    "Compute & Virtual Desktop":   "#6366f1",
    "Analytics & Warehousing":     "#0ea5e9",
    "Developer Platforms":         "#f97316",
    "Infrastructure & Networking": "#8b5cf6",
    "Storage & Data Lake":         "#10b981",
    "Security & Monitoring":       "#ef4444",
    "Data Integration":            "#f59e0b",
    "DevOps & Tooling":            "#06b6d4",
    "Databases":                   "#84cc16",
    "Other":                       "#6b7280",
}

MONTH_NAMES = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]


# ── API helpers ────────────────────────────────────────────────────────────────

def get_token():
    r = subprocess.run(
        ["az", "account", "get-access-token",
         "--resource", "https://management.azure.com/",
         "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        print("ERROR: az account get-access-token failed. Run 'az login' first.", file=sys.stderr)
        sys.exit(1)
    return r.stdout.strip()


def cm_query(token, sub_id, payload, _retries=3):
    import time
    url = (f"https://management.azure.com/subscriptions/{sub_id}"
           f"/providers/Microsoft.CostManagement/query?api-version=2023-11-01")
    for attempt in range(_retries):
        try:
            r = requests.post(
                url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
                timeout=60
            )
            d = r.json()
        except Exception as e:
            print(f"  WARN: API call failed for {sub_id[:8]}: {e}", file=sys.stderr)
            return []
        err = d.get("error", {})
        if err.get("code") == "429":
            wait = 4 ** attempt
            print(f"  429 rate limit — retrying in {wait}s...", file=sys.stderr)
            time.sleep(wait)
            continue
        if err:
            print(f"  WARN: API error for {sub_id[:8]}: {err}", file=sys.stderr)
            return []
        rows = d.get("properties", {}).get("rows", [])
        cols = [c["name"] for c in d.get("properties", {}).get("columns", [])]
        return [dict(zip(cols, row)) for row in rows]
    print(f"  WARN: gave up after {_retries} retries for {sub_id[:8]}", file=sys.stderr)
    return []


def fetch_mtd(token, sub_id):
    return cm_query(token, sub_id, {
        "type": "ActualCost",
        "dataSet": {
            "granularity": "None",
            "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
            "grouping": [
                {"type": "Dimension", "name": "ServiceName"},
                {"type": "Dimension", "name": "PricingModel"},
            ]
        },
        "timeframe": "BillingMonthToDate",
    })


def fetch_last_month(token, sub_id):
    return cm_query(token, sub_id, {
        "type": "ActualCost",
        "dataSet": {
            "granularity": "None",
            "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
            "grouping": [{"type": "Dimension", "name": "ServiceName"}]
        },
        "timeframe": "TheLastBillingMonth",
    })


def fetch_trend(token, sub_id, months=6):
    today = date.today()
    m, y = today.month - months, today.year
    while m <= 0:
        m += 12; y -= 1
    start = date(y, m, 1)
    rows = cm_query(token, sub_id, {
        "type": "ActualCost",
        "dataSet": {
            "granularity": "Monthly",
            "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
        },
        "timeframe": "Custom",
        "timePeriod": {
            "from": f"{start.isoformat()}T00:00:00Z",
            "to": f"{today.isoformat()}T23:59:59Z",
        }
    })
    result = {}
    for row in rows:
        cost = float(row.get("Cost", 0) or 0)
        month_key = None
        for k, v in row.items():
            if k in ("Cost", "Currency"):
                continue
            # Integer YYYYMMDD
            if isinstance(v, (int, float)) and 20200101 <= v <= 20991231:
                dt = str(int(v))
                month_key = f"{dt[:4]}-{dt[4:6]}"
                break
            # ISO string like "2026-01-01T00:00:00" or "2026-01-01"
            if isinstance(v, str) and len(v) >= 7 and v[:4].isdigit() and v[4:5] in ("-", ""):
                month_key = f"{v[:4]}-{v[5:7]}" if v[4] == "-" else f"{v[:4]}-{v[4:6]}"
                break
        if month_key:
            result[month_key] = result.get(month_key, 0) + cost
    return result


def fetch_all_meters(token, sub_id):
    """Fetch every ServiceName + Meter + ServiceTier row MTD — filter in Python."""
    return cm_query(token, sub_id, {
        "type": "ActualCost",
        "dataSet": {
            "granularity": "None",
            "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
            "grouping": [
                {"type": "Dimension", "name": "ServiceName"},
                {"type": "Dimension", "name": "Meter"},
                {"type": "Dimension", "name": "ServiceTier"},
            ]
        },
        "timeframe": "BillingMonthToDate",
    })


def filter_meters(all_rows, service_name):
    return sorted(
        [r for r in all_rows
         if r.get("ServiceName", "") == service_name and float(r.get("Cost", 0) or 0) > 0.5],
        key=lambda x: -float(x.get("Cost", 0) or 0)
    )


# ── Data processing ────────────────────────────────────────────────────────────

def process(mtd_rows, last_rows):
    service_total   = defaultdict(float)
    on_demand_svc   = defaultdict(float)
    reserved_svc    = defaultdict(float)

    for row in mtd_rows:
        svc   = row.get("ServiceName", "Other") or "Other"
        cost  = float(row.get("Cost", 0) or 0)
        model = row.get("PricingModel", "OnDemand") or "OnDemand"
        service_total[svc] += cost
        if model.lower() == "reservation":
            reserved_svc[svc] += cost
        else:
            on_demand_svc[svc] += cost

    cat_total = defaultdict(float)
    for svc, cost in service_total.items():
        cat_total[CATEGORY_MAP.get(svc, "Other")] += cost

    last_svc = defaultdict(float)
    for row in last_rows:
        svc  = row.get("ServiceName", "Other") or "Other"
        cost = float(row.get("Cost", 0) or 0)
        last_svc[svc] += cost

    return {
        "service_total":  dict(service_total),
        "cat_total":      dict(cat_total),
        "on_demand_svc":  dict(on_demand_svc),
        "reserved_svc":   dict(reserved_svc),
        "last_svc":       dict(last_svc),
        "grand_total":    sum(service_total.values()),
        "last_total":     sum(last_svc.values()),
    }


def build_trend_data(raw, months=6):
    today = date.today()
    labels, display = [], []
    m, y = today.month - months + 1, today.year
    while m <= 0:
        m += 12; y -= 1
    for i in range(months):
        mm, yy = m + i, y
        while mm > 12:
            mm -= 12; yy += 1
        key = f"{yy}-{mm:02d}"
        labels.append(key)
        display.append(f"{MONTH_NAMES[mm-1]} {yy}")
    idoh_d   = raw.get(IDOH_ID,   {})
    shared_d = raw.get(SHARED_ID, {})
    if isinstance(idoh_d,   list): idoh_d   = {}
    if isinstance(shared_d, list): shared_d = {}
    idoh_vals   = [round(idoh_d.get(lbl,   0)) for lbl in labels]
    shared_vals = [round(shared_d.get(lbl, 0)) for lbl in labels]
    combined    = [i + s for i, s in zip(idoh_vals, shared_vals)]
    return display, idoh_vals, shared_vals, combined


def compute_opportunities(sd):
    opps = []
    idoh_od   = sd[IDOH_ID]["on_demand_svc"]
    shared_od = sd[SHARED_ID]["on_demand_svc"]

    vm = shared_od.get("Virtual Machines", 0)
    if vm > 200:
        opps.append({
            "opportunity": "Reserved Instances — AVD Session Hosts (Shared)",
            "details":     "FSv2 Windows hosts running fully on-demand",
            "current":     vm,
            "savings":     round(vm * 0.35),
            "action":      "Purchase 1-year VM Reserved Instances for FSv2/Dv3 Windows",
            "effort":      "Low",
            "detail_key":  "vm",
        })

    db = idoh_od.get("Azure Databricks", 0)
    if db > 200:
        opps.append({
            "opportunity": "Databricks Compute Savings Plan (IDOH)",
            "details":     "All Databricks spend is currently on-demand",
            "current":     db,
            "savings":     round(db * 0.20),
            "action":      "Purchase 1-year Databricks compute savings plan",
            "effort":      "Low",
            "detail_key":  "db",
        })

    fw = shared_od.get("Azure Firewall", 0)
    if fw > 200:
        opps.append({
            "opportunity": "Azure Firewall — Commitment Pricing (Shared)",
            "details":     "Firewall running on-demand with no commitment",
            "current":     fw,
            "savings":     round(fw * 0.20),
            "action":      "Switch to Azure Firewall 1-year commitment pricing",
            "effort":      "Low",
            "detail_key":  "fw",
        })

    la_total = (idoh_od.get("Log Analytics", 0) + shared_od.get("Log Analytics", 0))
    if la_total > 150:
        opps.append({
            "opportunity": "Log Analytics — Commitment Tier",
            "details":     f"${la_total:,.0f}/mo combined across both subscriptions",
            "current":     la_total,
            "savings":     round(la_total * 0.30),
            "action":      "Switch from Pay-As-You-Go to daily GB commitment tier",
            "effort":      "Low",
            "detail_key":  "la",
        })

    syn_shared = shared_od.get("Azure Synapse Analytics", 0)
    if syn_shared > 200:
        opps.append({
            "opportunity": "Synapse Reserved Capacity (Shared)",
            "details":     "Synapse Dedicated SQL pool running on-demand in Shared",
            "current":     syn_shared,
            "savings":     round(syn_shared * 0.37),
            "action":      "Purchase Synapse Dedicated SQL pool Reserved Capacity (1-year)",
            "effort":      "Low",
            "detail_key":  "syn",
        })

    return sorted(opps, key=lambda x: -x["savings"])


def build_opp_details(detail_raw):
    """Build JSON-serialisable detail dicts for each savings opportunity pop-up."""

    def rows_table(raw_rows, total, sub_label=None):
        out = []
        for r in raw_rows:
            cost = float(r.get("Cost", 0) or 0)
            pct  = f"{cost/total*100:.1f}%" if total else "—"
            row  = [r.get("Meter",""), r.get("ServiceTier",""), f"${cost:,.2f}", pct]
            if sub_label:
                row.insert(2, sub_label)
            out.append(row)
        return out

    details = {}

    # ── VM / AVD ──────────────────────────────────────────────────────────────
    vm_rows = detail_raw.get("vm_shared", [])
    total_vm = sum(float(r.get("Cost",0) or 0) for r in vm_rows)

    def vm_rows_table(raw_rows, total):
        out = []
        for r in raw_rows:
            cost    = float(r.get("Cost", 0) or 0)
            pct     = f"{cost/total*100:.1f}%" if total else "—"
            purpose = _vm_purpose(r.get("ServiceTier", ""))
            out.append([r.get("Meter",""), purpose, r.get("ServiceTier",""), f"${cost:,.2f}", pct])
        return out

    details["vm"] = {
        "title":   "VM Meter Breakdown — Shared Subscription",
        "explain": (
            "Every VM listed below is billed at full on-demand (pay-as-you-go) rates — "
            "no Reserved Instances are applied. The <strong>FSv2 Series Windows</strong> rows "
            "are the AVD session hosts your staff log into as virtual desktops. "
            "Purchasing 1-year Reserved Instances for those sizes locks in a ~35% lower hourly "
            "rate. Nothing changes for the VMs or the users — only the billing rate."
        ),
        "headers": ["VM Size", "Purpose", "Series / OS", "MTD Cost", "% of VMs"],
        "rows":    vm_rows_table(vm_rows, total_vm),
        "note":    f"All {len(vm_rows)} meter lines shown · Total ${total_vm:,.2f} MTD · PricingModel: OnDemand",
    }

    # ── Databricks ────────────────────────────────────────────────────────────
    db_rows = detail_raw.get("db_idoh", [])
    total_db = sum(float(r.get("Cost",0) or 0) for r in db_rows)
    details["db"] = {
        "title":   "Databricks Meter Breakdown — IDOH Subscription",
        "explain": (
            "All Databricks compute is billed on-demand (no savings plan applied). "
            "A <strong>1-year Databricks Savings Plan</strong> commits to a fixed $/hour of "
            "DBU compute and the discount applies automatically across all clusters and job "
            "runs — no changes to workloads, notebooks, or pipelines required."
        ),
        "headers": ["Meter", "Service Tier", "MTD Cost", "% of Databricks"],
        "rows":    rows_table(db_rows, total_db),
        "note":    f"All {len(db_rows)} meter lines shown · Total ${total_db:,.2f} MTD · PricingModel: OnDemand",
    }

    # ── Firewall ──────────────────────────────────────────────────────────────
    fw_rows = detail_raw.get("fw_shared", [])
    total_fw = sum(float(r.get("Cost",0) or 0) for r in fw_rows)
    details["fw"] = {
        "title":   "Azure Firewall Meter Breakdown — Shared Subscription",
        "explain": (
            "Azure Firewall charges for two things: <strong>deployment hours</strong> "
            "(a fixed cost per hour the firewall exists, regardless of traffic) and "
            "<strong>data processed</strong> (per GB inspected). Commitment pricing applies "
            "a ~20% discount to the deployment hour component only — data processing stays "
            "pay-as-you-go. No firewall rules, policies, or traffic routing change."
        ),
        "headers": ["Meter", "Service Tier", "MTD Cost", "% of Firewall"],
        "rows":    rows_table(fw_rows, total_fw),
        "note":    f"All {len(fw_rows)} meter lines shown · Total ${total_fw:,.2f} MTD · PricingModel: OnDemand",
    }

    # ── Log Analytics ─────────────────────────────────────────────────────────
    la_idoh   = detail_raw.get("la_idoh",   [])
    la_shared = detail_raw.get("la_shared", [])
    total_la  = sum(float(r.get("Cost",0) or 0) for r in la_idoh + la_shared)
    la_rows   = []
    for r in la_idoh:
        cost = float(r.get("Cost",0) or 0)
        pct  = f"{cost/total_la*100:.1f}%" if total_la else "—"
        la_rows.append([r.get("Meter",""), "IDOH", r.get("ServiceTier",""), f"${cost:,.2f}", pct])
    for r in la_shared:
        cost = float(r.get("Cost",0) or 0)
        pct  = f"{cost/total_la*100:.1f}%" if total_la else "—"
        la_rows.append([r.get("Meter",""), "Shared", r.get("ServiceTier",""), f"${cost:,.2f}", pct])
    la_rows.sort(key=lambda x: -float(x[3].replace("$","").replace(",","")))
    details["la"] = {
        "title":   "Log Analytics Meter Breakdown — Both Subscriptions",
        "explain": (
            "Log Analytics charges per GB of data ingested. Above <strong>100 GB/day</strong> "
            "a commitment tier reduces the per-GB price by 25–30% with no changes to what is "
            "logged, how long it is retained, or how queries run. Each workspace can be switched "
            "to commitment pricing independently — workspaces in both subscriptions are eligible."
        ),
        "headers": ["Meter", "Subscription", "Service Tier", "MTD Cost", "% of LA Total"],
        "rows":    la_rows,
        "note":    f"Total ${total_la:,.2f} MTD · PricingModel: OnDemand",
    }

    return details


def compute_flags(sd, days_elapsed, days_in_month):
    flags = []
    idoh_od   = sd[IDOH_ID]["on_demand_svc"]
    shared_od = sd[SHARED_ID]["on_demand_svc"]
    idoh_svc  = sd[IDOH_ID]["service_total"]
    shared_svc= sd[SHARED_ID]["service_total"]

    # GPU cluster in Databricks DEV
    flags.append({
        "level":  "yellow",
        "title":  "GPU Cluster Configured in Databricks DEV",
        "detail": "An NC6s_v3 (NVIDIA V100) cluster is provisioned in the DEV workspace. "
                  "It is currently terminated but will cost ~$3.06/hr when started. "
                  "Recommend removing or converting to a CPU cluster if not actively required.",
    })

    # Databricks fully on-demand
    db = idoh_od.get("Azure Databricks", 0)
    if db > 500:
        flags.append({
            "level":  "orange",
            "title":  f"Databricks Entirely On-Demand — ${db:,.0f} MTD",
            "detail": "No savings plan or reserved capacity is applied to Databricks. "
                      "This is the largest unprotected spend item in the IDOH subscription and "
                      "a high-priority target for a 1-year savings plan.",
        })

    # Shared VMs no RI
    vm = shared_od.get("Virtual Machines", 0)
    if vm > 1000:
        flags.append({
            "level":  "orange",
            "title":  f"AVD Session Hosts Fully On-Demand — ${vm:,.0f} MTD (Shared)",
            "detail": "All FSv2 Windows virtual machines backing AVD host pools are running "
                      "on-demand pricing. With 142 host pools in scope, Reserved Instances "
                      "represent the single largest cost reduction opportunity available.",
        })

    # Defender for Cloud
    def_shared = shared_svc.get("Microsoft Defender for Cloud", 0)
    if def_shared > 800:
        flags.append({
            "level":  "yellow",
            "title":  f"Microsoft Defender for Cloud — ${def_shared:,.0f} MTD (Shared)",
            "detail": "ARM service layer charges are elevated. Review whether all Defender "
                      "plans (Servers, Storage, Containers, Key Vault, DNS, ARM) are required "
                      "or whether scope can be reduced to critical workloads only.",
        })

    # Log Analytics
    la_shared = shared_svc.get("Log Analytics", 0)
    if la_shared > 500:
        flags.append({
            "level":  "yellow",
            "title":  f"Log Analytics Ingestion — ${la_shared:,.0f} MTD (Shared)",
            "detail": "High ingestion volume detected. Review which data sources are sending "
                      "to Log Analytics, whether data is retained longer than required, and "
                      "whether a commitment tier can reduce per-GB cost.",
        })

    return flags


# ── HTML builder ───────────────────────────────────────────────────────────────

def esc(s):
    return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")


def build_html(generated, today, days_in_month, days_elapsed, days_remaining,
               sd, combined_cats, combined_total, combined_last,
               projected, opportunities, opp_details, flags,
               trend_labels, trend_idoh, trend_shared, trend_combined):

    idoh_total   = sd[IDOH_ID]["grand_total"]
    shared_total = sd[SHARED_ID]["grand_total"]
    idoh_last    = sd[IDOH_ID]["last_total"]
    shared_last  = sd[SHARED_ID]["last_total"]
    month_name   = today.strftime("%B %Y")
    billing_period = f"{today.strftime('%B 1')} – {today.strftime('%B %d, %Y')}"

    def delta_card(mtd, last):
        if last < 1:
            return '<span class="no-data">No prior month data</span>'
        proj = mtd * (days_in_month / days_elapsed) if days_elapsed else 0
        pct  = (proj - last) / last * 100
        if pct > 10:
            css, arrow = "var(--red)", "▲"
        elif pct > 0:
            css, arrow = "var(--yel)", "▲"
        elif pct < -5:
            css, arrow = "var(--grn)", "▼"
        else:
            css, arrow = "var(--grn)", "▼"
        return (f'<span style="color:{css}">{arrow} {abs(pct):.0f}% vs June</span> '
                f'<span class="proj-note">(projected ${proj:,.0f})</span>')

    # ── category bars ──────────────────────────────────────────────────────────
    sorted_cats = sorted(combined_cats.items(), key=lambda x: -x[1])
    cat_max     = sorted_cats[0][1] if sorted_cats else 1
    cat_bars_html = ""
    for cat, cost in sorted_cats:
        if cost < 1:
            continue
        pct   = cost / combined_total * 100 if combined_total else 0
        w_pct = cost / cat_max * 100
        color = CAT_COLOR.get(cat, CAT_COLOR["Other"])
        idoh_cat   = sd[IDOH_ID]["cat_total"].get(cat, 0)
        shared_cat = sd[SHARED_ID]["cat_total"].get(cat, 0)
        sub_bits = []
        if idoh_cat > 0:
            sub_bits.append(f'<span style="color:#38bdf8">IDOH ${idoh_cat:,.0f}</span>')
        if shared_cat > 0:
            sub_bits.append(f'<span style="color:#a78bfa">Shared ${shared_cat:,.0f}</span>')
        sub_detail = " &nbsp;·&nbsp; ".join(sub_bits)
        cat_bars_html += f"""
        <div class="cat-row">
          <div class="cat-label">{esc(cat)}</div>
          <div class="cat-bar-wrap">
            <div class="cat-bar" style="width:{w_pct:.1f}%;background:{color}"></div>
          </div>
          <div class="cat-right">
            <div class="cat-amount">${cost:,.0f} <span class="cat-pct">{pct:.0f}%</span></div>
            <div class="cat-sub">{sub_detail}</div>
          </div>
        </div>"""

    # ── savings opportunities ──────────────────────────────────────────────────
    total_savings_mo = sum(o["savings"] for o in opportunities)
    total_savings_yr = total_savings_mo * 12
    pct_of_spend     = (total_savings_mo / combined_total * 100) if combined_total else 0

    opp_rows_html = ""
    EFFORT_COLORS = {"Low": ("#14532d","#4ade80"), "Medium": ("#78350f","#fbbf24"), "High": ("#450a0a","#f87171")}
    for i, o in enumerate(opportunities):
        dot_colors = ["#ef4444","#f97316","#f59e0b","#facc15","#a3e635"]
        dot_color  = dot_colors[min(i, len(dot_colors)-1)]
        bg, fg = EFFORT_COLORS.get(o["effort"], ("#1e1e1e","#9ca3af"))
        dk = o.get("detail_key", "")
        detail_btn = (f' <button class="opp-info-btn" onclick="openOppDetail(\'{dk}\')" '
                      f'title="See underlying data">&#x24D8; Details</button>') if dk else ""
        opp_rows_html += f"""
        <tr>
          <td><span class="pri-dot" style="background:{dot_color}"></span></td>
          <td>
            <strong>{esc(o['opportunity'])}</strong>{detail_btn}
            <span class="sub-text">{esc(o['details'])}</span>
          </td>
          <td>${o['current']:,.0f}/mo</td>
          <td class="savings-cell">
            ${o['savings']:,.0f}/mo
            <span class="sub-text" style="color:#86efac">${o['savings']*12:,.0f}/yr</span>
          </td>
          <td>{esc(o['action'])}</td>
          <td><span class="effort-badge" style="background:{bg};color:{fg}">{esc(o['effort'])}</span></td>
        </tr>"""

    # ── flags ─────────────────────────────────────────────────────────────────
    flags_html = ""
    LEVEL_STYLE = {
        "orange": ("#f97316", "⚠"),
        "yellow": ("#f59e0b", "●"),
        "red":    ("#ef4444", "🔴"),
    }
    for flag in flags:
        border_color, icon = LEVEL_STYLE.get(flag["level"], ("#6b7280","•"))
        flags_html += f"""
        <div class="flag-item" style="border-left-color:{border_color}">
          <div class="flag-title">{icon} {esc(flag['title'])}</div>
          <div class="flag-detail">{esc(flag['detail'])}</div>
        </div>"""

    # ── RI savings banner ──────────────────────────────────────────────────────
    idoh_reserved = sum(sd[IDOH_ID]["reserved_svc"].values())
    est_ri_save   = round(idoh_reserved * 0.54)  # 35% discount → saved/paid ≈ 0.54
    ri_html = ""
    if idoh_reserved > 0:
        ri_html = (f'<div class="ri-active">&#x2713; Reserved Instance active on '
                   f'<strong>Synapse Dedicated SQL Pool (IDOH)</strong> &nbsp;·&nbsp; '
                   f'Est. <strong>${est_ri_save:,.0f}/mo</strong> savings vs on-demand</div>')

    # ── Opportunity detail JSON ────────────────────────────────────────────────
    opp_details_js = json.dumps(opp_details)

    # ── Chart.js JSON ──────────────────────────────────────────────────────────
    tl_js  = json.dumps(trend_labels)
    ti_js  = json.dumps(trend_idoh)
    ts_js  = json.dumps(trend_shared)
    tc_js  = json.dumps(trend_combined)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Azure Cost Report — IDOH Metadata Marketplace</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js/dist/chart.umd.min.js"></script>
<style>
:root{{
  --bg:#0d0f1a;--sur:#12141f;--sur2:#1a1d2e;--brd:#2a2d3e;
  --txt:#e8eaf0;--mut:#6b7280;--acc:#6366f1;
  --grn:#4ade80;--yel:#facc15;--red:#f87171;--cyn:#38bdf8;--org:#fb923c;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--txt);font-family:'Segoe UI',system-ui,sans-serif;font-size:13px;line-height:1.5}}
a{{color:var(--cyn);text-decoration:none}}
a:hover{{text-decoration:underline}}

/* header */
.hdr{{background:var(--sur);border-bottom:1px solid var(--brd);padding:14px 28px;display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}}
.hdr-title{{font-size:17px;font-weight:800;color:var(--txt)}}
.hdr-sub{{font-size:11px;color:var(--mut);margin-top:2px}}
.back-link{{font-size:11px;color:var(--mut)}}
.back-link:hover{{color:var(--cyn)}}

/* layout */
.page{{max-width:1200px;margin:0 auto;padding:24px 28px}}
.section{{margin-bottom:32px}}
.section-title{{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:var(--mut);margin-bottom:14px;padding-bottom:6px;border-bottom:1px solid var(--brd)}}

/* scorecards */
.scorecards{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:20px}}
@media(max-width:700px){{.scorecards{{grid-template-columns:1fr}}}}
.scorecard{{background:var(--sur);border:1px solid var(--brd);border-radius:10px;padding:20px}}
.sc-label{{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);margin-bottom:10px}}
.sc-amount{{font-size:34px;font-weight:800;line-height:1;margin-bottom:8px;letter-spacing:-.5px}}
.sc-delta{{font-size:12px;margin-bottom:4px;min-height:18px}}
.sc-note{{font-size:11px;color:var(--mut)}}
.proj-note{{font-size:10px;color:var(--mut)}}
.no-data{{color:var(--mut);font-size:11px}}
.proj-big{{font-size:17px;font-weight:700;color:var(--txt);margin-top:6px}}

/* RI banner */
.ri-banner{{background:var(--sur2);border:1px solid var(--brd);border-radius:8px;padding:12px 16px;margin-bottom:24px;display:flex;flex-wrap:wrap;gap:12px;align-items:flex-start}}
.ri-active{{color:var(--grn);font-size:12px;flex:1;min-width:260px}}
.ri-opportunity{{color:var(--org);font-size:12px;flex:1;min-width:260px}}

/* category bars */
.cat-row{{display:grid;grid-template-columns:210px 1fr 190px;gap:10px;align-items:center;margin-bottom:9px}}
@media(max-width:700px){{.cat-row{{grid-template-columns:1fr;gap:4px}}}}
.cat-label{{font-size:12px;font-weight:600;text-align:right;padding-right:10px;white-space:nowrap}}
.cat-bar-wrap{{background:var(--sur2);border-radius:4px;height:20px;overflow:hidden}}
.cat-bar{{height:100%;border-radius:4px}}
.cat-right{{}}
.cat-amount{{font-size:12px;font-weight:700}}
.cat-pct{{font-size:10px;color:var(--mut);font-weight:400;margin-left:4px}}
.cat-sub{{font-size:10px;color:var(--mut);margin-top:1px}}

/* chart */
.chart-wrap{{background:var(--sur);border:1px solid var(--brd);border-radius:10px;padding:20px;position:relative;height:320px}}

/* table */
.tbl-wrap{{background:var(--sur);border:1px solid var(--brd);border-radius:10px;overflow:hidden}}
table{{width:100%;border-collapse:collapse}}
th{{background:var(--sur2);padding:9px 12px;text-align:left;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:var(--mut);border-bottom:2px solid var(--brd);white-space:nowrap}}
td{{padding:10px 12px;border-bottom:1px solid var(--brd);vertical-align:top;font-size:12px}}
tr:last-child td{{border-bottom:none}}
tr:hover td{{background:var(--sur2)}}
.sub-text{{font-size:10px;color:var(--mut);line-height:1.4;display:block;margin-top:3px}}
.pri-dot{{display:inline-block;width:9px;height:9px;border-radius:50%;margin-top:2px}}
.savings-cell{{color:#4ade80;font-weight:700}}
.effort-badge{{display:inline-block;padding:2px 9px;border-radius:4px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.03em}}

/* savings total */
.savings-total{{display:flex;gap:0;border-top:2px solid var(--brd)}}
.st-item{{flex:1;padding:14px 16px;text-align:center;border-right:1px solid var(--brd)}}
.st-item:last-child{{border-right:none}}
.st-label{{font-size:10px;color:var(--mut);text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px}}
.st-amount{{font-size:24px;font-weight:800;color:var(--grn)}}

/* flags */
.flag-item{{background:var(--sur);border:1px solid var(--brd);border-left:3px solid;border-radius:8px;padding:13px 16px;margin-bottom:8px}}
.flag-title{{font-size:12px;font-weight:700;margin-bottom:4px}}
.flag-detail{{font-size:11px;color:var(--mut);line-height:1.6}}

/* gen-ts */
.gen-ts-wrap{{font-size:11px;white-space:nowrap}}

/* opp detail button */
.opp-info-btn{{display:inline-flex;align-items:center;gap:3px;margin-left:8px;
  padding:2px 8px;border-radius:4px;border:1px solid var(--brd);background:var(--sur2);
  color:var(--cyn);font-size:10px;font-weight:700;cursor:pointer;font-family:inherit;
  vertical-align:middle;transition:border-color .12s,background .12s}}
.opp-info-btn:hover{{border-color:var(--cyn);background:#0c2a35}}

/* opportunity detail modal */
.opp-overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.65);
  z-index:500;align-items:center;justify-content:center;padding:24px}}
.opp-overlay.open{{display:flex}}
.opp-box{{background:var(--sur);border:1px solid var(--brd);border-radius:12px;
  max-width:780px;width:100%;max-height:85vh;overflow-y:auto;
  box-shadow:0 8px 32px rgba(0,0,0,.6)}}
.opp-box-hdr{{display:flex;align-items:flex-start;justify-content:space-between;
  gap:12px;padding:18px 20px 14px;border-bottom:1px solid var(--brd)}}
.opp-box-title{{font-size:14px;font-weight:800;color:var(--txt);line-height:1.3}}
.opp-box-close{{background:none;border:1px solid var(--brd);border-radius:6px;
  color:var(--mut);font-size:14px;cursor:pointer;padding:2px 9px;flex-shrink:0;
  font-family:inherit;transition:border-color .12s,color .12s}}
.opp-box-close:hover{{border-color:var(--red);color:var(--red)}}
.opp-box-explain{{padding:14px 20px;font-size:12px;color:var(--mut);line-height:1.7;
  border-bottom:1px solid var(--brd)}}
.opp-box-explain strong{{color:var(--txt)}}
.opp-box-body{{padding:16px 20px}}
.opp-box-note{{font-size:10px;color:var(--mut);margin-top:12px;padding-top:8px;
  border-top:1px solid var(--brd)}}
.opp-tbl{{width:100%;border-collapse:collapse;font-size:12px}}
.opp-tbl th{{background:var(--sur2);padding:7px 10px;text-align:left;
  font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;
  color:var(--mut);border-bottom:2px solid var(--brd);white-space:nowrap}}
.opp-tbl td{{padding:8px 10px;border-bottom:1px solid var(--brd);vertical-align:middle}}
.opp-tbl tr:last-child td{{border-bottom:none}}
.opp-tbl tr:hover td{{background:var(--sur2)}}
.opp-tbl .cost-col{{font-weight:700;color:var(--txt);text-align:right}}
.opp-tbl .pct-col{{color:var(--mut);text-align:right}}

/* footer */
.footer{{text-align:center;padding:20px;color:var(--mut);font-size:11px;border-top:1px solid var(--brd);margin-top:32px}}
</style>
</head>
<body>

<div class="hdr">
  <div>
    <div class="hdr-title">Azure Cost Report</div>
    <div class="hdr-sub">
      ECAE IDOH Production &nbsp;&middot;&nbsp; ECAE Shared Production &nbsp;&middot;&nbsp; {billing_period}
      &nbsp;&mdash;&nbsp; <a class="back-link" href="index.html">&#8592; Marketplace</a>
    </div>
  </div>
  <div class="gen-ts-wrap">
    Generated <span id="gen-ts" data-ts="{generated}">&#x21BB; {generated}</span>
    <script>(function(){{var s=document.getElementById('gen-ts'),h=(Date.now()-new Date(s.dataset.ts.replace(/ [A-Z]{{2,4}}$/,'').replace(' ','T')))/36e5;s.style.color=h<25?'var(--grn)':h<168?'var(--yel)':'var(--red)';s.style.fontWeight='700';}})();</script>
  </div>
</div>

<div class="page">

  <!-- ── SCORECARDS ── -->
  <div class="scorecards">

    <div class="scorecard" style="border-top:3px solid #38bdf8">
      <div class="sc-label">ECAE IDOH Production</div>
      <div class="sc-amount" style="color:#38bdf8">${idoh_total:,.0f}</div>
      <div class="sc-delta">{delta_card(idoh_total, idoh_last)}</div>
      <div class="sc-note">Month-to-date &middot; {days_elapsed} of {days_in_month} days</div>
    </div>

    <div class="scorecard" style="border-top:3px solid #a78bfa">
      <div class="sc-label">ECAE Shared Production</div>
      <div class="sc-amount" style="color:#a78bfa">${shared_total:,.0f}</div>
      <div class="sc-delta">{delta_card(shared_total, shared_last)}</div>
      <div class="sc-note">Month-to-date &middot; {days_elapsed} of {days_in_month} days</div>
    </div>

    <div class="scorecard" style="border-top:3px solid #4ade80">
      <div class="sc-label">Combined &mdash; {month_name}</div>
      <div class="sc-amount" style="color:#4ade80">${combined_total:,.0f}</div>
      <div class="proj-big">Projected month-end: ${projected:,.0f}</div>
      <div class="sc-note" style="margin-top:6px">{days_remaining} days remaining in billing period</div>
    </div>

  </div>

  <!-- ── RI / DISCOUNT BANNER ── -->
  <div class="ri-banner">
    {ri_html if ri_html else '<div class="ri-active" style="color:var(--mut)">No active Reserved Instances detected</div>'}
    <div class="ri-opportunity">&#x26A1; Identified savings: <strong>${total_savings_mo:,.0f}/mo (${total_savings_yr:,.0f}/yr)</strong> — {pct_of_spend:.0f}% of current combined spend &mdash; see Savings Opportunities below</div>
  </div>

  <!-- ── SPEND BY CATEGORY ── -->
  <div class="section">
    <div class="section-title">Spend by Business Category &mdash; Combined MTD</div>
    {cat_bars_html}
  </div>

  <!-- ── MONTHLY TREND ── -->
  <div class="section">
    <div class="section-title">Monthly Spend Trend &mdash; Last 6 Months</div>
    <div class="chart-wrap">
      <canvas id="trendChart"></canvas>
    </div>
  </div>

  <!-- ── SAVINGS OPPORTUNITIES ── -->
  <div class="section">
    <div class="section-title">Savings Opportunities &mdash; Ranked by Est. Monthly Impact</div>
    <div class="tbl-wrap">
      <table>
        <thead>
          <tr>
            <th style="width:16px"></th>
            <th>Opportunity</th>
            <th>Current Spend</th>
            <th>Est. Savings</th>
            <th>Action Required</th>
            <th>Effort</th>
          </tr>
        </thead>
        <tbody>
          {opp_rows_html}
        </tbody>
      </table>
      <div class="savings-total">
        <div class="st-item">
          <div class="st-label">Total Monthly Savings</div>
          <div class="st-amount">${total_savings_mo:,.0f}</div>
        </div>
        <div class="st-item">
          <div class="st-label">Total Annual Savings</div>
          <div class="st-amount">${total_savings_yr:,.0f}</div>
        </div>
        <div class="st-item">
          <div class="st-label">% of Combined Spend</div>
          <div class="st-amount">{pct_of_spend:.0f}%</div>
        </div>
      </div>
    </div>
    <p style="font-size:10px;color:var(--mut);margin-top:10px;line-height:1.6">
      &#x2139;&#xFE0F; Savings estimates are based on current Azure published discount rates for Reserved Instances (VMs ~35%), Databricks Savings Plans (~20%), Azure Firewall commitment (~20%), and Log Analytics commitment tiers (~30%). Actual savings may vary based on utilization, term length, and payment option selected.
    </p>
  </div>

  <!-- ── FLAGGED ITEMS ── -->
  <div class="section">
    <div class="section-title">Items Requiring Attention</div>
    {flags_html}
  </div>

</div>

<div class="footer">
  IDOH Azure Metadata Marketplace &nbsp;&middot;&nbsp; Azure Cost Report &nbsp;&middot;&nbsp; Source: Azure Cost Management API
</div>

<!-- ── Savings Opportunity Detail Modal ── -->
<div class="opp-overlay" id="opp-overlay" onclick="if(event.target===this)closeOppDetail()">
  <div class="opp-box">
    <div class="opp-box-hdr">
      <div class="opp-box-title" id="opp-box-title"></div>
      <button class="opp-box-close" onclick="closeOppDetail()">&#x2715;</button>
    </div>
    <div class="opp-box-explain" id="opp-box-explain"></div>
    <div class="opp-box-body">
      <table class="opp-tbl" id="opp-box-tbl"></table>
      <div class="opp-box-note" id="opp-box-note"></div>
    </div>
  </div>
</div>

<script>
const OPP_DETAILS = {opp_details_js};

function openOppDetail(key) {{
  const d = OPP_DETAILS[key];
  if (!d) return;
  document.getElementById('opp-box-title').textContent   = d.title;
  document.getElementById('opp-box-explain').innerHTML   = d.explain;
  document.getElementById('opp-box-note').textContent    = d.note || '';

  // determine which columns are cost/pct by position from end
  const headers = d.headers;
  const costIdx = headers.length - 2;  // second-to-last = cost
  const pctIdx  = headers.length - 1;  // last = %

  let html = '<thead><tr>' +
    headers.map((h, i) => {{
      let cls = '';
      if (i === costIdx) cls = ' style="text-align:right"';
      if (i === pctIdx)  cls = ' style="text-align:right"';
      return `<th${{cls}}>${{h}}</th>`;
    }}).join('') + '</tr></thead><tbody>';

  d.rows.forEach(row => {{
    html += '<tr>' + row.map((cell, i) => {{
      let cls = '';
      if (i === costIdx) cls = ' class="cost-col"';
      if (i === pctIdx)  cls = ' class="pct-col"';
      return `<td${{cls}}>${{cell}}</td>`;
    }}).join('') + '</tr>';
  }});
  html += '</tbody>';
  document.getElementById('opp-box-tbl').innerHTML = html;
  document.getElementById('opp-overlay').classList.add('open');
}}

function closeOppDetail() {{
  document.getElementById('opp-overlay').classList.remove('open');
}}

document.addEventListener('keydown', e => {{
  if (e.key === 'Escape') closeOppDetail();
}});
</script>

<script>
(function(){{
  const ctx = document.getElementById('trendChart');
  const labels   = {tl_js};
  const idoh     = {ti_js};
  const shared   = {ts_js};
  const combined = {tc_js};

  new Chart(ctx, {{
    type: 'line',
    data: {{
      labels: labels,
      datasets: [
        {{
          label: 'ECAE IDOH Production',
          data: idoh,
          borderColor: '#38bdf8',
          backgroundColor: 'rgba(56,189,248,0.07)',
          borderWidth: 2,
          pointRadius: 5,
          pointHoverRadius: 7,
          fill: true,
          tension: 0.35,
        }},
        {{
          label: 'ECAE Shared Production',
          data: shared,
          borderColor: '#a78bfa',
          backgroundColor: 'rgba(167,139,250,0.07)',
          borderWidth: 2,
          pointRadius: 5,
          pointHoverRadius: 7,
          fill: true,
          tension: 0.35,
        }},
        {{
          label: 'Combined',
          data: combined,
          borderColor: '#4ade80',
          backgroundColor: 'transparent',
          borderWidth: 2,
          borderDash: [5, 4],
          pointRadius: 4,
          pointHoverRadius: 6,
          fill: false,
          tension: 0.35,
        }},
      ]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{
          labels: {{
            color: '#9ca3af',
            font: {{ size: 11 }},
            boxWidth: 26,
            padding: 18,
          }},
        }},
        tooltip: {{
          backgroundColor: '#1a1d2e',
          borderColor: '#2a2d3e',
          borderWidth: 1,
          titleColor: '#e8eaf0',
          bodyColor: '#9ca3af',
          padding: 10,
          callbacks: {{
            label: function(ctx) {{
              const v = ctx.raw;
              return '  ' + ctx.dataset.label + ': $' + (v || 0).toLocaleString();
            }}
          }}
        }}
      }},
      scales: {{
        x: {{
          grid: {{ color: '#1e2030' }},
          ticks: {{ color: '#6b7280', font: {{ size: 11 }} }},
        }},
        y: {{
          grid: {{ color: '#1e2030' }},
          ticks: {{
            color: '#6b7280',
            font: {{ size: 11 }},
            callback: function(v) {{ return '$' + v.toLocaleString(); }}
          }},
          beginAtZero: true,
        }}
      }}
    }}
  }});
}})();
</script>

</body>
</html>"""


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    now = datetime.now(tz=EASTERN)
    generated  = now.strftime("%Y-%m-%d %H:%M %Z")
    today      = date.today()
    days_in_mo = calendar.monthrange(today.year, today.month)[1]
    days_elapsed   = today.day
    days_remaining = days_in_mo - days_elapsed

    print("Fetching Azure ARM token...", flush=True)
    token = get_token()
    print("  OK", flush=True)

    print("Querying Cost Management API (6 parallel calls)...", flush=True)
    futures = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        for sid in [IDOH_ID, SHARED_ID]:
            futures[ex.submit(fetch_mtd,        token, sid)] = ("mtd",   sid)
            futures[ex.submit(fetch_last_month,  token, sid)] = ("last",  sid)
            futures[ex.submit(fetch_trend,       token, sid)] = ("trend", sid)

    raw = {"mtd": {}, "last": {}, "trend": {}}
    for fut in as_completed(futures):
        kind, sid = futures[fut]
        try:
            result = fut.result()
            raw[kind][sid] = result
            print(f"  {kind}/{SUB_SHORT[sid]}: {len(result)} rows", flush=True)
        except Exception as e:
            print(f"  WARN: {kind}/{sid[:8]}: {e}", flush=True)
            raw[kind][sid] = []

    # Process per subscription
    sd = {}
    for sid in [IDOH_ID, SHARED_ID]:
        sd[sid] = process(raw["mtd"].get(sid, []), raw["last"].get(sid, []))
        print(f"  {SUB_SHORT[sid]} MTD total: ${sd[sid]['grand_total']:,.0f}", flush=True)

    # Combined category totals
    combined_cats = defaultdict(float)
    for sid in [IDOH_ID, SHARED_ID]:
        for cat, cost in sd[sid]["cat_total"].items():
            combined_cats[cat] += cost

    combined_total = sum(s["grand_total"] for s in sd.values())
    combined_last  = sum(s["last_total"]  for s in sd.values())
    projected      = combined_total * (days_in_mo / days_elapsed) if days_elapsed else 0

    opportunities = compute_opportunities(sd)
    flags         = compute_flags(sd, days_elapsed, days_in_mo)
    trend_labels, trend_idoh, trend_shared, trend_combined = build_trend_data(raw["trend"])

    print("Fetching meter-level detail (2 calls — one per subscription)...", flush=True)
    meters_idoh, meters_shared = [], []
    try:
        meters_idoh   = fetch_all_meters(token, IDOH_ID)
        print(f"  IDOH meters: {len(meters_idoh)} rows", flush=True)
    except Exception as e:
        print(f"  WARN: meters/IDOH: {e}", flush=True)
    try:
        meters_shared = fetch_all_meters(token, SHARED_ID)
        print(f"  Shared meters: {len(meters_shared)} rows", flush=True)
    except Exception as e:
        print(f"  WARN: meters/Shared: {e}", flush=True)

    detail_raw = {
        "vm_shared": filter_meters(meters_shared, "Virtual Machines"),
        "db_idoh":   filter_meters(meters_idoh,   "Azure Databricks"),
        "fw_shared": filter_meters(meters_shared, "Azure Firewall"),
        "la_idoh":   filter_meters(meters_idoh,   "Log Analytics"),
        "la_shared": filter_meters(meters_shared, "Log Analytics"),
    }

    opp_details = build_opp_details(detail_raw)

    print("Building HTML...", flush=True)
    html = build_html(
        generated       = generated,
        today           = today,
        days_in_month   = days_in_mo,
        days_elapsed    = days_elapsed,
        days_remaining  = days_remaining,
        sd              = sd,
        combined_cats   = dict(combined_cats),
        combined_total  = combined_total,
        combined_last   = combined_last,
        projected       = projected,
        opportunities   = opportunities,
        opp_details     = opp_details,
        flags           = flags,
        trend_labels    = trend_labels,
        trend_idoh      = trend_idoh,
        trend_shared    = trend_shared,
        trend_combined  = trend_combined,
    )

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved: {OUTPUT}", flush=True)
    print(f"  Combined MTD: ${combined_total:,.0f}  /  Projected: ${projected:,.0f}", flush=True)
    print(f"  Savings opportunities: ${sum(o['savings'] for o in opportunities):,.0f}/mo identified", flush=True)


if __name__ == "__main__":
    main()
