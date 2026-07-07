#!/usr/bin/env python3
"""
Azure API Management Metadata Report
Collects APIs, operations, products, subscriptions, backends, named values,
and policies for dev and prd environments.

Usage:
  python3 apim_metadata_report.py --env dev
  python3 apim_metadata_report.py --env prd

If service_name is None the script auto-discovers the APIM instance in the resource group.
"""

import argparse
import json
import re
import subprocess
import requests
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# ── environment config ─────────────────────────────────────────────────────────

ENVIRONMENTS = {
    "dev": {
        "subscription_id": "57493fde-eff8-432f-8574-4f1281bd2ce3",
        "resource_group":  "zus1-idoh-dev-v2-rg",
        "service_name":    None,   # auto-discover; or set e.g. "zus1-idoh-dev-v2-apim"
        "label": "DEV",
    },
    "prd": {
        "subscription_id": "57493fde-eff8-432f-8574-4f1281bd2ce3",
        "resource_group":  "zus1-idoh-prd-v1-rg",
        "service_name":    None,
        "label": "PRD",
    },
}

API_VER  = "2022-08-01"
MGMT     = "https://management.azure.com"

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

def get_all(url, token, params=None):
    p = {"api-version": API_VER}
    if params:
        p.update(params)
    results = []
    while url:
        r = requests.get(url, headers=hdrs(token), params=p)
        r.raise_for_status()
        data = r.json()
        results.extend(data.get("value", [data] if "properties" in data else []))
        url = data.get("nextLink")
        p   = {}
    return results

def get_one(url, token):
    r = requests.get(url, headers=hdrs(token), params={"api-version": API_VER})
    if r.status_code == 200:
        return r.json()
    return {}

def apim_base(sub, rg, svc):
    return f"{MGMT}/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.ApiManagement/service/{svc}"

# ── discovery ──────────────────────────────────────────────────────────────────

def find_service(sub, rg, token):
    url = f"{MGMT}/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.ApiManagement/service"
    svcs = get_all(url, token)
    if not svcs:
        raise SystemExit(f"ERROR: No APIM service found in resource group '{rg}'. "
                         f"Check that API Management is deployed and the resource group is correct.")
    if len(svcs) > 1:
        names = [s["name"] for s in svcs]
        print(f"  Multiple APIM services found: {names} — using first: {svcs[0]['name']}")
    return svcs[0]["name"], svcs[0]

# ── fetch ──────────────────────────────────────────────────────────────────────

def fetch_apis(base, token):
    apis = get_all(f"{base}/apis", token)
    out  = []
    for a in apis:
        p = a.get("properties", {})
        out.append({
            "id":           a.get("name", ""),
            "name":         p.get("displayName", a.get("name", "")),
            "path":         p.get("path", ""),
            "protocols":    ", ".join(p.get("protocols", [])),
            "service_url":  p.get("serviceUrl", ""),
            "version":      p.get("apiVersion", ""),
            "description":  p.get("description", ""),
            "type":         p.get("type", "http"),
            "is_current":   p.get("isCurrent", True),
            "revision":     p.get("apiRevision", ""),
            "subscription_required": p.get("subscriptionRequired", True),
        })
    return out

def fetch_operations(base, token, api_ids):
    results = []
    def _fetch(api_id):
        ops = get_all(f"{base}/apis/{api_id}/operations", token)
        out = []
        for o in ops:
            p = o.get("properties", {})
            out.append({
                "api_id":      api_id,
                "id":          o.get("name", ""),
                "name":        p.get("displayName", o.get("name", "")),
                "method":      p.get("method", ""),
                "url":         p.get("urlTemplate", ""),
                "description": p.get("description", ""),
            })
        return out

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_fetch, aid): aid for aid in api_ids}
        for fut in as_completed(futures):
            try:
                results.extend(fut.result())
            except Exception as e:
                print(f"    WARN ops for {futures[fut]}: {e}")
    return results

def fetch_products(base, token):
    products = get_all(f"{base}/products", token)
    out = []
    for prod in products:
        p = prod.get("properties", {})
        # get APIs linked to this product
        try:
            apis = get_all(f"{base}/products/{prod['name']}/apis", token)
            api_names = [a.get("properties", {}).get("displayName", a.get("name", "")) for a in apis]
        except Exception:
            api_names = []
        out.append({
            "id":                    prod.get("name", ""),
            "name":                  p.get("displayName", prod.get("name", "")),
            "description":           p.get("description", ""),
            "state":                 p.get("state", ""),
            "subscription_required": p.get("subscriptionRequired", True),
            "approval_required":     p.get("approvalRequired", False),
            "apis":                  api_names,
        })
    return out

def fetch_subscriptions(base, token):
    subs = get_all(f"{base}/subscriptions", token)
    out  = []
    for s in subs:
        p = s.get("properties", {})
        scope = p.get("scope", "")
        # scope is like /products/{id} or /apis/{id} or /
        scope_type = "Global"
        scope_name = ""
        if "/products/" in scope:
            scope_type = "Product"
            scope_name = scope.split("/products/")[-1]
        elif "/apis/" in scope:
            scope_type = "API"
            scope_name = scope.split("/apis/")[-1]
        out.append({
            "id":           s.get("name", ""),
            "name":         p.get("displayName", s.get("name", "")),
            "state":        p.get("state", ""),
            "scope_type":   scope_type,
            "scope_name":   scope_name,
            "created":      (p.get("createdDate", "") or "")[:10],
            "expiry":       (p.get("expirationDate", "") or "")[:10],
        })
    return out

def fetch_backends(base, token):
    backends = get_all(f"{base}/backends", token)
    out = []
    for b in backends:
        p = b.get("properties", {})
        creds = p.get("credentials", {}) or {}
        tls   = p.get("tls", {}) or {}
        out.append({
            "id":           b.get("name", ""),
            "name":         p.get("title", b.get("name", "")),
            "url":          p.get("url", ""),
            "protocol":     p.get("protocol", ""),
            "description":  p.get("description", ""),
            "auth_header":  ", ".join((creds.get("header") or {}).keys()),
            "auth_query":   ", ".join((creds.get("query")  or {}).keys()),
            "validate_cert":not tls.get("validateCertificateChain", True) and "Skip" or "Yes",
        })
    return out

def fetch_named_values(base, token):
    nvs = get_all(f"{base}/namedValues", token)
    out = []
    for nv in nvs:
        p = nv.get("properties", {})
        out.append({
            "id":      nv.get("name", ""),
            "name":    p.get("displayName", nv.get("name", "")),
            "value":   "[Secret]" if p.get("secret") else p.get("value", ""),
            "secret":  p.get("secret", False),
            "tags":    ", ".join(p.get("tags", [])),
        })
    return out

def fetch_policy(base, token, path=""):
    try:
        url = f"{base}/policies/policy" if not path else f"{base}/{path}/policies/policy"
        r = get_one(url, token)
        return r.get("properties", {}).get("value", "")
    except Exception:
        return ""

def fetch_api_policies(base, token, api_ids):
    results = {}
    def _fetch(aid):
        return aid, fetch_policy(base, token, f"apis/{aid}")
    with ThreadPoolExecutor(max_workers=6) as ex:
        for aid, policy in ex.map(lambda a: _fetch(a), api_ids):
            if policy:
                results[aid] = policy
    return results

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
.sb-section{padding:5px 12px 2px;font-size:10px;font-weight:700;color:var(--acc);
  text-transform:uppercase;letter-spacing:.5px;margin-top:4px}
.sb-item{padding:3px 12px 3px 16px;font-size:12px;cursor:pointer;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sb-item:hover{background:var(--sur2);color:var(--acc)}
.sb-item .path{color:var(--mut);font-size:10px;margin-left:4px}

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
  border-radius:6px;color:var(--txt);font-size:12px;width:100%;max-width:420px;outline:none}
.srch input:focus{border-color:var(--acc)}

/* tables */
table{width:100%;border-collapse:collapse;font-size:12px}
th{background:var(--sur2);padding:6px 10px;text-align:left;font-weight:700;
  border-bottom:2px solid var(--brd);white-space:nowrap;position:sticky;top:0;z-index:1}
td{padding:4px 10px;border-bottom:1px solid var(--brd);vertical-align:middle}
tr:hover td{background:var(--sur)}
tr.clickable{cursor:pointer}
.hidden{display:none!important}
.mono{font-family:monospace;font-size:11px;color:var(--cyn);word-break:break-all}
.mut{color:var(--mut);font-size:11px}
.chip{display:inline-block;font-size:10px;padding:1px 6px;border-radius:3px;
  font-weight:700;white-space:nowrap;margin:1px}
.chip-get{background:#1a3a2a;color:#4ade80}
.chip-post{background:#1e2a4a;color:#6c8eff}
.chip-put{background:#3a2a1e;color:#fb923c}
.chip-patch{background:#2d1e5f;color:#c084fc}
.chip-delete{background:#3a1a1a;color:#f87171}
.chip-head{background:#252836;color:#94a3b8}
.chip-options{background:#252836;color:#94a3b8}
.chip-state-published{background:#1a3a2a;color:#4ade80}
.chip-state-notPublished{background:#3a1a1a;color:#f87171}
.chip-scope{background:#252836;color:#c084fc;font-size:10px}
.chip-proto{background:#1e3a5f;color:#60a5fa}
.chip-secret{background:#3a2a1e;color:#fbbf24}

/* overview grid */
.ov-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:9px;margin-bottom:20px}
.ov-card{background:var(--sur);border:1px solid var(--brd);border-radius:8px;padding:12px 14px;cursor:pointer}
.ov-card:hover{border-color:var(--acc)}
.ov-card h3{font-size:12px;color:var(--acc);margin-bottom:5px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ov-card .ct{font-size:11px;color:var(--mut);display:flex;flex-direction:column;gap:2px}
.ov-card .ct strong{color:var(--txt)}
h2{font-size:14px;font-weight:700;margin:14px 0 10px;padding-bottom:4px;border-bottom:1px solid var(--brd)}

/* policy viewer */
.policy-wrap{background:var(--sur2);border:1px solid var(--brd);border-radius:8px;overflow:auto;
  max-height:500px;padding:12px 16px;font-size:11.5px;font-family:monospace;line-height:1.7;white-space:pre}
.xml-tag{color:#60a5fa}.xml-attr{color:#fbbf24}.xml-val{color:#4ade80}.xml-cm{color:var(--mut);font-style:italic}
.policy-item{margin-bottom:24px}
.policy-label{font-size:12px;font-weight:700;color:var(--acc);margin-bottom:6px}

/* modal */
.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:200;
  display:flex;align-items:center;justify-content:center;padding:20px}
.modal-box{background:var(--sur);border:1px solid var(--brd);border-radius:12px;
  width:800px;max-width:calc(100vw - 40px);max-height:88vh;
  display:flex;flex-direction:column;box-shadow:0 24px 70px rgba(0,0,0,.7)}
.modal-hdr{display:flex;align-items:center;gap:10px;padding:13px 16px;
  border-bottom:1px solid var(--brd);flex-shrink:0}
.modal-hdr-title{flex:1;font-size:14px;font-weight:700}
.modal-hdr-sub{font-size:11px;color:var(--mut);font-weight:400;margin-top:2px}
.modal-close{background:none;border:none;color:var(--mut);font-size:20px;cursor:pointer;
  padding:1px 7px;border-radius:4px;line-height:1}
.modal-close:hover{background:var(--sur2);color:var(--txt)}
.modal-body{overflow-y:auto;flex:1;padding:14px 16px}
.kv-table{width:100%;border-collapse:collapse;font-size:12px}
.kv-table td{padding:5px 10px;border-bottom:1px solid var(--brd);vertical-align:top}
.kv-table td:first-child{font-weight:600;width:160px;white-space:nowrap}
"""

# ── JavaScript ─────────────────────────────────────────────────────────────────

JS = """
const PAGE = 500;

function escH(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

// ── tabs ──────────────────────────────────────────────────────────────────────
function showTab(id){
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.sc').forEach(c=>c.classList.remove('active-card'));
  document.getElementById('p-'+id).classList.add('active');
  const tab=document.getElementById('tab-'+id);  if(tab) tab.classList.add('active');
  const card=document.getElementById('card-'+id); if(card) card.classList.add('active-card');
  if(id==='ops' && !opsLoaded){ opsLoaded=true; vtLoad('ops'); }
}

// ── virtual table (operations) ────────────────────────────────────────────────
const VTABLES = {
  ops: { data: OPS_DATA, filtered: OPS_DATA, loaded: 0 }
};
let opsLoaded = false;

function opsRow(o){
  const mc = {'GET':'chip-get','POST':'chip-post','PUT':'chip-put',
    'PATCH':'chip-patch','DELETE':'chip-delete','HEAD':'chip-head','OPTIONS':'chip-options'};
  const cls = mc[o.method] || 'chip-head';
  return '<tr>'
    +'<td class="mut">'+escH(o.api_name)+'</td>'
    +'<td>'+escH(o.name)+'</td>'
    +'<td><span class="chip '+cls+'">'+escH(o.method)+'</span></td>'
    +'<td class="mono">'+escH(o.url)+'</td>'
    +'<td class="mut">'+escH(o.description)+'</td>'
    +'</tr>';
}

function vtFilter(id, q){
  const vt=VTABLES[id];
  q=q.toLowerCase().trim();
  vt.filtered=q ? vt.data.filter(o=>
    (o.api_name||'').toLowerCase().includes(q)||
    (o.name||'').toLowerCase().includes(q)||
    (o.method||'').toLowerCase().includes(q)||
    (o.url||'').toLowerCase().includes(q)) : vt.data;
  vt.loaded=0;
  document.querySelector('#tbl-'+id+' tbody').innerHTML='';
  vtLoad(id);
}

function vtLoad(id){
  const vt=VTABLES[id];
  const end=Math.min(vt.loaded+PAGE, vt.filtered.length);
  const html=vt.filtered.slice(vt.loaded,end).map(opsRow).join('');
  document.querySelector('#tbl-'+id+' tbody').insertAdjacentHTML('beforeend',html);
  vt.loaded=end;
  const el=document.getElementById('cnt-'+id);
  if(el) el.textContent = vt.loaded>=vt.filtered.length
    ? vt.filtered.length.toLocaleString()+' operations'
    : 'Showing '+vt.loaded.toLocaleString()+' of '+vt.filtered.length.toLocaleString()+' — scroll for more';
  const sent=document.getElementById('sent-'+id);
  if(sent) sent.style.display = vt.loaded<vt.filtered.length ? '' : 'none';
}

new IntersectionObserver(entries=>{
  if(entries[0].isIntersecting && opsLoaded) vtLoad('ops');
},{rootMargin:'400px'}).observe(document.getElementById('sent-ops'));

// ── sidebar ───────────────────────────────────────────────────────────────────
function filterSB(q){
  q=q.toLowerCase();
  document.querySelectorAll('.sb-item').forEach(el=>{
    el.classList.toggle('hidden',!!q&&!el.textContent.toLowerCase().includes(q));
  });
}

function filterByApi(apiId){
  showTab('ops');
  const q=document.getElementById('ops-q');
  const api=API_DATA.find(a=>a.id===apiId);
  q.value=api?api.name:apiId;
  vtFilter('ops',q.value);
}

// ── overview ──────────────────────────────────────────────────────────────────
function buildOverview(){
  // API cards
  const agrid=document.getElementById('api-grid');
  [...API_DATA].sort((a,b)=>b.ops_count-a.ops_count).forEach(api=>{
    const c=document.createElement('div');
    c.className='ov-card';
    c.innerHTML='<h3>'+escH(api.name)+'</h3><div class="ct">'
      +'<span class="mono" style="font-size:10px">/'+escH(api.path)+'</span>'
      +'<span><strong>'+api.ops_count+'</strong> operations</span>'
      +(api.protocols?'<span>'+escH(api.protocols)+'</span>':'')
      +(api.version?'<span>v'+escH(api.version)+'</span>':'')
      +(api.service_url?'<span class="mono" style="font-size:10px">'+escH(api.service_url)+'</span>':'')
      +'</div>';
    c.addEventListener('click',()=>filterByApi(api.id));
    agrid.appendChild(c);
  });

  // Protocol breakdown
  const pgrid=document.getElementById('proto-grid');
  const protos={};
  API_DATA.forEach(a=>(a.protocols||'').split(', ').filter(Boolean).forEach(p=>protos[p]=(protos[p]||0)+1));
  Object.entries(protos).forEach(([p,n])=>{
    const c=document.createElement('div');
    c.className='ov-card';
    c.innerHTML='<h3><span class="chip chip-proto">'+escH(p)+'</span></h3>'
      +'<div class="ct"><span><strong>'+n+'</strong> APIs</span></div>';
    pgrid.appendChild(c);
  });
}

// ── XML highlight ─────────────────────────────────────────────────────────────
function hlXML(xml){
  return escH(xml)
    .replace(/(&lt;\/?[a-zA-Z][^&gt;]*?)(\/&gt;|&gt;)/g, (m,tag,close)=>{
      const t=tag.replace(/([a-zA-Z-]+)="([^"]*)"/g,
        '<span class="xml-attr">$1</span>="<span class="xml-val">$2</span>"');
      return '<span class="xml-tag">'+t+close+'</span>';
    })
    .replace(/&lt;!--[\s\S]*?--&gt;/g, m=>'<span class="xml-cm">'+m+'</span>');
}

// ── API detail modal ──────────────────────────────────────────────────────────
function openAPI(apiId){
  const api=API_DATA.find(a=>a.id===apiId);
  if(!api) return;
  document.getElementById('api-modal-name').textContent=api.name;
  document.getElementById('api-modal-sub').textContent=api.type+' · /'+api.path+(api.version?' · v'+api.version:'');

  let html='<table class="kv-table">';
  const rows=[
    ['Path',         '/'+api.path],
    ['Protocols',    api.protocols],
    ['Backend URL',  api.service_url],
    ['Version',      api.version],
    ['Revision',     api.revision],
    ['Type',         api.type],
    ['Subscription Required', api.subscription_required?'Yes':'No'],
    ['Description',  api.description],
  ];
  rows.filter(([,v])=>v).forEach(([k,v])=>{
    const mono=k==='Backend URL'||k==='Path'?'class="mono"':'';
    html+='<tr><td>'+escH(k)+'</td><td '+mono+'>'+escH(v)+'</td></tr>';
  });
  html+='</table>';

  // policy
  const policy=API_POLICIES[apiId];
  if(policy){
    html+='<h2 style="font-size:13px;margin-top:16px;margin-bottom:8px">Policy</h2>'
      +'<div class="policy-wrap">'+hlXML(policy)+'</div>';
  }

  document.getElementById('api-modal-body').innerHTML=html;
  document.getElementById('api-modal').style.display='flex';
}
function closeAPIModal(){document.getElementById('api-modal').style.display='none';}
document.getElementById('api-modal').addEventListener('click',e=>{
  if(e.target===document.getElementById('api-modal')) closeAPIModal();
});
document.addEventListener('keydown',e=>{ if(e.key==='Escape') closeAPIModal(); });

document.addEventListener('DOMContentLoaded',()=>{
  buildOverview();
  showTab('overview');
});
"""

# ── HTML ───────────────────────────────────────────────────────────────────────

def method_chip(method):
    cls = {"GET":"chip-get","POST":"chip-post","PUT":"chip-put",
           "PATCH":"chip-patch","DELETE":"chip-delete"}.get(method,"chip-head")
    return f'<span class="chip {cls}">{esc(method)}</span>'

def state_chip(state):
    cls = "chip-state-published" if state == "published" else "chip-state-notPublished"
    return f'<span class="chip {cls}">{esc(state)}</span>'

def build_html(apis, operations, products, subscriptions, backends,
               named_values, global_policy, api_policies,
               svc_name, svc_meta, env_cfg, generated):

    label = env_cfg["label"]

    # ops count per api
    ops_by_api = defaultdict(int)
    for o in operations:
        ops_by_api[o["api_id"]] += 1
    for a in apis:
        a["ops_count"] = ops_by_api.get(a["id"], 0)

    # api name lookup
    api_name_map = {a["id"]: a["name"] for a in apis}
    for o in operations:
        o["api_name"] = api_name_map.get(o["api_id"], o["api_id"])

    # JSON for JS
    api_json    = json.dumps(apis,         ensure_ascii=False, separators=(',',':'))
    ops_json    = json.dumps(operations,   ensure_ascii=False, separators=(',',':'))
    policy_json = json.dumps(api_policies, ensure_ascii=False, separators=(',',':'))

    # ── sidebar ────────────────────────────────────────────────────────────────
    sb = '<div class="sb-section">APIs</div>'
    for a in sorted(apis, key=lambda x: x["name"]):
        sb += (f'<div class="sb-item" onclick="filterByApi(\'{js_esc(a["id"])}\')">'
               f'{esc(a["name"])}<span class="path">/{esc(a["path"])}</span></div>')

    # ── APIs table ─────────────────────────────────────────────────────────────
    api_rows = '\n'.join(
        f'<tr class="clickable" onclick="openAPI(\'{js_esc(a["id"])}\')">'
        f'<td><strong>{esc(a["name"])}</strong></td>'
        f'<td class="mono">/{esc(a["path"])}</td>'
        f'<td>{"".join(f"<span class=\'chip chip-proto\'>{esc(p)}</span>" for p in a["protocols"].split(", ") if p)}</td>'
        f'<td class="mono" style="font-size:11px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{esc(a["service_url"])}">{esc(a["service_url"])}</td>'
        f'<td class="mut">{esc(a["version"])}</td>'
        f'<td style="text-align:center">{a["ops_count"]}</td>'
        f'<td>{"Yes" if not a["subscription_required"] else "<span class=\'mut\'>Required</span>"}</td>'
        f'<td class="mut" style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{esc(a["description"])}</td>'
        f'</tr>'
        for a in sorted(apis, key=lambda x: x["name"])
    )

    # ── products table ─────────────────────────────────────────────────────────
    prod_rows = '\n'.join(
        f'<tr><td><strong>{esc(p["name"])}</strong></td>'
        f'<td>{state_chip(p["state"])}</td>'
        f'<td>{"Yes" if p["subscription_required"] else "No"}</td>'
        f'<td>{"Yes" if p["approval_required"] else "No"}</td>'
        f'<td><div style="display:flex;flex-wrap:wrap;gap:2px">{"".join(f"<span class=\'chip chip-proto\'>{esc(a)}</span>" for a in p["apis"])}</div></td>'
        f'<td class="mut">{esc(p["description"])}</td></tr>'
        for p in sorted(products, key=lambda x: x["name"])
    )

    # ── subscriptions table ────────────────────────────────────────────────────
    sub_rows = '\n'.join(
        f'<tr><td>{esc(s["name"])}</td>'
        f'<td><span class="chip chip-scope">{esc(s["scope_type"])}</span></td>'
        f'<td class="mut">{esc(s["scope_name"])}</td>'
        f'<td>{state_chip(s["state"])}</td>'
        f'<td class="mut">{esc(s["created"])}</td>'
        f'<td class="mut">{esc(s["expiry"]) or "—"}</td></tr>'
        for s in sorted(subscriptions, key=lambda x: x["name"])
    )

    # ── backends table ─────────────────────────────────────────────────────────
    be_rows = '\n'.join(
        f'<tr><td><strong>{esc(b["name"])}</strong></td>'
        f'<td class="mono">{esc(b["url"])}</td>'
        f'<td><span class="chip chip-proto">{esc(b["protocol"])}</span></td>'
        f'<td class="mut">{esc(b["validate_cert"])}</td>'
        f'<td class="mut">{esc(b["description"])}</td></tr>'
        for b in sorted(backends, key=lambda x: x["name"])
    ) or '<tr><td colspan="5" class="mut" style="padding:12px">No backends configured.</td></tr>'

    # ── named values table ─────────────────────────────────────────────────────
    nv_rows = '\n'.join(
        f'<tr><td><strong>{esc(n["name"])}</strong></td>'
        f'<td>{"<span class=\'chip chip-secret\'>Secret</span>" if n["secret"] else ""}</td>'
        f'<td class="mono">{esc(n["value"])}</td>'
        f'<td class="mut">{esc(n["tags"])}</td></tr>'
        for n in sorted(named_values, key=lambda x: x["name"])
    ) or '<tr><td colspan="4" class="mut" style="padding:12px">No named values found.</td></tr>'

    # ── policy section ─────────────────────────────────────────────────────────
    def hl_xml(xml):
        if not xml: return ""
        x = (xml.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
               .replace('"',"&quot;"))
        x = re.sub(r'(&lt;[/?]?[a-zA-Z][^&]*?&gt;)', r'<span class="xml-tag">\1</span>', x)
        x = re.sub(r'&lt;!--.*?--&gt;', lambda m: f'<span class="xml-cm">{m.group()}</span>', x, flags=re.S)
        return x

    policy_html = ""
    if global_policy:
        policy_html += f'<div class="policy-item"><div class="policy-label">Global Policy</div><div class="policy-wrap">{hl_xml(global_policy)}</div></div>'
    if api_policies:
        policy_html += '<h2>Per-API Policies</h2>'
        for api_id, pol in sorted(api_policies.items()):
            api_name = api_name_map.get(api_id, api_id)
            policy_html += (f'<div class="policy-item">'
                            f'<div class="policy-label">🔗 {esc(api_name)} <span class="mut">({esc(api_id)})</span></div>'
                            f'<div class="policy-wrap">{hl_xml(pol)}</div></div>')
    if not policy_html:
        policy_html = '<p class="mut">No policies found.</p>'

    # ── service metadata ───────────────────────────────────────────────────────
    svc_props = svc_meta.get("properties", {})
    svc_url   = svc_props.get("gatewayUrl", "")
    svc_tier  = svc_meta.get("sku", {}).get("name", "")
    svc_loc   = svc_meta.get("location", "")

    js_final = (JS
        .replace('API_DATA',    'const API_DATA = ' + api_json + '; API_DATA')
        .replace('OPS_DATA',    'const OPS_DATA = ' + ops_json + '; OPS_DATA')
        .replace('API_POLICIES','const API_POLICIES = ' + policy_json + '; API_POLICIES'))

    # fix replace collisions — just inline them properly
    js_final = (f"const API_DATA={api_json};\n"
                f"const OPS_DATA={ops_json};\n"
                f"const API_POLICIES={policy_json};\n"
                + JS)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>APIM Metadata — {esc(svc_name)} ({label})</title>
<style>{CSS}</style>
</head>
<body>
<div class="layout">

<!-- SIDEBAR -->
<div class="sidebar">
  <div class="sb-hdr">API Management — {esc(label)}<small>{esc(svc_name)}</small></div>
  <div class="sb-search"><input placeholder="Filter APIs…" oninput="filterSB(this.value)"/></div>
  <div class="sb-body">{sb}</div>
</div>

<!-- MAIN -->
<div class="main">
<div class="main-hdr">
  <h1>Azure API Management — {esc(label)}</h1>
  <p class="sub">Service: <strong>{esc(svc_name)}</strong>
    {f'&nbsp;|&nbsp; Tier: <strong>{esc(svc_tier)}</strong>' if svc_tier else ''}
    {f'&nbsp;|&nbsp; Gateway: <strong>{esc(svc_url)}</strong>' if svc_url else ''}
    &nbsp;|&nbsp; Generated: {esc(generated)}</p>

  <div class="stats">
    <div class="sc" id="card-overview"   onclick="showTab('overview')">
      <div class="sc-n">{len(apis)}</div><div class="sc-l">APIs</div></div>
    <div class="sc" id="card-ops"        onclick="showTab('ops')">
      <div class="sc-n" style="color:var(--acc)">{len(operations)}</div><div class="sc-l">Operations</div></div>
    <div class="sc" id="card-products"   onclick="showTab('products')">
      <div class="sc-n" style="color:var(--pur)">{len(products)}</div><div class="sc-l">Products</div></div>
    <div class="sc" id="card-subs"       onclick="showTab('subs')">
      <div class="sc-n" style="color:var(--grn)">{len(subscriptions)}</div><div class="sc-l">Subscriptions</div></div>
    <div class="sc" id="card-backends"   onclick="showTab('backends')">
      <div class="sc-n" style="color:var(--cyn)">{len(backends)}</div><div class="sc-l">Backends</div></div>
    <div class="sc" id="card-namedvals"  onclick="showTab('namedvals')">
      <div class="sc-n" style="color:var(--yel)">{len(named_values)}</div><div class="sc-l">Named Values</div></div>
    <div class="sc" id="card-policies"   onclick="showTab('policies')">
      <div class="sc-n">{1 + len(api_policies)}</div><div class="sc-l">Policies</div></div>
  </div>
</div>

  <div class="tabs">
    <div class="tab" id="tab-overview"  onclick="showTab('overview')">Overview</div>
    <div class="tab" id="tab-apis"      onclick="showTab('apis')">APIs ({len(apis)})</div>
    <div class="tab" id="tab-ops"       onclick="showTab('ops')">Operations ({len(operations)})</div>
    <div class="tab" id="tab-products"  onclick="showTab('products')">Products ({len(products)})</div>
    <div class="tab" id="tab-subs"      onclick="showTab('subs')">Subscriptions ({len(subscriptions)})</div>
    <div class="tab" id="tab-backends"  onclick="showTab('backends')">Backends ({len(backends)})</div>
    <div class="tab" id="tab-namedvals" onclick="showTab('namedvals')">Named Values ({len(named_values)})</div>
    <div class="tab" id="tab-policies"  onclick="showTab('policies')">Policies</div>
  </div>

  <div class="content">

    <!-- OVERVIEW -->
    <div class="panel" id="p-overview">
      <h2>APIs</h2>
      <div class="ov-grid" id="api-grid"></div>
      <h2>Protocols</h2>
      <div class="ov-grid" id="proto-grid"></div>
    </div>

    <!-- APIs -->
    <div class="panel" id="p-apis">
      <p class="mut" style="margin-bottom:8px">Click any row to view connection details and policy.</p>
      <div class="srch"><input placeholder="Search APIs…" oninput="ft('api-tbl',this.value)"/></div>
      <table id="api-tbl">
        <thead><tr><th>Display Name</th><th>Path</th><th>Protocol</th><th>Backend URL</th>
          <th>Version</th><th>Ops</th><th>Open Access</th><th>Description</th></tr></thead>
        <tbody>{api_rows}</tbody>
      </table>
    </div>

    <!-- OPERATIONS -->
    <div class="panel" id="p-ops">
      <div class="srch"><input id="ops-q" placeholder="Search API, operation, method, or URL…"
           oninput="vtFilter('ops',this.value)"/></div>
      <div id="cnt-ops" class="mut" style="margin-bottom:8px"></div>
      <table id="tbl-ops">
        <thead><tr><th>API</th><th>Operation</th><th>Method</th><th>URL Template</th><th>Description</th></tr></thead>
        <tbody></tbody>
      </table>
      <div id="sent-ops" style="height:1px;margin-top:8px"></div>
    </div>

    <!-- PRODUCTS -->
    <div class="panel" id="p-products">
      <div class="srch"><input placeholder="Search products…" oninput="ft('prod-tbl',this.value)"/></div>
      <table id="prod-tbl">
        <thead><tr><th>Name</th><th>State</th><th>Subscription Required</th>
          <th>Approval Required</th><th>APIs</th><th>Description</th></tr></thead>
        <tbody>{prod_rows or '<tr><td colspan="6" class="mut" style="padding:12px">No products found.</td></tr>'}</tbody>
      </table>
    </div>

    <!-- SUBSCRIPTIONS -->
    <div class="panel" id="p-subs">
      <div class="srch"><input placeholder="Search subscriptions…" oninput="ft('sub-tbl',this.value)"/></div>
      <table id="sub-tbl">
        <thead><tr><th>Name</th><th>Scope</th><th>Scope Target</th><th>State</th>
          <th>Created</th><th>Expiry</th></tr></thead>
        <tbody>{sub_rows or '<tr><td colspan="6" class="mut" style="padding:12px">No subscriptions found.</td></tr>'}</tbody>
      </table>
    </div>

    <!-- BACKENDS -->
    <div class="panel" id="p-backends">
      <div class="srch"><input placeholder="Search backends…" oninput="ft('be-tbl',this.value)"/></div>
      <table id="be-tbl">
        <thead><tr><th>Name</th><th>URL</th><th>Protocol</th><th>Cert Validation</th><th>Description</th></tr></thead>
        <tbody>{be_rows}</tbody>
      </table>
    </div>

    <!-- NAMED VALUES -->
    <div class="panel" id="p-namedvals">
      <div class="srch"><input placeholder="Search named values…" oninput="ft('nv-tbl',this.value)"/></div>
      <table id="nv-tbl">
        <thead><tr><th>Name</th><th>Secret</th><th>Value</th><th>Tags</th></tr></thead>
        <tbody>{nv_rows}</tbody>
      </table>
    </div>

    <!-- POLICIES -->
    <div class="panel" id="p-policies">
      {policy_html}
    </div>

  </div><!-- /content -->
</div><!-- /main -->
</div><!-- /layout -->

<!-- API DETAIL MODAL -->
<div id="api-modal" class="modal-overlay" style="display:none">
  <div class="modal-box">
    <div class="modal-hdr">
      <div class="modal-hdr-title">
        <div>🔗 <span id="api-modal-name"></span></div>
        <div class="modal-hdr-sub" id="api-modal-sub"></div>
      </div>
      <button class="modal-close" onclick="closeAPIModal()">&#x2715;</button>
    </div>
    <div class="modal-body" id="api-modal-body"></div>
  </div>
</div>

<script>
function ft(tid,q){{
  q=(q||'').toLowerCase().trim();
  document.querySelectorAll('#'+tid+' tbody tr').forEach(tr=>{{
    tr.classList.toggle('hidden',!!q&&!tr.textContent.toLowerCase().includes(q));
  }});
}}
{js_final}
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

    print(f"\n=== APIM Metadata — {label} ===")
    print("Getting access token…")
    token = get_token()

    # discover or use configured service name
    svc_name = env_cfg.get("service_name")
    if svc_name:
        svc_meta = get_one(f"{MGMT}/subscriptions/{sub}/resourceGroups/{rg}"
                           f"/providers/Microsoft.ApiManagement/service/{svc_name}", token)
    else:
        print(f"  Auto-discovering APIM service in {rg}…")
        svc_name, svc_meta = find_service(sub, rg, token)

    print(f"  Service: {svc_name}")
    base = apim_base(sub, rg, svc_name)

    print("  Fetching APIs…", end="", flush=True)
    apis = fetch_apis(base, token)
    print(f" {len(apis)}")

    api_ids = [a["id"] for a in apis]

    print(f"  Fetching operations for {len(api_ids)} APIs…")
    operations = fetch_operations(base, token, api_ids)
    print(f"  {len(operations)} operations")

    print("  Fetching products…", end="", flush=True)
    products = fetch_products(base, token)
    print(f" {len(products)}")

    print("  Fetching subscriptions…", end="", flush=True)
    subscriptions = fetch_subscriptions(base, token)
    print(f" {len(subscriptions)}")

    print("  Fetching backends…", end="", flush=True)
    backends = fetch_backends(base, token)
    print(f" {len(backends)}")

    print("  Fetching named values…", end="", flush=True)
    named_values = fetch_named_values(base, token)
    print(f" {len(named_values)}")

    print("  Fetching global policy…")
    global_policy = fetch_policy(base, token)

    print("  Fetching per-API policies…")
    api_policies = fetch_api_policies(base, token, api_ids)
    print(f"  {len(api_policies)} APIs have policies")

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    print("\nBuilding HTML…")
    html = build_html(
        apis, operations, products, subscriptions, backends,
        named_values, global_policy, api_policies,
        svc_name, svc_meta, env_cfg, generated
    )

    out = f"/home/thedavidporter/apim_metadata_report_{args.env}.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\nSaved: {out}")
    print(f"  APIs          : {len(apis)}")
    print(f"  Operations    : {len(operations)}")
    print(f"  Products      : {len(products)}")
    print(f"  Subscriptions : {len(subscriptions)}")
    print(f"  Backends      : {len(backends)}")
    print(f"  Named Values  : {len(named_values)}")
    print(f"  API Policies  : {len(api_policies)}")

if __name__ == "__main__":
    main()
