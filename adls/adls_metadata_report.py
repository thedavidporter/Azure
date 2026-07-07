#!/usr/bin/env python3
"""
ADLS Gen2 Metadata Report
Discovers all HNS-enabled storage accounts in the active Azure subscription,
walks their file systems up to MAX_DEPTH directory levels, and generates a
self-contained interactive HTML report.
"""

import json
import subprocess
from datetime import datetime

import requests

MAX_DEPTH  = 3    # directory levels to walk (root listing + this many levels)
MAX_PATHS  = 500  # max items returned per list call
OUT_FILE   = "/home/thedavidporter/adls_metadata_report.html"

# ── helpers ────────────────────────────────────────────────────────────────────

def esc(s):
    if s is None: return ""
    return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def fmt_bytes(n):
    try: n = int(n)
    except: return "0 B"
    for u in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1024: return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"

# ── auth ───────────────────────────────────────────────────────────────────────

def get_token():
    r = subprocess.run(
        ["az", "account", "get-access-token",
         "--resource", "https://storage.azure.com/",
         "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"az login required:\n{r.stderr.strip()}")
    return r.stdout.strip()

def get_subscription_name():
    r = subprocess.run(
        ["az", "account", "show", "--query", "name", "-o", "tsv"],
        capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else "unknown"

# ── discovery ──────────────────────────────────────────────────────────────────

def get_adls_accounts():
    r = subprocess.run(["az", "storage", "account", "list", "-o", "json"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return [a for a in json.loads(r.stdout) if a.get("isHnsEnabled")]

# ── ADLS Gen2 REST API ────────────────────────────────────────────────────────

_session = requests.Session()

def _get(url, params, token):
    try:
        resp = _session.get(url, params=params,
                            headers={"Authorization": f"Bearer {token}"},
                            timeout=20)
        if resp.status_code in (403, 404):
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"    warning: {e}")
        return None

def list_filesystems(account, token):
    data = _get(f"https://{account}.dfs.core.windows.net/",
                {"resource": "account", "maxResults": 200}, token)
    return (data or {}).get("filesystems", [])

def list_paths(account, fs, path, token):
    params = {"resource": "filesystem", "recursive": "false",
              "maxResults": MAX_PATHS}
    if path:
        params["directory"] = path
    data = _get(f"https://{account}.dfs.core.windows.net/{fs}", params, token)
    return (data or {}).get("paths", [])

# ── tree walker ────────────────────────────────────────────────────────────────

def walk(account, fs, path, token, depth):
    """Return (nodes, agg_size, agg_files) for the given path."""
    raw = list_paths(account, fs, path, token)
    nodes, agg_size, agg_files = [], 0, 0

    for p in raw:
        is_dir = p.get("isDirectory") == "true"
        size   = int(p.get("contentLength") or 0)
        node = {
            "name":      p["name"].rsplit("/", 1)[-1],
            "path":      p["name"],
            "is_dir":    is_dir,
            "modified":  p.get("lastModified", ""),
            "agg_size":  0,
            "agg_files": 0,
            "children":  [],
        }
        if is_dir and depth + 1 < MAX_DEPTH:
            ch, cs, cf = walk(account, fs, p["name"], token, depth + 1)
            node["children"]  = ch
            node["agg_size"]  = cs
            node["agg_files"] = cf
        else:
            node["agg_size"]  = size
            node["agg_files"] = 0 if is_dir else 1

        agg_size  += node["agg_size"]
        agg_files += node["agg_files"]
        nodes.append(node)

    if len(raw) >= MAX_PATHS:
        nodes.append({
            "name": f"… ≥{MAX_PATHS} items (listing capped)",
            "path": "", "is_dir": False, "modified": "",
            "agg_size": 0, "agg_files": 0, "children": [], "truncated": True,
        })

    return nodes, agg_size, agg_files

def flatten_dirs(nodes, account, fs_name, result):
    for n in nodes:
        if n.get("truncated") or not n["is_dir"]:
            continue
        result.append({
            "account":   account,
            "fs":        fs_name,
            "path":      n["path"],
            "agg_size":  n["agg_size"],
            "agg_files": n["agg_files"],
            "modified":  n["modified"],
        })
        flatten_dirs(n["children"], account, fs_name, result)

# ── CSS ────────────────────────────────────────────────────────────────────────

CSS = """
:root{--bg:#0f1117;--sur:#1a1d27;--sur2:#252836;--brd:#2e3245;
  --txt:#e2e8f0;--mut:#8892a4;--acc:#6c8eff;--grn:#4ade80;--yel:#fbbf24;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font:13px/1.5 'Segoe UI',system-ui,sans-serif;overflow:hidden}
.layout{display:flex;height:100vh}

/* sidebar */
.sidebar{width:280px;min-width:160px;background:var(--sur);border-right:1px solid var(--brd);
  display:flex;flex-direction:column;overflow:hidden;flex-shrink:0}
.sb-hdr{padding:12px 14px;border-bottom:1px solid var(--brd);font-weight:700;font-size:13px}
.sb-hdr small{display:block;color:var(--mut);font-weight:400;font-size:11px;margin-top:2px}
.sb-search{padding:7px 10px;border-bottom:1px solid var(--brd)}
.sb-search input{width:100%;padding:5px 9px;background:var(--sur2);border:1px solid var(--brd);
  border-radius:6px;color:var(--txt);font-size:12px;outline:none}
.sb-search input:focus{border-color:var(--acc)}
.sb-body{overflow-y:auto;flex:1;padding:4px 0}
.sb-acc{padding:8px 12px 3px;font-size:10px;font-weight:700;color:var(--acc);
  text-transform:uppercase;letter-spacing:.5px;border-top:1px solid var(--brd)}
.sb-acc:first-child{border-top:none}
.sb-fs{padding:3px 14px;font-size:12px;color:var(--yel);font-weight:600;cursor:pointer;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sb-fs:hover{background:var(--sur2)}
.sb-dir{padding:2px 14px 2px 24px;font-size:11px;color:var(--mut);cursor:pointer;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sb-dir:hover{color:var(--txt)}
.hidden{display:none!important}

/* main */
.main{flex:1;overflow-y:auto;padding:22px 26px}
h1{font-size:20px;font-weight:700;margin-bottom:3px}
.sub{color:var(--mut);font-size:12px;margin-bottom:20px}
.stats{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:22px}
.sc{background:var(--sur);border:1px solid var(--brd);border-radius:10px;padding:12px 16px;min-width:110px}
.sc-n{font-size:22px;font-weight:700;color:var(--acc);line-height:1}
.sc-l{font-size:11px;color:var(--mut);margin-top:3px}

/* tabs */
.tabs{display:flex;gap:2px;border-bottom:2px solid var(--brd);margin-bottom:18px}
.tab{padding:7px 14px;cursor:pointer;border-radius:6px 6px 0 0;font-size:12px;font-weight:600;
  color:var(--mut);border:1px solid transparent;border-bottom:none;margin-bottom:-2px;user-select:none}
.tab:hover{color:var(--txt);background:var(--sur)}
.tab.active{background:var(--sur);border-color:var(--brd);border-bottom-color:var(--sur);color:var(--txt)}
.panel{display:none}.panel.active{display:block}

/* overview cards */
.acc-card{background:var(--sur);border:1px solid var(--brd);border-radius:10px;
  padding:14px 18px;margin-bottom:12px}
.acc-card h2{font-size:14px;font-weight:700;color:var(--acc);margin-bottom:5px}
.acc-meta{font-size:11px;color:var(--mut);margin-bottom:12px;display:flex;gap:14px;flex-wrap:wrap}
.fs-grid{display:flex;flex-wrap:wrap;gap:8px}
.fs-chip{background:var(--sur2);border:1px solid var(--brd);border-radius:6px;padding:7px 12px;
  font-size:11px;min-width:130px}
.fs-chip strong{display:block;font-size:12px;color:var(--yel);margin-bottom:2px}
.fs-chip span{color:var(--mut)}

/* tree view */
.tree-wrap{padding:4px 0}
.t-acc{font-size:13px;font-weight:700;color:var(--acc);padding:12px 0 4px;
  border-bottom:1px solid var(--brd);margin-bottom:4px}
.t-acc:not(:first-child){margin-top:14px}
.t-fs{font-size:12px;font-weight:600;color:var(--yel);padding:5px 0;cursor:pointer;
  display:flex;align-items:center;gap:6px}
.t-fs-meta{font-size:11px;color:var(--mut);font-weight:400}
.t-fs-body{padding-left:16px}
.t-node{display:flex;align-items:baseline;gap:5px;padding:2px 0;font-size:12px}
.t-tog{cursor:pointer;color:var(--mut);font-size:10px;width:12px;flex-shrink:0;
  transition:transform .12s;user-select:none;line-height:1}
.t-tog.open{transform:rotate(90deg)}
.t-name{cursor:default}
.t-name.dir{color:var(--yel);cursor:pointer}
.t-name.trunc{color:var(--mut);font-style:italic}
.t-meta{color:var(--mut);font-size:11px;margin-left:4px}
.t-children{padding-left:16px;display:none}
.t-children.open{display:block}

/* tables */
table{width:100%;border-collapse:collapse;font-size:12px}
th{background:var(--sur2);padding:7px 11px;text-align:left;font-weight:700;
  border-bottom:2px solid var(--brd);white-space:nowrap;position:sticky;top:0;z-index:1}
td{padding:5px 11px;border-bottom:1px solid var(--brd);vertical-align:top}
tr:hover td{background:var(--sur)}
.srch{margin-bottom:12px}
.srch input{padding:7px 11px;background:var(--sur);border:1px solid var(--brd);
  border-radius:6px;color:var(--txt);font-size:12px;width:100%;max-width:440px;outline:none}
.srch input:focus{border-color:var(--acc)}
.sz{color:var(--grn);font-family:monospace;font-size:11px}
.mt{color:var(--mut);font-size:11px}
.mono{font-family:monospace;font-size:11px}
"""

# ── JavaScript ────────────────────────────────────────────────────────────────

JS = """
function escH(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

function fmtBytes(n){
  const u=['B','KB','MB','GB','TB','PB'];
  for(const unit of u){if(n<1024)return n.toFixed(1)+' '+unit;n/=1024;}
  return n.toFixed(1)+' PB';
}

// ── tabs ──────────────────────────────────────────────────────────────────────
function showTab(id,el){
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.getElementById('p-'+id).classList.add('active');
  el.classList.add('active');
}

// ── table search ──────────────────────────────────────────────────────────────
function ft(tid,q){
  q=q.toLowerCase();
  document.querySelectorAll('#'+tid+' tbody tr').forEach(tr=>{
    tr.classList.toggle('hidden',!!q&&!tr.textContent.toLowerCase().includes(q));
  });
}

// ── sidebar ───────────────────────────────────────────────────────────────────
function buildSidebar(){
  const sb=document.getElementById('sb-body');
  TREE_DATA.forEach(acc=>{
    const ah=document.createElement('div');
    ah.className='sb-acc';
    ah.textContent=acc.account;
    sb.appendChild(ah);
    acc.filesystems.forEach(fs=>{
      const fh=document.createElement('div');
      fh.className='sb-fs';
      fh.textContent='📂 '+fs.name;
      fh.title=fmtBytes(fs.agg_size)+' · '+fs.agg_files.toLocaleString()+' files';
      fh.addEventListener('click',()=>{
        showTab('tree',document.getElementById('tab-tree'));
        const el=document.getElementById('t-'+acc.account+'-'+fs.name);
        if(el)setTimeout(()=>el.scrollIntoView({behavior:'smooth',block:'start'}),50);
      });
      sb.appendChild(fh);
      fs.nodes.filter(n=>n.is_dir).slice(0,10).forEach(n=>{
        const d=document.createElement('div');
        d.className='sb-dir';
        d.textContent='└ '+n.name+(n.agg_size?' ('+fmtBytes(n.agg_size)+')':'');
        sb.appendChild(d);
      });
    });
  });
}

function filterSB(q){
  q=q.toLowerCase().trim();
  document.querySelectorAll('.sb-fs,.sb-dir').forEach(el=>{
    el.classList.toggle('hidden',!!q&&!el.textContent.toLowerCase().includes(q));
  });
}

// ── tree tab ──────────────────────────────────────────────────────────────────
function renderNode(n, container){
  const wrap=document.createElement('div');
  const row=document.createElement('div');
  row.className='t-node';

  if(n.truncated){
    const nm=document.createElement('span');
    nm.className='t-name trunc';
    nm.textContent=n.name;
    row.appendChild(nm);
    wrap.appendChild(row);
    container.appendChild(wrap);
    return;
  }

  if(n.is_dir){
    const tog=document.createElement('span');
    tog.className='t-tog';
    tog.textContent='▶';
    row.appendChild(tog);

    const nm=document.createElement('span');
    nm.className='t-name dir';
    nm.textContent='📁 '+n.name;
    row.appendChild(nm);

    const meta=document.createElement('span');
    meta.className='t-meta';
    const parts=[];
    if(n.agg_size)parts.push(fmtBytes(n.agg_size));
    if(n.agg_files)parts.push(n.agg_files.toLocaleString()+' files');
    if(n.modified)parts.push(n.modified.slice(5,16));
    meta.textContent=parts.join(' · ');
    row.appendChild(meta);

    const children=document.createElement('div');
    children.className='t-children';
    let rendered=false;

    function toggle(){
      const open=children.classList.toggle('open');
      tog.classList.toggle('open',open);
      if(open&&!rendered){
        (n.children||[]).forEach(c=>renderNode(c,children));
        rendered=true;
      }
    }
    tog.addEventListener('click',toggle);
    nm.addEventListener('click',toggle);
    wrap.appendChild(row);
    wrap.appendChild(children);
  } else {
    const sp=document.createElement('span');
    sp.style.cssText='width:12px;display:inline-block;flex-shrink:0';
    row.appendChild(sp);
    const nm=document.createElement('span');
    nm.className='t-name';
    nm.textContent='📄 '+n.name;
    row.appendChild(nm);
    if(n.agg_size||n.modified){
      const meta=document.createElement('span');
      meta.className='t-meta';
      const parts=[];
      if(n.agg_size)parts.push(fmtBytes(n.agg_size));
      if(n.modified)parts.push(n.modified.slice(5,16));
      meta.textContent=parts.join(' · ');
      row.appendChild(meta);
    }
    wrap.appendChild(row);
  }
  container.appendChild(wrap);
}

function renderTree(){
  const body=document.getElementById('tree-body');
  TREE_DATA.forEach(acc=>{
    const ah=document.createElement('div');
    ah.className='t-acc';
    ah.textContent='🗄 '+acc.account+'  ('+acc.location+' · '+acc.resource_group+')';
    body.appendChild(ah);
    acc.filesystems.forEach(fs=>{
      const fh=document.createElement('div');
      fh.className='t-fs';
      fh.id='t-'+acc.account+'-'+fs.name;
      fh.innerHTML='📂 <strong>'+escH(fs.name)+'</strong>'
        +'<span class="t-fs-meta">'+fmtBytes(fs.agg_size)
        +' &nbsp;·&nbsp; '+fs.agg_files.toLocaleString()+' files</span>';
      body.appendChild(fh);
      const fb=document.createElement('div');
      fb.className='t-fs-body';
      fs.nodes.forEach(n=>renderNode(n,fb));
      body.appendChild(fb);
    });
  });
}

// ── largest dirs table ────────────────────────────────────────────────────────
function renderLargest(){
  const tbody=document.querySelector('#large-tbl tbody');
  const rows=[...LARGE_DATA].sort((a,b)=>b.agg_size-a.agg_size).slice(0,200);
  tbody.innerHTML=rows.map(r=>`<tr>
    <td>${escH(r.account)}</td>
    <td>${escH(r.fs)}</td>
    <td class="mono">${escH(r.path)}</td>
    <td class="sz">${fmtBytes(r.agg_size)}</td>
    <td class="mt">${r.agg_files.toLocaleString()}</td>
    <td class="mt">${escH((r.modified||'').slice(5,16))}</td>
  </tr>`).join('');
}

document.addEventListener('DOMContentLoaded',()=>{
  buildSidebar();
  renderTree();
  renderLargest();
});
"""

# ── HTML builder ──────────────────────────────────────────────────────────────

def build_html(account_data, subscription, generated):
    n_accounts  = len(account_data)
    n_fs        = sum(len(a["filesystems"]) for a in account_data)
    total_size  = sum(fs["agg_size"]  for a in account_data for fs in a["filesystems"])
    total_files = sum(fs["agg_files"] for a in account_data for fs in a["filesystems"])

    # overview cards
    acc_cards = []
    for a in account_data:
        fs_chips = "".join(
            f'<div class="fs-chip"><strong>📂 {esc(fs["name"])}</strong>'
            f'<span>{fmt_bytes(fs["agg_size"])} &nbsp;·&nbsp; {fs["agg_files"]:,} files</span></div>'
            for fs in a["filesystems"]
        ) or '<span style="color:var(--mut)">No file systems found</span>'
        acc_cards.append(
            f'<div class="acc-card">'
            f'<h2>🗄 {esc(a["account"])}</h2>'
            f'<div class="acc-meta">'
            f'<span>Region: {esc(a["location"])}</span>'
            f'<span>Resource Group: {esc(a["resource_group"])}</span>'
            f'<span>{len(a["filesystems"])} file systems</span>'
            f'</div>'
            f'<div class="fs-grid">{fs_chips}</div>'
            f'</div>'
        )

    # flatten directories for the Largest table
    large_data = []
    for a in account_data:
        for fs in a["filesystems"]:
            flatten_dirs(fs["nodes"], a["account"], fs["name"], large_data)

    tree_json  = json.dumps(account_data, ensure_ascii=False, separators=(',', ':'))
    large_json = json.dumps(large_data,   ensure_ascii=False, separators=(',', ':'))
    depth_note = f"(showing ≤ {MAX_DEPTH} levels deep; sizes are partial if content extends further)"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>ADLS Gen2 Metadata — {esc(subscription)}</title>
<style>{CSS}.help-fab{{position:fixed;bottom:22px;right:22px;width:38px;height:38px;border-radius:50%;background:var(--acc);color:#fff;font-size:17px;font-weight:700;display:flex;align-items:center;justify-content:center;text-decoration:none;z-index:9999;box-shadow:0 2px 10px rgba(0,0,0,.5);opacity:.8;transition:opacity .15s;line-height:1}}.help-fab:hover{{opacity:1}}</style>
<script>
const TREE_DATA  = {tree_json};
const LARGE_DATA = {large_json};
</script>
</head>
<body>
<a href="help.html" class="help-fab" title="Help &amp; Guide">?</a>
<div class="layout">

<!-- SIDEBAR -->
<div class="sidebar">
  <div class="sb-hdr">ADLS Gen2<small>{esc(subscription)}</small></div>
  <div class="sb-search">
    <input placeholder="Filter…" oninput="filterSB(this.value)"/>
  </div>
  <div class="sb-body" id="sb-body"></div>
</div>

<!-- MAIN -->
<div class="main">
  <h1>ADLS Gen2 Metadata Report</h1>
  <p class="sub">Subscription: <strong>{esc(subscription)}</strong>
    &nbsp;|&nbsp; Generated: {esc(generated)}
    &nbsp;|&nbsp; <span style="color:var(--mut)">{esc(depth_note)}</span>
  </p>

  <div class="stats">
    <div class="sc"><div class="sc-n">{n_accounts}</div><div class="sc-l">Accounts</div></div>
    <div class="sc"><div class="sc-n">{n_fs}</div><div class="sc-l">File Systems</div></div>
    <div class="sc"><div class="sc-n">{fmt_bytes(total_size)}</div><div class="sc-l">Size (visible)</div></div>
    <div class="sc"><div class="sc-n">{total_files:,}</div><div class="sc-l">Files (visible)</div></div>
  </div>

  <div class="tabs">
    <div class="tab active" onclick="showTab('overview',this)">Overview</div>
    <div class="tab" id="tab-tree" onclick="showTab('tree',this)">Directory Tree</div>
    <div class="tab" onclick="showTab('large',this)">Largest Directories</div>
  </div>

  <div class="panel active" id="p-overview">
    {''.join(acc_cards)}
  </div>

  <div class="panel" id="p-tree">
    <div class="tree-wrap" id="tree-body"></div>
  </div>

  <div class="panel" id="p-large">
    <div class="srch">
      <input placeholder="Search account, path…" oninput="ft('large-tbl',this.value)"/>
    </div>
    <table id="large-tbl">
      <thead><tr>
        <th>Account</th><th>File System</th><th>Path</th>
        <th>Size</th><th>Files</th><th>Last Modified</th>
      </tr></thead>
      <tbody></tbody>
    </table>
  </div>

</div><!-- /main -->
</div><!-- /layout -->

<script>{JS}</script>
</body>
</html>"""

# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print("Getting Azure subscription…")
    subscription = get_subscription_name()
    print(f"  {subscription}")

    print("Getting storage access token…")
    token = get_token()

    print("Discovering ADLS Gen2 accounts (isHnsEnabled)…")
    accounts = get_adls_accounts()
    print(f"  Found {len(accounts)} account(s)")
    if not accounts:
        print("No ADLS Gen2 accounts found. Ensure you're logged in: az login")
        return

    account_data = []
    for acc in accounts:
        name = acc["name"]
        rg   = acc["resourceGroup"]
        loc  = acc["location"]
        print(f"\n  {name}  ({loc} · {rg})")

        filesystems = list_filesystems(name, token)
        print(f"    {len(filesystems)} file system(s)")

        fs_data = []
        for fs in filesystems:
            fs_name = fs["name"]
            print(f"    Walking {fs_name}…", end="", flush=True)
            nodes, agg_size, agg_files = walk(name, fs_name, "", token, 0)
            print(f" {fmt_bytes(agg_size)}, {agg_files:,} files")
            fs_data.append({
                "name":      fs_name,
                "modified":  fs.get("lastModified", ""),
                "nodes":     nodes,
                "agg_size":  agg_size,
                "agg_files": agg_files,
            })

        account_data.append({
            "account":        name,
            "resource_group": rg,
            "location":       loc,
            "filesystems":    fs_data,
        })

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    print("\nBuilding HTML…")
    html = build_html(account_data, subscription, generated)

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Report saved to:\n  {OUT_FILE}")


    try:
        import generate_metadata_index
        generate_metadata_index.main()
        print("  Index updated       : index.html")
    except Exception as exc:
        print(f"  Warning: could not update index.html: {exc}")
if __name__ == "__main__":
    main()
