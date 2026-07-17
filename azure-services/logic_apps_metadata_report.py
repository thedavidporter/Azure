import os
#!/usr/bin/env python3
"""
Azure Logic Apps Metadata Report
Collects workflows, trigger types, actions, connections, and recent run history
for dev and prd environments, then writes a self-contained interactive HTML report.

Usage:
  python3 logic_apps_metadata_report.py --env dev
  python3 logic_apps_metadata_report.py --env prd
"""

import argparse
import json
import subprocess
import requests
from collections import defaultdict
from datetime import datetime

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

API_VER          = "2016-06-01"
MGMT             = "https://management.azure.com"
RUN_LIMIT        = 25   # recent runs to fetch per workflow

# ── auth ───────────────────────────────────────────────────────────────────────

def get_token():
    result = subprocess.run(
        ["az", "account", "get-access-token", "--resource", "https://management.azure.com/"],
        capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)["accessToken"]

def headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# ── API helpers ────────────────────────────────────────────────────────────────

def get_all(url, token, params=None):
    """Follow nextLink pagination and return combined value list."""
    p = {"api-version": API_VER}
    if params:
        p.update(params)
    results = []
    while url:
        r = requests.get(url, headers=headers(token), params=p)
        r.raise_for_status()
        data = r.json()
        results.extend(data.get("value", []))
        url   = data.get("nextLink")
        p     = {}   # nextLink already has params
    return results

def get_one(url, token):
    r = requests.get(url, headers=headers(token), params={"api-version": API_VER})
    if r.status_code == 200:
        return r.json()
    return {}

# ── fetch ──────────────────────────────────────────────────────────────────────

def list_workflows(sub, rg, token):
    if rg:
        url = f"{MGMT}/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.Logic/workflows"
    else:
        url = f"{MGMT}/subscriptions/{sub}/providers/Microsoft.Logic/workflows"
    return get_all(url, token)

def get_run_history(sub, rg, name, token):
    url = (f"{MGMT}/subscriptions/{sub}/resourceGroups/{rg}"
           f"/providers/Microsoft.Logic/workflows/{name}/runs")
    try:
        return get_all(url, token, params={"$top": RUN_LIMIT})
    except Exception:
        return []

# ── parsing helpers ────────────────────────────────────────────────────────────

def parse_trigger(definition):
    triggers = definition.get("triggers", {})
    if not triggers:
        return "None", ""
    name, trig = next(iter(triggers.items()))
    ttype = trig.get("type", "Unknown")
    detail = ""
    if ttype == "Recurrence":
        rec = trig.get("recurrence", {})
        freq = rec.get("frequency", "")
        interval = rec.get("interval", "")
        detail = f"Every {interval} {freq}" if interval and freq else ""
    elif ttype in ("Request", "HttpWebhook"):
        detail = "HTTP/Webhook"
    elif ttype == "ApiConnection":
        conn = (trig.get("inputs", {}).get("host", {})
                    .get("connection", {}).get("name", ""))
        detail = conn.replace("@parameters('$connections')['", "").replace("']['connectionId']", "")
    return ttype, detail

def parse_actions(definition):
    actions = definition.get("actions", {})
    type_counts = defaultdict(int)
    for act in actions.values():
        t = act.get("type", "Unknown")
        type_counts[t] += 1
    return dict(type_counts)

def parse_connections(definition, parameters):
    conns = {}
    conn_params = parameters.get("$connections", {}).get("value", {})
    for key, val in conn_params.items():
        api = val.get("id", "").split("/")[-1] if val.get("id") else key
        conns[key] = api
    return conns

def rg_from_id(resource_id):
    parts = resource_id.split("/")
    try:
        return parts[parts.index("resourceGroups") + 1]
    except (ValueError, IndexError):
        return ""

def fmt_dt(iso):
    if not iso:
        return ""
    try:
        return iso[:16].replace("T", " ")
    except Exception:
        return iso

def run_summary(runs):
    if not runs:
        return {"total": 0, "succeeded": 0, "failed": 0, "last_run": "", "last_status": ""}
    statuses = [r.get("properties", {}).get("status", "") for r in runs]
    last = runs[0].get("properties", {})
    return {
        "total":       len(runs),
        "succeeded":   statuses.count("Succeeded"),
        "failed":      statuses.count("Failed"),
        "last_run":    fmt_dt(last.get("startTime", "")),
        "last_status": last.get("status", ""),
    }

# ── collect ────────────────────────────────────────────────────────────────────

def collect(env_cfg):
    sub   = env_cfg["subscription_id"]
    rg    = env_cfg["resource_group"]
    token = get_token()

    print(f"  Listing Logic Apps in {rg or 'entire subscription'}…")
    raw = list_workflows(sub, rg, token)
    print(f"  Found {len(raw)} workflows")

    workflows  = []
    all_runs   = []
    conn_index = defaultdict(set)   # api_name → set of workflow names that use it

    for i, w in enumerate(raw, 1):
        name     = w.get("name", "")
        wid      = w.get("id", "")
        wrg      = rg_from_id(wid) or rg
        props    = w.get("properties", {})
        defn     = props.get("definition", {})
        params   = props.get("parameters", {})
        state    = props.get("state", "")
        location = w.get("location", "")
        tags     = w.get("tags", {})
        created  = fmt_dt(props.get("createdTime", ""))
        changed  = fmt_dt(props.get("changedTime", ""))

        trig_type, trig_detail = parse_trigger(defn)
        action_types           = parse_actions(defn)
        connections            = parse_connections(defn, params)

        for key, api in connections.items():
            conn_index[api].add(name)

        print(f"  [{i}/{len(raw)}] {name} — fetching run history…")
        runs = get_run_history(sub, wrg, name, token)
        rs   = run_summary(runs)

        for run in runs:
            rp = run.get("properties", {})
            all_runs.append({
                "workflow":    name,
                "rg":          wrg,
                "run_id":      run.get("name", ""),
                "status":      rp.get("status", ""),
                "start":       fmt_dt(rp.get("startTime", "")),
                "end":         fmt_dt(rp.get("endTime", "")),
                "trigger":     rp.get("trigger", {}).get("name", ""),
            })

        workflows.append({
            "name":         name,
            "rg":           wrg,
            "state":        state,
            "location":     location,
            "trigger_type": trig_type,
            "trigger_detail": trig_detail,
            "action_count": sum(action_types.values()),
            "action_types": action_types,
            "connections":  connections,
            "created":      created,
            "changed":      changed,
            "tags":         tags,
            **{f"run_{k}": v for k, v in rs.items()},
        })

    conn_list = [
        {"api": api, "count": len(wf_set), "workflows": sorted(wf_set)}
        for api, wf_set in sorted(conn_index.items(), key=lambda x: -len(x[1]))
    ]

    return workflows, all_runs, conn_list

# ── CSS ────────────────────────────────────────────────────────────────────────

CSS = """
:root{--bg:#0f1117;--sur:#1a1d27;--sur2:#252836;--brd:#2e3245;
  --txt:#e2e8f0;--mut:#8892a4;--acc:#6c8eff;--grn:#4ade80;
  --red:#f87171;--yel:#fbbf24;--pur:#c084fc;--cyn:#22d3ee;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font:13px/1.5 'Segoe UI',system-ui,sans-serif;overflow:hidden}
.layout{display:flex;height:100vh}

/* sidebar */
.sidebar{width:260px;min-width:160px;background:var(--sur);border-right:1px solid var(--brd);
  display:flex;flex-direction:column;overflow:hidden;flex-shrink:0}
.sb-hdr{padding:12px 14px;border-bottom:1px solid var(--brd);font-weight:700;font-size:13px}
.sb-hdr small{display:block;color:var(--mut);font-weight:400;font-size:11px;margin-top:2px}
.sb-search{padding:7px 10px;border-bottom:1px solid var(--brd)}
.sb-search input{width:100%;padding:5px 9px;background:var(--sur2);border:1px solid var(--brd);
  border-radius:6px;color:var(--txt);font-size:12px;outline:none}
.sb-search input:focus{border-color:var(--acc)}
.sb-body{overflow-y:auto;flex:1;padding:4px 0}
.sb-rg{padding:5px 12px 2px;font-size:10px;font-weight:700;color:var(--acc);
  text-transform:uppercase;letter-spacing:.5px;margin-top:4px}
.sb-item{padding:3px 12px 3px 18px;font-size:12px;cursor:pointer;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sb-item:hover{background:var(--sur2);color:var(--acc)}
.sb-item .st{font-size:9px;padding:1px 4px;border-radius:3px;margin-left:4px;vertical-align:middle}
.st-en{background:#1a3a2a;color:#4ade80}
.st-dis{background:#3a1a1a;color:#f87171}

/* main */
.main{flex:1;overflow:hidden;display:flex;flex-direction:column}
.main-hdr{padding:18px 26px 0;flex-shrink:0}
h1{font-size:20px;font-weight:700;margin-bottom:2px}
.sub{color:var(--mut);font-size:12px;margin-bottom:14px}

/* stat cards */
.stats{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.sc{background:var(--sur);border:1px solid var(--brd);border-radius:10px;
  padding:11px 15px;min-width:105px;cursor:pointer;transition:border-color .15s}
.sc:hover{border-color:var(--acc)}
.sc.active-card{border-color:var(--acc);background:var(--sur2)}
.sc-n{font-size:20px;font-weight:700;line-height:1}
.sc-l{font-size:10px;color:var(--mut);margin-top:3px}

/* tabs */
.tabs{display:flex;gap:2px;border-bottom:2px solid var(--brd);padding:0 26px;
  flex-shrink:0;flex-wrap:wrap}
.tab{padding:6px 13px;cursor:pointer;border-radius:6px 6px 0 0;font-size:12px;font-weight:600;
  color:var(--mut);border:1px solid transparent;border-bottom:none;
  margin-bottom:-2px;user-select:none;white-space:nowrap}
.tab:hover{color:var(--txt);background:var(--sur)}
.tab.active{background:var(--sur);border-color:var(--brd);border-bottom-color:var(--sur);color:var(--txt)}

/* content */
.content{flex:1;overflow-y:auto;padding:16px 26px}
.panel{display:none}.panel.active{display:block}

/* search */
.srch{margin-bottom:10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.srch input{padding:6px 11px;background:var(--sur);border:1px solid var(--brd);
  border-radius:6px;color:var(--txt);font-size:12px;width:100%;max-width:380px;outline:none}
.srch input:focus{border-color:var(--acc)}
.srch select{padding:6px 10px;background:var(--sur);border:1px solid var(--brd);
  border-radius:6px;color:var(--txt);font-size:12px;outline:none}

/* tables */
table{width:100%;border-collapse:collapse;font-size:12px}
th{background:var(--sur2);padding:6px 10px;text-align:left;font-weight:700;
  border-bottom:2px solid var(--brd);white-space:nowrap;position:sticky;top:0;z-index:1}
td{padding:4px 10px;border-bottom:1px solid var(--brd);vertical-align:middle}
tr:hover td{background:var(--sur)}
.hidden{display:none!important}
.mono{font-family:monospace;font-size:11px;color:var(--mut)}
.chip{display:inline-block;font-size:10px;padding:1px 6px;border-radius:3px;
  font-weight:700;white-space:nowrap}
.chip-en{background:#1a3a2a;color:#4ade80}
.chip-dis{background:#3a1a1a;color:#f87171}
.chip-sus{background:#3a2a1a;color:#fbbf24}
.chip-ok{background:#1a3a2a;color:#4ade80}
.chip-fail{background:#3a1a1a;color:#f87171}
.chip-run{background:#1e2a4a;color:#6c8eff}
.chip-other{background:#252836;color:#94a3b8}
.chip-trig{background:#252836;color:#c084fc;font-size:10px}
.mut{color:var(--mut);font-size:11px}

/* overview grid */
.ov-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:9px;margin-bottom:20px}
.ov-card{background:var(--sur);border:1px solid var(--brd);border-radius:8px;padding:12px 14px;cursor:pointer}
.ov-card:hover{border-color:var(--acc)}
.ov-card h3{font-size:12px;color:var(--acc);margin-bottom:6px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.ov-card .ct{font-size:11px;color:var(--mut);display:flex;flex-direction:column;gap:3px}
.ov-card .ct strong{color:var(--txt)}
h2{font-size:14px;font-weight:700;margin:14px 0 10px;padding-bottom:4px;border-bottom:1px solid var(--brd)}
"""

# ── JavaScript ─────────────────────────────────────────────────────────────────

JS = """
function escH(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

function showTab(id){
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.sc').forEach(c=>c.classList.remove('active-card'));
  document.getElementById('p-'+id).classList.add('active');
  const tab=document.getElementById('tab-'+id);
  if(tab) tab.classList.add('active');
  const card=document.getElementById('card-'+id);
  if(card) card.classList.add('active-card');
}

function ft(tid,q){
  q=(q||'').toLowerCase().trim();
  const sel=document.getElementById('state-filter');
  const stateQ=sel?(sel.value||''):'';
  document.querySelectorAll('#'+tid+' tbody tr').forEach(tr=>{
    const hay=tr.textContent.toLowerCase();
    const stateCell=tr.dataset.state||'';
    const textOk=!q||hay.includes(q);
    const stateOk=!stateQ||stateCell.toLowerCase()===stateQ.toLowerCase();
    tr.classList.toggle('hidden',!(textOk&&stateOk));
  });
}

function filterByRg(rg){
  showTab('workflows');
  document.getElementById('wf-q').value=rg;
  ft('wf-tbl',rg);
}

function filterByState(state){
  showTab('workflows');
  const sel=document.getElementById('state-filter');
  if(sel) sel.value=state;
  ft('wf-tbl', document.getElementById('wf-q').value);
}

function filterSB(q){
  q=q.toLowerCase();
  document.querySelectorAll('.sb-item').forEach(el=>{
    el.classList.toggle('hidden',!!q&&!el.textContent.toLowerCase().includes(q));
  });
}

function buildOverview(){
  const grid=document.getElementById('rg-grid');
  const rgs={};
  WF_DATA.forEach(w=>{
    if(!rgs[w.rg]) rgs[w.rg]={total:0,enabled:0,disabled:0,triggers:{}};
    rgs[w.rg].total++;
    if(w.state==='Enabled') rgs[w.rg].enabled++;
    else rgs[w.rg].disabled++;
    rgs[w.rg].triggers[w.trigger_type]=(rgs[w.rg].triggers[w.trigger_type]||0)+1;
  });
  Object.entries(rgs).sort((a,b)=>b[1].total-a[1].total).forEach(([rg,s])=>{
    const top=Object.entries(s.triggers).sort((a,b)=>b[1]-a[1]).slice(0,3)
      .map(([t,n])=>`<span class="chip chip-trig">${escH(t)}</span> ${n}`).join(' &nbsp; ');
    const c=document.createElement('div');
    c.className='ov-card';
    c.innerHTML=`<h3>📦 ${escH(rg)}</h3><div class="ct">
      <span><strong>${s.total}</strong> workflows</span>
      <span style="color:var(--grn)"><strong>${s.enabled}</strong> enabled</span>
      ${s.disabled?`<span style="color:var(--red)"><strong>${s.disabled}</strong> disabled</span>`:''}
      <span style="margin-top:4px">${top}</span></div>`;
    c.addEventListener('click',()=>filterByRg(rg));
    grid.appendChild(c);
  });
}

function buildTriggerGrid(){
  const grid=document.getElementById('trig-grid');
  const types={};
  WF_DATA.forEach(w=>{types[w.trigger_type]=(types[w.trigger_type]||0)+1;});
  Object.entries(types).sort((a,b)=>b[1]-a[1]).forEach(([t,n])=>{
    const c=document.createElement('div');
    c.className='ov-card';
    c.innerHTML=`<h3><span class="chip chip-trig">${escH(t)}</span></h3>
      <div class="ct"><span><strong>${n}</strong> workflows</span></div>`;
    grid.appendChild(c);
  });
}

document.addEventListener('DOMContentLoaded',()=>{
  buildOverview();
  buildTriggerGrid();
  showTab('overview');
});
"""

# ── HTML ───────────────────────────────────────────────────────────────────────

def state_chip(state):
    cls = {"Enabled": "chip-en", "Disabled": "chip-dis", "Suspended": "chip-sus"}.get(state, "chip-other")
    return f'<span class="chip {cls}">{state}</span>'

def status_chip(status):
    cls = {"Succeeded": "chip-ok", "Failed": "chip-fail", "Running": "chip-run"}.get(status, "chip-other")
    return f'<span class="chip {cls}">{status}</span>'

def esc(s):
    if s is None: return ""
    return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")

def build_html(workflows, all_runs, conn_list, env_cfg, generated):
    label = env_cfg["label"]
    rg    = env_cfg["resource_group"]

    n_total    = len(workflows)
    n_enabled  = sum(1 for w in workflows if w["state"] == "Enabled")
    n_disabled = sum(1 for w in workflows if w["state"] == "Disabled")
    n_conns    = len(conn_list)
    n_failed   = sum(1 for r in all_runs if r["status"] == "Failed")

    # ── sidebar ────────────────────────────────────────────────────────────────
    by_rg = defaultdict(list)
    for w in sorted(workflows, key=lambda x: x["name"]):
        by_rg[w["rg"]].append(w)

    sb_html = ""
    for rg_name in sorted(by_rg):
        sb_html += f'<div class="sb-rg">{esc(rg_name)}</div>'
        for w in by_rg[rg_name]:
            st_cls = "st-en" if w["state"] == "Enabled" else "st-dis"
            st_lbl = "EN" if w["state"] == "Enabled" else "DIS"
            sb_html += (
                f'<div class="sb-item" onclick="filterByRg(\'{esc(rg_name)}\')">'
                f'{esc(w["name"])}<span class="st {st_cls}">{st_lbl}</span></div>'
            )

    # ── workflow rows ──────────────────────────────────────────────────────────
    wf_rows = []
    for w in sorted(workflows, key=lambda x: x["name"]):
        act_types = ", ".join(f"{t}×{n}" for t, n in
                              sorted(w["action_types"].items(), key=lambda x: -x[1])[:4])
        last_st   = w.get("run_last_status", "")
        last_st_h = status_chip(last_st) if last_st else '<span class="mut">—</span>'
        conns_s   = ", ".join(w.get("connections", {}).values())
        wf_rows.append(
            f'<tr data-state="{esc(w["state"])}">'
            f'<td>{esc(w["name"])}</td>'
            f'<td class="mut">{esc(w["rg"])}</td>'
            f'<td>{state_chip(w["state"])}</td>'
            f'<td><span class="chip chip-trig">{esc(w["trigger_type"])}</span>'
            f'{"<br><span class=\'mut\'>"+esc(w["trigger_detail"])+"</span>" if w["trigger_detail"] else ""}</td>'
            f'<td style="text-align:center">{w["action_count"]}</td>'
            f'<td class="mut" style="font-size:11px">{esc(act_types)}</td>'
            f'<td class="mut">{esc(w.get("run_last_run",""))}</td>'
            f'<td>{last_st_h}</td>'
            f'<td style="text-align:center">{w.get("run_succeeded",0)}</td>'
            f'<td style="text-align:center;color:var(--red)">{w.get("run_failed",0)}</td>'
            f'<td class="mut" style="font-size:11px">{esc(conns_s)}</td>'
            f'</tr>'
        )

    # ── run history rows ───────────────────────────────────────────────────────
    run_rows = []
    for r in sorted(all_runs, key=lambda x: x["start"], reverse=True)[:500]:
        run_rows.append(
            f'<tr>'
            f'<td>{esc(r["workflow"])}</td>'
            f'<td class="mut">{esc(r["rg"])}</td>'
            f'<td>{status_chip(r["status"])}</td>'
            f'<td class="mut">{esc(r["start"])}</td>'
            f'<td class="mut">{esc(r["end"])}</td>'
            f'<td class="mut">{esc(r["trigger"])}</td>'
            f'</tr>'
        )

    # ── connection rows ────────────────────────────────────────────────────────
    conn_rows = []
    for c in conn_list:
        wf_list = ", ".join(c["workflows"][:10])
        if len(c["workflows"]) > 10:
            wf_list += f" +{len(c['workflows'])-10} more"
        conn_rows.append(
            f'<tr>'
            f'<td>{esc(c["api"])}</td>'
            f'<td style="text-align:center">{c["count"]}</td>'
            f'<td class="mut" style="font-size:11px">{esc(wf_list)}</td>'
            f'</tr>'
        )

    wf_json = json.dumps(
        [{"name": w["name"], "rg": w["rg"], "state": w["state"],
          "trigger_type": w["trigger_type"]} for w in workflows],
        ensure_ascii=False, separators=(',', ':')
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>Logic Apps — {label}</title>
<style>{CSS}.help-fab{{position:fixed;bottom:22px;right:22px;width:38px;height:38px;border-radius:50%;background:var(--acc);color:#fff;font-size:17px;font-weight:700;display:flex;align-items:center;justify-content:center;text-decoration:none;z-index:9999;box-shadow:0 2px 10px rgba(0,0,0,.5);opacity:.8;transition:opacity .15s;line-height:1}}.help-fab:hover{{opacity:1}}</style>
<script>const WF_DATA={wf_json};</script>
</head>
<body>
<a href="help.html" class="help-fab" title="Help &amp; Guide">?</a>
<div class="layout">

<!-- SIDEBAR -->
<div class="sidebar">
  <div class="sb-hdr">Logic Apps — {esc(label)}<small>{esc(rg)}</small></div>
  <div class="sb-search">
    <input placeholder="Filter workflows…" oninput="filterSB(this.value)"/>
  </div>
  <div class="sb-body">{sb_html}</div>
</div>

<!-- MAIN -->
<div class="main">
<div class="main-hdr">
  <h1>Azure Logic Apps — {esc(label)}</h1>
  <p class="sub">Resource Group: <strong>{esc(rg)}</strong> &nbsp;|&nbsp; Generated: <span id="gen-ts" data-ts="{esc(generated)}">&#x21BB; {esc(generated)}</span><script>(function(){{var s=document.getElementById(\'gen-ts\'),h=(Date.now()-new Date(s.dataset.ts.replace(\' \',\'T\')))/36e5;s.style.color=h<25?'var(--grn)':h<168?'var(--yel)':'var(--red)';s.style.fontWeight='700';}})();</script></p>

  <div class="stats">
    <div class="sc" id="card-overview"    onclick="showTab('overview')"          title="Total number of Logic App workflows (Standard or Consumption) across all resource groups in this environment. Each workflow is an independent integration process with its own trigger and actions.">
      <div class="sc-n">{n_total}</div><div class="sc-l">Total Workflows</div></div>
    <div class="sc" id="card-workflows"   onclick="showTab('workflows')"         title="Workflows currently in an Enabled state — actively listening for their trigger and ready to run. A trigger can be an HTTP request, a schedule (Recurrence), a Service Bus message, or a storage event.">
      <div class="sc-n" style="color:var(--grn)">{n_enabled}</div><div class="sc-l">Enabled</div></div>
    <div class="sc"                       onclick="filterByState('Disabled')"    title="Workflows that have been manually disabled. They will not fire even if their trigger condition is met. Common reasons: maintenance, decommissioning, or temporarily pausing an integration.">
      <div class="sc-n" style="color:var(--red)">{n_disabled}</div><div class="sc-l">Disabled</div></div>
    <div class="sc" id="card-runs"        onclick="showTab('runs')"              title="Number of workflow runs in a Failed status within the recent lookback window. A run fails when an action returns an error and no retry or error-handling branch catches it. Click to see the full run history.">
      <div class="sc-n" style="color:var(--red)">{n_failed}</div><div class="sc-l">Recent Failures</div></div>
    <div class="sc" id="card-connections" onclick="showTab('connections')"       title="API connectors used by these workflows to communicate with external services (Teams, Office 365, SQL, Service Bus, HTTP, etc.). Each connector wraps an external API and handles authentication separately from the workflow itself.">
      <div class="sc-n" style="color:var(--pur)">{n_conns}</div><div class="sc-l">Connectors</div></div>
  </div>
</div>

  <div class="tabs">
    <div class="tab" id="tab-overview"    onclick="showTab('overview')">Overview</div>
    <div class="tab" id="tab-workflows"   onclick="showTab('workflows')">Workflows ({n_total})</div>
    <div class="tab" id="tab-runs"        onclick="showTab('runs')">Run History</div>
    <div class="tab" id="tab-connections" onclick="showTab('connections')">Connectors ({n_conns})</div>
  </div>

  <div class="content">

    <!-- OVERVIEW -->
    <div class="panel" id="p-overview">
      <h2>By Resource Group</h2>
      <div class="ov-grid" id="rg-grid"></div>
      <h2>By Trigger Type</h2>
      <div class="ov-grid" id="trig-grid"></div>
    </div>

    <!-- WORKFLOWS -->
    <div class="panel" id="p-workflows">
      <div class="srch">
        <input id="wf-q" placeholder="Search name, resource group, trigger…"
               oninput="ft('wf-tbl',this.value)"/>
        <select id="state-filter" onchange="ft('wf-tbl',document.getElementById('wf-q').value)">
          <option value="">All States</option>
          <option value="Enabled">Enabled</option>
          <option value="Disabled">Disabled</option>
        </select>
      </div>
      <table id="wf-tbl">
        <thead><tr>
          <th>Name</th><th>Resource Group</th><th>State</th><th>Trigger</th>
          <th>Actions</th><th>Action Types</th><th>Last Run</th><th>Last Status</th>
          <th>✓</th><th>✗</th><th>Connections</th>
        </tr></thead>
        <tbody>{''.join(wf_rows)}</tbody>
      </table>
    </div>

    <!-- RUN HISTORY -->
    <div class="panel" id="p-runs">
      <div class="srch">
        <input placeholder="Search workflow or status…" oninput="ft('run-tbl',this.value)"/>
      </div>
      <p class="mut" style="margin-bottom:8px">Most recent {RUN_LIMIT} runs per workflow (up to 500 shown).</p>
      <table id="run-tbl">
        <thead><tr>
          <th>Workflow</th><th>Resource Group</th><th>Status</th>
          <th>Start</th><th>End</th><th>Trigger</th>
        </tr></thead>
        <tbody>{''.join(run_rows)}</tbody>
      </table>
    </div>

    <!-- CONNECTIONS -->
    <div class="panel" id="p-connections">
      <div class="srch">
        <input placeholder="Search connector…" oninput="ft('conn-tbl',this.value)"/>
      </div>
      <table id="conn-tbl">
        <thead><tr><th>Connector / API</th><th>Workflows Using It</th><th>Workflow Names</th></tr></thead>
        <tbody>{''.join(conn_rows) if conn_rows else '<tr><td colspan="3" style="color:var(--mut);padding:16px">No API connections found — workflows may use inline HTTP actions or built-in triggers only.</td></tr>'}</tbody>
      </table>
    </div>

  </div><!-- /content -->
</div><!-- /main -->
</div><!-- /layout -->
<script>{JS}</script>
</body>
</html>"""

# ── main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["dev", "prd"], required=True)
    args   = parser.parse_args()

    env_cfg = ENVIRONMENTS[args.env]
    label   = env_cfg["label"]

    print(f"\n=== Logic Apps Metadata — {label} ===")
    print("Getting access token…")

    workflows, all_runs, conn_list = collect(env_cfg)

    print(f"\n  Workflows  : {len(workflows)}")
    print(f"  Enabled    : {sum(1 for w in workflows if w['state']=='Enabled')}")
    print(f"  Disabled   : {sum(1 for w in workflows if w['state']=='Disabled')}")
    print(f"  Connectors : {len(conn_list)}")
    print(f"  Run records: {len(all_runs)}")

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    print("\nBuilding HTML…")
    html = build_html(workflows, all_runs, conn_list, env_cfg, generated)

    out = f"/home/thedavidporter/logic_apps_metadata_report_{args.env}.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved: {out}")


    if not os.environ.get('PUBLISH_RUNNING'):
        try:
            import generate_metadata_index
            generate_metadata_index.main()
            print("  Index updated       : index.html")
        except Exception as exc:
            print(f"  Warning: could not update index.html: {exc}")

if __name__ == "__main__":
    main()
