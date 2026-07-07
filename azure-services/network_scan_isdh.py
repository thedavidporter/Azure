#!/usr/bin/env python3
"""
Network Drive File Scanner — \\State.in.us\file1\ISDH\Shared\ISDH6\ITS
Walks the share and finds all SAS and data files, then generates
a self-contained interactive HTML report.

WSL mount steps:
  sudo mkdir -p /mnt/state_file1
  sudo mount -t drvfs '\\State.in.us\file1' /mnt/state_file1
  # then ROOT_PATH below points to the subfolder within the share
"""

import os
import json
from collections import defaultdict
from datetime import datetime

ROOT_PATH = "/mnt/state_file1/ISDH/Shared/ISDH6/ITS"
OUT_FILE  = "/home/thedavidporter/network_scan_its.html"

SKIP_DIRS = {
    "$RECYCLE.BIN", "System Volume Information", "RECYCLER",
    ".git", "__pycache__", "node_modules", ".Trash-0",
}

SAS_EXT = {
    ".sas":      "SAS Program",
    ".sas7bdat": "SAS Dataset",
    ".sas7bcat": "SAS Catalog",
    ".sas7bvew": "SAS View",
    ".sas7bndx": "SAS Index",
    ".xpt":      "SAS Transport",
    ".sas7bput": "SAS Utility",
}

DATA_EXT = {
    ".csv":     "CSV",
    ".tsv":     "TSV",
    ".txt":     "Text/Flat",
    ".xlsx":    "Excel",
    ".xls":     "Excel (Legacy)",
    ".xlsm":    "Excel (Macro)",
    ".parquet": "Parquet",
    ".avro":    "Avro",
    ".json":    "JSON",
    ".xml":     "XML",
    ".db":      "SQLite",
    ".sqlite":  "SQLite",
    ".sqlite3": "SQLite",
    ".mdb":     "Access DB",
    ".accdb":   "Access DB",
}

ALL_EXT = {**SAS_EXT, **DATA_EXT}

# ── helpers ────────────────────────────────────────────────────────────────────

def esc(s):
    if s is None: return ""
    return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def fmt_bytes(n):
    try: n = int(n)
    except: return "0 B"
    for u in ("B","KB","MB","GB","TB"):
        if n < 1024: return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"

# ── scanner ────────────────────────────────────────────────────────────────────

def scan(root):
    files = []
    total = 0
    errors = 0

    print(f"Scanning: {root}")
    print("(This may take a while on large shares…)\n")

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and not d.startswith('$')
        ]
        dirnames.sort()

        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in ALL_EXT:
                continue

            fpath = os.path.join(dirpath, fname)
            try:
                st   = os.stat(fpath)
                size = st.st_size
                mod  = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            except (PermissionError, OSError) as e:
                size = 0
                mod  = ""
                errors += 1

            rel_path = os.path.relpath(fpath, root).replace("\\", "/")
            rel_dir  = os.path.relpath(dirpath, root).replace("\\", "/")
            if rel_dir == ".":
                rel_dir = "(root)"

            files.append({
                "name":     fname,
                "ext":      ext,
                "type":     ALL_EXT[ext],
                "category": "SAS" if ext in SAS_EXT else "Data",
                "path":     rel_path,
                "dir":      rel_dir,
                "size":     size,
                "modified": mod,
            })
            total += 1
            if total % 500 == 0:
                print(f"  … {total:,} matching files found so far")

    print(f"\nScan complete: {total:,} files ({errors} permission errors)")
    return files

# ── aggregations ───────────────────────────────────────────────────────────────

def build_dir_summary(files):
    dirs = defaultdict(lambda: {"count": 0, "size": 0, "sas": 0, "data": 0})
    for f in files:
        top = f["dir"].split("/")[0]
        dirs[top]["count"] += 1
        dirs[top]["size"]  += f["size"]
        if f["category"] == "SAS":
            dirs[top]["sas"] += 1
        else:
            dirs[top]["data"] += 1
    return dict(dirs)

def build_type_summary(files):
    types = defaultdict(lambda: {"count": 0, "size": 0})
    for f in files:
        types[f["type"]]["count"] += 1
        types[f["type"]]["size"]  += f["size"]
    return dict(types)

# ── CSS ────────────────────────────────────────────────────────────────────────

CSS = """
:root{--bg:#0f1117;--sur:#1a1d27;--sur2:#252836;--brd:#2e3245;
  --txt:#e2e8f0;--mut:#8892a4;--acc:#6c8eff;--grn:#4ade80;
  --red:#f87171;--yel:#fbbf24;--pur:#c084fc;--cyn:#22d3ee;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font:13px/1.5 'Segoe UI',system-ui,sans-serif;overflow:hidden}
.layout{display:flex;height:100vh}

/* sidebar */
.sidebar{width:280px;min-width:160px;background:var(--sur);border-right:1px solid var(--brd);
  display:flex;flex-direction:column;overflow:hidden;flex-shrink:0}
.sb-hdr{padding:12px 14px;border-bottom:1px solid var(--brd);font-weight:700;font-size:13px}
.sb-hdr small{display:block;color:var(--mut);font-weight:400;font-size:11px;margin-top:2px;
  word-break:break-all}
.sb-search{padding:7px 10px;border-bottom:1px solid var(--brd)}
.sb-search input{width:100%;padding:5px 9px;background:var(--sur2);border:1px solid var(--brd);
  border-radius:6px;color:var(--txt);font-size:12px;outline:none}
.sb-search input:focus{border-color:var(--acc)}
.sb-body{overflow-y:auto;flex:1;padding:4px 0}
.sb-dir{padding:4px 12px;font-size:12px;cursor:pointer;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;color:var(--txt)}
.sb-dir:hover{background:var(--sur2);color:var(--acc)}
.sb-dir span{color:var(--mut);font-size:10px;margin-left:5px}

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
.srch{margin-bottom:10px;display:flex;gap:8px;align-items:center}
.srch input{padding:6px 11px;background:var(--sur);border:1px solid var(--brd);
  border-radius:6px;color:var(--txt);font-size:12px;width:100%;max-width:440px;outline:none}
.srch input:focus{border-color:var(--acc)}
.row-counter{font-size:11px;color:var(--mut);margin-bottom:8px}

/* tables */
table{width:100%;border-collapse:collapse;font-size:12px}
th{background:var(--sur2);padding:6px 10px;text-align:left;font-weight:700;
  border-bottom:2px solid var(--brd);white-space:nowrap;position:sticky;top:0;z-index:1}
td{padding:4px 10px;border-bottom:1px solid var(--brd);vertical-align:top}
tr:hover td{background:var(--sur)}
.hidden{display:none!important}
.mono{font-family:monospace;font-size:11px;color:var(--mut);word-break:break-all}
.sz{color:var(--grn);font-family:monospace;font-size:11px;white-space:nowrap}
.mt{color:var(--mut);font-size:11px;white-space:nowrap}
.chip{display:inline-block;font-size:10px;padding:1px 6px;border-radius:3px;
  font-weight:700;white-space:nowrap}

/* type chips */
.chip-sas-prog{background:#1e3a5f;color:#60a5fa}
.chip-sas-dat{background:#1a3a2a;color:#4ade80}
.chip-sas-other{background:#252836;color:#94a3b8}
.chip-data-flat{background:#3a2a1e;color:#fb923c}
.chip-data-xls{background:#1a3a3a;color:#22d3ee}
.chip-data-db{background:#2d1e5f;color:#c084fc}
.chip-data-other{background:#252836;color:#94a3b8}

/* overview grid */
.ov-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:9px;margin-bottom:20px}
.ov-card{background:var(--sur);border:1px solid var(--brd);border-radius:8px;padding:12px 14px;cursor:pointer}
.ov-card:hover{border-color:var(--acc)}
.ov-card h3{font-size:12px;color:var(--acc);margin-bottom:5px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ov-card .ct{font-size:11px;color:var(--mut);display:flex;flex-direction:column;gap:2px}
.ov-card .ct strong{color:var(--txt)}
h2{font-size:14px;font-weight:700;margin:16px 0 10px;padding-bottom:4px;border-bottom:1px solid var(--brd)}
"""

# ── JavaScript ────────────────────────────────────────────────────────────────

JS = """
const PAGE = 500;

function escH(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

function fmtBytes(n){
  const u=['B','KB','MB','GB','TB'];
  for(const unit of u){if(n<1024)return n.toFixed(1)+' '+unit;n/=1024;}
  return n.toFixed(1)+' TB';
}

function chipClass(type){
  if(type==='SAS Program') return 'chip-sas-prog';
  if(type==='SAS Dataset') return 'chip-sas-dat';
  if(type.startsWith('SAS')) return 'chip-sas-other';
  if(['CSV','TSV','Text/Flat'].includes(type)) return 'chip-data-flat';
  if(type.startsWith('Excel')) return 'chip-data-xls';
  if(type.includes('SQLite')||type.includes('Access')) return 'chip-data-db';
  return 'chip-data-other';
}

// ── tabs ──────────────────────────────────────────────────────────────────────
function showTab(id, el){
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.sc').forEach(c=>c.classList.remove('active-card'));
  document.getElementById('p-'+id).classList.add('active');
  const tab=document.getElementById('tab-'+id);
  if(tab) tab.classList.add('active');
  const card=document.getElementById('card-'+id);
  if(card) card.classList.add('active-card');
  // init virtual table on first open
  if(VTABLES[id] && !VTABLES[id].init){ VTABLES[id].init=true; vtLoad(id); }
}

// ── virtual table ─────────────────────────────────────────────────────────────
const VTABLES = {
  sas:  { data: null, filtered: null, loaded: 0, init: false, q: '' },
  data: { data: null, filtered: null, loaded: 0, init: false, q: '' },
};

function vtRow(f){
  return `<tr>
    <td>${escH(f.name)}</td>
    <td><span class="chip ${chipClass(f.type)}">${escH(f.type)}</span></td>
    <td class="mono">${escH(f.dir)}</td>
    <td class="sz">${fmtBytes(f.size)}</td>
    <td class="mt">${escH(f.modified)}</td>
  </tr>`;
}

function vtFilter(id, q){
  const vt=VTABLES[id];
  vt.q=q.toLowerCase().trim();
  vt.filtered = vt.q
    ? vt.data.filter(f=>
        f.name.toLowerCase().includes(vt.q) ||
        f.dir.toLowerCase().includes(vt.q)  ||
        f.type.toLowerCase().includes(vt.q))
    : vt.data;
  vt.loaded=0;
  document.querySelector('#tbl-'+id+' tbody').innerHTML='';
  vtLoad(id);
}

function vtLoad(id){
  const vt=VTABLES[id];
  if(!vt.filtered) return;
  const sentinel=document.getElementById('sent-'+id);
  const end=Math.min(vt.loaded+PAGE, vt.filtered.length);
  const html=vt.filtered.slice(vt.loaded,end).map(vtRow).join('');
  document.querySelector('#tbl-'+id+' tbody').insertAdjacentHTML('beforeend',html);
  vt.loaded=end;
  const el=document.getElementById('cnt-'+id);
  if(el) el.textContent = vt.loaded >= vt.filtered.length
    ? vt.filtered.length.toLocaleString()+' files'
    : `Showing ${vt.loaded.toLocaleString()} of ${vt.filtered.length.toLocaleString()} — scroll for more`;
  if(sentinel){
    sentinel.style.display = vt.loaded < vt.filtered.length ? '' : 'none';
  }
}

function vtObserve(id){
  const sentinel=document.getElementById('sent-'+id);
  if(!sentinel) return;
  new IntersectionObserver(entries=>{
    if(entries[0].isIntersecting) vtLoad(id);
  },{rootMargin:'400px'}).observe(sentinel);
}

// ── sidebar ───────────────────────────────────────────────────────────────────
function buildSidebar(){
  const sb=document.getElementById('sb-body');
  Object.entries(DIR_SUMMARY)
    .sort((a,b)=>b[1].count-a[1].count)
    .forEach(([dir,s])=>{
      const d=document.createElement('div');
      d.className='sb-dir';
      d.innerHTML=escH(dir)+'<span>'+s.count.toLocaleString()+'</span>';
      d.addEventListener('click',()=>filterByDir(dir));
      sb.appendChild(d);
    });
}

function filterSB(q){
  q=q.toLowerCase();
  document.querySelectorAll('.sb-dir').forEach(el=>{
    el.classList.toggle('hidden',!!q&&!el.textContent.toLowerCase().includes(q));
  });
}

function filterByDir(dir){
  showTab('sas',null);
  document.getElementById('q-sas').value=dir;
  vtFilter('sas',dir);
  showTab('data',null);
  document.getElementById('q-data').value=dir;
  vtFilter('data',dir);
  // show whichever has results
  const hasSas=VTABLES['sas'].filtered.length>0;
  showTab(hasSas?'sas':'data',null);
}

// ── overview cards ────────────────────────────────────────────────────────────
function buildOverview(){
  const grid=document.getElementById('dir-grid');
  Object.entries(DIR_SUMMARY)
    .sort((a,b)=>b[1].count-a[1].count)
    .slice(0,50)
    .forEach(([dir,s])=>{
      const c=document.createElement('div');
      c.className='ov-card';
      c.innerHTML=`<h3>📁 ${escH(dir)}</h3><div class="ct">
        <span><strong>${s.count.toLocaleString()}</strong> files</span>
        <span><strong>${s.sas.toLocaleString()}</strong> SAS &nbsp;·&nbsp;
              <strong>${s.data.toLocaleString()}</strong> data</span>
        <span>${fmtBytes(s.size)}</span></div>`;
      c.addEventListener('click',()=>filterByDir(dir));
      grid.appendChild(c);
    });
}

function buildTypeGrid(){
  const grid=document.getElementById('type-grid');
  Object.entries(TYPE_SUMMARY)
    .sort((a,b)=>b[1].count-a[1].count)
    .forEach(([type,s])=>{
      const c=document.createElement('div');
      c.className='ov-card';
      c.innerHTML=`<h3><span class="chip ${chipClass(type)}">${escH(type)}</span></h3>
        <div class="ct">
          <span><strong>${s.count.toLocaleString()}</strong> files</span>
          <span>${fmtBytes(s.size)}</span></div>`;
      grid.appendChild(c);
    });
}

document.addEventListener('DOMContentLoaded',()=>{
  VTABLES.sas.data      = SAS_FILES;
  VTABLES.sas.filtered  = SAS_FILES;
  VTABLES.data.data     = DATA_FILES;
  VTABLES.data.filtered = DATA_FILES;

  buildSidebar();
  buildOverview();
  buildTypeGrid();
  vtObserve('sas');
  vtObserve('data');

  // pre-load the overview vtable on first tab (sas is lazy)
  showTab('overview', null);
});
"""

# ── HTML builder ──────────────────────────────────────────────────────────────

def build_html(files, root, generated):
    sas_files  = [f for f in files if f["category"] == "SAS"]
    data_files = [f for f in files if f["category"] == "Data"]
    dir_summary  = build_dir_summary(files)
    type_summary = build_type_summary(files)

    total_size = sum(f["size"] for f in files)
    n_sas_prog = sum(1 for f in sas_files if f["ext"] == ".sas")
    n_sas_dat  = sum(1 for f in sas_files if f["ext"] == ".sas7bdat")

    sas_json  = json.dumps(sas_files,  ensure_ascii=False, separators=(',',':'))
    data_json = json.dumps(data_files, ensure_ascii=False, separators=(',',':'))
    dir_json  = json.dumps(dir_summary,  ensure_ascii=False, separators=(',',':'))
    type_json = json.dumps(type_summary, ensure_ascii=False, separators=(',',':'))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>Network Scan — ISDH</title>
<style>{CSS}</style>
<script>
const SAS_FILES   = {sas_json};
const DATA_FILES  = {data_json};
const DIR_SUMMARY = {dir_json};
const TYPE_SUMMARY= {type_json};
</script>
</head>
<body>
<div class="layout">

<!-- SIDEBAR -->
<div class="sidebar">
  <div class="sb-hdr">ITS Network Share<small>{esc(root)}</small></div>
  <div class="sb-search">
    <input placeholder="Filter folders…" oninput="filterSB(this.value)"/>
  </div>
  <div class="sb-body" id="sb-body"></div>
</div>

<!-- MAIN -->
<div class="main">
<div class="main-hdr">
  <h1>Network File Scan — ISDH</h1>
  <p class="sub">Root: <strong>{esc(root)}</strong> &nbsp;|&nbsp; Generated: {esc(generated)}</p>

  <div class="stats">
    <div class="sc" id="card-overview"  onclick="showTab('overview',null)">
      <div class="sc-n">{len(files):,}</div><div class="sc-l">Total Files</div></div>
    <div class="sc" id="card-sas"       onclick="showTab('sas',null)">
      <div class="sc-n" style="color:var(--acc)">{len(sas_files):,}</div><div class="sc-l">SAS Files</div></div>
    <div class="sc"                      onclick="showTab('sas',null)">
      <div class="sc-n" style="color:var(--acc)">{n_sas_prog:,}</div><div class="sc-l">SAS Programs</div></div>
    <div class="sc"                      onclick="showTab('sas',null)">
      <div class="sc-n" style="color:var(--grn)">{n_sas_dat:,}</div><div class="sc-l">SAS Datasets</div></div>
    <div class="sc" id="card-data"      onclick="showTab('data',null)">
      <div class="sc-n" style="color:var(--yel)">{len(data_files):,}</div><div class="sc-l">Data Files</div></div>
    <div class="sc">
      <div class="sc-n">{fmt_bytes(total_size)}</div><div class="sc-l">Total Size</div></div>
  </div>
</div>

  <div class="tabs">
    <div class="tab active" id="tab-overview" onclick="showTab('overview',this)">Overview</div>
    <div class="tab"        id="tab-sas"      onclick="showTab('sas',this)">SAS Files ({len(sas_files):,})</div>
    <div class="tab"        id="tab-data"     onclick="showTab('data',this)">Data Files ({len(data_files):,})</div>
  </div>

  <div class="content">

    <!-- OVERVIEW -->
    <div class="panel active" id="p-overview">
      <h2>Top Folders by File Count</h2>
      <div class="ov-grid" id="dir-grid"></div>
      <h2>Files by Type</h2>
      <div class="ov-grid" id="type-grid"></div>
    </div>

    <!-- SAS FILES -->
    <div class="panel" id="p-sas">
      <div class="srch">
        <input id="q-sas" placeholder="Search name, folder, or type…"
               oninput="vtFilter('sas',this.value)"/>
      </div>
      <div id="cnt-sas" class="row-counter"></div>
      <table id="tbl-sas">
        <thead><tr><th>File Name</th><th>Type</th><th>Folder</th><th>Size</th><th>Modified</th></tr></thead>
        <tbody></tbody>
      </table>
      <div id="sent-sas" style="height:1px;margin-top:8px"></div>
    </div>

    <!-- DATA FILES -->
    <div class="panel" id="p-data">
      <div class="srch">
        <input id="q-data" placeholder="Search name, folder, or type…"
               oninput="vtFilter('data',this.value)"/>
      </div>
      <div id="cnt-data" class="row-counter"></div>
      <table id="tbl-data">
        <thead><tr><th>File Name</th><th>Type</th><th>Folder</th><th>Size</th><th>Modified</th></tr></thead>
        <tbody></tbody>
      </table>
      <div id="sent-data" style="height:1px;margin-top:8px"></div>
    </div>

  </div><!-- /content -->
</div><!-- /main -->
</div><!-- /layout -->

<script>{JS}</script>
</body>
</html>"""

# ── main ──────────────────────────────────────────────────────────────────────

def main():
    start = datetime.now()
    files = scan(ROOT_PATH)

    if not files:
        print("No matching files found. Check the path and that the share is accessible.")
        return

    sas  = [f for f in files if f["category"] == "SAS"]
    data = [f for f in files if f["category"] == "Data"]
    print(f"\n  SAS files  : {len(sas):,}  ({sum(f['size'] for f in sas)/1e9:.2f} GB)")
    print(f"  Data files : {len(data):,}  ({sum(f['size'] for f in data)/1e9:.2f} GB)")
    print(f"  Scan time  : {(datetime.now()-start).seconds}s")

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    print("\nBuilding HTML…")
    html = build_html(files, ROOT_PATH, generated)

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Report saved to:\n  {OUT_FILE}")

if __name__ == "__main__":
    main()
