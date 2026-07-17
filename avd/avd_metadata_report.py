import os
#!/usr/bin/env python3
"""
Azure Virtual Desktop (AVD) Session Host Inventory Report
Queries all host pools in the ECAE Shared Production subscription and generates
an HTML report showing status, last heartbeat, and assigned user per machine.

Usage:
  python3 avd_metadata_report.py
"""

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

# ── config ─────────────────────────────────────────────────────────────────────

SUBSCRIPTION_ID = "5d3a4b9c-0e31-477c-9122-bb3be662e2a9"  # ECAE Shared Production
MGMT            = "https://management.azure.com"
AVD_API         = "2021-07-12"
OUT_FILE        = "/home/thedavidporter/avd_metadata_report.html"

# ── auth ───────────────────────────────────────────────────────────────────────

def get_token():
    r = subprocess.run(
        ["az", "account", "get-access-token",
         "--resource", "https://management.azure.com/",
         "--subscription", SUBSCRIPTION_ID,
         "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True, check=True,
    )
    return r.stdout.strip()

def hdrs(token):
    return {"Authorization": f"Bearer {token}"}

# ── REST helpers ───────────────────────────────────────────────────────────────

def get_all(url, token):
    results, params = [], {"api-version": AVD_API}
    while url:
        try:
            r = requests.get(url, headers=hdrs(token), params=params, timeout=30)
        except requests.exceptions.RequestException:
            break
        if r.status_code not in (200,):
            break
        data = r.json()
        results.extend(data.get("value", []))
        url    = data.get("nextLink")
        params = {}
    return results

# ── data collection ────────────────────────────────────────────────────────────

def rg_from_id(rid):
    parts = (rid or "").split("/")
    lower = [p.lower() for p in parts]
    try:
        return parts[lower.index("resourcegroups") + 1]
    except (ValueError, IndexError):
        return ""

def fetch_session_hosts(token, hp):
    hp_name = hp["name"]
    rg      = rg_from_id(hp.get("id", ""))
    url     = (f"{MGMT}/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{rg}"
               f"/providers/Microsoft.DesktopVirtualization/hostPools/{hp_name}"
               f"/sessionHosts")
    hosts   = get_all(url, token)
    return hp_name, rg, hosts

def parse_heartbeat(ts):
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt
    except Exception:
        return None

def days_ago(dt):
    if dt is None:
        return None
    now = datetime.now(timezone.utc)
    return (now - dt).days

def collect():
    print("  Fetching token…", end=" ", flush=True)
    token = get_token()
    print("ok")

    print("  Listing host pools…", end=" ", flush=True)
    url    = (f"{MGMT}/subscriptions/{SUBSCRIPTION_ID}/providers"
              f"/Microsoft.DesktopVirtualization/hostPools")
    hpools = get_all(url, token)
    print(f"{len(hpools)} host pools")

    print(f"  Fetching session hosts in parallel (max_workers=20)…", end=" ", flush=True)
    machines = []
    with ThreadPoolExecutor(max_workers=20) as ex:
        futures = {ex.submit(fetch_session_hosts, token, hp): hp for hp in hpools}
        for future in as_completed(futures):
            try:
                hp_name, rg, hosts = future.result()
                for h in hosts:
                    p    = h.get("properties", {})
                    hb   = parse_heartbeat(p.get("lastHeartBeat"))
                    name = h.get("name", "").split("/")[-1]  # strip "hostpool/hostname"
                    machines.append({
                        "name":          name,
                        "host_pool":     hp_name,
                        "resource_group": rg,
                        "status":        p.get("status", "Unknown"),
                        "last_heartbeat": p.get("lastHeartBeat", ""),
                        "days_ago":      days_ago(hb),
                        "sessions":      p.get("sessions", 0),
                        "assigned_user": p.get("assignedUser", ""),
                        "os_version":    p.get("osVersion", ""),
                        "agent_version": p.get("agentVersion", ""),
                    })
            except Exception as exc:
                print(f"\n    [WARN] {exc}", flush=True)

    machines.sort(key=lambda m: (m["days_ago"] is None, m["days_ago"] or 0), reverse=True)
    print(f"{len(machines)} session hosts")
    return hpools, machines

# ── HTML ───────────────────────────────────────────────────────────────────────

CSS = """
:root{
  --bg:#0f1117;--sur:#1a1d27;--sur2:#252836;--brd:#2e3245;
  --txt:#e2e8f0;--mut:#8892a4;--acc:#6c8eff;--grn:#4ade80;
  --red:#f87171;--yel:#fbbf24;--pur:#c084fc;--cyn:#22d3ee;--org:#fb923c;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;overflow:hidden}
body{background:var(--bg);color:var(--txt);font:13px/1.5 'Segoe UI',system-ui,sans-serif}
.layout{display:flex;height:100vh;overflow:hidden}

/* sidebar */
.sidebar{width:200px;background:var(--sur);border-right:1px solid var(--brd);
  display:flex;flex-direction:column;flex-shrink:0;overflow:hidden}
.sb-hdr{padding:14px 14px 10px;font-size:13px;font-weight:700;border-bottom:1px solid var(--brd)}
.sb-hdr small{display:block;font-size:10px;color:var(--mut);font-weight:400;margin-top:2px}
.sb-body{padding:8px 0;overflow-y:auto}
.sb-section{font-size:10px;font-weight:700;color:var(--mut);padding:10px 14px 4px;
  text-transform:uppercase;letter-spacing:.5px}
.sb-stat{padding:6px 14px;font-size:12px;display:flex;justify-content:space-between;align-items:center}
.sb-stat span{color:var(--mut)}
.sb-stat b{color:var(--txt)}

/* main */
.main{flex:1;display:flex;flex-direction:column;overflow:hidden}
.main-hdr{padding:14px 24px 10px;border-bottom:1px solid var(--brd);flex-shrink:0}
.main-hdr h1{font-size:17px;font-weight:800}
.sub{font-size:11px;color:var(--mut);margin-top:2px}

/* stats */
.stats{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.sc{background:var(--sur);border:1px solid var(--brd);border-radius:8px;
  padding:8px 14px;cursor:pointer;min-width:80px;text-align:center;transition:border-color .15s}
.sc:hover{border-color:var(--acc)}
.sc.active{border-color:var(--acc);background:var(--sur2)}
.sc-n{font-size:18px;font-weight:800}
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

/* filter row */
.filter-row{display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap}
.filter-row input,.filter-row select{
  background:var(--sur);border:1px solid var(--brd);border-radius:6px;
  color:var(--txt);padding:5px 10px;font-size:12px;outline:none}
.filter-row input{flex:1;min-width:180px}
.filter-row input:focus,.filter-row select:focus{border-color:var(--acc)}
.mut{color:var(--mut)}

/* table */
table{width:100%;border-collapse:collapse;font-size:12px}
th{background:var(--sur);padding:7px 10px;text-align:left;font-size:10px;
  font-weight:700;text-transform:uppercase;letter-spacing:.4px;color:var(--mut);
  border-bottom:2px solid var(--brd);position:sticky;top:0;z-index:1;cursor:pointer;user-select:none}
th:hover{color:var(--txt)}
th .sort-arrow{font-size:9px;margin-left:4px;opacity:.5}
td{padding:6px 10px;border-bottom:1px solid var(--brd);vertical-align:top}
tr:hover td{background:var(--sur)}
.mono{font-family:'Cascadia Code','Fira Code',monospace;font-size:11px}
h2{font-size:13px;font-weight:700;margin:14px 0 8px}

/* chips */
.chip{display:inline-block;font-size:10px;padding:2px 7px;border-radius:3px;font-weight:700;white-space:nowrap}
.st-available   {background:#1a3a2a;color:#4ade80}
.st-unavailable {background:var(--sur2);color:var(--mut)}
.st-needs       {background:#2a1a0a;color:#fb923c}
.st-shutdown    {background:var(--sur2);color:var(--mut)}
.st-upgrading   {background:#1e2a4a;color:#6c8eff}
.st-unknown     {background:var(--sur2);color:var(--mut)}

/* staleness chips */
.age-fresh {background:#1a3a2a;color:#4ade80}
.age-recent{background:#2a2a0a;color:#fbbf24}
.age-stale {background:#3a1a1a;color:#f87171}
.age-old   {background:#3a1a1a;color:#f87171}

/* sessions chip */
.chip-sessions{background:#1e2a4a;color:#6c8eff}
.chip-nosess  {background:var(--sur2);color:var(--mut)}

/* host pool groups */
.hp-group{background:var(--sur);border:1px solid var(--brd);border-radius:6px;margin-bottom:8px}
.hp-group-hdr{padding:9px 14px;font-weight:700;font-size:12px;cursor:pointer;
  display:flex;align-items:center;justify-content:space-between;user-select:none}
.hp-group-hdr:hover{background:var(--sur2);border-radius:6px}
.hp-group-body{display:none;border-top:1px solid var(--brd)}
.hp-group-body.open{display:block}

/* overview cards */
.ov-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px;margin-bottom:18px}
.ov-card{background:var(--sur);border:1px solid var(--brd);border-radius:8px;padding:12px 14px}
.ov-card h3{font-size:11px;font-weight:700;margin-bottom:8px;color:var(--mut);text-transform:uppercase;letter-spacing:.4px}
.ov-card .row{display:flex;justify-content:space-between;font-size:12px;padding:2px 0}
.ov-card .row b{color:var(--txt)}

/* staleness bar */
.bar-wrap{background:var(--sur2);border-radius:4px;height:8px;margin-top:4px;overflow:hidden}
.bar-fill{height:100%;border-radius:4px;transition:width .3s}
"""

JS = r"""
const MACHINES = __MACHINES__;
const HOSTPOOLS = __HOSTPOOLS__;
const GENERATED = "__GENERATED__";

function esc(s){
  if(s==null||s===undefined) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function statusChip(s){
  const sl = (s||'').toLowerCase();
  let cls = 'st-unknown';
  if(sl==='available')          cls='st-available';
  else if(sl==='unavailable')   cls='st-unavailable';
  else if(sl==='needsassistance') cls='st-needs';
  else if(sl==='shutdown')      cls='st-shutdown';
  else if(sl.includes('upgrad')) cls='st-upgrading';
  return `<span class="chip ${cls}">${esc(s||'Unknown')}</span>`;
}

function ageChip(days){
  if(days===null||days===undefined) return '<span class="chip age-old">Never</span>';
  let cls='age-fresh', label=days+'d ago';
  if(days>90)      cls='age-old';
  else if(days>30) cls='age-stale';
  else if(days>7)  cls='age-recent';
  if(days===0) label='Today';
  return `<span class="chip ${cls}">${esc(label)}</span>`;
}

function sessChip(n){
  if(!n) return '<span class="chip chip-nosess">0</span>';
  return `<span class="chip chip-sessions">${n}</span>`;
}

function fmtDate(ts){
  if(!ts) return '<span class="mut">—</span>';
  try{
    const d = new Date(ts);
    return d.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'});
  }catch(e){return esc(ts)}
}

// ── tabs ────────────────────────────────────────────────────────────────────────
function showTab(name){
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.getElementById('tab-'+name)?.classList.add('active');
  const panel = document.getElementById('p-'+name);
  if(panel){
    panel.classList.add('active');
    renderPanel(name);
  }
}

// ── render ──────────────────────────────────────────────────────────────────────
let sortCol = 'days_ago', sortDir = -1;

function sortedMachines(list){
  return [...list].sort((a,b)=>{
    let av = a[sortCol], bv = b[sortCol];
    if(av===null||av===undefined) av = sortDir>0 ? -Infinity : Infinity;
    if(bv===null||bv===undefined) bv = sortDir>0 ? -Infinity : Infinity;
    if(typeof av === 'string') av = av.toLowerCase();
    if(typeof bv === 'string') bv = bv.toLowerCase();
    if(av < bv) return sortDir;
    if(av > bv) return -sortDir;
    return 0;
  });
}

function machineRow(m){
  const shortName = m.name.split('.')[0];
  return `<tr>
    <td class="mono">${esc(shortName)}</td>
    <td class="mono" style="font-size:11px;color:var(--mut)">${esc(m.host_pool)}</td>
    <td>${statusChip(m.status)}</td>
    <td>${ageChip(m.days_ago)}</td>
    <td style="font-size:11px;color:var(--mut)">${fmtDate(m.last_heartbeat)}</td>
    <td>${sessChip(m.sessions)}</td>
    <td style="font-size:11px">${esc(m.assigned_user)||'<span class="mut">—</span>'}</td>
    <td style="font-size:11px;color:var(--mut)">${esc(m.os_version)||'<span class="mut">—</span>'}</td>
  </tr>`;
}

function renderMachineTable(tbodyId, countId, searchId, statusId, list){
  const search  = (document.getElementById(searchId)?.value||'').toLowerCase();
  const statusF = document.getElementById(statusId)?.value||'';
  let filtered = sortedMachines(list).filter(m=>{
    if(statusF && m.status!==statusF) return false;
    if(search){
      const hay = [m.name, m.host_pool, m.assigned_user, m.status].join(' ').toLowerCase();
      if(!hay.includes(search)) return false;
    }
    return true;
  });
  document.getElementById(tbodyId).innerHTML = filtered.map(machineRow).join('');
  const el = document.getElementById(countId);
  if(el) el.textContent = filtered.length + ' of ' + list.length;
}

function setSort(col){
  if(sortCol===col){ sortDir*=-1; } else { sortCol=col; sortDir=-1; }
  renderPanel(document.querySelector('.panel.active')?.id?.replace('p-',''));
}

function renderPanel(name){
  if(!name) return;
  if(name==='all')    renderMachineTable('all-tbody','all-count','all-search','all-status', MACHINES);
  if(name==='stale')  renderMachineTable('stale-tbody','stale-count','stale-search','stale-status', MACHINES.filter(m=>m.days_ago===null||m.days_ago>90));
  if(name==='active') renderMachineTable('act-tbody','act-count','act-search','act-status', MACHINES.filter(m=>m.sessions>0));
  if(name==='pools')  renderHostPools();
  if(name==='overview') renderOverview();
}

function renderOverview(){
  const total   = MACHINES.length;
  const active  = MACHINES.filter(m=>m.days_ago!==null&&m.days_ago<=30).length;
  const stale   = MACHINES.filter(m=>m.days_ago===null||m.days_ago>90).length;
  const sesAct  = MACHINES.filter(m=>m.sessions>0).length;
  const needs   = MACHINES.filter(m=>m.status==='NeedsAssistance').length;
  const assigned= MACHINES.filter(m=>m.assigned_user).length;

  // status breakdown
  const byStatus={};
  MACHINES.forEach(m=>{ byStatus[m.status]=(byStatus[m.status]||0)+1; });
  document.getElementById('ov-status').innerHTML =
    Object.entries(byStatus).sort((a,b)=>b[1]-a[1]).map(([s,n])=>
      `<div class="row"><span>${statusChip(s)}</span><b>${n}</b></div>`
    ).join('');

  // staleness breakdown
  const b30  = MACHINES.filter(m=>m.days_ago!==null&&m.days_ago<=30).length;
  const b90  = MACHINES.filter(m=>m.days_ago!==null&&m.days_ago>30&&m.days_ago<=90).length;
  const b90p = MACHINES.filter(m=>m.days_ago===null||m.days_ago>90).length;
  document.getElementById('ov-staleness').innerHTML = `
    <div class="row"><span><span class="chip age-fresh">Active (≤30d)</span></span><b>${b30}</b></div>
    <div class="row"><span><span class="chip age-recent">Recent (31–90d)</span></span><b>${b90}</b></div>
    <div class="row"><span><span class="chip age-old">Stale (90d+)</span></span><b>${b90p}</b></div>
    <div class="bar-wrap" style="margin-top:8px">
      <div class="bar-fill" style="width:${Math.round(b30/total*100)}%;background:var(--grn)"></div>
    </div>`;

  // sessions
  document.getElementById('ov-sessions').innerHTML = `
    <div class="row"><span>With active sessions</span><b>${sesAct}</b></div>
    <div class="row"><span>No active sessions</span><b>${total-sesAct}</b></div>
    <div class="row"><span>Total sessions</span><b>${MACHINES.reduce((a,m)=>a+m.sessions,0)}</b></div>`;

  // host pools
  document.getElementById('ov-pools').innerHTML = `
    <div class="row"><span>Host pools</span><b>${HOSTPOOLS}</b></div>
    <div class="row"><span>Session hosts</span><b>${total}</b></div>
    <div class="row"><span>With assigned user</span><b>${assigned}</b></div>`;

  // stalest machines
  const stalest = [...MACHINES].filter(m=>m.days_ago===null||m.days_ago>180)
    .sort((a,b)=>(b.days_ago||99999)-(a.days_ago||99999)).slice(0,5);
  document.getElementById('ov-stalest').innerHTML = stalest.length ? stalest.map(m=>`
    <div style="display:flex;align-items:center;gap:6px;padding:3px 0;border-bottom:1px solid var(--brd)">
      <span class="mono" style="flex:1;font-size:11px">${esc(m.name.split('.')[0])}</span>
      ${ageChip(m.days_ago)}
    </div>`).join('') : '<span class="mut" style="font-size:12px">None found</span>';
}

function renderHostPools(){
  const byPool = {};
  MACHINES.forEach(m=>{
    if(!byPool[m.host_pool]) byPool[m.host_pool] = [];
    byPool[m.host_pool].push(m);
  });
  const search = (document.getElementById('pools-search')?.value||'').toLowerCase();
  let html = '';
  Object.entries(byPool).sort((a,b)=>a[0].localeCompare(b[0])).forEach(([hp, list])=>{
    const filtered = list.filter(m=>{
      if(!search) return true;
      return [m.name,m.assigned_user,m.status].join(' ').toLowerCase().includes(search);
    });
    if(!filtered.length) return;
    const active = filtered.filter(m=>m.days_ago!==null&&m.days_ago<=30).length;
    const stale  = filtered.filter(m=>m.days_ago===null||m.days_ago>90).length;
    html += `
      <div class="hp-group">
        <div class="hp-group-hdr" onclick="toggleHP(this)">
          <span class="mono">${esc(hp)}</span>
          <span style="display:flex;gap:8px;font-size:11px">
            <span style="color:var(--grn)">${active} active</span>
            ${stale?`<span style="color:var(--red)">${stale} stale</span>`:''}
            <span class="mut">${filtered.length} hosts</span>
            <span class="mut">▶</span>
          </span>
        </div>
        <div class="hp-group-body">
          <table>
            <thead><tr><th>Machine</th><th>Status</th><th>Last Active</th><th>Date</th><th>Sessions</th><th>Assigned User</th></tr></thead>
            <tbody>
              ${filtered.map(m=>`<tr>
                <td class="mono" style="font-size:11px">${esc(m.name.split('.')[0])}</td>
                <td>${statusChip(m.status)}</td>
                <td>${ageChip(m.days_ago)}</td>
                <td style="font-size:11px;color:var(--mut)">${fmtDate(m.last_heartbeat)}</td>
                <td>${sessChip(m.sessions)}</td>
                <td style="font-size:11px">${esc(m.assigned_user)||'<span class="mut">—</span>'}</td>
              </tr>`).join('')}
            </tbody>
          </table>
        </div>
      </div>`;
  });
  document.getElementById('pools-container').innerHTML = html || '<span class="mut">No results</span>';
}

function toggleHP(hdr){
  const body = hdr.nextElementSibling;
  const arrow = hdr.querySelector('.mut:last-child');
  body.classList.toggle('open');
  if(arrow) arrow.textContent = body.classList.contains('open') ? '▼' : '▶';
}

// ── init ────────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded',()=>{
  const genEl = document.getElementById('gen-time');
  genEl.textContent = '↻ ' + GENERATED;
  const h = (Date.now() - new Date(GENERATED.replace(' ','T'))) / 36e5;
  genEl.style.color = h < 25 ? 'var(--grn)' : h < 168 ? 'var(--yel)' : 'var(--red)';
  genEl.style.fontWeight = '700';
  document.getElementById('stat-total').textContent   = MACHINES.length;
  document.getElementById('stat-active').textContent  = MACHINES.filter(m=>m.days_ago!==null&&m.days_ago<=30).length;
  document.getElementById('stat-stale').textContent   = MACHINES.filter(m=>m.days_ago===null||m.days_ago>90).length;
  document.getElementById('stat-sessions').textContent= MACHINES.filter(m=>m.sessions>0).length;
  document.getElementById('stat-needs').textContent   = MACHINES.filter(m=>m.status==='NeedsAssistance').length;
  showTab('overview');
});
"""

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AVD Session Host Inventory</title>
<style>{css}</style>
</head>
<body>
<div class="layout">

  <!-- sidebar -->
  <div class="sidebar">
    <div class="sb-hdr">
      AVD Inventory
      <small>ECAE Shared Production</small>
    </div>
    <div class="sb-body">
      <div class="sb-section">Summary</div>
      <div class="sb-stat"><span>Total Machines</span><b id="sb-total">—</b></div>
      <div class="sb-stat"><span>Host Pools</span><b>{hp_count}</b></div>
      <div class="sb-stat"><span>Active ≤30d</span><b id="sb-active" style="color:var(--grn)">—</b></div>
      <div class="sb-stat"><span>Stale 90d+</span><b id="sb-stale" style="color:var(--red)">—</b></div>
      <div class="sb-stat"><span>Active Sessions</span><b id="sb-sessions" style="color:var(--acc)">—</b></div>
      <div class="sb-stat"><span>Needs Assistance</span><b id="sb-needs" style="color:var(--org)">—</b></div>
    </div>
  </div>

  <!-- main -->
  <div class="main">
    <div class="main-hdr">
      <h1>Azure Virtual Desktop — Session Host Inventory</h1>
      <div class="sub">ECAE Shared Production &nbsp;·&nbsp; Subscription: 5d3a4b9c-0e31-477c-9122-bb3be662e2a9 &nbsp;·&nbsp; Generated: <span id="gen-time"></span></div>
      <div class="stats">
        <div class="sc" onclick="showTab('all')">
          <div class="sc-n" id="stat-total">—</div><div class="sc-l">Total</div></div>
        <div class="sc" onclick="showTab('all')">
          <div class="sc-n" id="stat-active" style="color:var(--grn)">—</div><div class="sc-l">Active ≤30d</div></div>
        <div class="sc" onclick="showTab('stale')">
          <div class="sc-n" id="stat-stale" style="color:var(--red)">—</div><div class="sc-l">Stale 90d+</div></div>
        <div class="sc" onclick="showTab('active')">
          <div class="sc-n" id="stat-sessions" style="color:var(--acc)">—</div><div class="sc-l">Active Sessions</div></div>
        <div class="sc" onclick="showTab('all')">
          <div class="sc-n" id="stat-needs" style="color:var(--org)">—</div><div class="sc-l">Needs Assist.</div></div>
      </div>
    </div>

    <div class="tabs">
      <div class="tab" id="tab-overview" onclick="showTab('overview')">Overview</div>
      <div class="tab" id="tab-all"      onclick="showTab('all')">All Machines</div>
      <div class="tab" id="tab-stale"    onclick="showTab('stale')">Stale (90d+)</div>
      <div class="tab" id="tab-active"   onclick="showTab('active')">Active Sessions</div>
      <div class="tab" id="tab-pools"    onclick="showTab('pools')">By Host Pool</div>
    </div>

    <div class="content">

      <!-- OVERVIEW -->
      <div class="panel" id="p-overview">
        <div class="ov-grid">
          <div class="ov-card">
            <h3>Status Breakdown</h3>
            <div id="ov-status"></div>
          </div>
          <div class="ov-card">
            <h3>Staleness</h3>
            <div id="ov-staleness"></div>
          </div>
          <div class="ov-card">
            <h3>Sessions</h3>
            <div id="ov-sessions"></div>
          </div>
          <div class="ov-card">
            <h3>Infrastructure</h3>
            <div id="ov-pools"></div>
          </div>
        </div>
        <h2>Stalest Machines (180d+)</h2>
        <div id="ov-stalest"></div>
      </div>

      <!-- ALL MACHINES -->
      <div class="panel" id="p-all">
        <div class="filter-row">
          <input id="all-search" placeholder="Search name, host pool, user…" oninput="renderPanel('all')"/>
          <select id="all-status" onchange="renderPanel('all')">
            <option value="">All statuses</option>
            <option>Available</option>
            <option>Unavailable</option>
            <option>NeedsAssistance</option>
            <option>Shutdown</option>
          </select>
          <span id="all-count" class="mut"></span>
        </div>
        <table>
          <thead><tr>
            <th onclick="setSort('name')">Machine <span class="sort-arrow">⇅</span></th>
            <th onclick="setSort('host_pool')">Host Pool <span class="sort-arrow">⇅</span></th>
            <th onclick="setSort('status')">Status <span class="sort-arrow">⇅</span></th>
            <th onclick="setSort('days_ago')">Last Active <span class="sort-arrow">⇅</span></th>
            <th>Date</th>
            <th onclick="setSort('sessions')">Sessions <span class="sort-arrow">⇅</span></th>
            <th onclick="setSort('assigned_user')">Assigned User <span class="sort-arrow">⇅</span></th>
            <th>OS Version</th>
          </tr></thead>
          <tbody id="all-tbody"></tbody>
        </table>
      </div>

      <!-- STALE -->
      <div class="panel" id="p-stale">
        <div class="filter-row">
          <input id="stale-search" placeholder="Search name, host pool, user…" oninput="renderPanel('stale')"/>
          <select id="stale-status" onchange="renderPanel('stale')">
            <option value="">All statuses</option>
            <option>Available</option>
            <option>Unavailable</option>
            <option>NeedsAssistance</option>
            <option>Shutdown</option>
          </select>
          <span id="stale-count" class="mut"></span>
        </div>
        <table>
          <thead><tr>
            <th onclick="setSort('name')">Machine <span class="sort-arrow">⇅</span></th>
            <th onclick="setSort('host_pool')">Host Pool <span class="sort-arrow">⇅</span></th>
            <th onclick="setSort('status')">Status <span class="sort-arrow">⇅</span></th>
            <th onclick="setSort('days_ago')">Last Active <span class="sort-arrow">⇅</span></th>
            <th>Date</th>
            <th onclick="setSort('sessions')">Sessions <span class="sort-arrow">⇅</span></th>
            <th onclick="setSort('assigned_user')">Assigned User <span class="sort-arrow">⇅</span></th>
            <th>OS Version</th>
          </tr></thead>
          <tbody id="stale-tbody"></tbody>
        </table>
      </div>

      <!-- ACTIVE SESSIONS -->
      <div class="panel" id="p-active">
        <div class="filter-row">
          <input id="act-search" placeholder="Search name, host pool, user…" oninput="renderPanel('active')"/>
          <select id="act-status" onchange="renderPanel('active')">
            <option value="">All statuses</option>
            <option>Available</option>
            <option>Unavailable</option>
            <option>NeedsAssistance</option>
          </select>
          <span id="act-count" class="mut"></span>
        </div>
        <table>
          <thead><tr>
            <th onclick="setSort('name')">Machine <span class="sort-arrow">⇅</span></th>
            <th onclick="setSort('host_pool')">Host Pool <span class="sort-arrow">⇅</span></th>
            <th onclick="setSort('status')">Status <span class="sort-arrow">⇅</span></th>
            <th onclick="setSort('days_ago')">Last Active <span class="sort-arrow">⇅</span></th>
            <th>Date</th>
            <th onclick="setSort('sessions')">Sessions <span class="sort-arrow">⇅</span></th>
            <th onclick="setSort('assigned_user')">Assigned User <span class="sort-arrow">⇅</span></th>
            <th>OS Version</th>
          </tr></thead>
          <tbody id="act-tbody"></tbody>
        </table>
      </div>

      <!-- BY HOST POOL -->
      <div class="panel" id="p-pools">
        <div class="filter-row">
          <input id="pools-search" placeholder="Search name, user…" oninput="renderPanel('pools')"/>
        </div>
        <div id="pools-container"></div>
      </div>

    </div>
  </div>
</div>
<script>{js}</script>
</body>
</html>"""


def build_html(hpools, machines, generated):
    machines_json = json.dumps(machines, default=str)
    js = (JS
          .replace("__MACHINES__", machines_json)
          .replace("__HOSTPOOLS__", str(len(hpools)))
          .replace("__GENERATED__", generated))

    # wire sidebar stats via JS init
    js += """
document.addEventListener('DOMContentLoaded',()=>{
  document.getElementById('sb-total').textContent   = MACHINES.length;
  document.getElementById('sb-active').textContent  = MACHINES.filter(m=>m.days_ago!==null&&m.days_ago<=30).length;
  document.getElementById('sb-stale').textContent   = MACHINES.filter(m=>m.days_ago===null||m.days_ago>90).length;
  document.getElementById('sb-sessions').textContent= MACHINES.filter(m=>m.sessions>0).length;
  document.getElementById('sb-needs').textContent   = MACHINES.filter(m=>m.status==='NeedsAssistance').length;
});
"""

    return HTML_TEMPLATE.format(
        css=CSS,
        js=js,
        hp_count=len(hpools),
    )


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    print("\n=== AVD Session Host Inventory ===")
    hpools, machines = collect()

    print("\nBuilding HTML…")
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    html      = build_html(hpools, machines, generated)

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved: {OUT_FILE}")

    active = sum(1 for m in machines if m["days_ago"] is not None and m["days_ago"] <= 30)
    stale  = sum(1 for m in machines if m["days_ago"] is None or m["days_ago"] > 90)
    print(f"  {len(hpools)} host pools  |  {len(machines)} session hosts  |  "
          f"{active} active (≤30d)  |  {stale} stale (90d+)")

    if not os.environ.get('PUBLISH_RUNNING'):
        try:
            import generate_metadata_index
            generate_metadata_index.main()
            print("  Index updated: index.html")
        except Exception as exc:
            print(f"  Warning: could not update index.html: {exc}")



if __name__ == "__main__":
    main()
