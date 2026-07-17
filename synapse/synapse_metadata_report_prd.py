#!/usr/bin/env python3
"""
Azure Synapse Metadata Analysis
Queries schemas, tables, views, stored procedures, columns,
foreign keys, and object dependencies — then writes a self-contained HTML report.
"""

import json
import struct
import subprocess
from datetime import datetime

import pyodbc

SERVER   = "zus1-idoh-prd-v1-sql-server.database.windows.net"
DATABASE = "zus1-idoh-prd-v1-sql-dw"
DRIVER   = "{ODBC Driver 18 for SQL Server}"

# ── auth ───────────────────────────────────────────────────────────────────────

def get_connection():
    result = subprocess.run(
        ["az", "account", "get-access-token",
         "--resource", "https://database.windows.net",
         "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"az login required: {result.stderr.strip()}")
    token = result.stdout.strip()
    token_bytes = token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
    conn_str = (f"Driver={DRIVER};Server=tcp:{SERVER},1433;"
                f"Database={DATABASE};Encrypt=yes;TrustServerCertificate=no;")
    return pyodbc.connect(conn_str, attrs_before={1256: token_struct})

def query(conn, sql):
    cur = conn.cursor()
    cur.execute(sql)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]

# ── SQL ────────────────────────────────────────────────────────────────────────

SCHEMAS_SQL = """
SELECT s.name AS schema_name, p.name AS owner,
       COUNT(o.object_id) AS object_count
FROM sys.schemas s
LEFT JOIN sys.database_principals p ON s.principal_id = p.principal_id
LEFT JOIN sys.objects o ON o.schema_id = s.schema_id
    AND o.type IN ('U','V','P','FN','TF','IF')
GROUP BY s.name, p.name
HAVING COUNT(o.object_id) > 0
ORDER BY s.name
"""

OBJECTS_SQL = """
SELECT s.name AS schema_name, o.name AS object_name,
       o.type_desc AS object_type,
       CONVERT(VARCHAR,o.create_date,23) AS created,
       CONVERT(VARCHAR,o.modify_date,23) AS modified
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id = o.schema_id
WHERE o.type IN ('U','V','P','FN','TF','IF')
ORDER BY s.name, o.type_desc, o.name
"""

COLUMNS_SQL = """
SELECT c.TABLE_SCHEMA AS schema_name, c.TABLE_NAME AS table_name,
       c.COLUMN_NAME, c.ORDINAL_POSITION AS ordinal,
       c.DATA_TYPE,
       COALESCE(CAST(c.CHARACTER_MAXIMUM_LENGTH AS VARCHAR),
                CAST(c.NUMERIC_PRECISION AS VARCHAR)) AS max_length,
       c.IS_NULLABLE, c.COLUMN_DEFAULT
FROM INFORMATION_SCHEMA.COLUMNS c
JOIN INFORMATION_SCHEMA.TABLES t
  ON t.TABLE_SCHEMA = c.TABLE_SCHEMA AND t.TABLE_NAME = c.TABLE_NAME
ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION
"""

# Views + procs + functions (anything with a SQL definition in sys.sql_modules)
DEFS_SQL = """
SELECT s.name AS schema_name, o.name AS obj_name, o.type_desc AS obj_type,
       m.definition
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id = o.schema_id
JOIN sys.sql_modules m ON m.object_id = o.object_id
WHERE o.type IN ('V','P','FN','TF','IF')
ORDER BY s.name, o.name
"""

FK_SQL = """
SELECT fk.name AS constraint_name,
       OBJECT_SCHEMA_NAME(fk.parent_object_id)                      AS parent_schema,
       OBJECT_NAME(fk.parent_object_id)                             AS parent_table,
       COL_NAME(fkc.parent_object_id, fkc.parent_column_id)         AS parent_column,
       OBJECT_SCHEMA_NAME(fk.referenced_object_id)                  AS ref_schema,
       OBJECT_NAME(fk.referenced_object_id)                         AS ref_table,
       COL_NAME(fkc.referenced_object_id, fkc.referenced_column_id) AS ref_column,
       'NOT ENFORCED' AS enforced
FROM sys.foreign_keys fk
JOIN sys.foreign_key_columns fkc ON fk.object_id = fkc.constraint_object_id
ORDER BY parent_schema, parent_table
"""

DEPS_SQL = """
SELECT DISTINCT
    OBJECT_SCHEMA_NAME(d.referencing_id) AS ref_schema,
    OBJECT_NAME(d.referencing_id)        AS ref_object,
    o1.type_desc                         AS ref_type,
    COALESCE(d.referenced_schema_name,'') AS dep_schema,
    d.referenced_entity_name             AS dep_object,
    COALESCE(o2.type_desc,'EXTERNAL')    AS dep_type
FROM sys.sql_expression_dependencies d
JOIN sys.objects o1 ON o1.object_id = d.referencing_id
LEFT JOIN sys.objects o2 ON o2.object_id = d.referenced_id
WHERE OBJECT_NAME(d.referencing_id) IS NOT NULL
ORDER BY ref_schema, ref_object
"""

ROW_COUNTS_SQL = """
SELECT s.name AS schema_name, t.name AS table_name, SUM(nps.row_count) AS row_count
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
"""

DIST_SQL = """
SELECT s.name AS schema_name, t.name AS table_name,
       tdp.distribution_policy_desc,
       c.name AS distribution_column
FROM sys.tables t
JOIN sys.schemas s ON s.schema_id = t.schema_id
JOIN sys.pdw_table_distribution_properties tdp ON tdp.object_id = t.object_id
LEFT JOIN sys.pdw_column_distribution_properties cdp
    ON cdp.object_id = t.object_id AND cdp.distribution_ordinal = 1
LEFT JOIN sys.columns c ON c.object_id = t.object_id AND c.column_id = cdp.column_id
"""

INDEX_SQL = """
SELECT s.name AS schema_name, t.name AS table_name,
       i.type_desc AS index_type
FROM sys.tables t
JOIN sys.schemas s ON s.schema_id = t.schema_id
JOIN sys.indexes i ON i.object_id = t.object_id AND i.index_id IN (0, 1)
"""

# ── helpers ────────────────────────────────────────────────────────────────────

def esc(s):
    if s is None: return ""
    return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")

def js_esc(s):
    if s is None: return ""
    return str(s).replace("\\","\\\\").replace("'","\\'").replace('"','\\"').replace("\n","\\n").replace("\r","")

# ── CSS ────────────────────────────────────────────────────────────────────────

CSS = """
:root{--bg:#0f1117;--sur:#1a1d27;--sur2:#252836;--brd:#2e3245;
  --txt:#e2e8f0;--mut:#8892a4;--acc:#6c8eff;--grn:#4ade80;
  --red:#f87171;--pur:#c084fc;--cyn:#22d3ee;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font:14px/1.5 'Segoe UI',system-ui,sans-serif;overflow:hidden}
.layout{display:flex;height:100vh}

/* ── sidebar ── */
.sidebar{width:270px;min-width:180px;background:var(--sur);border-right:1px solid var(--brd);
  display:flex;flex-direction:column;overflow:hidden;flex-shrink:0}
.sb-hdr{padding:12px 14px;border-bottom:1px solid var(--brd);font-weight:700;font-size:13px;line-height:1.4}
.sb-hdr small{display:block;color:var(--mut);font-weight:400;font-size:11px;margin-top:2px}
.sb-search{padding:7px 10px;border-bottom:1px solid var(--brd)}
.sb-search input{width:100%;padding:5px 9px;background:var(--sur2);border:1px solid var(--brd);
  border-radius:6px;color:var(--txt);font-size:12px;outline:none}
.sb-search input:focus{border-color:var(--acc)}
.sb-list{overflow-y:auto;flex:1;padding-bottom:12px}

/* schema group */
.sch-item{}
.sch-hdr{display:flex;align-items:center;gap:6px;padding:6px 10px;cursor:pointer;
  font-size:11px;font-weight:700;color:var(--acc);text-transform:uppercase;letter-spacing:.4px;
  user-select:none;border-bottom:1px solid var(--brd);position:sticky;top:0;
  background:var(--sur);z-index:2}
.sch-hdr:hover{background:var(--sur2)}
.sch-hdr .arr{font-size:9px;flex-shrink:0;transition:transform .15s;display:inline-block}
.sch-hdr.open .arr{transform:rotate(90deg)}
.sch-body{display:none;padding:2px 0}
.sch-hdr.open + .sch-body{display:block}
.obj-row{display:flex;align-items:center;gap:5px;padding:3px 8px 3px 20px;
  font-size:12px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  border-radius:4px;margin:1px 4px}
.obj-row:hover{background:var(--sur2)}
.obj-row.active{background:var(--brd);color:var(--txt)}
.bdg{font-size:9px;padding:1px 4px;border-radius:3px;flex-shrink:0;font-weight:700}
.bdg-T{background:#1e3a5f;color:#60a5fa}
.bdg-V{background:#1a3a2a;color:#4ade80}
.bdg-P{background:#3a2a1e;color:#fb923c}

/* ── main area ── */
.main{flex:1;overflow-y:auto;padding:22px 26px}
h1{font-size:20px;font-weight:700;margin-bottom:2px}
.sub{color:var(--mut);font-size:12px;margin-bottom:20px}

/* stat cards */
.stats{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:22px}
.sc{background:var(--sur);border:1px solid var(--brd);border-radius:10px;padding:13px 16px;min-width:100px;cursor:pointer;transition:border-color .15s}
.sc:hover{border-color:var(--acc)}
.sc.active-card{border-color:var(--acc);background:var(--sur2)}
.sc-n{font-size:24px;font-weight:700;line-height:1}
.sc-l{font-size:11px;color:var(--mut);margin-top:3px}

/* tabs */
.tabs{display:flex;gap:2px;border-bottom:2px solid var(--brd);margin-bottom:16px;flex-wrap:wrap}
.tab{padding:7px 14px;cursor:pointer;border-radius:6px 6px 0 0;font-size:12px;font-weight:600;
  color:var(--mut);border:1px solid transparent;border-bottom:none;margin-bottom:-2px;user-select:none}
.tab:hover{color:var(--txt);background:var(--sur)}
.tab.active{background:var(--sur);border-color:var(--brd);border-bottom-color:var(--sur);color:var(--txt)}
.panel{display:none}.panel.active{display:block}

/* search bar */
.srch{margin-bottom:12px}
.srch input{padding:7px 11px;background:var(--sur);border:1px solid var(--brd);
  border-radius:6px;color:var(--txt);font-size:12px;width:100%;max-width:420px;outline:none}
.srch input:focus{border-color:var(--acc)}

/* data tables */
.tw{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:12px}
th{background:var(--sur2);padding:7px 11px;text-align:left;font-weight:700;
  border-bottom:2px solid var(--brd);white-space:nowrap;position:sticky;top:0;z-index:1}
td{padding:5px 11px;border-bottom:1px solid var(--brd);vertical-align:top;
  max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
tr:hover td{background:var(--sur)}
tr.highlighted td{background:#1e2d4a!important;outline:2px solid var(--acc);outline-offset:-1px}
#obj-tbl tbody tr{cursor:pointer}

/* chips */
.chip{display:inline-block;font-size:10px;padding:1px 6px;border-radius:3px;white-space:nowrap}
.chip-USER_TABLE,.chip-EXTERNAL_TABLE{background:#1e3a5f;color:#60a5fa}
.chip-VIEW{background:#1a3a2a;color:#4ade80}
.chip-SQL_STORED_PROCEDURE,.chip-SQL_SCALAR_FUNCTION,
.chip-SQL_TABLE_VALUED_FUNCTION,.chip-SQL_INLINE_TABLE_VALUED_FUNCTION{background:#3a2a1e;color:#fb923c}
.null-y{color:var(--red);font-size:11px}
.null-n{color:var(--mut);font-size:11px}
.enf-n{background:#3a1e1e;color:#f87171;font-size:10px;padding:1px 5px;border-radius:3px}

/* schema overview grid */
.sg{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:9px;margin-bottom:18px;align-items:start}
.sc2{background:var(--sur);border:1px solid var(--brd);border-radius:8px;padding:11px 13px}
.sc2:hover{border-color:var(--acc)}
.sc2 h3{font-size:12px;color:var(--acc);margin-bottom:6px;cursor:pointer;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sc2 h3:hover{text-decoration:underline}
.sc2 .ct{display:flex;gap:10px;font-size:11px}
.sc2 .ct span{color:var(--mut)} .sc2 .ct strong{color:var(--txt)}
.schema-narrative{margin-top:8px;border-top:1px solid var(--brd);padding-top:7px}
.schema-narrative summary{cursor:pointer;color:var(--acc);font-size:11px;user-select:none}
.schema-narrative .view-desc{max-height:420px;overflow-y:auto;padding-right:4px;white-space:pre-wrap}

/* proc cards */
.pc{background:var(--sur);border:1px solid var(--brd);border-radius:8px;padding:13px;margin-bottom:9px}
.pc h3{font-size:13px;margin-bottom:3px}
.pc .pm{font-size:11px;color:var(--mut);margin-bottom:7px}
.pc details summary{cursor:pointer;color:var(--acc);font-size:12px;user-select:none}
.view-desc{font-size:12px;color:var(--txt);line-height:1.7;margin:8px 0 2px;padding:0}
pre{background:var(--sur2);padding:11px;border-radius:6px;overflow-x:auto;
  font-size:11px;line-height:1.6;white-space:pre-wrap;word-break:break-word;margin-top:0}

/* copy-code button */
.code-wrap{position:relative;margin-top:7px}
.copy-btn{position:absolute;top:7px;right:7px;z-index:2;
  display:inline-flex;align-items:center;gap:4px;
  background:var(--sur);border:1px solid var(--brd);border-radius:5px;
  padding:3px 8px;cursor:pointer;color:var(--mut);
  font-size:10px;font-weight:700;font-family:inherit;line-height:1.4;
  opacity:0;transition:opacity .15s,border-color .15s,color .15s}
.code-wrap:hover .copy-btn{opacity:1}
.copy-btn:hover{border-color:var(--acc);color:var(--acc);background:var(--sur2)}
.copy-btn.copied{border-color:var(--grn)!important;color:var(--grn)!important;opacity:1!important}

/* dep colours */
.df{color:var(--cyn)} .dt{color:var(--pur)}
.rc{color:var(--mut);font-size:11px}
.hidden{display:none!important}

/* ── object detail modal ─────────────────────────────────────────────────── */
.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:200;
  display:flex;align-items:center;justify-content:center;padding:20px}
.modal-box{background:var(--sur);border:1px solid var(--brd);border-radius:12px;
  width:960px;max-width:calc(100vw - 40px);max-height:88vh;
  display:flex;flex-direction:column;box-shadow:0 24px 70px rgba(0,0,0,.7)}
.modal-hdr{display:flex;align-items:center;gap:10px;padding:13px 16px;
  border-bottom:1px solid var(--brd);flex-shrink:0;min-width:0}
.modal-hdr-title{flex:1;min-width:0;display:flex;align-items:center;gap:8px;
  font-size:14px;font-weight:700;overflow:hidden}
.modal-hdr-title span.name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.modal-meta-row{font-size:11px;color:var(--mut);padding:0 16px 8px;
  display:flex;gap:16px;flex-wrap:wrap;flex-shrink:0}
.modal-meta-row span strong{color:var(--txt)}
.modal-close{background:none;border:none;color:var(--mut);font-size:20px;cursor:pointer;
  padding:1px 7px;border-radius:4px;line-height:1;flex-shrink:0}
.modal-close:hover{background:var(--sur2);color:var(--txt)}
.modal-tabs{display:flex;gap:3px;padding:8px 14px;border-bottom:1px solid var(--brd);flex-shrink:0}
.mtab{padding:5px 13px;background:none;border:1px solid transparent;border-radius:5px;
  color:var(--mut);font-size:12px;font-weight:600;cursor:pointer;user-select:none}
.mtab:hover{color:var(--txt)}
.mtab.active{background:var(--sur2);border-color:var(--brd);color:var(--txt)}
.modal-body{overflow:auto;flex:1;padding:14px 16px}
.modal-col-tbl{width:100%;border-collapse:collapse;font-size:12px}
.modal-col-tbl th{background:var(--sur2);padding:6px 10px;text-align:left;
  font-weight:700;border-bottom:2px solid var(--brd);position:sticky;top:0;z-index:1;
  white-space:nowrap}
.modal-col-tbl td{padding:4px 10px;border-bottom:1px solid var(--brd);vertical-align:top}
.modal-col-tbl tr:hover td{background:var(--sur2)}
.modal-col-tbl .col-name{font-weight:600;color:var(--txt)}
.modal-col-tbl .col-type{color:#93c5fd;font-family:monospace;font-size:11px}
.modal-col-tbl .col-ord{color:var(--mut);text-align:right;width:36px}
.modal-col-tbl .col-def{color:var(--mut);font-family:monospace;font-size:11px;
  max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.modal-col-tbl .col-ai-desc{color:var(--mut);font-size:11px;max-width:280px}
.modal-ddl pre{background:var(--sur2);padding:14px 16px;border-radius:8px;font-size:11.5px;
  line-height:1.7;white-space:pre;word-break:normal;overflow-x:auto;
  border:1px solid var(--brd);color:#e2e8f0;max-height:none}
.modal-empty{color:var(--mut);padding:20px 0;font-size:13px}
.modal-ai-desc{border-left:3px solid var(--acc);background:var(--sur2);
  border-radius:0 6px 6px 0;padding:8px 12px;margin:0 0 10px;
  font-size:12px;color:var(--txt);line-height:1.55;flex-shrink:0}
.kw{color:#c084fc} .fn-name{color:#93c5fd} .str-lit{color:#86efac}
.cm{color:#6b7898;font-style:italic}
"""

# ── JavaScript ─────────────────────────────────────────────────────────────────

JS = r"""
// ── helpers ───────────────────────────────────────────────────────────────────
function escH(s){
  return String(s||'')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── tab switching ─────────────────────────────────────────────────────────────
function showTab(id, el){
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.sc').forEach(c => c.classList.remove('active-card'));
  document.getElementById('p-' + id).classList.add('active');
  const tab = document.getElementById('tab-' + id);
  if(tab) tab.classList.add('active');
  const card = document.getElementById('card-' + id);
  if(card) card.classList.add('active-card');
  // lazy-render columns tab on first open
  if(id === 'columns' && !colTabReady){
    renderColTab(document.getElementById('col-q').value);
  }
}

// ── generic table search filter ───────────────────────────────────────────────
function ft(tid, q){
  q = q.toLowerCase().trim();
  document.querySelectorAll('#' + tid + ' tbody tr').forEach(tr => {
    const hay = tr.textContent.toLowerCase() + ' ' + (tr.dataset.key || '');
    tr.classList.toggle('hidden', !!q && !hay.includes(q));
  });
}

// ── sidebar search filter ─────────────────────────────────────────────────────
function filterSB(q){
  q = q.toLowerCase().trim();
  document.querySelectorAll('.sch-item').forEach(item => {
    const rows = item.querySelectorAll('.obj-row');
    let any = false;
    rows.forEach(r => {
      const show = !q || (r.dataset.n || '').includes(q);
      r.classList.toggle('hidden', !show);
      if (show) any = true;
    });
    const schName = (item.querySelector('.sch-hdr') || {}).textContent || '';
    const schMatch = !q || schName.toLowerCase().includes(q);
    item.classList.toggle('hidden', !!q && !any && !schMatch);
    if (q && (any || schMatch)) item.querySelector('.sch-hdr').classList.add('open');
  });
}

// ── sidebar schema toggle ─────────────────────────────────────────────────────
document.querySelectorAll('.sch-hdr').forEach(h => {
  h.addEventListener('click', () => h.classList.toggle('open'));
});

// ── navigate from schema card to All-Objects tab ──────────────────────────────
function filterBySchema(schema){
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('p-objects').classList.add('active');
  document.getElementById('tab-objects').classList.add('active');
  const inp = document.getElementById('obj-q');
  inp.value = schema;
  ft('obj-tbl', schema);
}

// ── navigate to a row in All-Objects (from dep/FK cross-links) ───────────────
function gotoObj(key, el){
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('p-objects').classList.add('active');
  document.getElementById('tab-objects').classList.add('active');
  const inp = document.getElementById('obj-q');
  inp.value = '';
  document.querySelectorAll('#obj-tbl tbody tr').forEach(tr => tr.classList.remove('hidden'));
  document.querySelectorAll('#obj-tbl tbody tr').forEach(tr => tr.classList.remove('highlighted'));
  const target = document.querySelector('#obj-tbl tbody tr[data-key="' + key + '"]');
  if (target) {
    target.classList.add('highlighted');
    requestAnimationFrame(() => target.scrollIntoView({ block: 'center', behavior: 'smooth' }));
  }
  document.querySelectorAll('.obj-row').forEach(r => r.classList.remove('active'));
  if (el) el.classList.add('active');
}

// ── column data from JSON (no heavy DOM rows) ─────────────────────────────────
// COL_DATA format: [schema, table, ordinal, col_name, type, len, nullable, default, desc]
const COL_DATA = __COL_DATA__;

const COL_MAP = {};
for(const c of COL_DATA){
  const key = c[0] + '||' + c[1];
  if(!COL_MAP[key]) COL_MAP[key] = [];
  COL_MAP[key].push({ord:c[2], name:c[3], type:c[4], len:c[5], null:c[6], def:c[7], desc:c[8]||''});
}

// ── distribution + index map ──────────────────────────────────────────────────
// {schema||table: {policy, col, index}}
const DIST_MAP = __DIST_MAP__;

// ── columns tab: render-on-demand with infinite scroll ───────────────────────
const COL_PAGE = 200;   // rows per batch
let colFiltered  = [];  // current working dataset (full or filtered)
let colOffset    = 0;   // how many rows have been rendered so far
let colTabReady  = false;

function colRow(c){
  const nl = c[6]==='YES'
    ? '<span class="null-y">YES</span>'
    : '<span class="null-n">NO</span>';
  return '<tr>'
    + '<td>' + escH(c[0]) + '</td>'
    + '<td>' + escH(c[1]) + '</td>'
    + '<td>' + c[2]       + '</td>'
    + '<td>' + escH(c[3]) + '</td>'
    + '<td>' + escH(c[4]) + '</td>'
    + '<td>' + escH(c[5]) + '</td>'
    + '<td>' + nl         + '</td>'
    + '<td>' + escH(c[7]) + '</td>'
    + '</tr>';
}

function updateColLabel(){
  const total = colFiltered.length;
  const shown = Math.min(colOffset, total);
  const lq    = (document.getElementById('col-q').value||'').trim();
  document.getElementById('col-count').textContent = shown >= total
    ? (lq ? `${total.toLocaleString()} matching columns` : `All ${total.toLocaleString()} columns`)
    : `Showing ${shown.toLocaleString()} of ${total.toLocaleString()} — scroll for more`;
}

function appendColRows(){
  const batch = colFiltered.slice(colOffset, colOffset + COL_PAGE);
  if(!batch.length) return;
  document.querySelector('#col-tbl tbody')
    .insertAdjacentHTML('beforeend', batch.map(colRow).join(''));
  colOffset += batch.length;
  updateColLabel();
}

function renderColTab(q){
  q = (q||'').toLowerCase().trim();
  colFiltered = q
    ? COL_DATA.filter(c =>
        c[0].toLowerCase().includes(q) ||
        c[1].toLowerCase().includes(q) ||
        c[3].toLowerCase().includes(q) ||
        c[4].toLowerCase().includes(q))
    : COL_DATA;
  colOffset = 0;
  document.querySelector('#col-tbl tbody').innerHTML = '';
  appendColRows();
  colTabReady = true;
}

// IntersectionObserver: load next batch when sentinel scrolls into view
const colObserver = new IntersectionObserver(entries => {
  if(entries[0].isIntersecting && colTabReady) appendColRows();
}, { root: document.querySelector('.main'), rootMargin: '200px' });
colObserver.observe(document.getElementById('col-sentinel'));

function copyCode(btn){
  const pre = btn.closest('.code-wrap').querySelector('pre');
  navigator.clipboard.writeText(pre.textContent).then(()=>{
    btn.classList.add('copied');
    btn.querySelector('.copy-lbl').textContent='Copied!';
    setTimeout(()=>{btn.classList.remove('copied');btn.querySelector('.copy-lbl').textContent='Copy';},2000);
  }).catch(()=>{});
}

// ── SQL syntax highlight (lightweight) ───────────────────────────────────────
function hlSQL(code){
  const KW = /\b(SELECT|FROM|WHERE|JOIN|LEFT|RIGHT|INNER|OUTER|FULL|ON|AND|OR|NOT|IN|IS|NULL|AS|WITH|UNION|ALL|DISTINCT|GROUP\s+BY|ORDER\s+BY|HAVING|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TABLE|VIEW|PROCEDURE|FUNCTION|EXEC|EXECUTE|BEGIN|END|IF|ELSE|THEN|CASE|WHEN|END|RETURN|SET|DECLARE|TOP|CAST|CONVERT|COALESCE|ISNULL|COUNT|SUM|MAX|MIN|AVG|ROW_NUMBER|OVER|PARTITION\s+BY|GO)\b/gi;
  const STR = /('[^']*')/g;
  const CMT = /(--[^\n]*)|(\/\*[\s\S]*?\*\/)/g;
  return escH(code)
    .replace(CMT, m => `<span class="cm">${m}</span>`)
    .replace(STR, m => `<span class="str-lit">${m}</span>`)
    .replace(KW,  m => `<span class="kw">${m}</span>`);
}

// ── build SELECT statement for a table from COL_MAP ─────────────────────────
function buildSelect(schema, name, cols){
  if(!cols||!cols.length) return '-- No column metadata available';
  const colLines = cols.map((c, i) =>
    (i === 0 ? 'SELECT ' : '       ') + c.name + (i < cols.length - 1 ? ',' : '')
  );
  return colLines.join('\n') + '\nFROM ' + schema + '.' + name + ';';
}

// ── build DDL for a table from COL_MAP ───────────────────────────────────────
// Types that have implicit fixed length in Synapse — do not append (n)
const NO_LEN_TYPES = new Set([
  'INT','BIGINT','TINYINT','SMALLINT','BIT',
  'FLOAT','REAL','DATE','DATETIME','DATETIME2',
  'SMALLDATETIME','DATETIMEOFFSET','UNIQUEIDENTIFIER',
  'MONEY','SMALLMONEY'
]);

function buildDDL(schema, name, cols){
  if(!cols||!cols.length) return '-- No column metadata available';
  const lines = cols.map(c => {
    let t = c.type.toUpperCase();
    if(!NO_LEN_TYPES.has(t) && c.len && c.len!=='' && c.len!=='None' && c.len!=='null'){
      t += '(' + (c.len==='-1'?'MAX':c.len) + ')';
    }
    let line = '    [' + c.name + '] ' + t;
    if(c.def && c.def!=='' && c.def!=='None' && c.def!=='null')
      line += ' DEFAULT ' + c.def;
    line += c.null==='YES' ? ' NULL' : ' NOT NULL';
    return line;
  });

  const dist = DIST_MAP[schema + '||' + name];
  let withClause = '';
  if(dist){
    let distPart = dist.policy === 'HASH' && dist.col
      ? 'DISTRIBUTION = HASH([' + dist.col + '])'
      : 'DISTRIBUTION = ' + (dist.policy || 'ROUND_ROBIN');
    let idxType = (dist.index || '').toUpperCase();
    let idxPart = idxType === 'CLUSTERED COLUMNSTORE' ? 'CLUSTERED COLUMNSTORE INDEX'
                : idxType === 'HEAP'                  ? 'HEAP'
                : idxType ? idxType : '';
    withClause = '\nWITH (\n    ' + distPart
               + (idxPart ? ',\n    ' + idxPart : '')
               + '\n)';
  }

  return 'CREATE TABLE [' + schema + '].[' + name + '] (\n'
       + lines.join(',\n') + '\n)' + withClause + ';\nGO';
}

// ── object detail modal ───────────────────────────────────────────────────────
// DEF_MAP: {schema||name: sql_definition} for views, procs, functions
const DEF_MAP = __DEF_MAP__;

// OBJ_META: {schema||name: {type, created, modified, rows}}
const OBJ_META = __OBJ_META__;

function openDetail(key, el){
  const sep   = key.indexOf('||');
  const schema = key.slice(0, sep);
  const name   = key.slice(sep + 2);
  const cols  = COL_MAP[key] || [];
  const def   = DEF_MAP[key] || null;
  const meta  = OBJ_META[key] || {};

  // ── header ──
  const typeLabel = (meta.type||'OBJECT').replace(/_/g,' ');
  const typeClass = (meta.type||'').replace(/\s/g,'_');
  document.getElementById('modal-schema').textContent = schema + '.';
  document.getElementById('modal-name').textContent   = name;
  const badge = document.getElementById('modal-badge');
  badge.className = 'chip chip-' + (meta.type||'');
  const TYPE_ICON = {
    'VIEW': '👁',
    'SQL_STORED_PROCEDURE': '⚙',
    'SQL_SCALAR_FUNCTION': 'ƒ',
    'SQL_TABLE_VALUED_FUNCTION': 'ƒ',
    'SQL_INLINE_TABLE_VALUED_FUNCTION': 'ƒ',
  };
  const modalIcon = TYPE_ICON[meta.type] || '';
  badge.textContent = (modalIcon ? modalIcon + ' ' : '') + typeLabel;

  // ── meta row ──
  const metaRow = document.getElementById('modal-meta-row');
  const parts = [];
  if(meta.created)  parts.push('<span>Created <strong>' + escH(meta.created) + '</strong></span>');
  if(meta.modified) parts.push('<span>Modified <strong>' + escH(meta.modified) + '</strong></span>');
  if(meta.rows!==undefined && meta.rows!=='')
    parts.push('<span>Rows <strong>' + Number(meta.rows).toLocaleString() + '</strong></span>');
  if(cols.length) parts.push('<span>Columns <strong>' + cols.length + '</strong></span>');
  metaRow.innerHTML = parts.join('');

  // ── AI description banner ──
  const aiDescEl = document.getElementById('modal-ai-desc');
  if(meta.desc){
    aiDescEl.textContent = meta.desc;
    aiDescEl.style.display = '';
  } else {
    aiDescEl.style.display = 'none';
  }

  // ── column tab ──
  const colBody = document.getElementById('modal-cols-body');
  const mtabCols = document.getElementById('mtab-cols');
  if(cols.length){
    const hasDesc = cols.some(c => c.desc);
    const rows = cols.map(c => {
      const nl = c.null==='YES'
        ? '<span class="null-y">YES</span>'
        : '<span class="null-n">NO</span>';
      let typeStr = c.type.toUpperCase();
      if(c.len && c.len!=='' && c.len!=='None' && c.len!=='null')
        typeStr += '(' + (c.len==='-1'?'MAX':c.len) + ')';
      const defStr = (c.def && c.def!=='None' && c.def!=='null') ? escH(c.def) : '';
      return '<tr>'
           + '<td class="col-ord">' + c.ord + '</td>'
           + '<td class="col-name">' + escH(c.name) + '</td>'
           + '<td class="col-type">' + escH(typeStr) + '</td>'
           + '<td>' + nl + '</td>'
           + '<td class="col-def">' + defStr + '</td>'
           + (hasDesc ? '<td class="col-ai-desc">' + escH(c.desc||'') + '</td>' : '')
           + '</tr>';
    }).join('');
    colBody.innerHTML =
      '<table class="modal-col-tbl">'
      + '<thead><tr><th class="col-ord">#</th><th>Column Name</th>'
      + '<th>Data Type</th><th>Nullable</th><th>Default</th>'
      + (hasDesc ? '<th>Description</th>' : '')
      + '</tr></thead>'
      + '<tbody>' + rows + '</tbody></table>';
    mtabCols.style.display = '';
  } else {
    colBody.innerHTML = '<p class="modal-empty">No column metadata available for this object type.</p>';
    mtabCols.style.display = 'none';
  }

  // ── DDL / definition tab ──
  const ddlBody  = document.getElementById('modal-ddl-body');
  const mtabDDL  = document.getElementById('mtab-ddl');
  const isTable  = meta.type && meta.type.includes('TABLE');
  const hasDef   = !!def;

  if(hasDef){
    // view / proc / function — show real SQL definition with syntax highlight
    ddlBody.innerHTML = '<div class="modal-ddl"><div class="code-wrap"><button class="copy-btn" onclick="copyCode(this)" title="Copy to clipboard"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg><span class="copy-lbl">Copy</span></button><pre>' + hlSQL(def) + '</pre></div></div>';
    document.getElementById('mtab-ddl').textContent = isTable ? 'DDL' : 'SQL Definition';
    mtabDDL.style.display = '';
  } else if(isTable && cols.length){
    // table — reconstruct DDL from column metadata
    const ddl = buildDDL(schema, name, cols);
    ddlBody.innerHTML = '<div class="modal-ddl"><div class="code-wrap"><button class="copy-btn" onclick="copyCode(this)" title="Copy to clipboard"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg><span class="copy-lbl">Copy</span></button><pre>' + hlSQL(ddl) + '</pre></div></div>';
    document.getElementById('mtab-ddl').textContent = 'DDL';
    mtabDDL.style.display = '';
  } else {
    ddlBody.innerHTML = '<p class="modal-empty">No SQL definition available for this object.</p>';
    mtabDDL.style.display = 'none';
  }

  // ── Select tab ──
  const selBody  = document.getElementById('modal-select-body');
  const mtabSel  = document.getElementById('mtab-select');
  if(isTable && cols.length){
    const sel = buildSelect(schema, name, cols);
    selBody.innerHTML = '<div class="modal-ddl"><div class="code-wrap"><button class="copy-btn" onclick="copyCode(this)" title="Copy to clipboard"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg><span class="copy-lbl">Copy</span></button><pre>' + hlSQL(sel) + '</pre></div></div>';
    mtabSel.style.display = '';
  } else {
    selBody.innerHTML = '<p class="modal-empty">Select statements are only available for tables.</p>';
    mtabSel.style.display = 'none';
  }

  // ── show correct default tab ──
  const defaultTab = cols.length ? 'cols' : 'ddl';
  showModalTab(defaultTab);

  // ── open ──
  document.getElementById('obj-modal').style.display = 'flex';

  // mark sidebar active
  document.querySelectorAll('.obj-row').forEach(r => r.classList.remove('active'));
  if(el) el.classList.add('active');
}

function closeModal(){
  document.getElementById('obj-modal').style.display = 'none';
}

function showModalTab(tab){
  document.querySelectorAll('.mtab').forEach(t => t.classList.remove('active'));
  const active = document.getElementById('mtab-' + tab);
  if(active) active.classList.add('active');
  document.getElementById('modal-cols-body').style.display    = tab==='cols'   ? '' : 'none';
  document.getElementById('modal-ddl-body').style.display     = tab==='ddl'    ? '' : 'none';
  document.getElementById('modal-select-body').style.display  = tab==='select' ? '' : 'none';
}

// close on overlay click or Escape
document.getElementById('obj-modal').addEventListener('click', e => {
  if(e.target === document.getElementById('obj-modal')) closeModal();
});
document.addEventListener('keydown', e => {
  if(e.key === 'Escape') closeModal();
});
"""

# ── HTML builder ───────────────────────────────────────────────────────────────

def build_html(schemas, objects, columns, fks, deps, defs, row_counts, generated,
               distributions=None, indexes=None):
    rc_map = {(r['schema_name'], r['table_name']): r['row_count'] for r in row_counts}

    type_counts = {}
    for o in objects:
        type_counts[o['object_type']] = type_counts.get(o['object_type'], 0) + 1
    n_tables = sum(v for k, v in type_counts.items() if 'TABLE' in k)
    n_views  = type_counts.get('VIEW', 0)
    # procs = anything that has a definition and is not a view
    views_defs = [d for d in defs if d['obj_type'] == 'VIEW']
    procs = [d for d in defs if 'TABLE' not in d['obj_type'] and d['obj_type'] != 'VIEW']
    n_procs = len(procs)

    # ── definition map (views + procs + functions) ─────────────────────────────
    def_map = {
        f"{d['schema_name']}||{d['obj_name']}": d['definition'] or ""
        for d in defs
    }

    # ── plain-English view descriptions ────────────────────────────────────────
    import os as _os
    def _load_desc(filename):
        p = f"/home/thedavidporter/{filename}"
        if _os.path.exists(p):
            with open(p, encoding="utf-8") as _f:
                return json.load(_f)
        return {}

    view_descriptions   = _load_desc("view_descriptions.json")
    proc_descriptions   = _load_desc("proc_descriptions.json")
    table_descriptions  = _load_desc("table_descriptions.json")
    column_descriptions = _load_desc("column_descriptions.json")
    schema_narratives   = _load_desc("schema_narratives_prd.json")

    # ── object metadata map (type, created, modified, row_count) ───────────────
    obj_meta = {}
    for o in objects:
        k = f"{o['schema_name']}||{o['object_name']}"
        rc = rc_map.get((o['schema_name'], o['object_name']), '')
        obj_meta[k] = {
            "type":     o['object_type'],
            "created":  o['created'],
            "modified": o['modified'],
            "rows":     int(rc) if rc != '' else '',
            "desc":     table_descriptions.get(k, '') or view_descriptions.get(k, '') or proc_descriptions.get(k, ''),
        }

    def_map_json  = json.dumps(def_map,  ensure_ascii=False, separators=(',', ':'))
    obj_meta_json = json.dumps(obj_meta, ensure_ascii=False, separators=(',', ':'))

    # ── distribution + index map ───────────────────────────────────────────────
    dist_map = {}
    for d in (distributions or []):
        k = f"{d['schema_name']}||{d['table_name']}"
        dist_map[k] = {'policy': d['distribution_policy_desc'] or '', 'col': d['distribution_column'] or ''}
    for i in (indexes or []):
        k = f"{i['schema_name']}||{i['table_name']}"
        if k in dist_map:
            dist_map[k]['index'] = i['index_type'] or ''
    dist_map_json = json.dumps(dist_map, ensure_ascii=False, separators=(',', ':'))

    # ── sidebar ────────────────────────────────────────────────────────────────
    obs_by_schema = {}
    for o in objects:
        obs_by_schema.setdefault(o['schema_name'], []).append(o)

    sb = []
    for schema in sorted(obs_by_schema):
        sb.append(
            f'<div class="sch-item">'
            f'<div class="sch-hdr"><span class="arr">&#x25B6;</span>{esc(schema)}</div>'
            f'<div class="sch-body">'
        )
        for o in sorted(obs_by_schema[schema], key=lambda x: (x['object_type'], x['object_name'])):
            t   = o['object_type']
            b   = 'T' if 'TABLE' in t else ('V' if t == 'VIEW' else 'P')
            key = js_esc(f"{o['schema_name']}||{o['object_name']}")
            dn  = esc(o['object_name'])
            data_n = esc(f"{o['schema_name']}.{o['object_name']}".lower())
            obj_icon = ('👁 ' if t == 'VIEW'
                        else ('⚙ ' if t == 'SQL_STORED_PROCEDURE'
                        else ('ƒ ' if 'FUNCTION' in t else '')))
            sb.append(
                f'<div class="obj-row" data-n="{data_n}" '
                f'onclick="openDetail(\'{key}\',this)">'
                f'<span class="bdg bdg-{b}">{b}</span>{obj_icon}{dn}</div>'
            )
        sb.append('</div></div>')
    sidebar = '\n'.join(sb)

    # ── schema overview cards ──────────────────────────────────────────────────
    sch_counts = {}
    for o in objects:
        s = o['schema_name']; t = o['object_type']
        sch_counts.setdefault(s, [0, 0, 0])
        if 'TABLE' in t:  sch_counts[s][0] += 1
        elif t == 'VIEW': sch_counts[s][1] += 1
        else:             sch_counts[s][2] += 1

    def _schema_card(s, c):
        narrative = schema_narratives.get(s, '')
        narrative_html = (
            f'<details class="schema-narrative"><summary>Schema Overview</summary>'
            f'<p class="view-desc">{esc(narrative)}</p></details>'
            if narrative else ''
        )
        return (
            f'<div class="sc2" title="Click schema name to filter objects">'
            f'<h3 onclick="filterBySchema(\'{js_esc(s)}\')">{esc(s)}</h3>'
            f'<div class="ct">'
            f'<span><strong>{c[0]}</strong> tables</span>'
            f'<span><strong>{c[1]}</strong> views</span>'
            f'<span><strong>{c[2]}</strong> procs</span>'
            f'</div>{narrative_html}</div>'
        )

    cards = ''.join(
        _schema_card(s, c)
        for s, c in sorted(sch_counts.items())
    )

    # ── objects table rows ─────────────────────────────────────────────────────
    obj_rows = []
    for o in objects:
        s  = o['schema_name'];  n = o['object_name']
        es = esc(s);            en = esc(n)
        t  = o['object_type']
        chip_icon = ('👁 ' if t == 'VIEW'
                     else ('⚙ ' if t == 'SQL_STORED_PROCEDURE'
                     else ('ƒ ' if 'FUNCTION' in t else '')))
        chip = f'<span class="chip chip-{t}">{chip_icon}{t.replace("_"," ")}</span>'
        rc   = rc_map.get((s, n), '')
        rc_s = f'<span class="rc">{rc:,}</span>' if rc != '' else ''
        key  = esc(f"{s}||{n}")
        js_key = js_esc(f"{s}||{n}")
        obj_rows.append(
            f'<tr data-key="{key}" onclick="openDetail(\'{js_key}\')" '
            f'title="Click to view definition">'
            f'<td>{es}</td><td>{en}</td><td>{chip}</td>'
            f'<td>{esc(o["created"])}</td><td>{esc(o["modified"])}</td>'
            f'<td>{rc_s}</td></tr>'
        )

    # ── compact column data for JS (replaces 88k DOM rows) ────────────────────
    # Format per entry: [schema, table, ordinal, col_name, type, len, nullable, default, desc]
    col_data = [
        [c['schema_name'], c['table_name'], int(c['ordinal'] or 0),
         c['COLUMN_NAME'], c['DATA_TYPE'],
         str(c['max_length'] or ''), c['IS_NULLABLE'],
         str(c['COLUMN_DEFAULT'] or ''),
         column_descriptions.get(f"{c['schema_name']}||{c['table_name']}||{c['COLUMN_NAME']}", '')]
        for c in columns
    ]
    col_data_json = json.dumps(col_data, ensure_ascii=False, separators=(',', ':'))

    # ── FK content ─────────────────────────────────────────────────────────────
    if fks:
        fk_rows = [
            f'<tr>'
            f'<td>{esc(fk["parent_schema"])}</td><td>{esc(fk["parent_table"])}</td>'
            f'<td>{esc(fk["parent_column"])}</td>'
            f'<td style="color:var(--mut);text-align:center">&#x2192;</td>'
            f'<td>{esc(fk["ref_schema"])}</td><td>{esc(fk["ref_table"])}</td>'
            f'<td>{esc(fk["ref_column"])}</td>'
            f'<td><span class="enf-n">{esc(fk["enforced"])}</span></td>'
            f'<td>{esc(fk["constraint_name"])}</td></tr>'
            for fk in fks
        ]
        fk_content = (
            '<div class="tw"><table id="fk-tbl">'
            '<thead><tr><th>Parent Schema</th><th>Parent Table</th><th>Parent Col</th>'
            '<th></th>'
            '<th>Ref Schema</th><th>Ref Table</th><th>Ref Col</th>'
            '<th>Enforced</th><th>Constraint</th></tr></thead>'
            '<tbody>' + '\n'.join(fk_rows) + '</tbody></table></div>'
        )
    else:
        fk_content = (
            '<p style="color:var(--mut);padding:16px 0">'
            'No foreign key constraints found — typical for Synapse dedicated pools '
            '(FK constraints exist but are NOT ENFORCED).</p>'
        )

    # ── dependency rows ────────────────────────────────────────────────────────
    dep_rows = [
        f'<tr>'
        f'<td class="df">{esc(d["ref_schema"])}</td>'
        f'<td class="df">{esc(d["ref_object"])}</td>'
        f'<td>{esc(d["ref_type"])}</td>'
        f'<td class="dt">{esc(d["dep_schema"])}</td>'
        f'<td class="dt">{esc(d["dep_object"])}</td>'
        f'<td>{esc(d["dep_type"])}</td></tr>'
        for d in deps
    ]

    # ── proc/function cards (Procs tab — inline definition expand) ─────────────
    if procs:
        def _proc_card(p):
            key = f"{p['schema_name']}||{p['obj_name']}"
            desc = proc_descriptions.get(key, '')
            desc_html = (
                f'<details><summary>Plain English Summary</summary>'
                f'<p class="view-desc">{esc(desc)}</p>'
                f'</details>'
            ) if desc else ''
            p_icon = '⚙' if p['obj_type'] == 'SQL_STORED_PROCEDURE' else 'ƒ'
            return (
                f'<div class="pc" data-name="{esc(p["schema_name"]).lower()}.{esc(p["obj_name"]).lower()}">'
                f'<h3><span style="margin-right:5px;opacity:.8">{p_icon}</span>{esc(p["schema_name"])}.{esc(p["obj_name"])}</h3>'
                f'<div class="pm">{esc(p["obj_type"])}</div>'
                f'{desc_html}'
                f'<details><summary>Show / hide definition</summary>'
                f'<div class="code-wrap">'
                f'<button class="copy-btn" onclick="copyCode(this)" title="Copy to clipboard">'
                f'<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">'
                f'<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>'
                f'<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>'
                f'</svg>'
                f'<span class="copy-lbl">Copy</span></button>'
                f'<pre>{esc(p["definition"] or "-- definition not available")}</pre>'
                f'</div></details></div>'
            )
        proc_cards = ''.join(_proc_card(p) for p in procs)
        proc_search = (
            '<div class="srch"><input placeholder="Search schema or name…" '
            'oninput="(function(q){{ document.querySelectorAll(\'#p-procs .pc\')'
            '.forEach(c=>c.classList.toggle(\'hidden\',!!q&&!c.dataset.name.includes(q))) }})'
            '(this.value.toLowerCase())"/></div>'
        )
    else:
        proc_cards  = '<p style="color:var(--mut)">No stored procedures or functions found.</p>'
        proc_search = ''

    if views_defs:
        def _view_card(p):
            key = f"{p['schema_name']}||{p['obj_name']}"
            desc = view_descriptions.get(key, '')
            desc_html = (
                f'<details><summary>Plain English Summary</summary>'
                f'<p class="view-desc">{esc(desc)}</p>'
                f'</details>'
            ) if desc else ''
            return (
                f'<div class="pc" data-name="{esc(p["schema_name"]).lower()}.{esc(p["obj_name"]).lower()}">'
                f'<h3><span style="margin-right:5px;opacity:.8">👁</span>{esc(p["schema_name"])}.{esc(p["obj_name"])}</h3>'
                f'<div class="pm">{esc(p["obj_type"])}</div>'
                f'{desc_html}'
                f'<details><summary>Show / hide definition</summary>'
                f'<div class="code-wrap">'
                f'<button class="copy-btn" onclick="copyCode(this)" title="Copy to clipboard">'
                f'<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">'
                f'<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>'
                f'<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>'
                f'</svg>'
                f'<span class="copy-lbl">Copy</span></button>'
                f'<pre>{esc(p["definition"] or "-- definition not available")}</pre>'
                f'</div></details></div>'
            )
        view_cards = ''.join(_view_card(p) for p in views_defs)
        view_search = (
            '<div class="srch"><input placeholder="Search schema or name…" '
            'oninput="(function(q){{ document.querySelectorAll(\'#p-views .pc\')'
            '.forEach(c=>c.classList.toggle(\'hidden\',!!q&&!c.dataset.name.includes(q))) }})'
            '(this.value.toLowerCase())"/></div>'
        )
    else:
        view_cards  = '<p style="color:var(--mut)">No views found.</p>'
        view_search = ''

    obj_rows_html = '\n'.join(obj_rows)
    dep_rows_html = '\n'.join(dep_rows)

    # embed JS data — use replace not f-string to avoid brace conflicts
    js_with_data = (JS
        .replace('__COL_DATA__',  col_data_json)
        .replace('__DEF_MAP__',   def_map_json)
        .replace('__OBJ_META__',  obj_meta_json)
        .replace('__DIST_MAP__',  dist_map_json))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Synapse Metadata — {esc(DATABASE)}</title>
<style>{CSS}.help-fab{{position:fixed;bottom:22px;right:22px;width:38px;height:38px;border-radius:50%;background:var(--acc);color:#fff;font-size:17px;font-weight:700;display:flex;align-items:center;justify-content:center;text-decoration:none;z-index:9999;box-shadow:0 2px 10px rgba(0,0,0,.5);opacity:.8;transition:opacity .15s;line-height:1}}.help-fab:hover{{opacity:1}}</style>
</head>
<body>
<a href="help.html" class="help-fab" title="Help &amp; Guide">?</a>
<div class="layout">

<!-- ── SIDEBAR ── -->
<div class="sidebar">
  <div class="sb-hdr">&#x1F5C4; {esc(DATABASE)}<small>{esc(SERVER)}</small></div>
  <div class="sb-search">
    <input placeholder="Filter objects…" oninput="filterSB(this.value)"/>
  </div>
  <div class="sb-list">{sidebar}</div>
</div>

<!-- ── MAIN ── -->
<div class="main">
  <h1>Synapse Metadata Report</h1>
  <p class="sub">Database: <strong>{esc(DATABASE)}</strong> &nbsp;|&nbsp; Generated: <span id="gen-ts" data-ts="{esc(generated)}">&#x21BB; {esc(generated)}</span><script>(function(){{var s=document.getElementById(\'gen-ts\'),h=(Date.now()-new Date(s.dataset.ts.replace(\' \',\'T\')))/36e5;s.style.color=h<25?'var(--grn)':h<168?'var(--yel)':'var(--red)';s.style.fontWeight='700';}})();</script></p>

  <div class="stats">
    <div class="sc" id="card-overview" onclick="showTab('overview',null)" title="Schemas are logical namespaces that group related database objects (tables, views, procs) together by business domain or team. For example: dbo, stg, mart, ref."><div class="sc-n">{len(schemas)}</div><div class="sc-l">Schemas</div></div>
    <div class="sc" id="card-objects"  onclick="showTab('objects',null)"  title="Tables are the primary data storage objects in the dedicated SQL pool. In Synapse these are typically distributed across 60 distributions using HASH or ROUND_ROBIN, with columnstore or heap storage."><div class="sc-n">{n_tables}</div><div class="sc-l">Tables</div></div>
    <div class="sc" id="card-views"    onclick="showTab('views',null)"    title="Views are virtual tables defined by a saved SELECT query. They store no data themselves — they run the underlying query at read time. Often used to simplify complex joins or expose a cleaner schema to BI tools."><div class="sc-n">{n_views}</div><div class="sc-l">Views</div></div>
    <div class="sc" id="card-procs"    onclick="showTab('procs',null)"    title="Stored procedures and user-defined functions contain reusable T-SQL logic. Procedures are called by ADF pipelines or scheduled jobs to perform ETL transformations, aggregations, or data loads within the SQL pool."><div class="sc-n">{n_procs}</div><div class="sc-l">Procs / Functions</div></div>
    <div class="sc" id="card-fkeys"    onclick="showTab('fkeys',null)"    title="Foreign keys define referential integrity relationships between tables (e.g. fact table referencing a dimension). In Synapse dedicated pools, foreign keys are NOT enforced — they are metadata only, used by query optimizers and BI tools for auto-join suggestions."><div class="sc-n">{len(fks)}</div><div class="sc-l">Foreign Keys</div></div>
    <div class="sc" id="card-deps"     onclick="showTab('deps',null)"     title="Dependencies track which database objects reference other objects — for example, a view that reads from a table, or a stored procedure that calls another procedure. Useful for impact analysis before renaming or dropping an object."><div class="sc-n">{len(deps)}</div><div class="sc-l">Dependencies</div></div>
    <div class="sc" id="card-columns"  onclick="showTab('columns',null)"  title="Total number of columns across all tables in the database. Click to search and browse every column name, data type, and which table it belongs to."><div class="sc-n">{len(columns):,}</div><div class="sc-l">Columns</div></div>
  </div>

  <div class="tabs">
    <div class="tab active" id="tab-overview" onclick="showTab('overview',this)">Overview</div>
    <div class="tab"        id="tab-objects"  onclick="showTab('objects',this)">All Objects</div>
    <div class="tab"        id="tab-columns"  onclick="showTab('columns',this)">Columns</div>
    <div class="tab"        id="tab-fkeys"    onclick="showTab('fkeys',this)">Foreign Keys</div>
    <div class="tab"        id="tab-deps"     onclick="showTab('deps',this)">Dependencies</div>
    <div class="tab"        id="tab-views"    onclick="showTab('views',this)">Views</div>
    <div class="tab"        id="tab-procs"    onclick="showTab('procs',this)">Procs / Functions</div>
  </div>

  <!-- OVERVIEW -->
  <div class="panel active" id="p-overview">
    <p style="font-size:12px;color:var(--mut);margin-bottom:12px">Click any schema card to filter the All Objects tab. Click any object in the sidebar or table to view its definition.</p>
    <div class="sg">{cards}</div>
  </div>

  <!-- ALL OBJECTS -->
  <div class="panel" id="p-objects">
    <div class="srch">
      <input id="obj-q" placeholder="Search schema, name, type…" oninput="ft('obj-tbl',this.value)"/>
    </div>
    <div class="tw">
      <table id="obj-tbl">
        <thead><tr><th>Schema</th><th>Object Name</th><th>Type</th><th>Created</th><th>Modified</th><th>Rows</th></tr></thead>
        <tbody>{obj_rows_html}</tbody>
      </table>
    </div>
  </div>

  <!-- COLUMNS -->
  <div class="panel" id="p-columns">
    <div class="srch">
      <input id="col-q" placeholder="Search schema, table, or column name…"
             oninput="renderColTab(this.value)"/>
    </div>
    <div id="col-count" style="font-size:11px;color:var(--mut);margin-bottom:8px"></div>
    <div class="tw">
      <table id="col-tbl">
        <thead><tr><th>Schema</th><th>Table / View</th><th>#</th><th>Column</th><th>Type</th><th>Length</th><th>Nullable</th><th>Default</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
    <div id="col-sentinel" style="height:1px"></div>
  </div>

  <!-- FOREIGN KEYS -->
  <div class="panel" id="p-fkeys">
    <div class="srch">
      <input placeholder="Search tables or columns…" oninput="ft('fk-tbl',this.value)"/>
    </div>
    {fk_content}
  </div>

  <!-- DEPENDENCIES -->
  <div class="panel" id="p-deps">
    <div class="srch">
      <input placeholder="Search object name or type…" oninput="ft('dep-tbl',this.value)"/>
    </div>
    <div class="tw">
      <table id="dep-tbl">
        <thead><tr><th>Referencing Schema</th><th>Referencing Object</th><th>Type</th><th>Depends-On Schema</th><th>Depends-On Object</th><th>Dep Type</th></tr></thead>
        <tbody>{dep_rows_html}</tbody>
      </table>
    </div>
  </div>

  <!-- VIEWS -->
  <div class="panel" id="p-views">
    {view_search}
    <div>{view_cards}</div>
  </div>

  <!-- PROCEDURES / FUNCTIONS -->
  <div class="panel" id="p-procs">
    {proc_search}
    <div>{proc_cards}</div>
  </div>

</div><!-- /main -->
</div><!-- /layout -->

<!-- ── OBJECT DETAIL MODAL ── -->
<div id="obj-modal" class="modal-overlay" style="display:none">
  <div class="modal-box">
    <div class="modal-hdr">
      <div class="modal-hdr-title">
        <span id="modal-badge" class="chip"></span>
        <span><span id="modal-schema" style="color:var(--mut)"></span><span id="modal-name" class="name"></span></span>
      </div>
      <button class="modal-close" onclick="closeModal()" title="Close (Esc)">&#x2715;</button>
    </div>
    <div id="modal-meta-row" class="modal-meta-row"></div>
    <div id="modal-ai-desc" class="modal-ai-desc" style="display:none;margin:0 16px 4px"></div>
    <div class="modal-tabs">
      <button id="mtab-cols"    class="mtab active" onclick="showModalTab('cols')">Columns</button>
      <button id="mtab-ddl"     class="mtab"        onclick="showModalTab('ddl')">DDL / Definition</button>
      <button id="mtab-select"  class="mtab"        onclick="showModalTab('select')">Select</button>
    </div>
    <div class="modal-body">
      <div id="modal-cols-body"></div>
      <div id="modal-ddl-body"    style="display:none"></div>
      <div id="modal-select-body" style="display:none"></div>
    </div>
  </div>
</div>

<script>{js_with_data}</script>
</body>
</html>"""

# ── entry point ────────────────────────────────────────────────────────────────

def main():
    print("Connecting to Azure Synapse…")
    conn = get_connection()
    print("Connected.\n")

    def run(label, sql):
        print(f"  Querying {label}…", end="", flush=True)
        try:
            rows = query(conn, sql)
            print(f" {len(rows)} rows")
            return rows
        except Exception as e:
            print(f" ERROR: {e}")
            return []

    schemas       = run("schemas",               SCHEMAS_SQL)
    objects       = run("objects",               OBJECTS_SQL)
    columns       = run("columns",               COLUMNS_SQL)
    defs          = run("view/proc definitions",  DEFS_SQL)
    fks           = run("foreign keys",           FK_SQL)
    deps          = run("dependencies",           DEPS_SQL)
    row_counts    = run("row counts",             ROW_COUNTS_SQL)
    distributions = run("distributions",          DIST_SQL)
    indexes       = run("indexes",                INDEX_SQL)
    conn.close()

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    print("\nBuilding HTML…")
    html = build_html(schemas, objects, columns, fks, deps, defs, row_counts, generated,
                      distributions=distributions, indexes=indexes)

    out = "/home/thedavidporter/synapse_metadata_report_prd.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)

    # Save daily JSON snapshot for delta reporting
    import os, json as _json
    snap_dir = "/home/thedavidporter/snapshots"
    os.makedirs(snap_dir, exist_ok=True)
    snap_file = f"{snap_dir}/synapse_prd_{datetime.now().strftime('%Y%m%d')}.json"
    snapshot = {
        "generated": datetime.now().isoformat(),
        "database":  DATABASE,
        "objects":   objects,
        "columns":   columns,
        "row_counts": row_counts,
    }
    with open(snap_file, "w", encoding="utf-8") as f:
        _json.dump(snapshot, f, indent=2, default=str)
    print(f"  Snapshot saved to: {snap_file}")

    tc = {}
    for o in objects:
        tc[o['object_type']] = tc.get(o['object_type'], 0) + 1
    procs = [d for d in defs if 'TABLE' not in d['obj_type'] and d['obj_type'] != 'VIEW']

    print(f"\nReport saved to:\n  {out}\n")
    print(f"Summary:")
    print(f"  Schemas              : {len(schemas)}")
    print(f"  Tables               : {sum(v for k,v in tc.items() if 'TABLE' in k)}")
    print(f"  Views                : {tc.get('VIEW',0)}")
    print(f"  Procedures/Functions : {len(procs)}")
    print(f"  Definitions (total)  : {len(defs)}")
    print(f"  Foreign Keys         : {len(fks)}")
    print(f"  Object Dependencies  : {len(deps)}")
    print(f"  Columns              : {len(columns):,}")


    if not os.environ.get('PUBLISH_RUNNING'):
        try:
            import generate_metadata_index
            generate_metadata_index.main()
            print("  Index updated       : index.html")
        except Exception as exc:
            print(f"  Warning: could not update index.html: {exc}")

if __name__ == "__main__":
    main()
