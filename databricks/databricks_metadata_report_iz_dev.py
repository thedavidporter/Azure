#!/usr/bin/env python3
"""
Databricks Hive Metastore – Metadata Analysis Report
Workspace: zus1-idoh-iz-dev-v2-dbrk (adb-612192313963696.16.azuredatabricks.net)
Queries all databases, tables, views, columns, and DDL definitions,
then writes a self-contained interactive HTML report.
"""

import json
from collections import defaultdict
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from databricks import sql as dbsql

HOSTNAME  = "adb-612192313963696.16.azuredatabricks.net"
HTTP_PATH = "/sql/1.0/warehouses/c4236c65864c7aa3"
TOKEN     = os.environ.get("DATABRICKS_IZDEV_TOKEN", "")  # set env var or replace with your token
WORKSPACE = "adb-612192313963696"

# ── connection ─────────────────────────────────────────────────────────────────

def connect():
    return dbsql.connect(
        server_hostname=HOSTNAME,
        http_path=HTTP_PATH,
        access_token=TOKEN
    )

def qry(conn, sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        if not cur.description:
            return []
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

def qry_one(conn, sql):
    rows = qry(conn, sql)
    return rows[0] if rows else {}

# ── fetch helpers ──────────────────────────────────────────────────────────────

def get_databases(conn):
    rows = qry(conn, "SHOW DATABASES")
    return [r.get('databaseName') or r.get('namespace') or list(r.values())[0]
            for r in rows]

def get_tables(conn, db):
    rows = qry(conn, f"SHOW TABLES IN `{db}`")
    out = []
    for r in rows:
        name = r.get('tableName') or r.get('table_name') or ''
        is_tmp = str(r.get('isTemporary', 'false')).lower() == 'true'
        if name and not is_tmp:
            out.append(name)
    return out

def get_views(conn, db):
    try:
        rows = qry(conn, f"SHOW VIEWS IN `{db}`")
        return {r.get('viewName') or r.get('view_name') or ''
                for r in rows if r.get('viewName') or r.get('view_name')}
    except Exception:
        return set()

def get_columns(conn, db, tbl):
    try:
        rows = qry(conn, f"DESCRIBE `{db}`.`{tbl}`")
        cols = []
        for r in rows:
            name = r.get('col_name') or r.get('column_name') or ''
            dtype = r.get('data_type') or r.get('type') or ''
            comment = r.get('comment') or ''
            # DESCRIBE returns partition info after a blank separator row
            if not name or name.startswith('#'):
                break
            cols.append({'name': name, 'type': dtype, 'comment': comment})
        return cols
    except Exception:
        return []

def get_ddl(conn, db, tbl):
    try:
        rows = qry(conn, f"SHOW CREATE TABLE `{db}`.`{tbl}`")
        if rows:
            return list(rows[0].values())[0] or ''
        return ''
    except Exception:
        return ''

def get_extended(conn, db, tbl):
    """Returns dict with format, location, row_count, size_bytes, comment, tbltype."""
    import re
    info = {'format': '', 'location': '', 'row_count': '', 'size_bytes': '', 'comment': '', 'tbl_type': ''}
    try:
        rows = qry(conn, f"DESCRIBE TABLE EXTENDED `{db}`.`{tbl}`")
        for r in rows:
            k = (r.get('col_name') or '').strip().lower()
            v = (r.get('data_type') or r.get('type') or '').strip()
            if k == 'provider':           info['format']    = v
            elif k == 'location':         info['location']  = v
            elif k == 'comment':          info['comment']   = v
            elif k == 'type':             info['tbl_type']  = v
            elif 'numrows' in k or k == 'statistics':
                m = re.search(r'([\d,]+)\s+rows', v, re.I)
                if m: info['row_count'] = m.group(1).replace(',', '')
                m = re.search(r'([\d,]+)\s+bytes', v, re.I)
                if m: info['size_bytes'] = m.group(1).replace(',', '')
    except Exception:
        pass
    if info['size_bytes'] in ('', '0'):
        try:
            rows2 = qry(conn, f"DESCRIBE DETAIL `{db}`.`{tbl}`")
            if rows2:
                val = list(rows2[0].asDict().values())
                d = rows2[0].asDict()
                sb = d.get('sizeInBytes') or d.get('sizeinbytes') or 0
                if sb:
                    info['size_bytes'] = str(sb)
        except Exception:
            pass
    if info['row_count'] in ('', '0') and info['size_bytes'] not in ('', '0'):
        try:
            rows3 = qry(conn, f"SELECT COUNT(*) AS n FROM `{db}`.`{tbl}`")
            if rows3:
                info['row_count'] = str(list(rows3[0].values())[0])
        except Exception:
            pass
    return info

# ── helpers ────────────────────────────────────────────────────────────────────

def esc(s):
    if s is None: return ''
    return str(s).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('"','&quot;')

def js_esc(s):
    if s is None: return ''
    return str(s).replace('\\','\\\\').replace("'","\\'").replace('"','\\"').replace('\n','\\n').replace('\r','')

# ── CSS ────────────────────────────────────────────────────────────────────────

CSS = """
:root{--bg:#0c0e14;--sur:#141720;--sur2:#1e2130;--brd:#272c3e;
  --txt:#e2e8f0;--mut:#6b7898;--acc:#ff5f2e;--grn:#4ade80;
  --red:#f87171;--pur:#c084fc;--cyn:#22d3ee;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font:14px/1.5 'Segoe UI',system-ui,sans-serif;overflow:hidden}
.layout{display:flex;height:100vh}

/* sidebar */
.sidebar{width:270px;min-width:180px;background:var(--sur);border-right:1px solid var(--brd);
  display:flex;flex-direction:column;overflow:hidden;flex-shrink:0}
.sb-hdr{padding:12px 14px;border-bottom:1px solid var(--brd);font-weight:700;font-size:13px;line-height:1.4}
.sb-hdr small{display:block;color:var(--mut);font-weight:400;font-size:11px;margin-top:2px}
.sb-search{padding:7px 10px;border-bottom:1px solid var(--brd)}
.sb-search input{width:100%;padding:5px 9px;background:var(--sur2);border:1px solid var(--brd);
  border-radius:6px;color:var(--txt);font-size:12px;outline:none}
.sb-search input:focus{border-color:var(--acc)}
.sb-list{overflow-y:auto;flex:1;padding-bottom:12px}
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

/* main */
.main{flex:1;overflow-y:auto;padding:22px 26px}
h1{font-size:20px;font-weight:700;margin-bottom:2px}
.sub{color:var(--mut);font-size:12px;margin-bottom:20px}
.stats{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:22px}
.sc{background:var(--sur);border:1px solid var(--brd);border-radius:10px;padding:13px 16px;min-width:100px}
.sc-n{font-size:24px;font-weight:700;line-height:1}
.sc-l{font-size:11px;color:var(--mut);margin-top:3px}
.tabs{display:flex;gap:2px;border-bottom:2px solid var(--brd);margin-bottom:16px;flex-wrap:wrap}
.tab{padding:7px 14px;cursor:pointer;border-radius:6px 6px 0 0;font-size:12px;font-weight:600;
  color:var(--mut);border:1px solid transparent;border-bottom:none;margin-bottom:-2px;user-select:none}
.tab:hover{color:var(--txt);background:var(--sur)}
.tab.active{background:var(--sur);border-color:var(--brd);border-bottom-color:var(--sur);color:var(--txt)}
.panel{display:none}.panel.active{display:block}
.srch{margin-bottom:12px}
.srch input{padding:7px 11px;background:var(--sur);border:1px solid var(--brd);
  border-radius:6px;color:var(--txt);font-size:12px;width:100%;max-width:420px;outline:none}
.srch input:focus{border-color:var(--acc)}
.tw{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:12px}
th{background:var(--sur2);padding:7px 11px;text-align:left;font-weight:700;
  border-bottom:2px solid var(--brd);white-space:nowrap;position:sticky;top:0;z-index:1}
td{padding:5px 11px;border-bottom:1px solid var(--brd);vertical-align:top;
  max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
tr:hover td{background:var(--sur)}
#obj-tbl tbody tr{cursor:pointer}
.chip{display:inline-block;font-size:10px;padding:1px 6px;border-radius:3px;white-space:nowrap}
.chip-TABLE{background:#1e3a5f;color:#60a5fa}
.chip-VIEW{background:#1a3a2a;color:#4ade80}
.fmt{font-size:10px;padding:1px 5px;border-radius:3px;background:#2a1e3a;color:#c084fc}
.hidden{display:none!important}
.sg{display:grid;grid-template-columns:repeat(auto-fill,minmax(195px,1fr));gap:9px;margin-bottom:18px}
.sc2{background:var(--sur);border:1px solid var(--brd);border-radius:8px;padding:11px 13px;cursor:pointer}
.sc2:hover{border-color:var(--acc)}
.sc2 h3{font-size:12px;color:var(--acc);margin-bottom:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sc2 .ct{display:flex;gap:10px;font-size:11px}
.sc2 .ct span{color:var(--mut)} .sc2 .ct strong{color:var(--txt)}
.rc{color:var(--mut);font-size:11px}
.sc-link{cursor:pointer;transition:border-color .15s}
.sc-link:hover{border-color:var(--acc)!important}
.sc-link:hover .sc-l{color:var(--acc)}
pre{background:var(--sur2);padding:11px;border-radius:6px;overflow-x:auto;
  font-size:11px;line-height:1.6;white-space:pre-wrap;word-break:break-word;margin-top:0}
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

/* modal */
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
.modal-col-tbl th{background:var(--sur2);padding:6px 10px;text-align:left;font-weight:700;
  border-bottom:2px solid var(--brd);position:sticky;top:0;z-index:1;white-space:nowrap}
.modal-col-tbl td{padding:4px 10px;border-bottom:1px solid var(--brd);vertical-align:top}
.modal-col-tbl tr:hover td{background:var(--sur2)}
.modal-col-tbl .col-name{font-weight:600}
.modal-col-tbl .col-type{color:#93c5fd;font-family:monospace;font-size:11px}
.modal-col-tbl .col-comment{color:var(--mut);font-size:11px;max-width:300px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.modal-ddl pre{background:var(--sur2);padding:14px 16px;border-radius:8px;font-size:11.5px;
  line-height:1.7;white-space:pre;word-break:normal;overflow-x:auto;
  border:1px solid var(--brd);color:#e2e8f0;max-height:none;margin-top:0}
.modal-empty{color:var(--mut);padding:20px 0;font-size:13px}
.kw{color:#c084fc} .str-lit{color:#86efac} .cm{color:#6b7898;font-style:italic}
"""

# ── JavaScript ─────────────────────────────────────────────────────────────────

JS = r"""
function escH(s){
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function showTab(id,el){
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.getElementById('p-'+id).classList.add('active');
  el.classList.add('active');
  if(id==='columns'&&!colTabReady) renderColTab(document.getElementById('col-q').value);
}
function ft(tid,q){
  q=q.toLowerCase().trim();
  document.querySelectorAll('#'+tid+' tbody tr').forEach(tr=>{
    const hay=tr.textContent.toLowerCase()+' '+(tr.dataset.key||'');
    tr.classList.toggle('hidden',!!q&&!hay.includes(q));
  });
}
function filterSB(q){
  q=q.toLowerCase().trim();
  document.querySelectorAll('.sch-item').forEach(item=>{
    const rows=item.querySelectorAll('.obj-row');
    let any=false;
    rows.forEach(r=>{
      const show=!q||(r.dataset.n||'').includes(q);
      r.classList.toggle('hidden',!show);
      if(show) any=true;
    });
    const schName=(item.querySelector('.sch-hdr')||{}).textContent||'';
    const schMatch=!q||schName.toLowerCase().includes(q);
    item.classList.toggle('hidden',!!q&&!any&&!schMatch);
    if(q&&(any||schMatch)) item.querySelector('.sch-hdr').classList.add('open');
  });
}
document.querySelectorAll('.sch-hdr').forEach(h=>{
  h.addEventListener('click',()=>h.classList.toggle('open'));
});
function filterByDb(db){
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.getElementById('p-objects').classList.add('active');
  document.getElementById('tab-objects').classList.add('active');
  document.getElementById('obj-q').value=db;
  ft('obj-tbl',db);
}
function filterByType(type){
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.getElementById('p-objects').classList.add('active');
  document.getElementById('tab-objects').classList.add('active');
  document.getElementById('obj-q').value='';
  document.querySelectorAll('#obj-tbl tbody tr').forEach(tr=>{
    const chip=tr.querySelector('.chip');
    const match=!type||(chip&&chip.textContent.trim()===type);
    tr.classList.toggle('hidden',!match);
  });
}
function goToTab(id){
  const el=document.querySelector(`.tab[onclick*="${id}"]`);
  if(el) showTab(id,el);
}

// ── column data (compact JSON) ────────────────────────────────────────────────
// format: [db, table, ordinal, col_name, data_type, comment]
const COL_DATA = __COL_DATA__;
const COL_MAP  = {};
for(const c of COL_DATA){
  const key=c[0]+'||'+c[1];
  if(!COL_MAP[key]) COL_MAP[key]=[];
  COL_MAP[key].push({ord:c[2],name:c[3],type:c[4],comment:c[5]});
}

const COL_PAGE  = 200;
let colFiltered = [];
let colOffset   = 0;
let colTabReady = false;

function colRow(c){
  return '<tr>'
    +'<td>'+escH(c[0])+'</td>'
    +'<td>'+escH(c[1])+'</td>'
    +'<td>'+c[2]+'</td>'
    +'<td>'+escH(c[3])+'</td>'
    +'<td>'+escH(c[4])+'</td>'
    +'<td>'+escH(c[5])+'</td>'
    +'</tr>';
}
function updateColLabel(){
  const total=colFiltered.length;
  const shown=Math.min(colOffset,total);
  const lq=(document.getElementById('col-q').value||'').trim();
  document.getElementById('col-count').textContent=shown>=total
    ?(lq?`${total.toLocaleString()} matching columns`:`All ${total.toLocaleString()} columns`)
    :`Showing ${shown.toLocaleString()} of ${total.toLocaleString()} — scroll for more`;
}
function appendColRows(){
  const batch=colFiltered.slice(colOffset,colOffset+COL_PAGE);
  if(!batch.length) return;
  document.querySelector('#col-tbl tbody').insertAdjacentHTML('beforeend',batch.map(colRow).join(''));
  colOffset+=batch.length;
  updateColLabel();
}
function renderColTab(q){
  q=(q||'').toLowerCase().trim();
  colFiltered=q
    ?COL_DATA.filter(c=>c[0].toLowerCase().includes(q)||c[1].toLowerCase().includes(q)||c[3].toLowerCase().includes(q)||c[4].toLowerCase().includes(q))
    :COL_DATA;
  colOffset=0;
  document.querySelector('#col-tbl tbody').innerHTML='';
  appendColRows();
  colTabReady=true;
}
const colObserver=new IntersectionObserver(entries=>{
  if(entries[0].isIntersecting&&colTabReady) appendColRows();
},{root:document.querySelector('.main'),rootMargin:'200px'});
colObserver.observe(document.getElementById('col-sentinel'));

// ── object metadata + DDL maps ────────────────────────────────────────────────
const OBJ_META = __OBJ_META__;
const DDL_MAP  = __DDL_MAP__;

// ── SQL syntax highlight ──────────────────────────────────────────────────────
function copyCode(btn){
  const pre=btn.closest('.code-wrap').querySelector('pre');
  navigator.clipboard.writeText(pre.textContent).then(()=>{
    btn.classList.add('copied');
    btn.querySelector('.copy-lbl').textContent='Copied!';
    setTimeout(()=>{btn.classList.remove('copied');btn.querySelector('.copy-lbl').textContent='Copy';},2000);
  }).catch(()=>{});
}

function hlSQL(code){
  const KW=/\b(SELECT|FROM|WHERE|JOIN|LEFT|RIGHT|INNER|OUTER|FULL|CROSS|ON|AND|OR|NOT|IN|IS|NULL|AS|WITH|UNION|ALL|DISTINCT|GROUP\s+BY|ORDER\s+BY|HAVING|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TABLE|VIEW|FUNCTION|USING|PARTITIONED\s+BY|CLUSTERED\s+BY|STORED\s+AS|LOCATION|TBLPROPERTIES|COMMENT|ROW\s+FORMAT|FIELDS\s+TERMINATED|LINES\s+TERMINATED|EXTERNAL|REPLACE|EXISTS|IF|THEN|ELSE|END|CASE|WHEN|RETURN|SET|DECLARE|CAST|COALESCE|NULLIF|COUNT|SUM|MAX|MIN|AVG|OVER|PARTITION\s+BY|DELTA|PARQUET|ORC|CSV|JSON)\b/gi;
  const STR=/('[^']*'|`[^`]*`)/g;
  const CMT=/(--[^\n]*)|(\/\*[\s\S]*?\*\/)/g;
  return escH(code)
    .replace(CMT,m=>`<span class="cm">${m}</span>`)
    .replace(STR,m=>`<span class="str-lit">${m}</span>`)
    .replace(KW,m=>`<span class="kw">${m}</span>`);
}

// ── detail modal ──────────────────────────────────────────────────────────────
function openDetail(key,el){
  const sep=key.indexOf('||');
  const db=key.slice(0,sep), name=key.slice(sep+2);
  const cols=COL_MAP[key]||[];
  const ddl=DDL_MAP[key]||'';
  const meta=OBJ_META[key]||{};

  document.getElementById('modal-badge').textContent=meta.type||'TABLE';
  document.getElementById('modal-badge').className='chip chip-'+(meta.type||'TABLE');
  document.getElementById('modal-schema').textContent=db+'.';
  document.getElementById('modal-name').textContent=name;

  const parts=[];
  if(meta.format)    parts.push(`<span>Format <strong>${escH(meta.format)}</strong></span>`);
  if(meta.tbl_type)  parts.push(`<span>Type <strong>${escH(meta.tbl_type)}</strong></span>`);
  if(meta.row_count) parts.push(`<span>Rows <strong>${Number(meta.row_count).toLocaleString()}</strong></span>`);
  if(meta.size_bytes)parts.push(`<span>Size <strong>${(Number(meta.size_bytes)/1073741824).toFixed(2)} GB</strong></span>`);
  if(cols.length)    parts.push(`<span>Columns <strong>${cols.length}</strong></span>`);
  if(meta.comment)   parts.push(`<span style="color:var(--mut);font-style:italic">${escH(meta.comment)}</span>`);
  document.getElementById('modal-meta-row').innerHTML=parts.join('');

  // columns tab
  const mtabCols=document.getElementById('mtab-cols');
  if(cols.length){
    const rows=cols.map((c,i)=>
      '<tr>'
      +'<td style="color:var(--mut);text-align:right;width:36px">'+(i+1)+'</td>'
      +'<td class="col-name">'+escH(c.name)+'</td>'
      +'<td class="col-type">'+escH(c.type)+'</td>'
      +'<td class="col-comment">'+escH(c.comment||'')+'</td>'
      +'</tr>'
    ).join('');
    document.getElementById('modal-cols-body').innerHTML=
      '<table class="modal-col-tbl"><thead><tr><th>#</th><th>Column</th><th>Data Type</th><th>Comment</th></tr></thead>'
      +'<tbody>'+rows+'</tbody></table>';
    mtabCols.style.display='';
  } else {
    document.getElementById('modal-cols-body').innerHTML='<p class="modal-empty">No column metadata available.</p>';
    mtabCols.style.display='none';
  }

  // DDL tab
  const mtabDDL=document.getElementById('mtab-ddl');
  if(ddl){
    document.getElementById('modal-ddl-body').innerHTML='<div class="modal-ddl"><div class="code-wrap"><button class="copy-btn" onclick="copyCode(this)" title="Copy to clipboard"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg><span class="copy-lbl">Copy</span></button><pre>'+hlSQL(ddl)+'</pre></div></div>';
    mtabDDL.style.display='';
  } else {
    document.getElementById('modal-ddl-body').innerHTML='<p class="modal-empty">DDL not available.</p>';
    mtabDDL.style.display='none';
  }

  showModalTab(cols.length?'cols':'ddl');
  document.getElementById('obj-modal').style.display='flex';
  document.querySelectorAll('.obj-row').forEach(r=>r.classList.remove('active'));
  if(el) el.classList.add('active');
}
function closeModal(){document.getElementById('obj-modal').style.display='none';}
function showModalTab(tab){
  document.querySelectorAll('.mtab').forEach(t=>t.classList.remove('active'));
  const a=document.getElementById('mtab-'+tab);
  if(a) a.classList.add('active');
  document.getElementById('modal-cols-body').style.display=tab==='cols'?'':'none';
  document.getElementById('modal-ddl-body').style.display=tab==='ddl'?'':'none';
}
document.getElementById('obj-modal').addEventListener('click',e=>{
  if(e.target===document.getElementById('obj-modal')) closeModal();
});
document.addEventListener('keydown',e=>{if(e.key==='Escape') closeModal();});
"""

# ── HTML builder ───────────────────────────────────────────────────────────────

def build_html(databases, objects, col_data, generated):
    col_data_json = json.dumps(col_data, ensure_ascii=False, separators=(',', ':'))

    obj_meta = {o['key']: {
        'type':      o['obj_type'],
        'format':    o.get('format', ''),
        'tbl_type':  o.get('tbl_type', ''),
        'row_count': o.get('row_count', ''),
        'size_bytes':o.get('size_bytes', ''),
        'comment':   o.get('comment', ''),
    } for o in objects}

    ddl_map = {o['key']: o.get('ddl', '') for o in objects if o.get('ddl')}

    obj_meta_json = json.dumps(obj_meta, ensure_ascii=False, separators=(',', ':'))
    ddl_map_json  = json.dumps(ddl_map,  ensure_ascii=False, separators=(',', ':'))

    # ── sidebar ────────────────────────────────────────────────────────────────
    obs_by_db = defaultdict(list)
    for o in objects:
        obs_by_db[o['db']].append(o)

    sb = []
    for db in sorted(obs_by_db):
        sb.append(
            f'<div class="sch-item">'
            f'<div class="sch-hdr"><span class="arr">&#x25B6;</span>{esc(db)}</div>'
            f'<div class="sch-body">'
        )
        for o in sorted(obs_by_db[db], key=lambda x: (x['obj_type'], x['name'])):
            b   = 'V' if o['obj_type'] == 'VIEW' else 'T'
            key = js_esc(f"{o['db']}||{o['name']}")
            dn  = esc(o['name'])
            dn_l = esc(f"{o['db']}.{o['name']}".lower())
            sb.append(
                f'<div class="obj-row" data-n="{dn_l}" onclick="openDetail(\'{key}\',this)">'
                f'<span class="bdg bdg-{b}">{b}</span>{dn}</div>'
            )
        sb.append('</div></div>')
    sidebar = '\n'.join(sb)

    # ── database cards ─────────────────────────────────────────────────────────
    db_counts = defaultdict(lambda: [0, 0])
    for o in objects:
        if o['obj_type'] == 'VIEW': db_counts[o['db']][1] += 1
        else:                       db_counts[o['db']][0] += 1

    cards = ''.join(
        f'<div class="sc2" onclick="filterByDb(\'{js_esc(db)}\')" title="Click to filter">'
        f'<h3>{esc(db)}</h3>'
        f'<div class="ct"><span><strong>{c[0]}</strong> tables</span>'
        f'<span><strong>{c[1]}</strong> views</span></div></div>'
        for db, c in sorted(db_counts.items())
    )

    # ── objects table ──────────────────────────────────────────────────────────
    obj_rows = []
    for o in objects:
        key    = esc(f"{o['db']}||{o['name']}")
        js_key = js_esc(f"{o['db']}||{o['name']}")
        chip   = f'<span class="chip chip-{o["obj_type"]}">{o["obj_type"]}</span>'
        fmt    = f'<span class="fmt">{esc(o.get("format",""))}</span>' if o.get('format') else ''
        rc     = o.get('row_count','')
        rc_s   = f'<span class="rc">{int(rc):,}</span>' if rc else ''
        sz     = o.get('size_bytes','')
        sz_s   = f'<span class="rc">{float(sz)/1073741824:.2f} GB</span>' if sz else ''
        obj_rows.append(
            f'<tr data-key="{key}" onclick="openDetail(\'{js_key}\')" title="Click to view definition">'
            f'<td>{esc(o["db"])}</td><td>{esc(o["name"])}</td>'
            f'<td>{chip}</td><td>{fmt}</td>'
            f'<td>{rc_s}</td><td>{sz_s}</td>'
            f'<td style="max-width:200px">{esc(o.get("comment",""))}</td></tr>'
        )

    n_tables = sum(1 for o in objects if o['obj_type'] != 'VIEW')
    n_views  = sum(1 for o in objects if o['obj_type'] == 'VIEW')
    n_cols   = len(col_data)
    n_dbs    = len(databases)

    obj_rows_html = '\n'.join(obj_rows)

    js_with_data = (JS
        .replace('__COL_DATA__', col_data_json)
        .replace('__OBJ_META__', obj_meta_json)
        .replace('__DDL_MAP__',  ddl_map_json))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Databricks Metadata — {esc(WORKSPACE)} (IZ Dev)</title>
<style>{CSS}</style>
</head>
<body>
<div class="layout">

<div class="sidebar">
  <div class="sb-hdr">&#x1F9F1; Databricks Hive Metastore<small>{esc(HOSTNAME)}</small></div>
  <div class="sb-search"><input placeholder="Filter objects…" oninput="filterSB(this.value)"/></div>
  <div class="sb-list">{sidebar}</div>
</div>

<div class="main">
  <h1>Databricks Metadata Report <span style="font-size:13px;color:var(--mut);font-weight:400">(IZ Dev)</span></h1>
  <p class="sub">Workspace: <strong>{esc(HOSTNAME)}</strong> &nbsp;|&nbsp; Generated: {esc(generated)}</p>

  <div class="stats">
    <div class="sc sc-link" onclick="goToTab('overview')" title="View all databases">
      <div class="sc-n">{n_dbs}</div><div class="sc-l">Databases</div></div>
    <div class="sc sc-link" onclick="filterByType('TABLE')" title="View all tables">
      <div class="sc-n">{n_tables}</div><div class="sc-l">Tables &#x2192;</div></div>
    <div class="sc sc-link" onclick="filterByType('VIEW')" title="View all views">
      <div class="sc-n">{n_views}</div><div class="sc-l">Views &#x2192;</div></div>
    <div class="sc sc-link" onclick="goToTab('columns')" title="Browse all columns">
      <div class="sc-n">{n_cols:,}</div><div class="sc-l">Columns &#x2192;</div></div>
  </div>

  <div class="tabs">
    <div class="tab active" onclick="showTab('overview',this)">Overview</div>
    <div class="tab" onclick="showTab('objects',this)" id="tab-objects">All Objects</div>
    <div class="tab" onclick="showTab('columns',this)">Columns</div>
  </div>

  <div class="panel active" id="p-overview">
    <p style="font-size:12px;color:var(--mut);margin-bottom:12px">Click any database card to filter objects. Click any object in the sidebar or table to view its columns and DDL.</p>
    <div class="sg">{cards}</div>
  </div>

  <div class="panel" id="p-objects">
    <div class="srch">
      <input id="obj-q" placeholder="Search database, name, type, format…" oninput="ft('obj-tbl',this.value)"/>
    </div>
    <div class="tw">
      <table id="obj-tbl">
        <thead><tr><th>Database</th><th>Name</th><th>Type</th><th>Format</th><th>Rows</th><th>Size</th><th>Comment</th></tr></thead>
        <tbody>{obj_rows_html}</tbody>
      </table>
    </div>
  </div>

  <div class="panel" id="p-columns">
    <div class="srch">
      <input id="col-q" placeholder="Search database, table, or column name…" oninput="renderColTab(this.value)"/>
    </div>
    <div id="col-count" style="font-size:11px;color:var(--mut);margin-bottom:8px"></div>
    <div class="tw">
      <table id="col-tbl">
        <thead><tr><th>Database</th><th>Table / View</th><th>#</th><th>Column</th><th>Type</th><th>Comment</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
    <div id="col-sentinel" style="height:1px"></div>
  </div>

</div>
</div>

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
    <div class="modal-tabs">
      <button id="mtab-cols" class="mtab active" onclick="showModalTab('cols')">Columns</button>
      <button id="mtab-ddl"  class="mtab"        onclick="showModalTab('ddl')">DDL / Definition</button>
    </div>
    <div class="modal-body">
      <div id="modal-cols-body"></div>
      <div id="modal-ddl-body" style="display:none"></div>
    </div>
  </div>
</div>

<script>{js_with_data}</script>
</body>
</html>"""

# ── main ───────────────────────────────────────────────────────────────────────

def main():
    print("Connecting to Databricks (IZ Dev)…")
    conn = connect()
    print("Connected.\n")

    print("  Fetching databases…", end='', flush=True)
    databases = get_databases(conn)
    print(f" {len(databases)} found")

    print("  Fetching tables and views…", flush=True)
    raw_objects = []
    for db in databases:
        tables = get_tables(conn, db)
        views  = get_views(conn, db)
        for t in tables:
            raw_objects.append({'db': db, 'name': t, 'is_view': t in views})
        if tables:
            print(f"    {db}: {len(tables)} objects ({len([t for t in tables if t in views])} views)")

    print(f"\n  Total objects: {len(raw_objects)}")
    print(f"\n  Fetching columns, DDL and stats for {len(raw_objects)} objects…")

    def fetch_object(o):
        db, name = o['db'], o['name']
        c2 = connect()
        try:
            cols = get_columns(c2, db, name)
            ddl  = get_ddl(c2, db, name)
            ext  = get_extended(c2, db, name)
            is_view = ddl.strip().upper().startswith('CREATE VIEW') or o['is_view']
            return {
                'db': db, 'name': name,
                'key': f"{db}||{name}",
                'obj_type': 'VIEW' if is_view else 'TABLE',
                'columns': cols,
                'ddl':     ddl,
                **ext
            }
        finally:
            c2.close()

    objects  = []
    col_data = []
    done     = 0

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(fetch_object, o): o for o in raw_objects}
        for fut in as_completed(futures):
            done += 1
            try:
                obj = fut.result()
                objects.append(obj)
                for i, c in enumerate(obj['columns'], 1):
                    col_data.append([
                        obj['db'], obj['name'], i,
                        c['name'], c['type'], c.get('comment','')
                    ])
            except Exception as e:
                o = futures[fut]
                print(f"    WARN: {o['db']}.{o['name']} — {e}")
            if done % 20 == 0 or done == len(raw_objects):
                print(f"    {done}/{len(raw_objects)} objects processed…")

    conn.close()

    print(f"\n  Objects   : {len(objects)}")
    print(f"  Tables    : {sum(1 for o in objects if o['obj_type']=='TABLE')}")
    print(f"  Views     : {sum(1 for o in objects if o['obj_type']=='VIEW')}")
    print(f"  Columns   : {len(col_data):,}")

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    print("\nBuilding HTML…")
    html = build_html(databases, objects, col_data, generated)

    out = f"/home/thedavidporter/databricks_iz_metadata_{datetime.now().strftime('%Y%m%d_%H%M')}.html"
    with open(out, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"\nSaved to: {out}")

if __name__ == '__main__':
    main()
