#!/usr/bin/env python3
"""
Azure SQL Data Warehouse Metadata Report
Collects schemas, tables (with distribution + index types), views, stored
procedures, and columns from a Dedicated SQL Pool.

Usage:
  python3 sql_dw_metadata_report.py --env dev
  python3 sql_dw_metadata_report.py --env prd
"""

import argparse
import json
import struct
import subprocess
from datetime import datetime

import pyodbc

# ── config ─────────────────────────────────────────────────────────────────────

ENVIRONMENTS = {
    "dev": {
        "server":   "zus1-idoh-dev-v2-sql-server",
        "database": "zus1-idoh-dev-v2-sql-dw",
        "label":    "DEV",
    },
    "prd": {
        "server":   "zus1-idoh-prd-v1-sql-server",
        "database": "zus1-idoh-prd-v1-sql-dw",
        "label":    "PRD",
    },
}
SUBSCRIPTION_ID = "57493fde-eff8-432f-8574-4f1281bd2ce3"

SYS_SCHEMAS = (
    "sys", "INFORMATION_SCHEMA", "guest", "db_owner", "db_accessadmin",
    "db_securityadmin", "db_ddladmin", "db_backupoperator", "db_datareader",
    "db_datawriter", "db_denydatareader", "db_denydatawriter",
)
SYS_IN     = "','".join(SYS_SCHEMAS)
SYS_FILTER = f"s.name NOT IN ('{SYS_IN}')"

# ── schema layer classification ─────────────────────────────────────────────────

def schema_layer(name):
    if name.startswith("SM_"):        return "source"
    if name.startswith("DM_"):        return "mart"
    if name.startswith("Reporting_"): return "reporting"
    if name.startswith("HUB_"):       return "hub"
    if name.endswith("_DBA"):         return "ops"
    return "other"

LAYER_META = {
    "source":    {"label": "SM — Source / Staging",  "color": "#fb923c"},
    "mart":      {"label": "DM — Data Mart",          "color": "#6c8eff"},
    "reporting": {"label": "Reporting",               "color": "#4ade80"},
    "hub":       {"label": "HUB — Hub",               "color": "#c084fc"},
    "ops":       {"label": "Operations / DBA",        "color": "#8892a4"},
    "other":     {"label": "Other",                   "color": "#22d3ee"},
}

# ── auth + connection ──────────────────────────────────────────────────────────

def get_conn(server, database):
    print("  Authenticating…", end="", flush=True)
    token_raw = subprocess.check_output(
        ["az", "account", "get-access-token",
         "--resource", "https://database.windows.net/",
         "--subscription", SUBSCRIPTION_ID,
         "--query", "accessToken", "-o", "tsv"],
        stderr=subprocess.DEVNULL,
    ).decode().strip()
    token_bytes  = token_raw.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
    conn_str = (
        f"Driver={{ODBC Driver 18 for SQL Server}};"
        f"Server={server}.database.windows.net,1433;"
        f"Database={database};"
        "Encrypt=yes;TrustServerCertificate=no;"
    )
    conn = pyodbc.connect(conn_str, attrs_before={1256: token_struct})
    print(" connected.")
    return conn

# ── queries ────────────────────────────────────────────────────────────────────

def fetch_schemas(cur):
    cur.execute(f"""
        SELECT s.name,
               COUNT(DISTINCT t.object_id) AS tbl_cnt,
               COUNT(DISTINCT v.object_id) AS view_cnt,
               COUNT(DISTINCT p.object_id) AS proc_cnt
        FROM sys.schemas s
        LEFT JOIN sys.tables     t ON s.schema_id = t.schema_id
        LEFT JOIN sys.views      v ON s.schema_id = v.schema_id
        LEFT JOIN sys.procedures p ON s.schema_id = p.schema_id
        WHERE {SYS_FILTER}
        GROUP BY s.name
        ORDER BY s.name
    """)
    return [
        {
            "name":        r[0],
            "table_count": r[1],
            "view_count":  r[2],
            "proc_count":  r[3],
            "layer":       schema_layer(r[0]),
        }
        for r in cur.fetchall()
    ]


def fetch_tables(cur):
    # sys.partitions.rows is a stale statistics estimate in Synapse Dedicated Pool
    # (commonly 1,000 for many tables). Row counts are fetched separately via
    # sys.dm_pdw_nodes_db_partition_stats and merged in collect().
    cur.execute(f"""
        SELECT
            s.name                            AS schema_name,
            t.name                            AS table_name,
            tdp.distribution_policy_desc      AS dist_type,
            ISNULL(dc.name, '')               AS dist_column,
            ISNULL(i.type_desc, 'HEAP')       AS index_type
        FROM sys.tables t
        JOIN sys.schemas s
            ON t.schema_id = s.schema_id
        JOIN sys.pdw_table_distribution_properties tdp
            ON t.object_id = tdp.object_id
        LEFT JOIN sys.pdw_column_distribution_properties cdp
            ON t.object_id = cdp.object_id AND cdp.distribution_ordinal = 1
        LEFT JOIN sys.columns dc
            ON cdp.object_id = dc.object_id AND cdp.column_id = dc.column_id
        LEFT JOIN (
            SELECT object_id, MAX(index_id) AS top_idx
            FROM sys.indexes
            WHERE index_id IN (0, 1)
            GROUP BY object_id
        ) best ON best.object_id = t.object_id
        LEFT JOIN sys.indexes i
            ON i.object_id = t.object_id AND i.index_id = best.top_idx
        WHERE {SYS_FILTER}
        ORDER BY s.name, t.name
    """)
    return [
        {
            "schema":      r[0],
            "name":        r[1],
            "dist_type":   (r[2] or "").replace("_", " ").title(),
            "dist_column": r[3] or "",
            "index_type":  r[4] or "HEAP",
            "row_count":   0,   # filled by fetch_row_counts in collect()
            "layer":       schema_layer(r[0]),
        }
        for r in cur.fetchall()
    ]


def fetch_row_counts(cur):
    """Accurate per-table row counts from the PDW node partition stats DMV."""
    cur.execute("""
        SELECT s.name AS schema_name, t.name AS table_name,
               SUM(nps.row_count) AS row_count
        FROM sys.tables t
        JOIN sys.schemas s ON t.schema_id = s.schema_id
        JOIN sys.pdw_table_mappings tm ON tm.object_id = t.object_id
        JOIN sys.pdw_nodes_tables nt ON nt.name = tm.physical_name
        JOIN sys.dm_pdw_nodes_db_partition_stats nps
            ON nps.object_id        = nt.object_id
            AND nps.pdw_node_id     = nt.pdw_node_id
            AND nps.distribution_id = nt.distribution_id
            AND nps.index_id < 2
        GROUP BY s.name, t.name
    """)
    return {(r[0], r[1]): r[2] for r in cur.fetchall()}


def fetch_views(cur):
    cur.execute(f"""
        SELECT s.name, v.name, ISNULL(m.definition, '')
        FROM sys.views      v
        JOIN sys.schemas    s ON v.schema_id = s.schema_id
        JOIN sys.sql_modules m ON v.object_id = m.object_id
        WHERE {SYS_FILTER}
        ORDER BY s.name, v.name
    """)
    return [
        {"schema": r[0], "name": r[1], "definition": r[2], "layer": schema_layer(r[0])}
        for r in cur.fetchall()
    ]


def fetch_procs(cur):
    cur.execute(f"""
        SELECT s.name, p.name, ISNULL(m.definition, '')
        FROM sys.procedures  p
        JOIN sys.schemas     s ON p.schema_id = s.schema_id
        JOIN sys.sql_modules m ON p.object_id = m.object_id
        WHERE {SYS_FILTER}
        ORDER BY s.name, p.name
    """)
    return [
        {"schema": r[0], "name": r[1], "definition": r[2], "layer": schema_layer(r[0])}
        for r in cur.fetchall()
    ]


def fetch_columns(cur):
    cur.execute(f"""
        SELECT
            s.name          AS schema_name,
            o.name          AS object_name,
            o.type          AS obj_type,
            c.column_id,
            c.name          AS col_name,
            tp.name         AS base_type,
            c.max_length,
            c.precision,
            c.scale,
            c.is_nullable,
            c.is_identity,
            ISNULL(df.definition, '') AS default_val
        FROM sys.columns c
        JOIN sys.objects  o  ON c.object_id      = o.object_id
        JOIN sys.schemas  s  ON o.schema_id       = s.schema_id
        JOIN sys.types    tp ON c.user_type_id    = tp.user_type_id
        LEFT JOIN sys.default_constraints df
            ON c.default_object_id = df.object_id
        WHERE o.type IN ('U','V')
          AND {SYS_FILTER}
        ORDER BY s.name, o.name, c.column_id
    """)
    cols = []
    for r in cur.fetchall():
        btype = r[5]; ml = r[6]; prec = r[7]; scale = r[8]
        if btype in ("nvarchar", "nchar"):
            dtype = f"{btype}({'MAX' if ml == -1 else ml // 2})"
        elif btype in ("varchar", "char", "binary", "varbinary"):
            dtype = f"{btype}({'MAX' if ml == -1 else ml})"
        elif btype in ("decimal", "numeric"):
            dtype = f"{btype}({prec},{scale})"
        else:
            dtype = btype
        cols.append({
            "schema":      r[0],
            "object":      r[1],
            "obj_type":    "table" if r[2].strip() == "U" else "view",
            "col_id":      r[3],
            "name":        r[4],
            "data_type":   dtype,
            "nullable":    bool(r[9]),
            "is_identity": bool(r[10]),
            "default":     r[11],
            "layer":       schema_layer(r[0]),
        })
    return cols


def collect(env_cfg):
    conn = get_conn(env_cfg["server"], env_cfg["database"])
    cur  = conn.cursor()

    print("  Fetching schemas…",  end="", flush=True)
    schemas = fetch_schemas(cur);  print(f" {len(schemas)}")

    print("  Fetching tables…",   end="", flush=True)
    tables  = fetch_tables(cur);   print(f" {len(tables)}")

    print("  Fetching row counts…", end="", flush=True)
    rc_map  = fetch_row_counts(cur); print(f" {len(rc_map)}")
    for t in tables:
        t["row_count"] = rc_map.get((t["schema"], t["name"]), 0)

    print("  Fetching views…",    end="", flush=True)
    views   = fetch_views(cur);    print(f" {len(views)}")

    print("  Fetching procs…",    end="", flush=True)
    procs   = fetch_procs(cur);    print(f" {len(procs)}")

    print("  Fetching columns…",  end="", flush=True)
    columns = fetch_columns(cur);  print(f" {len(columns)}")

    conn.close()
    return schemas, tables, views, procs, columns

# ── CSS ───────────────────────────────────────────────────────────────────────

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
.sidebar{width:240px;background:var(--sur);border-right:1px solid var(--brd);
  display:flex;flex-direction:column;flex-shrink:0;overflow:hidden}
.sb-hdr{padding:14px 16px 10px;font-size:13px;font-weight:700;border-bottom:1px solid var(--brd);
  flex-shrink:0}
.sb-hdr small{display:block;font-size:10px;color:var(--mut);font-weight:400;margin-top:2px}
.sb-body{overflow-y:auto;flex:1;padding:8px 0}
.sb-section{font-size:10px;font-weight:700;color:var(--mut);padding:10px 14px 4px;
  text-transform:uppercase;letter-spacing:.5px}
.sb-item{padding:5px 14px;cursor:pointer;font-size:12px;border-left:2px solid transparent;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--txt)}
.sb-item:hover{background:var(--sur2)}
.sb-item.active{background:var(--sur2);border-left-color:var(--acc);color:var(--acc)}
.sb-badge{float:right;font-size:10px;color:var(--mut)}

/* main */
.main{flex:1;display:flex;flex-direction:column;overflow:hidden}
.main-hdr{padding:14px 24px 10px;border-bottom:1px solid var(--brd);flex-shrink:0}
.main-hdr h1{font-size:17px;font-weight:800}
.sub{font-size:11px;color:var(--mut);margin-top:3px}

/* stats */
.stats{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.sc{background:var(--sur);border:1px solid var(--brd);border-radius:8px;
  padding:8px 14px;cursor:pointer;min-width:70px;text-align:center;transition:border-color .15s}
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
h2{font-size:13px;font-weight:700;margin:14px 0 8px}

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
  border-bottom:2px solid var(--brd);position:sticky;top:0;z-index:1}
td{padding:6px 10px;border-bottom:1px solid var(--brd);vertical-align:top}
th[title]{cursor:help;border-bottom:2px dashed var(--mut);text-decoration:none}
tr:hover td{background:var(--sur)}
.mono{font-family:'Cascadia Code','Fira Code',monospace;font-size:11px}

/* chips */
.chip{display:inline-block;font-size:10px;padding:2px 7px;border-radius:3px;font-weight:700;white-space:nowrap}
.chip-hash  {background:#1a3a2a;color:#4ade80}
.chip-rr    {background:#1e2a4a;color:#6c8eff}
.chip-rep   {background:#2d1e5f;color:#c084fc}
.chip-ccs   {background:#1a3a2a;color:#4ade80}
.chip-heap  {background:#2a2a0a;color:#fbbf24}
.chip-clust {background:#1e2a4a;color:#6c8eff}
.chip-src   {background:#3a2010;color:#fb923c}
.chip-mart  {background:#1e2a4a;color:#6c8eff}
.chip-rep2  {background:#1a3a2a;color:#4ade80}
.chip-hub   {background:#2d1e5f;color:#c084fc}
.chip-ops   {background:var(--sur2);color:var(--mut)}
.chip-other {background:var(--sur2);color:var(--cyn)}
.chip-null  {background:var(--sur2);color:var(--mut)}
.chip-notnull{background:#1a3a2a;color:#4ade80}
.chip-id    {background:#2d1e5f;color:#c084fc}
.chip-rg    {font-size:10px;padding:2px 6px;border-radius:3px;background:var(--sur2);color:var(--mut)}

/* overview grid */
.ov-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px;margin-top:10px}
.ov-card{background:var(--sur);border:1px solid var(--brd);border-radius:8px;padding:12px 14px;cursor:pointer;transition:border-color .15s}
.ov-card:hover{border-color:var(--acc)}
.ov-card h3{font-size:12px;font-weight:700;margin-bottom:6px}
.ov-card .kv{font-size:11px;color:var(--mut);display:flex;flex-direction:column;gap:3px}
.ov-card .kv b{color:var(--txt)}

/* layer bar */
.layer-bar{display:flex;gap:12px;flex-wrap:wrap;margin:10px 0}
.layer-pill{display:flex;align-items:center;gap:6px;font-size:11px}
.layer-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}

/* definition block */
.def-toggle{cursor:pointer;color:var(--acc);font-size:11px;user-select:none}
.def-block{display:none;margin-top:6px;background:var(--sur2);border:1px solid var(--brd);
  border-radius:4px;padding:10px;font-family:'Cascadia Code','Fira Code',monospace;
  font-size:11px;white-space:pre-wrap;word-break:break-all;max-height:300px;overflow-y:auto;color:var(--txt)}
.def-block.open{display:block}

/* plain-English description */
.pe-desc{background:#1a2a1a;border-left:3px solid var(--grn);border-radius:0 4px 4px 0;
  padding:7px 10px;margin-bottom:6px;font-size:12px;color:#c8e6c8;line-height:1.5}
.pe-label{display:inline-block;font-size:9px;font-weight:700;text-transform:uppercase;
  letter-spacing:.5px;color:var(--grn);margin-bottom:3px;margin-right:6px}

/* view cards */
.pc{background:var(--sur);border:1px solid var(--brd);border-radius:8px;padding:13px;margin-bottom:9px}
.pc h3{font-size:13px;margin:0}
.pc .pm{font-size:11px;color:var(--mut);margin-bottom:7px;margin-top:3px}
.pc details summary{cursor:pointer;color:var(--acc);font-size:12px;user-select:none}
.view-desc{font-size:12px;color:var(--txt);line-height:1.7;margin:8px 0 2px;padding:0}

/* help fab */
.help-fab{position:fixed;bottom:22px;right:22px;width:38px;height:38px;
  border-radius:50%;background:var(--acc);color:#fff;font-size:17px;font-weight:700;
  display:flex;align-items:center;justify-content:center;
  text-decoration:none;z-index:9999;box-shadow:0 2px 10px rgba(0,0,0,.5);
  opacity:.8;transition:opacity .15s;line-height:1}
.help-fab:hover{opacity:1}

/* row count */
.row-count{color:var(--mut);font-size:11px}
.row-count b{color:var(--txt)}

/* copy-code button */
.code-wrap{position:relative;margin-top:8px}
.code-wrap pre{margin-top:0!important}
.copy-btn{position:absolute;top:7px;right:7px;z-index:2;
  display:inline-flex;align-items:center;gap:4px;
  background:var(--sur);border:1px solid var(--brd);border-radius:5px;
  padding:3px 8px;cursor:pointer;color:var(--mut);
  font-size:10px;font-weight:700;font-family:inherit;line-height:1.4;
  opacity:0;transition:opacity .15s,border-color .15s,color .15s}
.code-wrap:hover .copy-btn{opacity:1}
.copy-btn:hover{border-color:var(--acc);color:var(--acc);background:var(--sur2)}
.copy-btn.copied{border-color:var(--grn)!important;color:var(--grn)!important;opacity:1!important}
"""

# ── JS ─────────────────────────────────────────────────────────────────────────

JS = """
const DATA = __DATA__;

function copyCode(btn){
  const pre = btn.closest('.code-wrap').querySelector('pre');
  navigator.clipboard.writeText(pre.textContent).then(()=>{
    btn.classList.add('copied');
    btn.querySelector('.copy-lbl').textContent='Copied!';
    setTimeout(()=>{btn.classList.remove('copied');btn.querySelector('.copy-lbl').textContent='Copy';},2000);
  }).catch(()=>{});
}

function esc(s){
  if(s==null) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── layer chip ─────────────────────────────────────────────────────────────────
const LAYER_CHIP = {
  source:    '<span class="chip chip-src">SM</span>',
  mart:      '<span class="chip chip-mart">DM</span>',
  reporting: '<span class="chip chip-rep2">RPT</span>',
  hub:       '<span class="chip chip-hub">HUB</span>',
  ops:       '<span class="chip chip-ops">OPS</span>',
  other:     '<span class="chip chip-other">—</span>',
};
const LAYER_COLORS = __LAYER_COLORS__;

function layerChip(l){ return LAYER_CHIP[l]||''; }

function distChip(d){
  if(d==='Hash')        return '<span class="chip chip-hash">HASH</span>';
  if(d==='Round Robin') return '<span class="chip chip-rr">ROUND ROBIN</span>';
  if(d==='Replicate')   return '<span class="chip chip-rep">REPLICATE</span>';
  return `<span class="chip chip-ops">${esc(d)}</span>`;
}
function idxChip(i){
  if(i==='CLUSTERED COLUMNSTORE') return '<span class="chip chip-ccs">COLUMNSTORE</span>';
  if(i==='HEAP')                  return '<span class="chip chip-heap">HEAP</span>';
  if(i==='CLUSTERED')             return '<span class="chip chip-clust">CLUSTERED</span>';
  return `<span class="chip chip-ops">${esc(i)}</span>`;
}
function fmtRows(n){
  if(n==null||n===0) return '<span class="mut">0</span>';
  return `<b>${Number(n).toLocaleString()}</b>`;
}

// ── sidebar + schema filter ────────────────────────────────────────────────────
let activeSchema = '__all__';
function sbSelect(name, el){
  document.querySelectorAll('.sb-item').forEach(e=>e.classList.remove('active'));
  if(el) el.classList.add('active');
  activeSchema = name;
  // Individual schema click → jump to Tables; layer/all → re-render current panel
  if(!name.startsWith('__')){
    showTab('tables');
  } else {
    const active = document.querySelector('.panel.active');
    if(active) renderPanel(active.id.replace('p-',''));
  }
}
function matchSchema(item){
  if(activeSchema==='__all__') return true;
  if(activeSchema.startsWith('__layer__')){
    return item.layer === activeSchema.replace('__layer__','');
  }
  return item.schema === activeSchema || item.name === activeSchema;
}

// ── tabs ───────────────────────────────────────────────────────────────────────
function showTab(id){
  document.querySelectorAll('.tab,.panel,.sc').forEach(e=>e.classList.remove('active'));
  document.getElementById('tab-'+id)?.classList.add('active');
  document.getElementById('p-'+id)?.classList.add('active');
  document.getElementById('card-'+id)?.classList.add('active');
  renderPanel(id);
}
function renderPanel(id){
  if(id==='overview')  renderOverview();
  if(id==='schemas')   renderSchemas();
  if(id==='tables')    renderTables();
  if(id==='views')     renderViews();
  if(id==='procs')     renderProcs();
  if(id==='columns')   renderColumns();
}

// ── OVERVIEW ──────────────────────────────────────────────────────────────────
function renderOverview(){
  // dist breakdown
  const distCounts = {};
  DATA.tables.forEach(t=>{ distCounts[t.dist_type]=(distCounts[t.dist_type]||0)+1; });
  document.getElementById('ov-dist').innerHTML = Object.entries(distCounts)
    .sort((a,b)=>b[1]-a[1])
    .map(([k,v])=>`<div style="display:flex;align-items:center;gap:8px;margin:3px 0">
      ${distChip(k)}<span class="mut">${v} tables</span></div>`).join('');

  // index breakdown
  const idxCounts = {};
  DATA.tables.forEach(t=>{ idxCounts[t.index_type]=(idxCounts[t.index_type]||0)+1; });
  document.getElementById('ov-idx').innerHTML = Object.entries(idxCounts)
    .sort((a,b)=>b[1]-a[1])
    .map(([k,v])=>`<div style="display:flex;align-items:center;gap:8px;margin:3px 0">
      ${idxChip(k)}<span class="mut">${v} tables</span></div>`).join('');

  // layer breakdown
  const layerCounts = {};
  DATA.schemas.forEach(s=>{ layerCounts[s.layer]=(layerCounts[s.layer]||0)+1; });
  const layerOrder = ['source','mart','reporting','hub','ops','other'];
  document.getElementById('ov-layers').innerHTML = layerOrder
    .filter(l=>layerCounts[l])
    .map(l=>`<div class="layer-pill">
      <div class="layer-dot" style="background:${LAYER_COLORS[l]||'#888'}"></div>
      <span><b>${layerCounts[l]}</b> <span class="mut">${__LAYER_LABELS__[l]||l}</span></span>
    </div>`).join('');

  // top tables by row count
  const topTables = [...DATA.tables].sort((a,b)=>b.row_count-a.row_count).slice(0,20);
  document.getElementById('ov-top-tables').innerHTML = topTables.map(t=>`
    <tr>
      <td>${layerChip(t.layer)}</td>
      <td class="mono">${esc(t.schema)}</td>
      <td class="mono"><b>${esc(t.name)}</b></td>
      <td>${distChip(t.dist_type)}${t.dist_column?` <span class="mut">(${esc(t.dist_column)})</span>`:''}</td>
      <td>${idxChip(t.index_type)}</td>
      <td style="text-align:right">${fmtRows(t.row_count)}</td>
    </tr>`).join('');
}

// ── SCHEMAS ────────────────────────────────────────────────────────────────────
function renderSchemas(){
  const q=(document.getElementById('schema-search')?.value||'').toLowerCase();
  document.getElementById('schema-search').oninput=renderSchemas;
  let schemas=DATA.schemas;
  if(activeSchema.startsWith('__layer__')){
    const l=activeSchema.replace('__layer__','');
    schemas=schemas.filter(s=>s.layer===l);
  }
  if(q) schemas=schemas.filter(s=>s.name.toLowerCase().includes(q));
  document.getElementById('schema-tbody').innerHTML = schemas.map(s=>`<tr>
    <td>${layerChip(s.layer)}</td>
    <td class="mono"><b>${esc(s.name)}</b></td>
    <td style="text-align:right">${s.table_count||'<span class="mut">0</span>'}</td>
    <td style="text-align:right">${s.view_count||'<span class="mut">0</span>'}</td>
    <td style="text-align:right">${s.proc_count||'<span class="mut">0</span>'}</td>
  </tr>`).join('') || '<tr><td colspan="5" class="mut" style="padding:12px">No schemas.</td></tr>';
  document.getElementById('schema-count').textContent=`${schemas.length} schemas`;
}

// ── TABLES ─────────────────────────────────────────────────────────────────────
function renderTables(){
  const q=(document.getElementById('tbl-search')?.value||'').toLowerCase();
  const distF=(document.getElementById('tbl-dist-sel')?.value||'');
  const idxF=(document.getElementById('tbl-idx-sel')?.value||'');
  document.getElementById('tbl-search').oninput=renderTables;
  document.getElementById('tbl-dist-sel').onchange=renderTables;
  document.getElementById('tbl-idx-sel').onchange=renderTables;

  let tables=DATA.tables.filter(matchSchema);
  if(distF) tables=tables.filter(t=>t.dist_type===distF);
  if(idxF)  tables=tables.filter(t=>t.index_type===idxF);
  if(q)     tables=tables.filter(t=>t.name.toLowerCase().includes(q)||t.schema.toLowerCase().includes(q)||t.dist_column.toLowerCase().includes(q));

  document.getElementById('tbl-tbody').innerHTML = tables.map(t=>`<tr>
    <td>${layerChip(t.layer)}</td>
    <td class="mono mut">${esc(t.schema)}</td>
    <td class="mono"><b>${esc(t.name)}</b></td>
    <td>${distChip(t.dist_type)}${t.dist_column?` <span class="mono mut" style="font-size:10px">(${esc(t.dist_column)})</span>`:''}</td>
    <td>${idxChip(t.index_type)}</td>
    <td style="text-align:right">${fmtRows(t.row_count)}</td>
  </tr>`).join('') || '<tr><td colspan="6" class="mut" style="padding:12px">No tables.</td></tr>';
  document.getElementById('tbl-count').textContent=`${tables.length} of ${DATA.tables.length}`;
}

// ── VIEWS ──────────────────────────────────────────────────────────────────────
function renderViews(){
  const q=(document.getElementById('view-search')?.value||'').toLowerCase();
  document.getElementById('view-search').oninput=renderViews;
  let views=DATA.views.filter(matchSchema);
  if(q) views=views.filter(v=>v.name.toLowerCase().includes(q)||v.schema.toLowerCase().includes(q));
  const cards=views.map(v=>{
    const descHtml=v.desc
      ? `<details><summary>Plain English Summary</summary><p class="view-desc">${esc(v.desc)}</p></details>`
      : '';
    return `<div class="pc" data-name="${esc(v.schema).toLowerCase()}.${esc(v.name).toLowerCase()}">
      <div style="display:flex;align-items:center;gap:8px">${layerChip(v.layer)}<h3>${esc(v.schema)}.${esc(v.name)}</h3></div>
      <div class="pm">VIEW</div>
      ${descHtml}
      <details><summary>Show / hide definition</summary>
      <div class="code-wrap"><button class="copy-btn" onclick="copyCode(this)" title="Copy to clipboard"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg><span class="copy-lbl">Copy</span></button>
      <pre style="margin-top:0;background:var(--sur2);border:1px solid var(--brd);border-radius:4px;padding:10px;font-size:11px;white-space:pre-wrap;word-break:break-all;max-height:300px;overflow-y:auto">${esc(v.definition||'-- definition not available')}</pre>
      </div></details></div>`;
  }).join('')||'<p style="color:var(--mut)">No views.</p>';
  document.getElementById('view-cards').innerHTML=cards;
  document.getElementById('view-count').textContent=`${views.length} of ${DATA.views.length}`;
}

// ── STORED PROCEDURES ──────────────────────────────────────────────────────────
function renderProcs(){
  const q=(document.getElementById('proc-search')?.value||'').toLowerCase();
  document.getElementById('proc-search').oninput=renderProcs;
  let procs=DATA.procs.filter(matchSchema);
  if(q) procs=procs.filter(p=>p.name.toLowerCase().includes(q)||p.schema.toLowerCase().includes(q));
  const cards=procs.map(p=>{
    const descHtml=p.desc
      ? `<details><summary>Plain English Summary</summary><p class="view-desc">${esc(p.desc)}</p></details>`
      : '';
    return `<div class="pc" data-name="${esc(p.schema).toLowerCase()}.${esc(p.name).toLowerCase()}">
      <div style="display:flex;align-items:center;gap:8px">${layerChip(p.layer)}<h3>${esc(p.schema)}.${esc(p.name)}</h3></div>
      <div class="pm">SQL_STORED_PROCEDURE</div>
      ${descHtml}
      <details><summary>Show / hide definition</summary>
      <div class="code-wrap"><button class="copy-btn" onclick="copyCode(this)" title="Copy to clipboard"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg><span class="copy-lbl">Copy</span></button>
      <pre style="margin-top:0;background:var(--sur2);border:1px solid var(--brd);border-radius:4px;padding:10px;font-size:11px;white-space:pre-wrap;word-break:break-all;max-height:300px;overflow-y:auto">${esc(p.definition||'-- definition not available')}</pre>
      </div></details></div>`;
  }).join('')||'<p style="color:var(--mut)">No stored procedures.</p>';
  document.getElementById('proc-cards').innerHTML=cards;
  document.getElementById('proc-count').textContent=`${procs.length} of ${DATA.procs.length}`;
}

// ── COLUMNS ────────────────────────────────────────────────────────────────────
const PAGE_SIZE = 500;
let colPage = 0;
let colFiltered = [];

function renderColumns(){
  const q=(document.getElementById('col-search')?.value||'').toLowerCase();
  const typeF=(document.getElementById('col-type-sel')?.value||'');
  document.getElementById('col-search').oninput=()=>{ colPage=0; renderColumns(); };
  document.getElementById('col-type-sel').onchange=()=>{ colPage=0; renderColumns(); };

  colFiltered=DATA.columns.filter(matchSchema);
  if(typeF) colFiltered=colFiltered.filter(c=>c.obj_type===typeF);
  if(q)     colFiltered=colFiltered.filter(c=>
    c.name.toLowerCase().includes(q)||
    c.object.toLowerCase().includes(q)||
    c.schema.toLowerCase().includes(q)||
    c.data_type.toLowerCase().includes(q)
  );

  renderColPage();
}
function renderColPage(){
  const start=colPage*PAGE_SIZE;
  const page=colFiltered.slice(start, start+PAGE_SIZE);
  document.getElementById('col-tbody').innerHTML=page.map(c=>`<tr>
    <td>${layerChip(c.layer)}</td>
    <td class="mono mut">${esc(c.schema)}</td>
    <td class="mono">${esc(c.object)}</td>
    <td class="mono"><b>${esc(c.name)}</b>${c.is_identity?' <span class="chip chip-id">IDENTITY</span>':''}</td>
    <td class="mono">${esc(c.data_type)}</td>
    <td>${c.nullable?'<span class="chip chip-null">NULL</span>':'<span class="chip chip-notnull">NOT NULL</span>'}</td>
    <td class="mono mut" style="font-size:10px">${esc(c.default||'')}</td>
  </tr>`).join('')||'<tr><td colspan="7" class="mut" style="padding:12px">No columns.</td></tr>';

  const total=colFiltered.length;
  const pages=Math.ceil(total/PAGE_SIZE);
  document.getElementById('col-count').textContent=`${total.toLocaleString()} columns (page ${colPage+1}/${pages||1})`;
  document.getElementById('col-prev').disabled=colPage===0;
  document.getElementById('col-next').disabled=colPage>=pages-1;
}
function colPrev(){ if(colPage>0){ colPage--; renderColPage(); } }
function colNext(){ const pages=Math.ceil(colFiltered.length/PAGE_SIZE); if(colPage<pages-1){ colPage++; renderColPage(); } }

function toggleDef(id){
  const el=document.getElementById(id);
  const tog=el.previousElementSibling;
  el.classList.toggle('open');
  tog.textContent=el.classList.contains('open')?'▼ Hide SQL':'▶ Show SQL';
}

document.addEventListener('DOMContentLoaded',()=>showTab('overview'));
"""

# ── HTML builder ───────────────────────────────────────────────────────────────

def esc(s):
    if s is None: return ""
    return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")


def build_html(env_cfg, schemas, tables, views, procs, columns, generated):
    import os as _os
    env_label = env_cfg["label"]
    server    = env_cfg["server"]
    database  = env_cfg["database"]

    n_schemas = len(schemas)
    n_tables  = len(tables)
    n_views   = len(views)
    n_procs   = len(procs)
    n_cols    = len(columns)

    # plain-English descriptions (shared with synapse report — same schemas/objects)
    _view_desc_file = "/home/thedavidporter/view_descriptions.json"
    _proc_desc_file = "/home/thedavidporter/proc_descriptions.json"
    view_descriptions: dict = {}
    proc_descriptions: dict = {}
    if _os.path.exists(_view_desc_file):
        with open(_view_desc_file, encoding="utf-8") as _f:
            view_descriptions = json.load(_f)
    if _os.path.exists(_proc_desc_file):
        with open(_proc_desc_file, encoding="utf-8") as _f:
            proc_descriptions = json.load(_f)

    # attach descriptions to each object before JSON serialisation
    for v in views:
        v["desc"] = view_descriptions.get(f"{v['schema']}||{v['name']}", "")
    for p in procs:
        p["desc"] = proc_descriptions.get(f"{p['schema']}||{p['name']}", "")

    # sidebar — grouped by layer
    layer_order = ["source", "mart", "reporting", "hub", "ops", "other"]
    sb_items = ""
    for layer in layer_order:
        layer_schemas = [s for s in schemas if s["layer"] == layer]
        if not layer_schemas:
            continue
        meta = LAYER_META[layer]
        sb_items += (
            f'<div class="sb-section" style="cursor:pointer;color:{meta["color"]}" '
            f'onclick="sbSelect(\'__layer__{layer}\',this)">'
            f'{meta["label"]} ({len(layer_schemas)})</div>'
        )
        for s in layer_schemas:
            badge = ""
            if s["table_count"]:
                badge = f'<span class="sb-badge">{s["table_count"]}t</span>'
            sb_items += (
                f'<div class="sb-item" onclick="sbSelect(\'{s["name"]}\',this)">'
                f'{esc(s["name"])}{badge}</div>'
            )

    data_json = json.dumps({
        "schemas": schemas,
        "tables":  tables,
        "views":   views,
        "procs":   procs,
        "columns": columns,
    }, ensure_ascii=False, default=str)

    layer_colors_json = json.dumps({k: v["color"] for k, v in LAYER_META.items()})
    layer_labels_json = json.dumps({k: v["label"] for k, v in LAYER_META.items()})

    js = (JS
          .replace("__DATA__", data_json)
          .replace("__LAYER_COLORS__", layer_colors_json)
          .replace("__LAYER_LABELS__", layer_labels_json))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>SQL DW Metadata — {esc(env_label)}</title>
<style>{CSS}</style>
</head>
<body>
<a href="help.html" class="help-fab" title="Help &amp; Guide">?</a>
<div class="layout">

<!-- SIDEBAR -->
<div class="sidebar">
  <div class="sb-hdr">SQL Data Warehouse<small>{esc(env_label)} — {esc(database)}</small></div>
  <div class="sb-body">
    <div class="sb-section">Filter</div>
    <div class="sb-item active" onclick="sbSelect('__all__',this)">All Schemas</div>
    {sb_items}
  </div>
</div>

<!-- MAIN -->
<div class="main">
  <div class="main-hdr">
    <h1>Azure SQL Data Warehouse — {esc(env_label)}</h1>
    <p class="sub">{esc(server)}.database.windows.net &nbsp;·&nbsp; {esc(database)}</p>
    <p class="sub">Generated: <span id="gen-ts" data-ts="{esc(generated)}">&#x21BB; {esc(generated)}</span><script>(function(){{var s=document.getElementById(\'gen-ts\'),h=(Date.now()-new Date(s.dataset.ts.replace(\' \',\'T\')))/36e5;s.style.color=h<25?'var(--grn)':h<168?'var(--yel)':'var(--red)';s.style.fontWeight='700';}})();</script></p>

    <div class="stats">
      <div class="sc" id="card-overview"  onclick="showTab('overview')"  title="Schemas are logical namespaces that group related tables, views, and procedures by business domain or team. Common patterns: dbo (default), stg (staging), mart (data mart), ref (reference/lookup data).">
        <div class="sc-n">{n_schemas}</div><div class="sc-l">Schemas</div></div>
      <div class="sc" id="card-tables"   onclick="showTab('tables')"    title="Tables are the physical data storage objects in the Azure SQL Database. Unlike Synapse dedicated pools, Azure SQL tables use standard row-based storage with clustered indexes — no distribution keys or columnstore by default.">
        <div class="sc-n" style="color:var(--acc)">{n_tables}</div><div class="sc-l">Tables</div></div>
      <div class="sc" id="card-views"    onclick="showTab('views')"     title="Views are virtual tables defined by a saved SELECT query — they store no data themselves. Often used to join across schemas, hide complexity from BI tools, or expose a stable interface when the underlying table structure changes.">
        <div class="sc-n" style="color:var(--cyn)">{n_views}</div><div class="sc-l">Views</div></div>
      <div class="sc" id="card-procs"    onclick="showTab('procs')"     title="Stored procedures encapsulate reusable T-SQL logic — ETL transformations, aggregations, data loads, or business rules. ADF pipelines call these via SqlServerStoredProcedure activities to perform work inside the database rather than moving data out first.">
        <div class="sc-n" style="color:var(--pur)">{n_procs}</div><div class="sc-l">Stored Procs</div></div>
      <div class="sc" id="card-columns"  onclick="showTab('columns')"   title="Total number of columns across all tables in this database. Click to search and browse every column name, data type, nullability, and which table it belongs to.">
        <div class="sc-n" style="color:var(--mut)">{n_cols:,}</div><div class="sc-l">Columns</div></div>
    </div>
  </div>

  <div class="tabs">
    <div class="tab" id="tab-overview" onclick="showTab('overview')">Overview</div>
    <div class="tab" id="tab-schemas"  onclick="showTab('schemas')">Schemas ({n_schemas})</div>
    <div class="tab" id="tab-tables"   onclick="showTab('tables')">Tables ({n_tables})</div>
    <div class="tab" id="tab-views"    onclick="showTab('views')">Views ({n_views})</div>
    <div class="tab" id="tab-procs"    onclick="showTab('procs')">Stored Procedures ({n_procs})</div>
    <div class="tab" id="tab-columns"  onclick="showTab('columns')">Columns ({n_cols:,})</div>
  </div>

  <div class="content">

    <!-- OVERVIEW -->
    <div class="panel" id="p-overview">
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px;margin-bottom:20px">

        <div class="ov-card">
          <h3>Schema Layers</h3>
          <div id="ov-layers" class="layer-bar"></div>
        </div>

        <div class="ov-card">
          <h3>Table Distributions</h3>
          <div id="ov-dist"></div>
        </div>

        <div class="ov-card">
          <h3>Index Types</h3>
          <div id="ov-idx"></div>
        </div>

      </div>

      <h2>Top 20 Tables by Row Count</h2>
      <table>
        <thead><tr>
          <th title="Schema naming layer — SM_* = Source/Staging · DM_* = Data Mart · Reporting_* = Reporting · HUB_* = Hub · *_DBA = Operations · — = Other (no recognized prefix)">Layer</th><th>Schema</th><th>Table</th>
          <th>Distribution</th><th>Index</th><th>Row Count</th>
        </tr></thead>
        <tbody id="ov-top-tables"></tbody>
      </table>
    </div>

    <!-- SCHEMAS -->
    <div class="panel" id="p-schemas">
      <div class="filter-row">
        <input id="schema-search" placeholder="Search schemas…"/>
        <span id="schema-count" class="mut"></span>
      </div>
      <table>
        <thead><tr>
          <th title="Schema naming layer — SM_* = Source/Staging · DM_* = Data Mart · Reporting_* = Reporting · HUB_* = Hub · *_DBA = Operations · — = Other (no recognized prefix)">Layer</th><th>Schema</th><th>Tables</th><th>Views</th><th>Procs</th>
        </tr></thead>
        <tbody id="schema-tbody"></tbody>
      </table>
    </div>

    <!-- TABLES -->
    <div class="panel" id="p-tables">
      <div class="filter-row">
        <input id="tbl-search" placeholder="Search tables…"/>
        <select id="tbl-dist-sel">
          <option value="">All distributions</option>
          <option value="Hash">Hash</option>
          <option value="Round Robin">Round Robin</option>
          <option value="Replicate">Replicate</option>
        </select>
        <select id="tbl-idx-sel">
          <option value="">All index types</option>
          <option value="CLUSTERED COLUMNSTORE">Clustered Columnstore</option>
          <option value="HEAP">Heap</option>
          <option value="CLUSTERED">Clustered</option>
        </select>
        <span id="tbl-count" class="mut"></span>
      </div>
      <table>
        <thead><tr>
          <th title="Schema naming layer — SM_* = Source/Staging · DM_* = Data Mart · Reporting_* = Reporting · HUB_* = Hub · *_DBA = Operations · — = Other (no recognized prefix)">Layer</th><th>Schema</th><th>Table</th>
          <th>Distribution</th><th>Index</th><th>Row Count</th>
        </tr></thead>
        <tbody id="tbl-tbody"></tbody>
      </table>
    </div>

    <!-- VIEWS -->
    <div class="panel" id="p-views">
      <div class="filter-row">
        <input id="view-search" placeholder="Search views…"/>
        <span id="view-count" class="mut"></span>
      </div>
      <div id="view-cards"></div>
    </div>

    <!-- STORED PROCEDURES -->
    <div class="panel" id="p-procs">
      <div class="filter-row">
        <input id="proc-search" placeholder="Search stored procedures…"/>
        <span id="proc-count" class="mut"></span>
      </div>
      <div id="proc-cards"></div>
    </div>

    <!-- COLUMNS -->
    <div class="panel" id="p-columns">
      <div class="filter-row">
        <input id="col-search" placeholder="Search columns, tables, data types…"/>
        <select id="col-type-sel">
          <option value="">Tables &amp; Views</option>
          <option value="table">Tables only</option>
          <option value="view">Views only</option>
        </select>
        <button onclick="colPrev()" id="col-prev"
          style="background:var(--sur);border:1px solid var(--brd);color:var(--txt);
          padding:5px 10px;border-radius:6px;cursor:pointer;font-size:12px">◀ Prev</button>
        <button onclick="colNext()" id="col-next"
          style="background:var(--sur);border:1px solid var(--brd);color:var(--txt);
          padding:5px 10px;border-radius:6px;cursor:pointer;font-size:12px">Next ▶</button>
        <span id="col-count" class="mut"></span>
      </div>
      <table>
        <thead><tr>
          <th title="Schema naming layer — SM_* = Source/Staging · DM_* = Data Mart · Reporting_* = Reporting · HUB_* = Hub · *_DBA = Operations · — = Other (no recognized prefix)">Layer</th><th>Schema</th><th>Table / View</th><th>Column</th>
          <th>Data Type</th><th>Nullable</th><th>Default</th>
        </tr></thead>
        <tbody id="col-tbody"></tbody>
      </table>
    </div>

  </div>
</div>
</div>
<script>{js}</script>
</body>
</html>"""


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["dev", "prd"], required=True)
    args = parser.parse_args()

    env_cfg   = ENVIRONMENTS[args.env]
    out_file  = f"/home/thedavidporter/sql_dw_metadata_report_{args.env}.html"

    print(f"\n=== SQL DW Metadata — {env_cfg['label']} ===")
    print(f"  Server  : {env_cfg['server']}.database.windows.net")
    print(f"  Database: {env_cfg['database']}")

    schemas, tables, views, procs, columns = collect(env_cfg)

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    print("\nBuilding HTML…")
    html = build_html(env_cfg, schemas, tables, views, procs, columns, generated)

    with open(out_file, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\nSaved: {out_file}")
    print(f"  Schemas : {len(schemas)}")
    print(f"  Tables  : {len(tables)}")
    print(f"  Views   : {len(views)}")
    print(f"  Procs   : {len(procs)}")
    print(f"  Columns : {len(columns):,}")



    if not os.environ.get('PUBLISH_RUNNING'):
        try:
            import generate_metadata_index
            generate_metadata_index.main()
            print("  Index updated       : index.html")
        except Exception as exc:
            print(f"  Warning: could not update index.html: {exc}")

if __name__ == "__main__":
    main()
