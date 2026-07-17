#!/usr/bin/env python3
"""
Generates index.html — a central landing page linking to all metadata reports.
Add to publish script: run this first so the index is always current.
"""

import os
import sys
from datetime import datetime

OUT_FILE    = "/home/thedavidporter/index.html"
BASE_URL    = "https://zus1idohdevv2dbrkdl.z13.web.core.windows.net"
REPORTS_DIR = "/home/thedavidporter"

REPORTS = [
    {
        "category": "Data Catalog",
        "icon": "🗂️",
        "items": [
            {
                "title": "IDOH Data Catalog",
                "env": "ALL",
                "desc": "Browse available datasets by domain, view stewardship and refresh cadence, track requested datasets, and submit new data requests.",
                "url":  "data_catalog.html",
            },
        ],
    },
    {
        "category": "Azure Synapse Analytics",
        "icon": "🧱",
        "items": [
            {
                "title": "Synapse Metadata",
                "env": "DEV",
                "desc": "Schemas, tables, views, stored procedures, foreign keys, dependencies, and columns.",
                "url":  "synapse_metadata_report_dev.html",
            },
            {
                "title": "Synapse Metadata",
                "env": "PRD",
                "desc": "Schemas, tables, views, stored procedures, foreign keys, dependencies, and columns.",
                "url":  "synapse_metadata_report_prd.html",
            },
            {
                "title": "Synapse Delta",
                "env": "DEV",
                "desc": "Day-over-day changes to objects, columns, and row counts.",
                "url":  "synapse_metadata_delta_dev.html",
            },
            {
                "title": "Synapse Delta",
                "env": "PRD",
                "desc": "Day-over-day changes to objects, columns, and row counts.",
                "url":  "synapse_metadata_delta_prd.html",
            },
        ],
    },
    {
        "category": "Azure Data Factory",
        "icon": "🔁",
        "items": [
            {
                "title": "ADF Metadata",
                "env": "DEV",
                "desc": "Pipelines, activities, datasets, linked services, triggers, data flows, and integration runtimes.",
                "url":  "adf_metadata_report_dev.html",
            },
            {
                "title": "ADF Metadata",
                "env": "PRD",
                "desc": "Pipelines, activities, datasets, linked services, triggers, data flows, and integration runtimes.",
                "url":  "adf_metadata_report_prd.html",
            },
        ],
    },
    {
        "category": "Azure Data Lake Storage Gen2",
        "icon": "🗄️",
        "items": [
            {
                "title": "ADLS Gen2 Metadata",
                "env": "ALL",
                "desc": "All HNS-enabled storage accounts, filesystems, directory trees, and storage sizes.",
                "url":  "adls_metadata_report.html",
            },
        ],
    },
    {
        "category": "Azure Logic Apps",
        "icon": "⚡",
        "items": [
            {
                "title": "Logic Apps Metadata",
                "env": "DEV",
                "desc": "Workflows, trigger types, action counts, API connections, and recent run history.",
                "url":  "logic_apps_metadata_report_dev.html",
            },
            {
                "title": "Logic Apps Metadata",
                "env": "PRD",
                "desc": "Workflows, trigger types, action counts, API connections, and recent run history.",
                "url":  "logic_apps_metadata_report_prd.html",
            },
        ],
    },
    {
        "category": "Azure Databricks",
        "icon": "⚙️",
        "items": [
            {
                "title": "Databricks Metadata",
                "env": "ALL",
                "desc": "All 3 workspaces (IZ-DEV, DEV, PRD) — clusters, jobs, repos, SQL warehouses, cluster policies, and secret scope names.",
                "url":  "databricks_metadata_report.html",
            },
        ],
    },
    {
        "category": "Azure SQL Data Warehouse",
        "icon": "🏛️",
        "items": [
            {
                "title": "SQL DW Metadata",
                "env": "DEV",
                "desc": "Schemas (SM/DM/Reporting layers), tables with distribution types and index types, views, stored procedures, and columns.",
                "url":  "sql_dw_metadata_report_dev.html",
            },
            {
                "title": "SQL DW Metadata",
                "env": "PRD",
                "desc": "Schemas (SM/DM/Reporting layers), tables with distribution types and index types, views, stored procedures, and columns.",
                "url":  "sql_dw_metadata_report_prd.html",
            },
        ],
    },
    {
        "category": "Azure Networking",
        "icon": "🌐",
        "items": [
            {
                "title": "VNet Metadata",
                "env": "ALL",
                "desc": "VNets, subnets, NSG rules, private endpoints, VNet peerings, and data-exfil risk indicators.",
                "url":  "vnet_metadata_report.html",
            },
        ],
    },
    {
        "category": "Azure Virtual Desktop",
        "icon": "🖥️",
        "items": [
            {
                "title": "AVD Session Host Inventory",
                "env": "ALL",
                "desc": "All 142 host pools in ECAE Shared Production — session host status, last heartbeat, active sessions, assigned users, and stale machine identification.",
                "url":  "avd_metadata_report.html",
            },
        ],
    },
    {
        "category": "Azure Security & Access",
        "icon": "🔐",
        "items": [
            {
                "title": "Security Groups & Access",
                "env": "ALL",
                "desc": "All Entra ID security groups with Azure role assignments across every subscription — group members, roles held, and which Synapse, Databricks, ADF, Key Vault, and storage resources each group can access.",
                "url":  "azure_security_groups_report.html",
            },
        ],
    },
    {
        "category": "Azure DevOps",
        "icon": "🔧",
        "items": [
            {
                "title": "DevOps Metadata",
                "env": "ALL",
                "desc": "Repos, branches (active/stale/ahead-behind), build pipelines, run history, pull requests, branch policies, and deployment environments.",
                "url":  "ado_metadata_report.html",
            },
        ],
    },
    {
        "category": "Azure Key Vault",
        "icon": "🔑",
        "items": [
            {
                "title": "Key Vault Metadata",
                "env": "DEV",
                "desc": "Secrets, keys, and certificates (names and metadata only — no values). Access policies and expiry status.",
                "url":  "keyvault_metadata_report_dev.html",
            },
            {
                "title": "Key Vault Metadata",
                "env": "PRD",
                "desc": "Secrets, keys, and certificates (names and metadata only — no values). Access policies and expiry status.",
                "url":  "keyvault_metadata_report_prd.html",
            },
        ],
    },
]

ENV_COLORS = {
    "DEV":  ("var(--acc)", "#1e2a4a"),
    "PRD":  ("var(--grn)", "#1a3a2a"),
    "ALL":  ("var(--pur)", "#2d1e5f"),
}

CSS = """
:root{--bg:#0f1117;--sur:#1a1d27;--sur2:#252836;--brd:#2e3245;
  --txt:#e2e8f0;--mut:#8892a4;--acc:#6c8eff;--grn:#4ade80;
  --red:#f87171;--yel:#fbbf24;--pur:#c084fc;--cyn:#22d3ee;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font:14px/1.6 'Segoe UI',system-ui,sans-serif;
  min-height:100vh;padding:40px 24px 60px}
.page{max-width:900px;margin:0 auto}
.hero{margin-bottom:40px;text-align:center}
.hero h1{font-size:28px;font-weight:800;margin-bottom:6px}
.hero p{color:var(--mut);font-size:13px}
.hero .badge{display:inline-block;margin-top:10px;font-size:11px;
  padding:3px 10px;border-radius:20px;background:var(--sur);border:1px solid var(--brd);color:var(--mut)}
.section{margin-bottom:36px}
.section-hdr{display:flex;align-items:center;gap:10px;margin-bottom:14px;
  padding-bottom:8px;border-bottom:1px solid var(--brd)}
.section-hdr h2{font-size:15px;font-weight:700}
.section-icon{font-size:18px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:10px}
.card{background:var(--sur);border:1px solid var(--brd);border-radius:10px;
  padding:16px 18px;display:flex;flex-direction:column;gap:8px;
  color:var(--txt);cursor:pointer;transition:border-color .15s,background .15s}
.card:hover{border-color:var(--acc);background:var(--sur2)}
.card-top{display:flex;align-items:center;justify-content:space-between;gap:8px}
.card-title{font-size:13px;font-weight:700}
.env-badge{font-size:10px;font-weight:700;padding:2px 8px;border-radius:4px;white-space:nowrap}
.card-desc{font-size:12px;color:var(--mut);line-height:1.5}
.card-footer{display:flex;align-items:center;justify-content:space-between;margin-top:2px}
.card-link{font-size:11px;color:var(--acc)}
.card-refresh{font-size:10px;color:var(--grn)}
.card-refresh.stale{color:var(--yel)}
.card-refresh.never{color:var(--red)}
.footer{text-align:center;color:var(--mut);font-size:11px;margin-top:48px;
  padding-top:20px;border-top:1px solid var(--brd)}

/* publish-in-progress banner */
.run-banner{background:#2a1e00;border:1px solid var(--yel);border-radius:8px;
  padding:10px 18px;margin-bottom:28px;display:flex;align-items:center;gap:12px;
  font-size:13px;color:var(--yel)}
.run-dot{width:8px;height:8px;border-radius:50%;background:var(--yel);flex-shrink:0;
  animation:run-pulse 1.2s ease-in-out infinite}
@keyframes run-pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.35;transform:scale(.65)}}
.run-info{flex:1;display:flex;flex-direction:column;gap:6px}
.run-progress-track{width:100%;height:6px;border-radius:3px;background:rgba(255,200,0,.2);overflow:hidden}
.run-progress-fill{height:100%;border-radius:3px;background:var(--yel);transition:width .3s ease}

/* feedback widget */
.fb-toggle{position:fixed;bottom:24px;left:24px;z-index:1000;
  background:var(--sur);border:1px solid var(--brd);border-radius:24px;
  padding:8px 16px;cursor:pointer;color:var(--acc);font-size:12px;font-weight:700;
  font-family:inherit;box-shadow:0 2px 12px rgba(0,0,0,.4);transition:border-color .15s}
.fb-toggle:hover{border-color:var(--acc)}
.fb-toggle::after{content:attr(data-tooltip);position:absolute;bottom:calc(100% + 10px);left:0;
  background:#1a2333;color:var(--txt);font-size:11px;font-weight:400;font-style:italic;
  padding:7px 11px;border-radius:7px;border:1px solid var(--brd);
  white-space:normal;width:200px;line-height:1.5;text-align:left;
  opacity:0;pointer-events:none;transition:opacity .18s;box-shadow:0 4px 14px rgba(0,0,0,.4)}
.fb-toggle:hover::after{opacity:1}
.fb-panel{position:fixed;bottom:68px;left:24px;z-index:1001;width:320px;
  background:var(--sur);border:1px solid var(--brd);border-radius:12px;
  box-shadow:0 4px 24px rgba(0,0,0,.5);display:none;flex-direction:column;overflow:hidden}
.fb-panel.open{display:flex}
.fb-panel-hdr{padding:12px 16px;border-bottom:1px solid var(--brd);
  font-size:13px;font-weight:700;color:var(--txt)}
.fb-tabs{display:flex;border-bottom:1px solid var(--brd)}
.fb-tab{flex:1;padding:8px;font-size:11px;font-weight:700;text-align:center;
  cursor:pointer;color:var(--mut);background:none;border:none;font-family:inherit;
  border-bottom:2px solid transparent;transition:color .12s,border-color .12s}
.fb-tab.active{color:var(--acc);border-bottom-color:var(--acc)}
.fb-body{padding:14px 16px;display:flex;flex-direction:column;gap:10px}
.fb-label{font-size:10px;font-weight:700;text-transform:uppercase;
  letter-spacing:.5px;color:var(--mut);margin-bottom:3px}
.fb-input{width:100%;padding:7px 10px;border-radius:6px;border:1px solid var(--brd);
  background:var(--sur2);color:var(--txt);font-size:12px;font-family:inherit;outline:none}
.fb-input:focus{border-color:var(--acc)}
.fb-dt{background:var(--sur2);border:1px solid var(--brd);border-radius:6px;
  padding:7px 10px;font-size:12px;color:var(--mut)}
.fb-pri-row{display:flex;gap:6px}
@keyframes pri-pop{
  0%  {transform:scale(1)}
  35% {transform:scale(1.18)}
  65% {transform:scale(.93)}
  82% {transform:scale(1.05)}
  100%{transform:scale(1)}
}
.fb-pri-btn{flex:1;padding:6px 0;border-radius:5px;font-size:10px;font-weight:700;
  cursor:pointer;font-family:inherit;
  transition:background .15s,border-color .15s,color .15s,box-shadow .15s;
  background:var(--sur2);color:var(--mut)}
#fb-pri-Low    {border:1px solid #2d6648}
#fb-pri-Medium {border:1px solid #806010}
#fb-pri-High   {border:1px solid #8a5020}
#fb-pri-Critical{border:1px solid #8a2828}
.fb-pri-btn:hover{background:var(--sur);color:var(--txt)}
.fb-pri-btn.active-low{
  background:#1a3a2a;border-color:var(--grn);color:var(--grn);
  box-shadow:0 0 0 2px rgba(74,222,128,.35),0 0 12px rgba(74,222,128,.25);
  animation:pri-pop .35s cubic-bezier(.36,.07,.19,.97)}
.fb-pri-btn.active-medium{
  background:#3a300a;border-color:var(--yel);color:var(--yel);
  box-shadow:0 0 0 2px rgba(251,191,36,.35),0 0 12px rgba(251,191,36,.25);
  animation:pri-pop .35s cubic-bezier(.36,.07,.19,.97)}
.fb-pri-btn.active-high{
  background:#3a2a1e;border-color:#fb923c;color:#fb923c;
  box-shadow:0 0 0 2px rgba(251,146,60,.35),0 0 12px rgba(251,146,60,.25);
  animation:pri-pop .35s cubic-bezier(.36,.07,.19,.97)}
.fb-pri-btn.active-critical{
  background:#3a1a1a;border-color:var(--red);color:var(--red);
  box-shadow:0 0 0 2px rgba(248,113,113,.35),0 0 12px rgba(248,113,113,.25);
  animation:pri-pop .35s cubic-bezier(.36,.07,.19,.97)}
.fb-submit{padding:8px;border-radius:6px;border:none;background:var(--acc);
  color:#fff;font-size:12px;font-weight:700;cursor:pointer;font-family:inherit;
  transition:opacity .12s}
.fb-submit:hover{opacity:.85}
.fb-log{padding:12px 16px;max-height:300px;overflow-y:auto;display:flex;flex-direction:column;gap:8px}
.fb-log-entry{background:var(--sur2);border:1px solid var(--brd);border-radius:7px;padding:9px 11px}
.fb-log-meta{font-size:10px;color:var(--mut);margin-bottom:4px}
.fb-log-comment{font-size:12px;color:var(--txt)}
.fb-log-actions{display:flex;gap:8px;margin-top:8px}
.fb-log-btn{font-size:10px;font-weight:700;padding:3px 8px;border-radius:4px;
  border:1px solid var(--brd);background:none;color:var(--mut);cursor:pointer;font-family:inherit}
.fb-log-btn:hover{border-color:var(--acc);color:var(--acc)}
.fb-spinner{display:flex;flex-direction:column;align-items:center;gap:10px;padding:28px 0}
.fb-spinner-ring{width:28px;height:28px;border:3px solid var(--brd);border-top-color:var(--acc);border-radius:50%;animation:fb-spin .7s linear infinite}
@keyframes fb-spin{to{transform:rotate(360deg)}}
.fb-spinner-word{font-size:11px;color:var(--mut);font-style:italic;min-width:110px;text-align:center}
.fb-btn-spin{display:inline-block;width:10px;height:10px;border:2px solid rgba(255,255,255,.4);border-top-color:#fff;border-radius:50%;animation:fb-spin .6s linear infinite;vertical-align:middle;margin-right:4px}
"""


def get_last_refreshed(url):
    """Return (label, css_class, cache_buster) for a report's local file mtime."""
    local_path = os.path.join(REPORTS_DIR, url)
    if not os.path.exists(local_path):
        return "Never generated", "never", "0"
    raw_mtime = os.path.getmtime(local_path)
    mtime     = datetime.fromtimestamp(raw_mtime)
    now       = datetime.now()
    age_h     = (now - mtime).total_seconds() / 3600
    label     = mtime.strftime("%Y-%m-%d %H:%M")
    css       = "stale" if age_h > 25 else ""
    # Integer mtime used as cache-busting query param so browsers always load
    # the latest file after a publish run updates it.
    return label, css, str(int(raw_mtime))


def build_html(generated, running_since=None, step=None, total=None):
    sections_html = ""

    for group in REPORTS:
        cards_html = ""
        for item in group["items"]:
            env   = item["env"]
            color, bg = ENV_COLORS.get(env, ("var(--mut)", "var(--sur2)"))
            badge = f'<span class="env-badge" style="background:{bg};color:{color}">{env}</span>'
            refresh_ts, refresh_cls, cache_v = get_last_refreshed(item["url"])
            refresh_cls_attr = f' {refresh_cls}' if refresh_cls else ''
            versioned_url = f"{item['url']}?v={cache_v}"
            cards_html += f"""
        <div class="card" onclick="window.location='{versioned_url}'">
          <div class="card-top">
            <span class="card-title">{item['title']}</span>
            {badge}
          </div>
          <div class="card-desc">{item['desc']}</div>
          <div class="card-footer">
            <a class="card-link" href="{versioned_url}" target="_blank" onclick="event.stopPropagation()">Open report ↗</a>
            <span class="card-refresh{refresh_cls_attr}">↻ {refresh_ts}</span>
          </div>
        </div>"""

        sections_html += f"""
    <div class="section">
      <div class="section-hdr">
        <span class="section-icon">{group['icon']}</span>
        <h2>{group['category']}</h2>
      </div>
      <div class="grid">{cards_html}
      </div>
    </div>"""

    if running_since:
        if step is not None and total:
            pct = int(step / total * 100)
            step_txt = f'{step} of {total} reports complete ({pct}%)'
            bar = f'<div class="run-progress-track"><div class="run-progress-fill" style="width:{pct}%"></div></div>'
        else:
            pct = 0
            step_txt = 'reports will update when complete'
            bar = ''
        banner_html = (
            f'<div class="run-banner"><div class="run-dot"></div>'
            f'<div class="run-info"><span>Refresh in progress since {running_since} &mdash; {step_txt}</span>'
            f'{bar}</div></div>'
        )
    else:
        banner_html = ''

    auto_refresh = '<meta http-equiv="refresh" content="30"/>' if running_since else ''

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
{auto_refresh}
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>IDOH Azure Metadata Marketplace</title>
<style>{CSS}</style>
</head>
<body>
<div class="page">

  <div class="hero">
    <h1>IDOH Metadata Marketplace</h1>
    <p>Centralized metadata documentation for the Office of Data Analytics for Indiana Department of Health.</p>
    <span class="badge">{'Refreshing&hellip;' if running_since else f'Last published: {generated}'}</span>
    <br/><a href="help.html" style="display:inline-block;margin-top:14px;font-size:12px;
      color:var(--acc);border:1px solid var(--brd);border-radius:6px;padding:5px 16px;
      text-decoration:none;" title="Open the Help &amp; Guide page">&#10067; Help &amp; Guide</a>
  </div>

  {banner_html}

  {sections_html}

  <div class="footer">
    Hosted on Azure Databricks &nbsp;·&nbsp; idoh-metadata-marketplace-5757046586469840.0.azure.databricksapps.com
    <br/>Also available on Azure Static Web Apps &nbsp;·&nbsp; {BASE_URL}
  </div>

</div>

<!-- Feedback widget -->
<button class="fb-toggle" onclick="fbToggle()" data-tooltip="…you can also find recent updates to this app in the Changelog">💬 Feedback / Suggestions</button>
<div class="fb-panel" id="fb-panel">
  <div class="fb-panel-hdr" style="display:flex;align-items:center;justify-content:space-between">
    <span>Feedback &amp; Suggestions</span>
    <button onclick="fbToggle()" title="Close" style="background:none;border:none;color:var(--mut);font-size:18px;cursor:pointer;line-height:1;padding:0 2px">&times;</button>
  </div>
  <div class="fb-tabs">
    <button class="fb-tab active" id="fb-tab-new" onclick="fbShowTab('new')">New Entry</button>
    <button class="fb-tab" id="fb-tab-log" onclick="fbShowTab('log')">Submission Log</button>
  </div>
  <div id="fb-pane-new">
    <div class="fb-body">
      <div>
        <div class="fb-label">Your Name</div>
        <input class="fb-input" id="fb-name" placeholder="Enter your name" autocomplete="off" oninput="this.style.borderColor=''"/>
      </div>
      <div>
        <div class="fb-label">Date &amp; Time</div>
        <div class="fb-dt" id="fb-dt"></div>
      </div>
      <div>
        <div class="fb-label">Priority</div>
        <div class="fb-pri-row">
          <button class="fb-pri-btn" id="fb-pri-Low"      onclick="fbSetPri('Low')">Low</button>
          <button class="fb-pri-btn" id="fb-pri-Medium"   onclick="fbSetPri('Medium')">Medium</button>
          <button class="fb-pri-btn" id="fb-pri-High"     onclick="fbSetPri('High')">High</button>
          <button class="fb-pri-btn" id="fb-pri-Critical" onclick="fbSetPri('Critical')">Critical</button>
        </div>
      </div>
      <div>
        <div class="fb-label">Comment / Suggestion</div>
        <textarea class="fb-input" id="fb-comment" rows="4" placeholder="Describe your suggestion or issue…" style="resize:vertical" oninput="this.style.borderColor=''"></textarea>
      </div>
      <button class="fb-submit" onclick="fbSubmit()">Submit</button>
    </div>
  </div>
  <div id="fb-pane-log" style="display:none">
    <div style="display:flex;gap:8px;padding:10px 16px;border-bottom:1px solid var(--brd);flex-wrap:wrap">
      <button class="fb-log-btn" onclick="fbExportJSON()">Export JSON</button>
      <button class="fb-log-btn" onclick="fbExportCSV()">Export CSV</button>
      <button class="fb-log-btn" onclick="fbToggleDeleted()" id="fb-show-del">Show Deleted</button>
      <button class="fb-log-btn" style="margin-left:auto" onclick="fbLoadAndRender()">Refresh</button>
    </div>
    <div class="fb-log" id="fb-log"></div>
  </div>
</div>

<script>
let fbPri = 'Low';
let fbShowDeleted = false;
let fbEntries = [];

function fbToggle(){{
  const p = document.getElementById('fb-panel');
  const open = p.classList.toggle('open');
  if(open){{
    document.getElementById('fb-dt').textContent = new Date().toLocaleString();
    fbShowTab('new');
    fbSetPri('Low');
  }}
}}
function fbShowTab(t){{
  document.getElementById('fb-pane-new').style.display = t==='new' ? '' : 'none';
  document.getElementById('fb-pane-log').style.display = t==='log' ? '' : 'none';
  document.getElementById('fb-tab-new').classList.toggle('active', t==='new');
  document.getElementById('fb-tab-log').classList.toggle('active', t==='log');
  if(t==='log') fbLoadAndRender();
}}
function fbSetPri(p){{
  fbPri = p;
  ['Low','Medium','High','Critical'].forEach(v => {{
    const btn = document.getElementById('fb-pri-'+v);
    btn.classList.remove('active-low','active-medium','active-high','active-critical');
    if(v===p) btn.classList.add('active-'+v.toLowerCase());
  }});
}}

const FB_WORDS = ['Thinking…','Pondering…','Querying…','Fetching…','Analyzing…','Processing…','Computing…','Deliberating…','Ruminating…','Synthesizing…'];
let _fbWordTimer = null;
function fbShowSpinner(el){{
  let i = 0;
  el.innerHTML = '<div class="fb-spinner"><div class="fb-spinner-ring"></div><div class="fb-spinner-word">' + FB_WORDS[0] + '</div></div>';
  const wordEl = el.querySelector('.fb-spinner-word');
  _fbWordTimer = setInterval(() => {{ i = (i+1) % FB_WORDS.length; wordEl.textContent = FB_WORDS[i]; }}, 600);
}}
function fbClearSpinner(){{
  if(_fbWordTimer) {{ clearInterval(_fbWordTimer); _fbWordTimer = null; }}
}}

async function fbLoadAndRender(){{
  const el = document.getElementById('fb-log');
  fbClearSpinner();
  fbShowSpinner(el);
  try {{
    const r = await fetch('/api/feedback');
    if(!r.ok) throw new Error('HTTP ' + r.status);
    fbEntries = await r.json();
  }} catch(e) {{
    fbClearSpinner();
    el.innerHTML = '<p style="color:var(--red);font-size:12px">Could not load feedback: ' + e.message + '</p>';
    return;
  }}
  fbClearSpinner();
  fbRenderLog();
}}

function fbRenderLog(){{
  const el = document.getElementById('fb-log');
  const visible = fbShowDeleted ? fbEntries : fbEntries.filter(e => !e.deleted);
  if(!visible.length){{
    el.innerHTML='<p style="color:var(--mut);font-size:12px">' +
      (fbEntries.length && !fbShowDeleted ? 'All entries have been deleted. Click "Show Deleted" to view them.' : 'No submissions yet.') +
      '</p>';
    return;
  }}
  const PRI_COLOR = {{Low:'var(--grn)',Medium:'var(--yel)',High:'#fb923c',Critical:'var(--red)'}};
  el.innerHTML = visible.map((e) => {{
    const isDeleted = e.deleted;
    const pageLabel = e.page === 'help' ? '&nbsp;·&nbsp;<span style="color:var(--mut);font-size:10px">Help</span>' : '';
    return `<div class="fb-log-entry" style="${{isDeleted ? 'opacity:.45;border-style:dashed' : ''}}">
      <div class="fb-log-meta">
        <b style="color:var(--txt)">${{e.name}}</b> &nbsp;·&nbsp; ${{e.dt}}
        ${{e.priority ? `&nbsp;·&nbsp;<span style="color:${{PRI_COLOR[e.priority]||'var(--mut)'}};font-weight:700">${{e.priority}}</span>` : ''}}
        ${{pageLabel}}
        ${{isDeleted ? `&nbsp;·&nbsp;<span style="color:var(--red);font-size:10px">deleted ${{e.deletedAt||''}}</span>` : ''}}
      </div>
      <div class="fb-log-comment">${{e.comment}}</div>
      <div class="fb-log-actions">
        ${{isDeleted
          ? `<button class="fb-log-btn" onclick="fbRestore(${{e.id}})">Restore</button>`
          : `<button class="fb-log-btn" style="color:var(--red)" onclick="fbDelete(${{e.id}})">Delete</button>`
        }}
      </div>
    </div>`;
  }}).join('');
}}

async function fbSubmit(){{
  const nameEl    = document.getElementById('fb-name');
  const commentEl = document.getElementById('fb-comment');
  const name      = nameEl.value.trim();
  const comment   = commentEl.value.trim();
  const dt        = document.getElementById('fb-dt').textContent;
  let errors = [];
  const flag = (el, msg) => {{ el.style.borderColor='var(--red)'; errors.push(msg); }};
  nameEl.style.borderColor    = '';
  commentEl.style.borderColor = '';
  if(!name)    flag(nameEl,    'Your Name is required.');
  if(!fbPri)   errors.push('Please select a Priority.');
  if(!comment) flag(commentEl, 'Comment / Suggestion is required.');
  if(errors.length){{ alert(errors.join('\\n')); return; }}
  const btn = document.querySelector('.fb-submit');
  btn.disabled = true; btn.innerHTML = '<span class="fb-btn-spin"></span>Saving…';
  try {{
    const r = await fetch('/api/feedback', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{name, dt, priority: fbPri, comment, page: 'index'}})
    }});
    if(!r.ok) throw new Error('HTTP ' + r.status);
    const entry = await r.json();
    fbEntries.unshift(entry);
    nameEl.value = '';
    commentEl.value = '';
    fbSetPri('Low');
    fbShowTab('log');
  }} catch(e) {{
    alert('Failed to save: ' + e.message);
  }} finally {{
    btn.disabled = false; btn.innerHTML = 'Submit';
  }}
}}

async function fbDelete(entryId){{
  const idx = fbEntries.findIndex(e => e.id === entryId);
  if(idx < 0) return;
  try {{
    const r = await fetch('/api/feedback/' + entryId, {{
      method: 'PATCH',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{deleted: true}})
    }});
    if(!r.ok) throw new Error('HTTP ' + r.status);
    fbEntries[idx].deleted = true;
    fbEntries[idx].deletedAt = new Date().toLocaleString();
    fbRenderLog();
  }} catch(e) {{ alert('Failed to delete: ' + e.message); }}
}}

async function fbRestore(entryId){{
  const idx = fbEntries.findIndex(e => e.id === entryId);
  if(idx < 0) return;
  try {{
    const r = await fetch('/api/feedback/' + entryId, {{
      method: 'PATCH',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{deleted: false}})
    }});
    if(!r.ok) throw new Error('HTTP ' + r.status);
    fbEntries[idx].deleted = false;
    fbEntries[idx].deletedAt = null;
    fbRenderLog();
  }} catch(e) {{ alert('Failed to restore: ' + e.message); }}
}}

function fbToggleDeleted(){{
  fbShowDeleted = !fbShowDeleted;
  const btn = document.getElementById('fb-show-del');
  btn.textContent = fbShowDeleted ? 'Hide Deleted' : 'Show Deleted';
  btn.style.color = fbShowDeleted ? 'var(--acc)' : '';
  fbRenderLog();
}}
function fbExportJSON(){{
  if(!fbEntries.length){{ alert('No entries to export.'); return; }}
  const blob = new Blob([JSON.stringify(fbEntries, null, 2)], {{type:'application/json'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'idoh_feedback_' + new Date().toISOString().slice(0,10) + '.json';
  a.click();
}}
function fbExportCSV(){{
  if(!fbEntries.length){{ alert('No entries to export.'); return; }}
  const rows = [['ID','Name','Date/Time','Priority','Comment','Page','Deleted','DeletedAt'],
    ...fbEntries.map(e => [e.id,e.name,e.dt,e.priority,e.comment,e.page||'',e.deleted?'Yes':'No',e.deletedAt||''].map(v => '"'+String(v||'').replace(/"/g,'""')+'"'))];
  const blob = new Blob([rows.map(r=>r.join(',')).join('\\n')], {{type:'text/csv'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'idoh_feedback_' + new Date().toISOString().slice(0,10) + '.csv';
  a.click();
}}
</script>

</body>
</html>"""


def main():
    running_since = None
    step = None
    total = None
    if "--running" in sys.argv:
        idx = sys.argv.index("--running")
        running_since = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else datetime.now().strftime("%Y-%m-%d %H:%M")
    if "--step" in sys.argv:
        idx = sys.argv.index("--step")
        step = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else None
    if "--total" in sys.argv:
        idx = sys.argv.index("--total")
        total = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else None

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    html = build_html(generated, running_since=running_since, step=step, total=total)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved: {OUT_FILE}")

    try:
        import generate_help
        generate_help.main()
    except Exception as exc:
        print(f"Warning: could not generate help.html: {exc}")


if __name__ == "__main__":
    main()
