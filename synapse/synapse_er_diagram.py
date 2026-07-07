#!/usr/bin/env python3
"""
Azure Synapse – ER-Style Logical Diagram
Hierarchical entity-relationship diagram with drill-down support.
"""

import json, struct, subprocess
from collections import defaultdict
from datetime import datetime
import pyodbc

SERVER   = "zus1-idoh-dev-v2-sql-server.database.windows.net"
DATABASE = "zus1-idoh-dev-v2-sql-dw"
DRIVER   = "{ODBC Driver 18 for SQL Server}"

# ── auth ───────────────────────────────────────────────────────────────────────

def connect():
    r = subprocess.run(
        ["az","account","get-access-token","--resource","https://database.windows.net",
         "--query","accessToken","-o","tsv"], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"az login required: {r.stderr.strip()}")
    token = r.stdout.strip()
    tb = token.encode("utf-16-le")
    ts = struct.pack(f"<I{len(tb)}s", len(tb), tb)
    cs = (f"Driver={DRIVER};Server=tcp:{SERVER},1433;"
          f"Database={DATABASE};Encrypt=yes;TrustServerCertificate=no;")
    return pyodbc.connect(cs, attrs_before={1256: ts})

def qry(conn, sql):
    c = conn.cursor(); c.execute(sql)
    cols = [d[0] for d in c.description]
    return [dict(zip(cols, row)) for row in c.fetchall()]

# ── SQL ────────────────────────────────────────────────────────────────────────

OBJECTS_SQL = """
SELECT s.name AS schema_name, o.name AS obj_name, o.type_desc AS obj_type
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id = o.schema_id
WHERE o.type IN ('U','V','P','FN','TF','IF')
ORDER BY s.name, o.name
"""

COL_COUNTS_SQL = """
SELECT TABLE_SCHEMA + '||' + TABLE_NAME AS obj_id, COUNT(*) AS col_count
FROM INFORMATION_SCHEMA.COLUMNS
GROUP BY TABLE_SCHEMA, TABLE_NAME
"""

DEPS_SQL = """
SELECT DISTINCT
    OBJECT_SCHEMA_NAME(d.referencing_id) AS src_schema,
    OBJECT_NAME(d.referencing_id)        AS src_name,
    COALESCE(d.referenced_schema_name,
             OBJECT_SCHEMA_NAME(d.referencing_id)) AS tgt_schema,
    d.referenced_entity_name             AS tgt_name
FROM sys.sql_expression_dependencies d
JOIN sys.objects o1 ON o1.object_id = d.referencing_id
WHERE OBJECT_NAME(d.referencing_id) IS NOT NULL
  AND d.referenced_entity_name IS NOT NULL
"""

FK_SQL = """
SELECT DISTINCT
    OBJECT_SCHEMA_NAME(fk.parent_object_id)     AS src_schema,
    OBJECT_NAME(fk.parent_object_id)            AS src_name,
    OBJECT_SCHEMA_NAME(fk.referenced_object_id) AS tgt_schema,
    OBJECT_NAME(fk.referenced_object_id)        AS tgt_name
FROM sys.foreign_keys fk
"""

# ── graph builder ──────────────────────────────────────────────────────────────

def build_graph(objects, col_counts, deps, fks):
    col_map  = {r['obj_id']: r['col_count'] for r in col_counts}
    obj_set  = {f"{o['schema_name']}||{o['obj_name']}" for o in objects}

    # schema summary
    sch = defaultdict(lambda: [0, 0, 0])
    for o in objects:
        s = o['schema_name']; t = o['obj_type']
        if 'TABLE' in t:   sch[s][0] += 1
        elif t == 'VIEW':  sch[s][1] += 1
        else:              sch[s][2] += 1

    schemas = [{"id": s, "t": c[0], "v": c[1], "p": c[2]}
               for s, c in sorted(sch.items())]

    # object nodes
    obj_nodes = [
        {"id":  f"{o['schema_name']}||{o['obj_name']}",
         "s":   o['schema_name'],
         "n":   o['obj_name'],
         "tp":  o['obj_type'],
         "cc":  col_map.get(f"{o['schema_name']}||{o['obj_name']}", 0)}
        for o in objects
    ]

    # object-level edges (dep + fk), only within known objects
    edges = []
    seen  = set()
    for d in deps:
        src = f"{d['src_schema']}||{d['src_name']}"
        tgt = f"{d['tgt_schema']}||{d['tgt_name']}"
        if src in obj_set and tgt in obj_set and src != tgt:
            k = (src, tgt, 'dep')
            if k not in seen:
                edges.append({"s": src, "t": tgt, "k": "dep"})
                seen.add(k)
    for fk in fks:
        src = f"{fk['src_schema']}||{fk['src_name']}"
        tgt = f"{fk['tgt_schema']}||{fk['tgt_name']}"
        if src in obj_set and tgt in obj_set and src != tgt:
            k = (src, tgt, 'fk')
            if k not in seen:
                edges.append({"s": src, "t": tgt, "k": "fk"})
                seen.add(k)

    # schema-level edges (cross-schema only, aggregated)
    se_cnt = defaultdict(int)
    for e in edges:
        ss, ts_ = e['s'].split('||')[0], e['t'].split('||')[0]
        if ss != ts_:
            se_cnt[(ss, ts_)] += 1
    schema_edges = [{"s": s, "t": t, "n": n} for (s, t), n in sorted(se_cnt.items())]

    return {"db": DATABASE,
            "schemas": schemas, "se": schema_edges,
            "objects": obj_nodes, "edges": edges}

# ── HTML template ──────────────────────────────────────────────────────────────

TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>__TITLE__ – ER Diagram</title>
<style>
:root{--bg:#0c0e14;--sur:#141720;--sur2:#1e2130;--brd:#272c3e;
  --txt:#e2e8f0;--mut:#6b7898;--acc:#6c8eff;}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;background:var(--bg);color:var(--txt);
  font:13px/1.5 'Segoe UI',system-ui,sans-serif;overflow:hidden}

/* toolbar */
#tb{display:flex;align-items:center;gap:10px;padding:7px 14px;flex-wrap:wrap;
  background:var(--sur);border-bottom:1px solid var(--brd);flex-shrink:0}
#tb h2{font-size:13px;font-weight:700;white-space:nowrap}
#tb h2 small{color:var(--mut);font-weight:400}
.tb-sep{width:1px;height:20px;background:var(--brd);flex-shrink:0}
#search{padding:5px 10px;background:var(--sur2);border:1px solid var(--brd);
  border-radius:6px;color:var(--txt);font-size:12px;width:200px;outline:none}
#search:focus{border-color:var(--acc)}
.btn{padding:4px 11px;background:var(--sur2);border:1px solid var(--brd);
  border-radius:5px;color:var(--txt);font-size:12px;cursor:pointer;white-space:nowrap;
  user-select:none}
.btn:hover{border-color:var(--acc);color:var(--acc)}
.chk{display:flex;align-items:center;gap:4px;font-size:11px;cursor:pointer;white-space:nowrap}
.chk input{accent-color:var(--acc)}
#vlabel{font-size:12px;color:var(--mut);white-space:nowrap}
#stats{display:flex;gap:5px;flex-wrap:wrap}
.sc{font-size:10px;padding:2px 7px;border-radius:10px;white-space:nowrap}
.sc-s{background:#3b2a5e;color:#c084fc}
.sc-t{background:#1e3a5f;color:#60a5fa}
.sc-v{background:#1a3a2a;color:#4ade80}
.sc-p{background:#3a2a1e;color:#fb923c}
.sc-e{background:#1e2a3a;color:#94a3b8}

/* body */
#body{display:flex;flex:1;overflow:hidden;height:calc(100vh - 43px)}
#cwrap{flex:1;position:relative;overflow:hidden;cursor:default}
#cwrap.pan{cursor:grab} #cwrap.panning{cursor:grabbing}
#graph{display:block;width:100%;height:100%}

/* detail panel */
#panel{width:270px;min-width:200px;background:var(--sur);border-left:1px solid var(--brd);
  overflow-y:auto;padding:14px;font-size:12px;flex-shrink:0}
.hint{color:var(--mut);line-height:1.7;margin-top:6px}
.pn-hdr{display:flex;align-items:flex-start;gap:8px;margin-bottom:5px}
.pn-dot{width:11px;height:11px;border-radius:2px;flex-shrink:0;margin-top:2px}
.pn-hdr strong{font-size:13px;word-break:break-word;line-height:1.3}
.pn-meta{color:var(--mut);margin-bottom:10px;padding-left:19px;font-size:11px;
  display:flex;flex-direction:column;gap:2px}
.pn-sec{margin-bottom:10px}
.pn-sec-lbl{font-size:10px;font-weight:700;color:var(--mut);text-transform:uppercase;
  letter-spacing:.6px;margin-bottom:4px}
.dep-list{list-style:none;max-height:160px;overflow-y:auto;display:flex;flex-direction:column;gap:3px}
.dep-list li{display:flex;align-items:center;gap:5px;font-size:11px;
  padding:2px 0;border-bottom:1px solid var(--brd)}
.dc{font-size:9px;padding:1px 4px;border-radius:3px;flex-shrink:0;font-weight:700}
.dc-dep{background:#1e2a3a;color:#94a3b8}
.dc-fk{background:#3a3010;color:#fbbf24}
.drill-btn{width:100%;margin-top:8px;padding:6px;background:var(--acc);border:none;
  border-radius:6px;color:#fff;font-size:12px;cursor:pointer;font-weight:600}
.drill-btn:hover{opacity:.85}
.back-hint{font-size:10px;color:var(--mut);margin-top:6px;line-height:1.5}

/* tooltip */
#tip{position:fixed;pointer-events:none;display:none;z-index:200;
  background:rgba(20,23,32,0.97);border:1px solid var(--brd);border-radius:7px;
  padding:8px 11px;font-size:12px;max-width:260px;box-shadow:0 4px 20px rgba(0,0,0,.5)}
#tip .tn{font-weight:700;margin-bottom:2px}
#tip .tm{color:var(--mut);font-size:11px}

/* legend */
#legend{position:absolute;bottom:12px;left:12px;background:rgba(12,14,20,.9);
  border:1px solid var(--brd);border-radius:8px;padding:8px 12px;font-size:11px;
  pointer-events:none;display:flex;gap:12px;flex-wrap:wrap}
.lg{display:flex;align-items:center;gap:5px;color:var(--mut)}
.lg-box{width:11px;height:11px;border-radius:2px;flex-shrink:0}
.lg-line{width:20px;height:2px;flex-shrink:0}
</style>
</head>
<body>

<div id="tb">
  <h2>__TITLE__ <small>ER Diagram</small></h2>
  <div class="tb-sep"></div>
  <input id="search" placeholder="Search objects…" autocomplete="off"/>
  <button class="btn" id="back-btn" style="display:none" onclick="goBack()">&#x2190; Schema Overview</button>
  <span id="vlabel">Schema Overview</span>
  <div class="tb-sep"></div>
  <label class="chk"><input type="checkbox" id="ck-t" checked onchange="applyFilters()"> Tables</label>
  <label class="chk"><input type="checkbox" id="ck-v" checked onchange="applyFilters()"> Views</label>
  <label class="chk"><input type="checkbox" id="ck-p" checked onchange="applyFilters()"> Procs</label>
  <div class="tb-sep"></div>
  <button class="btn" onclick="fitView()">Fit View</button>
  <button class="btn" onclick="resetLayout()">Reset Layout</button>
  <div class="tb-sep"></div>
  <div id="stats"></div>
</div>

<div id="body">
  <div id="cwrap">
    <canvas id="graph"></canvas>
    <div id="legend">
      <div class="lg"><div class="lg-box" style="background:#1d4ed8"></div>Source (SM_)</div>
      <div class="lg"><div class="lg-box" style="background:#15803d"></div>Data Mart (DM_)</div>
      <div class="lg"><div class="lg-box" style="background:#b45309"></div>Reporting</div>
      <div class="lg"><div class="lg-box" style="background:#7c3aed"></div>Reference / Other</div>
      <div class="lg"><div class="lg-box" style="background:#3b82f6"></div>Table</div>
      <div class="lg"><div class="lg-box" style="background:#22c55e"></div>View</div>
      <div class="lg"><div class="lg-box" style="background:#f97316"></div>Proc / Fn</div>
      <div class="lg"><div class="lg-line" style="background:#4b6090"></div>Dependency</div>
      <div class="lg"><div class="lg-line" style="background:#ca8a04"></div>FK</div>
    </div>
  </div>
  <div id="panel"><p class="hint">Click any box to see details.<br><br><strong>Double-click</strong> a schema to explore its objects.</p></div>
</div>
<div id="tip"><div class="tn"></div><div class="tm"></div></div>

<script>
// ── DATA ──────────────────────────────────────────────────────────────────────
const RAW = __DATA__;

// ── HELPERS ───────────────────────────────────────────────────────────────────
function escH(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function escA(s){ return String(s||'').replace(/'/g,"\\'"); }

function typeGroup(t){
  if(!t) return 'other';
  if(t.includes('TABLE')) return 'table';
  if(t==='VIEW') return 'view';
  return 'proc';
}
function typeShort(t){
  if(!t) return '?';
  if(t.includes('TABLE')) return 'TABLE';
  if(t==='VIEW') return 'VIEW';
  if(t.includes('PROCEDURE')) return 'PROC';
  if(t.includes('FUNCTION')) return 'FN';
  return t.slice(0,5);
}

// Object type colors
const OBJ_COL = {
  USER_TABLE:'#3b82f6', EXTERNAL_TABLE:'#2563eb',
  VIEW:'#22c55e',
  SQL_STORED_PROCEDURE:'#f97316',
  SQL_SCALAR_FUNCTION:'#fb923c',
  SQL_TABLE_VALUED_FUNCTION:'#fb923c',
  SQL_INLINE_TABLE_VALUED_FUNCTION:'#fb923c',
  SCHEMA_source:'#1d4ed8', SCHEMA_datamart:'#15803d',
  SCHEMA_reporting:'#b45309', SCHEMA_reference:'#7c3aed',
  SCHEMA_recordlink:'#0891b2', SCHEMA_admin:'#475569',
  SCHEMA_other:'#4b5563'
};
function objColor(type, domain){
  if(type === 'SCHEMA') return OBJ_COL['SCHEMA_'+(domain||'other')] || '#4b5563';
  return OBJ_COL[type] || '#64748b';
}
function schemaDomain(id){
  const u = id.toUpperCase();
  if(u.startsWith('SM_'))          return 'source';
  if(u.startsWith('DM_'))          return 'datamart';
  if(u.startsWith('REPORTING_'))   return 'reporting';
  if(u.includes('REFERENCE'))      return 'reference';
  if(u.includes('RECORD_LINK'))    return 'recordlink';
  if(u.includes('_DBA')||u.startsWith('OPERATIONS')||u.startsWith('AUDITS')||u.startsWith('METADATA')) return 'admin';
  return 'other';
}

// ── BOX DIMENSIONS ────────────────────────────────────────────────────────────
const BW_SCH  = 175;  // schema box width
const BH_SCH  = 82;   // schema box height
const BW_OBJ  = 155;  // object box width
const BH_OBJ  = 54;   // object box height (home)
const BH_NBR  = 66;   // neighbor box height (extra schema label row)
const HDR_H   = 22;   // header bar height
const GAP_X   = 95;   // horizontal gap between rank columns
const GAP_Y   = 16;   // vertical gap between nodes in same rank

// ── LAYOUT ENGINE ─────────────────────────────────────────────────────────────
function computeRanks(nodes, rawEdges){
  const nodeSet = new Set(nodes.map(n => n.id));
  const deps = {};
  for(const n of nodes) deps[n.id] = [];

  for(const e of rawEdges){
    const s = e.s, t = e.t;
    if(nodeSet.has(s) && nodeSet.has(t) && s !== t){
      if(!deps[s].includes(t)) deps[s].push(t);
    }
  }

  const rank = {}, computing = new Set();
  function r(id){
    if(rank[id] !== undefined) return rank[id];
    if(computing.has(id)){ rank[id]=0; return 0; } // cycle guard
    computing.add(id);
    const d = deps[id]||[];
    rank[id] = d.length ? Math.max(...d.map(r))+1 : 0;
    computing.delete(id);
    return rank[id];
  }
  nodes.forEach(n => r(n.id));
  return rank;
}

function applyLayout(nodes, rawEdges, bw, bhFn){
  const rank = computeRanks(nodes, rawEdges);

  // group by rank
  const byRank = {};
  for(const n of nodes){
    const rk = rank[n.id]||0;
    if(!byRank[rk]) byRank[rk]=[];
    byRank[rk].push(n);
  }

  // sort within rank alphabetically
  for(const rk in byRank) byRank[rk].sort((a,b)=>a.label.localeCompare(b.label));

  // assign x,y (centered vertically around y=0)
  const cols = Object.keys(byRank).map(Number).sort((a,b)=>a-b);
  let cx = 0;
  for(const col of cols){
    const group = byRank[col];
    const hs = group.reduce((s,n)=>(s + bhFn(n) + GAP_Y), -GAP_Y);
    let y = -hs/2;
    for(const n of group){
      const h = bhFn(n);
      n.x = cx; n.y = y;
      n.bw = bw; n.bh = h;
      y += h + GAP_Y;
    }
    cx += bw + GAP_X;
  }

  return { byRank, cols };
}

// ── CAMERA ────────────────────────────────────────────────────────────────────
const C = document.getElementById('graph');
const ctx = C.getContext('2d');
let cam = {x:0, y:0, scale:1};

function worldToScreen(wx, wy){ return { x: wx*cam.scale+cam.x, y: wy*cam.scale+cam.y }; }
function screenToWorld(sx, sy){ return { x:(sx-cam.x)/cam.scale, y:(sy-cam.y)/cam.scale }; }

function fitView(){
  if(!sim||!sim.nodes.length) return;
  const pad = 60;
  let mx=Infinity,my=Infinity,xx=-Infinity,xy=-Infinity;
  for(const n of sim.nodes){
    mx=Math.min(mx,n.x); my=Math.min(my,n.y);
    xx=Math.max(xx,n.x+n.bw); xy=Math.max(xy,n.y+n.bh);
  }
  const W=C.width, H=C.height;
  cam.scale = Math.min((W-pad*2)/Math.max(xx-mx,1), (H-pad*2)/Math.max(xy-my,1), 2);
  cam.x = (W - (xx+mx)*cam.scale)/2;
  cam.y = (H - (xy+my)*cam.scale)/2;
}

// ── RENDERING ─────────────────────────────────────────────────────────────────
function roundRect(x, y, w, h, r){
  r = Math.min(r, w/2, h/2);
  ctx.beginPath();
  ctx.moveTo(x+r, y);
  ctx.lineTo(x+w-r, y);
  ctx.quadraticCurveTo(x+w, y, x+w, y+r);
  ctx.lineTo(x+w, y+h-r);
  ctx.quadraticCurveTo(x+w, y+h, x+w-r, y+h);
  ctx.lineTo(x+r, y+h);
  ctx.quadraticCurveTo(x, y+h, x, y+h-r);
  ctx.lineTo(x, y+r);
  ctx.quadraticCurveTo(x, y, x+r, y);
  ctx.closePath();
}
function roundRectTop(x, y, w, h, r){
  r = Math.min(r, w/2, h);
  ctx.beginPath();
  ctx.moveTo(x+r, y);
  ctx.lineTo(x+w-r, y);
  ctx.quadraticCurveTo(x+w, y, x+w, y+r);
  ctx.lineTo(x+w, y+h);
  ctx.lineTo(x, y+h);
  ctx.lineTo(x, y+r);
  ctx.quadraticCurveTo(x, y, x+r, y);
  ctx.closePath();
}

function drawBox(n, selId, connected, searchStr){
  const {x, y, bw: w, bh: h} = n;
  const isSel  = n.id === selId;
  const isDim  = !!selId && !connected.has(n.id);
  const isHit  = searchStr && n.label.toLowerCase().includes(searchStr);
  const domain = n.domain;
  const color  = objColor(n.type, domain);
  const fs  = 10;
  const fs2 = 11;

  // search glow
  if(isHit){
    ctx.save(); ctx.shadowColor='rgba(250,204,21,.5)'; ctx.shadowBlur=14;
    roundRect(x-2,y-2,w+4,h+4,7); ctx.strokeStyle='rgba(250,204,21,.5)';
    ctx.lineWidth=2; ctx.stroke(); ctx.restore();
  }

  // selection glow
  if(isSel){
    ctx.save(); ctx.shadowColor=color; ctx.shadowBlur=10;
    roundRect(x,y,w,h,6); ctx.strokeStyle=color;
    ctx.lineWidth=2; ctx.stroke(); ctx.restore();
  }

  // box body
  roundRect(x, y, w, h, 6);
  ctx.fillStyle = isDim ? '#0f1117' : (n.type==='SCHEMA' ? '#161923' : '#15182a');
  ctx.fill();
  ctx.strokeStyle = isDim ? '#1e2235' : (isSel ? '#fff' : '#2a3050');
  ctx.lineWidth = isSel ? 1.5 : 1;
  ctx.stroke();

  if(isDim){ return; }  // skip labels for dimmed nodes

  // header bar
  roundRectTop(x, y, w, HDR_H, 6);
  ctx.fillStyle = color;
  ctx.fill();

  // header text (type / domain)
  ctx.fillStyle = 'rgba(255,255,255,0.9)';
  ctx.font = `bold ${fs}px sans-serif`;
  ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
  const hdrText = n.type==='SCHEMA'
    ? (domain==='source'?'SOURCE':domain==='datamart'?'DATA MART':domain==='reporting'?'REPORTING':domain.toUpperCase())
    : typeShort(n.type);
  ctx.fillText(hdrText.slice(0,18), x+5, y+HDR_H/2);

  // object / schema name
  ctx.fillStyle = '#e2e8f0';
  ctx.font = `${isSel?'600 ':''}${fs2}px sans-serif`;
  ctx.textAlign = 'center'; ctx.textBaseline = 'top';
  const maxChars = Math.floor(w / (fs2*0.6));
  const nameDisp = n.label.length > maxChars ? n.label.slice(0, maxChars-1)+'…' : n.label;

  if(n.type === 'SCHEMA'){
    // schema name + counts
    ctx.fillText(nameDisp, x+w/2, y+HDR_H+5);
    ctx.font = `${fs}px sans-serif`;
    ctx.fillStyle = '#94a3b8';
    ctx.textBaseline = 'top';
    const counts = `${n.meta.t}T · ${n.meta.v}V · ${n.meta.p}P`;
    ctx.fillText(counts, x+w/2, y+HDR_H+5+fs2+4);
  } else {
    // object name
    ctx.fillText(nameDisp, x+w/2, y+HDR_H+5);
    // subline: col count or schema name
    ctx.font = `${fs}px sans-serif`;
    ctx.fillStyle = '#6b7898'; ctx.textBaseline = 'top';
    if(n.colCount > 0){
      ctx.fillText(`${n.colCount} col${n.colCount!==1?'s':''}`, x+w/2, y+HDR_H+5+fs2+4);
    }
    if(n.home === false && n.meta && n.meta.s){
      const subY = n.bh - fs - 7;
      ctx.fillStyle='#4a5a7a';
      ctx.fillText(n.meta.s.length>22?n.meta.s.slice(0,20)+'…':n.meta.s, x+w/2, y+subY);
    }
  }
}

// bezier control offset for ER-style connections
function bezCP(x1, x2){ return Math.max(Math.abs(x2-x1)*0.45, 55); }

function drawEdge(e, selId, connected){
  const {s,t,k} = e;
  // s=referencing (consumer), t=referenced (source/provider)
  // Draw in data-flow direction: source→consumer (left→right matches rank layout)
  const ox  = t.x+t.bw;  const oy  = t.y+t.bh/2;  // right-center of referenced
  const tx_ = s.x;        const ty_ = s.y+s.bh/2;  // left-center of referencing

  const isActive = !selId || s.id===selId || t.id===selId;
  const fk = k==='fk';

  const baseAlpha = isActive ? (fk?0.85:0.65) : (fk?0.1:0.1);
  const baseColor = fk ? `rgba(202,138,4,${baseAlpha})` : `rgba(75,96,148,${baseAlpha})`;
  const lw = isActive ? (fk ? 1.5 : 1) : 0.5;

  const cp = bezCP(ox, tx_);
  const cpx1=ox+cp, cpx2=tx_-cp;

  // draw curve
  ctx.beginPath();
  ctx.moveTo(ox, oy);
  ctx.bezierCurveTo(cpx1, oy, cpx2, ty_, tx_, ty_);
  ctx.strokeStyle = baseColor;
  ctx.lineWidth = lw;
  ctx.stroke();

  if(!isActive) return;

  // arrowhead at target (pointing right since tangent at t=1 is horizontal toward tx_)
  // True tangent direction at t=1: 3*(P3-P2) where P2=(cpx2,ty_), P3=(tx_,ty_)
  // dx = 3*(tx_-cpx2) = 3*cp, dy = 0 → angle = atan2(0, 3*cp) ≈ 0
  // But if oy != ty_, we approach from angle, so compute numerically
  const T = 0.97;
  const u = 1-T;
  const bx = u*u*u*ox + 3*u*u*T*cpx1 + 3*u*T*T*cpx2 + T*T*T*tx_;
  const by = u*u*u*oy + 3*u*u*T*oy   + 3*u*T*T*ty_  + T*T*T*ty_;
  const angle = Math.atan2(ty_-by, tx_-bx);
  const hs = 6;

  ctx.beginPath();
  ctx.moveTo(tx_, ty_);
  ctx.lineTo(tx_-hs*Math.cos(angle-0.4), ty_-hs*Math.sin(angle-0.4));
  ctx.lineTo(tx_-hs*Math.cos(angle+0.4), ty_-hs*Math.sin(angle+0.4));
  ctx.closePath();
  ctx.fillStyle = fk ? `rgba(202,138,4,${baseAlpha})` : `rgba(75,96,148,${baseAlpha})`;
  ctx.fill();
}

// rank column label bands
function drawRankBands(byRank){
  if(!byRank) return;
  const cols = Object.keys(byRank).map(Number).sort((a,b)=>a-b);
  if(cols.length < 2) return;
  const bandColors = ['rgba(30,74,214,.04)','rgba(21,128,61,.04)','rgba(180,83,9,.04)',
                      'rgba(124,58,237,.04)','rgba(8,145,178,.04)'];
  for(let i=0; i<cols.length; i++){
    const group = byRank[cols[i]];
    if(!group||!group.length) continue;
    let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
    for(const n of group){
      minX=Math.min(minX,n.x-10); minY=Math.min(minY,n.y-10);
      maxX=Math.max(maxX,n.x+n.bw+10); maxY=Math.max(maxY,n.y+n.bh+10);
    }
    ctx.fillStyle = bandColors[i % bandColors.length];
    ctx.fillRect(minX, minY-30, maxX-minX, maxY-minY+30);
  }
}

function render(){
  if(!sim) return;
  const W=C.width, H=C.height;
  ctx.clearRect(0,0,W,H);
  ctx.save();
  ctx.translate(cam.x, cam.y);
  ctx.scale(cam.scale, cam.scale);

  const selId = selNode ? selNode.id : null;
  const connected = new Set();
  if(selNode){
    connected.add(selId);
    for(const e of sim.edges){ if(e.s.id===selId) connected.add(e.t.id); if(e.t.id===selId) connected.add(e.s.id); }
  }
  const q = document.getElementById('search').value.trim().toLowerCase();

  // rank bands (subtle)
  if(VIEW_MODE==='schema') drawRankBands(sim.byRank);

  // edges (behind boxes)
  for(const e of sim.edges) drawEdge(e, selId, connected);

  // boxes
  for(const n of sim.nodes) drawBox(n, selId, connected, q);

  ctx.restore();
}

// ── STATE ─────────────────────────────────────────────────────────────────────
let VIEW_MODE = 'schema', FOCUS = null;
let sim = null;   // { nodes, edges, byRank }
let selNode = null, hovNode = null;
let mouseDown=false, panMode=false, dragNode=null, lastMX=0, lastMY=0;
let clickStart={x:0,y:0,t:0};

// ── VIEW BUILDERS ──────────────────────────────────────────────────────────────
function loadSchemaView(){
  VIEW_MODE='schema'; FOCUS=null; selNode=null; hovNode=null;

  const nodes = RAW.schemas.map(s=>({
    id:s.id, label:s.id, type:'SCHEMA',
    domain: schemaDomain(s.id),
    meta:s, bw:BW_SCH, bh:BH_SCH, colCount:0
  }));

  const rawEdges = RAW.se.map(e=>({s:e.s,t:e.t,k:'dep',n:e.n}));

  // build edge objects (src/tgt as node references for rendering)
  const nm = Object.fromEntries(nodes.map(n=>[n.id,n]));
  const edges = rawEdges.map(e=>({...e, s:nm[e.s], t:nm[e.t]})).filter(e=>e.s&&e.t);

  const { byRank } = applyLayout(nodes, rawEdges.map(e=>({s:e.s,t:e.t})), BW_SCH, ()=>BH_SCH);

  sim = { nodes, edges, byRank };
  fitViewDelayed();
  document.getElementById('back-btn').style.display='none';
  document.getElementById('vlabel').textContent='Schema Overview';
  updatePanel(null); updateStats();
}

function drillInto(schemaId){
  VIEW_MODE='objects'; FOCUS=schemaId; selNode=null; hovNode=null;

  const showT = document.getElementById('ck-t').checked;
  const showV = document.getElementById('ck-v').checked;
  const showP = document.getElementById('ck-p').checked;
  const typeOk = tp => (tp.includes('TABLE')&&showT)||(tp==='VIEW'&&showV)||(showP&&!tp.includes('TABLE')&&tp!=='VIEW');

  const homeObjs = RAW.objects.filter(o=>o.s===schemaId && typeOk(o.tp));
  const homeIds  = new Set(homeObjs.map(o=>o.id));

  const relEdges = RAW.edges.filter(e=>homeIds.has(e.s)||homeIds.has(e.t));
  const nbrIds   = new Set();
  for(const e of relEdges){ if(!homeIds.has(e.s)) nbrIds.add(e.s); if(!homeIds.has(e.t)) nbrIds.add(e.t); }

  const objLookup = Object.fromEntries(RAW.objects.map(o=>[o.id,o]));

  const nodes = [
    ...homeObjs.map(o=>({ id:o.id, label:o.n, type:o.tp, meta:o, colCount:o.cc, home:true, bw:BW_OBJ, bh:BH_OBJ })),
    ...[...nbrIds].map(id=>{ const o=objLookup[id]; if(!o||!typeOk(o.tp)) return null;
      return { id:o.id, label:o.n, type:o.tp, meta:o, colCount:o.cc, home:false, bw:BW_OBJ, bh:BH_NBR }; }).filter(Boolean)
  ];

  const rawEdges = relEdges.filter(e=>{
    const so=objLookup[e.s], to=objLookup[e.t];
    return so&&to&&typeOk(so.tp)&&typeOk(to.tp);
  });

  const nm = Object.fromEntries(nodes.map(n=>[n.id,n]));
  const edges = rawEdges.map(e=>({...e,s:nm[e.s],t:nm[e.t]})).filter(e=>e.s&&e.t);

  const { byRank } = applyLayout(nodes, rawEdges.map(e=>({s:e.s,t:e.t})), BW_OBJ, n=>n.bh);

  sim = { nodes, edges, byRank };
  fitViewDelayed();
  document.getElementById('back-btn').style.display='inline-block';
  document.getElementById('vlabel').textContent=`Objects: ${schemaId}`;
  updatePanel(null); updateStats();
}

function goBack(){ loadSchemaView(); }
function applyFilters(){ if(VIEW_MODE==='objects'&&FOCUS) drillInto(FOCUS); }
function resetLayout(){
  if(VIEW_MODE==='schema') loadSchemaView();
  else if(FOCUS) drillInto(FOCUS);
}
function fitViewDelayed(){ setTimeout(fitView, 60); }

// ── SELECTION & PANEL ─────────────────────────────────────────────────────────
function selectNode(n){
  selNode = (selNode&&selNode.id===n.id) ? null : n;
  updatePanel(selNode);
}

function updatePanel(n){
  const p = document.getElementById('panel');
  if(!n){
    p.innerHTML=`<p class="hint">Click any box to see details.<br><br>${VIEW_MODE==='schema'?'<strong>Double-click</strong> a schema box to explore its objects.':'Use <strong>Schema Overview</strong> to go back.'}</p>`;
    return;
  }
  const outE = sim.edges.filter(e=>e.s.id===n.id);
  const inE  = sim.edges.filter(e=>e.t.id===n.id);
  const color = objColor(n.type, n.domain);

  const depList = (arr, which) => arr.map(e=>{
    const o = which==='out'?e.t:e.s;
    return `<li><span class="dc dc-${e.k}">${e.k.toUpperCase()}</span>${escH(o.label)}</li>`;
  }).join('');

  const schemaStr = n.meta&&n.meta.s ? n.meta.s : n.id;

  p.innerHTML=`
<div class="pn-hdr">
  <div class="pn-dot" style="background:${color}"></div>
  <strong>${escH(n.label)}</strong>
</div>
<div class="pn-meta">
  <span>${typeShort(n.type)} · ${VIEW_MODE==='schema'?'Schema':(n.type==='SCHEMA'?'Schema':schemaStr)}</span>
  ${VIEW_MODE==='schema'&&n.meta?`<span>${n.meta.t} tables · ${n.meta.v} views · ${n.meta.p} procs</span>`:''}
  ${n.colCount?`<span>${n.colCount} columns</span>`:''}
  <span>${outE.length} outgoing · ${inE.length} incoming</span>
</div>
${outE.length?`<div class="pn-sec"><div class="pn-sec-lbl">Depends on (${outE.length})</div><ul class="dep-list">${depList(outE,'out')}</ul></div>`:''}
${inE.length ?`<div class="pn-sec"><div class="pn-sec-lbl">Referenced by (${inE.length})</div><ul class="dep-list">${depList(inE,'in')}</ul></div>`:''}
${VIEW_MODE==='schema'?`<button class="drill-btn" onclick="drillInto('${escA(n.id)}')">Explore Objects &#x2192;</button><p class="back-hint">Shows all objects in this schema and their dependency connections.</p>`:''}
`;
}

function updateStats(){
  const el = document.getElementById('stats');
  if(!sim){ el.innerHTML=''; return; }
  const c={};
  for(const n of sim.nodes){ const g=n.type==='SCHEMA'?'s':typeGroup(n.type).slice(0,1); c[g]=(c[g]||0)+1; }
  const dep=sim.edges.filter(e=>e.k==='dep').length;
  const fk=sim.edges.filter(e=>e.k==='fk').length;
  el.innerHTML=[
    c.s?`<span class="sc sc-s">${c.s} schemas</span>`:'',
    c.t?`<span class="sc sc-t">${c.t} tables</span>`:'',
    c.v?`<span class="sc sc-v">${c.v} views</span>`:'',
    c.p?`<span class="sc sc-p">${c.p} procs</span>`:'',
    (dep||fk)?`<span class="sc sc-e">${dep} dep · ${fk} FK</span>`:'',
  ].join('');
}

// ── HIT DETECTION ─────────────────────────────────────────────────────────────
function nodeAt(wx, wy){
  if(!sim) return null;
  for(let i=sim.nodes.length-1;i>=0;i--){
    const n=sim.nodes[i];
    if(wx>=n.x&&wx<=n.x+n.bw&&wy>=n.y&&wy<=n.y+n.bh) return n;
  }
  return null;
}

// ── TOOLTIP ───────────────────────────────────────────────────────────────────
const tip=document.getElementById('tip');
function showTip(n,px,py){
  tip.querySelector('.tn').textContent=n.label;
  const m=[];
  if(n.type!=='SCHEMA') m.push(n.type.replace(/_/g,' '));
  if(n.meta&&n.meta.s&&n.meta.s!==n.label) m.push(n.meta.s);
  if(n.colCount) m.push(n.colCount+' columns');
  const outE=sim.edges.filter(e=>e.s.id===n.id).length;
  const inE=sim.edges.filter(e=>e.t.id===n.id).length;
  if(outE||inE) m.push(`${outE} out / ${inE} in`);
  tip.querySelector('.tm').textContent=m.join(' · ');
  tip.style.display='block';
  const tx=px+14, ty=py-10;
  tip.style.left=(tx+tip.offsetWidth>window.innerWidth?px-tip.offsetWidth-6:tx)+'px';
  tip.style.top=ty+'px';
}
function hideTip(){ tip.style.display='none'; }

// ── MOUSE ─────────────────────────────────────────────────────────────────────
const wrap=document.getElementById('cwrap');

C.addEventListener('mousedown',e=>{
  e.preventDefault();
  mouseDown=true;
  lastMX=e.clientX; lastMY=e.clientY;
  clickStart={x:e.clientX,y:e.clientY,t:Date.now()};
  const rect=C.getBoundingClientRect();
  const {x,y}=screenToWorld(e.clientX-rect.left, e.clientY-rect.top);
  dragNode=nodeAt(x,y);
  if(dragNode){ wrap.classList.add('panning'); }
  else{ panMode=true; wrap.classList.add('pan','panning'); }
});

window.addEventListener('mousemove',e=>{
  const rect=C.getBoundingClientRect();
  const sx=e.clientX-rect.left, sy=e.clientY-rect.top;
  if(!mouseDown){
    const {x,y}=screenToWorld(sx,sy);
    const n=nodeAt(x,y);
    hovNode=n;
    wrap.style.cursor=n?'pointer':(panMode?'grabbing':'default');
    if(n) showTip(n,e.clientX,e.clientY); else hideTip();
    return;
  }
  const dx=e.clientX-lastMX, dy=e.clientY-lastMY;
  if(dragNode){ dragNode.x+=dx/cam.scale; dragNode.y+=dy/cam.scale; }
  else if(panMode){ cam.x+=dx; cam.y+=dy; }
  lastMX=e.clientX; lastMY=e.clientY;
});

window.addEventListener('mouseup',e=>{
  if(!mouseDown) return;
  mouseDown=false;
  const moved=Math.abs(e.clientX-clickStart.x)+Math.abs(e.clientY-clickStart.y);
  const quick=Date.now()-clickStart.t<350;
  if(moved<6&&quick){
    const rect=C.getBoundingClientRect();
    const {x,y}=screenToWorld(e.clientX-rect.left, e.clientY-rect.top);
    const n=nodeAt(x,y);
    if(n) selectNode(n);
    else{ selNode=null; updatePanel(null); }
  }
  panMode=false; dragNode=null;
  wrap.classList.remove('panning');
  wrap.style.cursor='default';
});

// double-click to drill into schema
C.addEventListener('dblclick',e=>{
  const rect=C.getBoundingClientRect();
  const {x,y}=screenToWorld(e.clientX-rect.left, e.clientY-rect.top);
  const n=nodeAt(x,y);
  if(n&&VIEW_MODE==='schema') drillInto(n.id);
});

C.addEventListener('wheel',e=>{
  e.preventDefault();
  const rect=C.getBoundingClientRect();
  const mx=e.clientX-rect.left, my=e.clientY-rect.top;
  const factor=e.deltaY<0?1.15:0.87;
  const ns=Math.max(0.05,Math.min(15,cam.scale*factor));
  cam.x=mx-(mx-cam.x)*(ns/cam.scale);
  cam.y=my-(my-cam.y)*(ns/cam.scale);
  cam.scale=ns;
},{passive:false});

// ── SEARCH ────────────────────────────────────────────────────────────────────
document.getElementById('search').addEventListener('input',()=>{}); // triggers re-render via RAF

// ── RESIZE & LOOP ─────────────────────────────────────────────────────────────
function resize(){
  const w=document.getElementById('cwrap');
  C.width=w.clientWidth; C.height=w.clientHeight;
}
window.addEventListener('resize',()=>{ resize(); fitView(); });
resize();

function loop(){ render(); requestAnimationFrame(loop); }

// ── INIT ──────────────────────────────────────────────────────────────────────
loadSchemaView();
requestAnimationFrame(loop);
</script>
</body>
</html>
"""

# ── main ───────────────────────────────────────────────────────────────────────

def main():
    print("Connecting to Azure Synapse…")
    conn = connect()
    print("Connected.\n")

    def run(label, sql):
        print(f"  Querying {label}…", end="", flush=True)
        try:
            rows = qry(conn, sql)
            print(f" {len(rows)} rows")
            return rows
        except Exception as e:
            print(f" ERROR: {e}")
            return []

    objects    = run("objects",           OBJECTS_SQL)
    col_counts = run("column counts",     COL_COUNTS_SQL)
    deps       = run("dependencies",      DEPS_SQL)
    fks        = run("foreign keys",      FK_SQL)
    conn.close()

    print("\nBuilding graph…")
    graph = build_graph(objects, col_counts, deps, fks)

    print(f"  Schemas        : {len(graph['schemas'])}")
    print(f"  Schema edges   : {len(graph['se'])}")
    print(f"  Object nodes   : {len(graph['objects'])}")
    print(f"  Object edges   : {len(graph['edges'])}")

    data_json = json.dumps(graph, separators=(',', ':'))
    html = TEMPLATE.replace('__DATA__', data_json).replace('__TITLE__', DATABASE)

    out = f"/home/thedavidporter/synapse_er_{datetime.now().strftime('%Y%m%d_%H%M')}.html"
    with open(out, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"\nSaved to: {out}")

if __name__ == "__main__":
    main()
