#!/usr/bin/env python3
"""
Azure Synapse – Logical Connections Visualizer
Force-directed graph of all object relationships, schema-level and object-level drill-down.
"""

import json, struct, subprocess
from collections import defaultdict
from datetime import datetime
import pyodbc

SERVER   = "zus1-idoh-dev-v2-sql-server.database.windows.net"
DATABASE = "zus1-idoh-dev-v2-sql-dw"
DRIVER   = "{ODBC Driver 18 for SQL Server}"

# ── auth ───────────────────────────────────────────────────────────────────────

def get_connection():
    r = subprocess.run(
        ["az","account","get-access-token","--resource","https://database.windows.net",
         "--query","accessToken","-o","tsv"],
        capture_output=True, text=True)
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

DEPS_SQL = """
SELECT DISTINCT
    OBJECT_SCHEMA_NAME(d.referencing_id)                          AS src_schema,
    OBJECT_NAME(d.referencing_id)                                 AS src_name,
    COALESCE(d.referenced_schema_name,
             OBJECT_SCHEMA_NAME(d.referencing_id))                AS tgt_schema,
    d.referenced_entity_name                                      AS tgt_name
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

# ── build graph JSON ───────────────────────────────────────────────────────────

def build_graph(objects, deps, fks):
    obj_ids = {f"{o['schema_name']}||{o['obj_name']}" for o in objects}

    # schema summary
    sch = defaultdict(lambda: [0, 0, 0])   # [tables, views, procs]
    for o in objects:
        s = o['schema_name']; t = o['obj_type']
        if 'TABLE' in t:   sch[s][0] += 1
        elif t == 'VIEW':  sch[s][1] += 1
        else:              sch[s][2] += 1

    schemas = [{"id": s, "t": c[0], "v": c[1], "p": c[2]} for s, c in sorted(sch.items())]

    # object nodes (compact keys)
    obj_nodes = [
        {"id": f"{o['schema_name']}||{o['obj_name']}",
         "s": o['schema_name'],
         "n": o['obj_name'],
         "tp": o['obj_type']}
        for o in objects
    ]

    # object-level edges (only between known objects)
    edges = []
    seen = set()
    for d in deps:
        src = f"{d['src_schema']}||{d['src_name']}"
        tgt = f"{d['tgt_schema']}||{d['tgt_name']}"
        if src in obj_ids and tgt in obj_ids and src != tgt:
            key = (src, tgt, 'dep')
            if key not in seen:
                edges.append({"s": src, "t": tgt, "k": "dep"})
                seen.add(key)
    for fk in fks:
        src = f"{fk['src_schema']}||{fk['src_name']}"
        tgt = f"{fk['tgt_schema']}||{fk['tgt_name']}"
        if src in obj_ids and tgt in obj_ids and src != tgt:
            key = (src, tgt, 'fk')
            if key not in seen:
                edges.append({"s": src, "t": tgt, "k": "fk"})
                seen.add(key)

    # schema-level edges (cross-schema only, aggregated)
    se_count = defaultdict(int)
    for e in edges:
        ss = e['s'].split('||')[0]
        ts = e['t'].split('||')[0]
        if ss != ts:
            se_count[(ss, ts)] += 1
    schema_edges = [{"s": s, "t": t, "n": n} for (s, t), n in sorted(se_count.items())]

    return {
        "db": DATABASE,
        "schemas": schemas,
        "se": schema_edges,
        "objects": obj_nodes,
        "edges": edges,
    }

# ── HTML template ──────────────────────────────────────────────────────────────
# Uses __DATA__ as placeholder; no f-string so JS braces are literal.

TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>__TITLE__ – Logical Connections</title>
<style>
:root{--bg:#0c0e14;--sur:#141720;--sur2:#1e2130;--brd:#272c3e;
  --txt:#e2e8f0;--mut:#6b7898;--acc:#6c8eff;}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;background:var(--bg);color:var(--txt);
  font:13px/1.5 'Segoe UI',system-ui,sans-serif;overflow:hidden}

/* ── toolbar ── */
#toolbar{display:flex;align-items:center;gap:10px;padding:8px 14px;
  background:var(--sur);border-bottom:1px solid var(--brd);flex-shrink:0;flex-wrap:wrap}
#toolbar h2{font-size:13px;font-weight:700;white-space:nowrap;margin-right:4px}
#toolbar h2 small{font-weight:400;color:var(--mut)}
#search{padding:5px 10px;background:var(--sur2);border:1px solid var(--brd);
  border-radius:6px;color:var(--txt);font-size:12px;width:200px;outline:none}
#search:focus{border-color:var(--acc)}
#view-label{font-size:12px;color:var(--mut);white-space:nowrap}
.tb-btn{padding:4px 11px;background:var(--sur2);border:1px solid var(--brd);
  border-radius:5px;color:var(--txt);font-size:12px;cursor:pointer;white-space:nowrap}
.tb-btn:hover{border-color:var(--acc);color:var(--acc)}
.tb-sep{width:1px;height:20px;background:var(--brd);flex-shrink:0}
.chk-lbl{display:flex;align-items:center;gap:4px;font-size:11px;cursor:pointer;white-space:nowrap}
.chk-lbl input{accent-color:var(--acc)}
#stat-chips{display:flex;gap:6px;flex-wrap:wrap}
.stat-chip{font-size:10px;padding:2px 7px;border-radius:10px;white-space:nowrap}
.chip-schema{background:#3b2a5e;color:#c084fc}
.chip-table{background:#1e3a5f;color:#60a5fa}
.chip-view{background:#1a3a2a;color:#4ade80}
.chip-proc{background:#3a2a1e;color:#fb923c}
.chip-dep{background:#1e2a3a;color:#94a3b8}
.chip-fk{background:#3a3010;color:#fbbf24}

/* ── body layout ── */
#body{display:flex;flex:1;overflow:hidden;height:calc(100vh - 44px)}
#canvas-wrap{flex:1;position:relative;overflow:hidden;cursor:grab}
#canvas-wrap.grabbing{cursor:grabbing}
#graph{display:block;width:100%;height:100%}

/* ── tooltip ── */
#tooltip{position:fixed;pointer-events:none;background:rgba(20,23,32,0.95);
  border:1px solid var(--brd);border-radius:7px;padding:8px 11px;font-size:12px;
  max-width:240px;display:none;z-index:100;box-shadow:0 4px 20px rgba(0,0,0,0.5)}
#tooltip .tt-name{font-weight:700;margin-bottom:3px}
#tooltip .tt-meta{color:var(--mut);font-size:11px}

/* ── detail panel ── */
#panel{width:280px;min-width:220px;background:var(--sur);border-left:1px solid var(--brd);
  overflow-y:auto;padding:14px;font-size:12px;flex-shrink:0}
.hint{color:var(--mut);font-size:12px;line-height:1.6;margin-top:8px}
.pn-hdr{display:flex;align-items:center;gap:8px;margin-bottom:6px}
.pn-dot{width:11px;height:11px;border-radius:50%;flex-shrink:0}
.pn-hdr strong{font-size:13px;word-break:break-all}
.pn-meta{display:flex;flex-direction:column;gap:3px;color:var(--mut);margin-bottom:12px;
  font-size:11px;padding-left:19px}
.pn-section{margin-bottom:12px}
.pn-sec-lbl{font-size:11px;font-weight:700;color:var(--mut);text-transform:uppercase;
  letter-spacing:.5px;margin-bottom:5px}
.dep-list{list-style:none;display:flex;flex-direction:column;gap:4px;
  max-height:180px;overflow-y:auto}
.dep-list li{display:flex;align-items:center;gap:6px;font-size:11px;
  padding:3px 0;border-bottom:1px solid var(--brd)}
.dc{font-size:9px;padding:1px 4px;border-radius:3px;flex-shrink:0;font-weight:700}
.dc-dep{background:#1e2a3a;color:#94a3b8}
.dc-fk{background:#3a3010;color:#fbbf24}
.drill-btn{width:100%;margin-top:10px;padding:7px;background:var(--acc);
  border:none;border-radius:6px;color:#fff;font-size:12px;cursor:pointer;font-weight:600}
.drill-btn:hover{opacity:.85}
.back-info{font-size:11px;color:var(--mut);margin-top:8px;line-height:1.5}

/* ── legend ── */
#legend{position:absolute;bottom:12px;left:12px;background:rgba(20,23,32,0.9);
  border:1px solid var(--brd);border-radius:8px;padding:8px 12px;font-size:11px;
  pointer-events:none;display:flex;gap:12px;flex-wrap:wrap}
.lg{display:flex;align-items:center;gap:5px;color:var(--mut)}
.lg-dot{width:9px;height:9px;border-radius:50%;flex-shrink:0}
.lg-line{width:18px;height:2px;flex-shrink:0}

/* ── minimap ── */
#minimap-wrap{position:absolute;bottom:12px;right:292px;
  background:rgba(14,16,22,0.85);border:1px solid var(--brd);border-radius:6px;overflow:hidden}
#minimap{display:block}
</style>
</head>
<body>

<div id="toolbar">
  <h2>__TITLE__ <small>Logical Connections</small></h2>
  <div class="tb-sep"></div>
  <input id="search" placeholder="Search objects…" autocomplete="off"/>
  <button class="tb-btn" id="back-btn" style="display:none" onclick="goBack()">&#x2190; Schema View</button>
  <span id="view-label" style="font-size:12px;color:var(--mut)">Schema Overview</span>
  <div class="tb-sep"></div>
  <label class="chk-lbl"><input type="checkbox" id="ck-table" checked onchange="applyFilters()"> Tables</label>
  <label class="chk-lbl"><input type="checkbox" id="ck-view"  checked onchange="applyFilters()"> Views</label>
  <label class="chk-lbl"><input type="checkbox" id="ck-proc"  checked onchange="applyFilters()"> Procs</label>
  <div class="tb-sep"></div>
  <button class="tb-btn" onclick="resetCamera()">Reset View</button>
  <button class="tb-btn" onclick="reheat()">Re-layout</button>
  <div class="tb-sep"></div>
  <div id="stat-chips"></div>
</div>

<div id="body">
  <div id="canvas-wrap">
    <canvas id="graph"></canvas>
    <div id="legend">
      <div class="lg"><div class="lg-dot" style="background:#a855f7"></div>Schema</div>
      <div class="lg"><div class="lg-dot" style="background:#3b82f6"></div>Table</div>
      <div class="lg"><div class="lg-dot" style="background:#22c55e"></div>View</div>
      <div class="lg"><div class="lg-dot" style="background:#f97316"></div>Proc / Fn</div>
      <div class="lg"><div class="lg-line" style="background:#4b6090"></div>Dependency</div>
      <div class="lg"><div class="lg-line" style="background:#ca8a04"></div>Foreign Key</div>
    </div>
    <div id="minimap-wrap"><canvas id="minimap" width="120" height="80"></canvas></div>
  </div>
  <div id="panel"><p class="hint">Click any node to see its details.<br><br>In Schema View, <strong>double-click</strong> a schema to drill into its objects.</p></div>
</div>

<div id="tooltip"><div class="tt-name"></div><div class="tt-meta"></div></div>

<script>
// ── DATA ──────────────────────────────────────────────────────────────────────
const RAW = __DATA__;

// ── COLORS ────────────────────────────────────────────────────────────────────
const COL = {
  USER_TABLE:'#3b82f6', EXTERNAL_TABLE:'#2563eb',
  VIEW:'#22c55e',
  SQL_STORED_PROCEDURE:'#f97316',
  SQL_SCALAR_FUNCTION:'#fb923c',
  SQL_TABLE_VALUED_FUNCTION:'#fb923c',
  SQL_INLINE_TABLE_VALUED_FUNCTION:'#fb923c',
  SCHEMA:'#a855f7',
  _default:'#64748b'
};
function nColor(type) { return COL[type] || COL._default; }
function isProc(t) { return t && (t.includes('PROCEDURE')||t.includes('FUNCTION')); }
function typeGroup(t) {
  if (!t) return 'unknown';
  if (t === 'SCHEMA') return 'schema';
  if (t.includes('TABLE')) return 'table';
  if (t === 'VIEW') return 'view';
  return 'proc';
}

// ── STATE ─────────────────────────────────────────────────────────────────────
let VIEW_MODE = 'schema';
let FOCUS = null;
let sim   = null;
let cam   = { x:0, y:0, scale:1 };
let selNode   = null;
let hovNode   = null;
let searchStr = '';
let showTable = true, showView = true, showProc = true;
let mouseDown = false, draggingCam = false, dragNode = null;
let lastMX = 0, lastMY = 0;

const canvas  = document.getElementById('graph');
const ctx     = canvas.getContext('2d');
const minimap = document.getElementById('minimap');
const mctx    = minimap.getContext('2d');

// ── FORCE SIMULATION ─────────────────────────────────────────────────────────
function makeSim(rawNodes, rawEdges) {
  const W = canvas.width, H = canvas.height;
  const nodeMap = {};
  const nodes = rawNodes.map((n, i) => {
    const angle = (i / rawNodes.length) * Math.PI * 2;
    const radius = Math.min(W, H) * 0.3;
    const node = {
      ...n,
      x: W/2 + radius * Math.cos(angle) + (Math.random()-0.5)*30,
      y: H/2 + radius * Math.sin(angle) + (Math.random()-0.5)*30,
      vx: 0, vy: 0, ax: 0, ay: 0,
      pinned: false
    };
    nodeMap[n.id] = node;
    return node;
  });
  const edges = rawEdges
    .map(e => ({ ...e, s: nodeMap[e.s || e.src], t: nodeMap[e.t || e.tgt] }))
    .filter(e => e.s && e.t && e.s !== e.t);
  return { nodes, edges, nodeMap, alpha: 1.0 };
}

function tickSim(s, W, H) {
  if (s.alpha < 0.004) return;
  const { nodes, edges } = s;
  const a = s.alpha;
  const cx = W / 2, cy = H / 2;

  // reset accelerations
  for (const n of nodes) { n.ax = 0; n.ay = 0; }

  // repulsion — O(n²), fine up to ~300 nodes
  const KR = VIEW_MODE === 'schema' ? 18000 : 6000;
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i+1; j < nodes.length; j++) {
      const a2 = nodes[i], b = nodes[j];
      const dx = (a2.x - b.x) || 0.01, dy = (a2.y - b.y) || 0.01;
      const d2 = Math.max(4, dx*dx + dy*dy);
      const d  = Math.sqrt(d2);
      const f  = (KR * a) / d2;
      a2.ax += f * dx/d;  a2.ay += f * dy/d;
      b.ax  -= f * dx/d;  b.ay  -= f * dy/d;
    }
  }

  // spring forces along edges
  const DL = VIEW_MODE === 'schema' ? 220 : 110;
  const KS = 0.18;
  for (const e of edges) {
    const { s, t } = e;
    const dx = t.x - s.x, dy = t.y - s.y;
    const d  = Math.sqrt(dx*dx + dy*dy) || 1;
    const f  = KS * (d - DL) * a;
    const fx = f*dx/d, fy = f*dy/d;
    s.ax += fx; s.ay += fy;
    t.ax -= fx; t.ay -= fy;
  }

  // gravity toward center
  const KG = 0.06;
  for (const n of nodes) {
    n.ax += (cx - n.x) * KG * a;
    n.ay += (cy - n.y) * KG * a;
  }

  // integrate
  for (const n of nodes) {
    if (n.pinned) { n.vx = 0; n.vy = 0; continue; }
    n.vx = (n.vx + n.ax) * 0.5;
    n.vy = (n.vy + n.ay) * 0.5;
    n.x += n.vx;
    n.y += n.vy;
  }

  s.alpha *= 0.975;
}

// ── RENDERING ─────────────────────────────────────────────────────────────────
function drawArrow(x1, y1, x2, y2, color, r2, width) {
  const dx = x2-x1, dy = y2-y1;
  const d = Math.sqrt(dx*dx + dy*dy);
  if (d < 2) return;
  const ex = x2 - dx/d * (r2 + 4);
  const ey = y2 - dy/d * (r2 + 4);
  const sx = x1 + dx/d * 4;
  const sy = y1 + dy/d * 4;
  ctx.beginPath();
  ctx.moveTo(sx, sy);
  ctx.lineTo(ex, ey);
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.stroke();
  // arrowhead
  const ang = Math.atan2(dy, dx);
  const hs = 5;
  ctx.beginPath();
  ctx.moveTo(ex, ey);
  ctx.lineTo(ex - hs*Math.cos(ang-0.45), ey - hs*Math.sin(ang-0.45));
  ctx.lineTo(ex - hs*Math.cos(ang+0.45), ey - hs*Math.sin(ang+0.45));
  ctx.closePath();
  ctx.fillStyle = color;
  ctx.fill();
}

function nodeRadius(n) {
  if (n.type === 'SCHEMA') {
    const tot = (n.meta.t||0) + (n.meta.v||0) + (n.meta.p||0);
    return Math.max(14, Math.min(40, 10 + Math.sqrt(tot) * 2.2));
  }
  if (n.type === 'VIEW') return 9;
  if (isProc(n.type)) return 8;
  return 8; // table
}

function render() {
  if (!sim) return;
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  ctx.save();
  ctx.translate(cam.x, cam.y);
  ctx.scale(cam.scale, cam.scale);

  const selId = selNode ? selNode.id : null;
  const connected = new Set();
  if (selNode) {
    connected.add(selId);
    for (const e of sim.edges) {
      if (e.s.id === selId) connected.add(e.t.id);
      if (e.t.id === selId) connected.add(e.s.id);
    }
  }

  const hasSel = !!selNode;
  const hasSearch = !!searchStr;

  // draw edges
  for (const e of sim.edges) {
    const active = !hasSel || (e.s.id === selId || e.t.id === selId);
    const fk = e.k === 'fk';
    let color;
    if (fk) {
      color = active ? 'rgba(202,138,4,0.85)' : 'rgba(202,138,4,0.12)';
    } else {
      color = active ? 'rgba(75,96,144,0.75)' : 'rgba(45,60,90,0.2)';
    }
    drawArrow(e.s.x, e.s.y, e.t.x, e.t.y, color, nodeRadius(e.t),
              (active && fk ? 1.5 : active ? 1 : 0.5) / cam.scale);
  }

  // draw nodes
  for (const n of sim.nodes) {
    const r = nodeRadius(n);
    const isSel  = n.id === selId;
    const isHov  = hovNode && n.id === hovNode.id;
    const isDim  = hasSel && !connected.has(n.id);
    const isHit  = hasSearch && n.label.toLowerCase().includes(searchStr.toLowerCase());
    const baseC  = nColor(n.type);

    // search pulse ring
    if (isHit) {
      ctx.beginPath();
      ctx.arc(n.x, n.y, r + 7, 0, Math.PI*2);
      ctx.fillStyle = 'rgba(250,204,21,0.18)';
      ctx.fill();
    }

    // selection ring
    if (isSel) {
      ctx.beginPath();
      ctx.arc(n.x, n.y, r + 5, 0, Math.PI*2);
      ctx.fillStyle = 'rgba(255,255,255,0.1)';
      ctx.fill();
    }

    // node fill
    ctx.beginPath();
    ctx.arc(n.x, n.y, r + (isSel ? 1 : 0), 0, Math.PI*2);
    ctx.fillStyle = isDim ? '#1a1d27' : baseC;
    if (!isDim && (isSel || isHov)) {
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth   = 1.5 / cam.scale;
      ctx.stroke();
    }
    ctx.fill();

    // label
    const showLabel = cam.scale > 0.25 || isSel || isHov;
    if (showLabel && !isDim) {
      ctx.globalAlpha = isDim ? 0.25 : 1;
      const fs = Math.min(12, Math.max(8, 10 / cam.scale));
      ctx.font = `${isSel ? '600 ' : ''}${fs}px sans-serif`;
      ctx.fillStyle = '#cbd5e1';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      const lbl = n.label.length > 24 ? n.label.slice(0, 22) + '…' : n.label;
      ctx.fillText(lbl, n.x, n.y + r + 3 / cam.scale);
      ctx.globalAlpha = 1;
    }

    // edge count badge (schema view)
    if (VIEW_MODE === 'schema' && n.type === 'SCHEMA' && cam.scale > 0.4) {
      const tot = (n.meta.t||0)+(n.meta.v||0)+(n.meta.p||0);
      const fs2 = Math.min(10, Math.max(7, 9 / cam.scale));
      ctx.font = `${fs2}px sans-serif`;
      ctx.fillStyle = '#94a3b8';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(tot + ' obj', n.x, n.y);
    }
  }

  ctx.restore();

  // schema view: draw edge dep-count badges in screen space
  if (VIEW_MODE === 'schema' && cam.scale > 0.55) {
    ctx.save();
    for (const e of sim.edges) {
      if (!e.n || e.n < 2) continue;
      const mx = (e.s.x + e.t.x)/2 * cam.scale + cam.x;
      const my = (e.s.y + e.t.y)/2 * cam.scale + cam.y;
      ctx.fillStyle = 'rgba(14,16,22,0.85)';
      ctx.font = '9px sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(e.n, mx, my);
    }
    ctx.restore();
  }

  drawMinimap();
}

function drawMinimap() {
  if (!sim || sim.nodes.length === 0) return;
  const MW = minimap.width, MH = minimap.height;
  mctx.clearRect(0, 0, MW, MH);
  mctx.fillStyle = 'rgba(14,16,22,0.7)';
  mctx.fillRect(0, 0, MW, MH);

  // find bounds
  let minX=Infinity, minY=Infinity, maxX=-Infinity, maxY=-Infinity;
  for (const n of sim.nodes) {
    minX = Math.min(minX, n.x); minY = Math.min(minY, n.y);
    maxX = Math.max(maxX, n.x); maxY = Math.max(maxY, n.y);
  }
  const span = Math.max(maxX - minX, maxY - minY, 1);
  const pad = 8;
  const sc = (Math.min(MW, MH) - pad*2) / span;

  for (const e of sim.edges) {
    const sx = (e.s.x - minX)*sc + pad, sy = (e.s.y - minY)*sc + pad;
    const tx = (e.t.x - minX)*sc + pad, ty = (e.t.y - minY)*sc + pad;
    mctx.beginPath(); mctx.moveTo(sx, sy); mctx.lineTo(tx, ty);
    mctx.strokeStyle = 'rgba(75,96,144,0.3)'; mctx.lineWidth = 0.5; mctx.stroke();
  }
  for (const n of sim.nodes) {
    const nx = (n.x - minX)*sc + pad, ny = (n.y - minY)*sc + pad;
    mctx.beginPath(); mctx.arc(nx, ny, 2, 0, Math.PI*2);
    mctx.fillStyle = nColor(n.type); mctx.fill();
  }

  // viewport indicator
  const vpX = (-cam.x / cam.scale - minX)*sc + pad;
  const vpY = (-cam.y / cam.scale - minY)*sc + pad;
  const vpW = (canvas.width  / cam.scale)*sc;
  const vpH = (canvas.height / cam.scale)*sc;
  mctx.strokeStyle = 'rgba(108,142,255,0.6)';
  mctx.lineWidth = 1;
  mctx.strokeRect(vpX, vpY, vpW, vpH);
}

// ── VIEW MANAGEMENT ───────────────────────────────────────────────────────────
function loadSchemaView() {
  VIEW_MODE = 'schema';
  FOCUS = null;
  selNode = null;
  hovNode = null;

  const rawNodes = RAW.schemas.map(s => ({
    id: s.id, label: s.id, type: 'SCHEMA', meta: s
  }));

  // filter edges based on checkboxes - for schema view show all
  const rawEdges = RAW.se.map(e => ({ s: e.s, t: e.t, k:'dep', n: e.n }));

  sim = makeSim(rawNodes, rawEdges);
  resetCamera();
  document.getElementById('back-btn').style.display = 'none';
  document.getElementById('view-label').textContent = 'Schema Overview';
  updatePanel(null);
  updateStats();
}

function drillInto(schemaId) {
  VIEW_MODE = 'objects';
  FOCUS = schemaId;
  selNode = null;
  hovNode = null;

  const showT = document.getElementById('ck-table').checked;
  const showV = document.getElementById('ck-view').checked;
  const showP = document.getElementById('ck-proc').checked;

  function typeVisible(tp) {
    if (tp.includes('TABLE')) return showT;
    if (tp === 'VIEW')        return showV;
    return showP;
  }

  // objects in focus schema
  const homeObjs = RAW.objects.filter(o => o.s === schemaId && typeVisible(o.tp));
  const homeIds  = new Set(homeObjs.map(o => o.id));

  // relevant edges
  const relEdges = RAW.edges.filter(e => homeIds.has(e.s) || homeIds.has(e.t));

  // neighbor ids from other schemas
  const neighborIds = new Set();
  for (const e of relEdges) {
    if (!homeIds.has(e.s)) neighborIds.add(e.s);
    if (!homeIds.has(e.t)) neighborIds.add(e.t);
  }

  // object lookup
  const objLookup = Object.fromEntries(RAW.objects.map(o => [o.id, o]));

  const allNodes = [
    ...homeObjs.map(o => ({
      id: o.id, label: o.n, type: o.tp, meta: o, home: true,
      r: 10
    })),
    ...[...neighborIds].map(id => {
      const o = objLookup[id];
      if (!o) return null;
      return { id: o.id, label: `${o.s}.${o.n}`, type: o.tp, meta: o, home: false, r: 7 };
    }).filter(Boolean)
  ];

  sim = makeSim(allNodes, relEdges);
  resetCamera();
  document.getElementById('back-btn').style.display = 'inline-block';
  document.getElementById('view-label').textContent = `Objects in ${schemaId}`;
  updatePanel(null);
  updateStats();
}

function goBack() { loadSchemaView(); }

function applyFilters() {
  showTable = document.getElementById('ck-table').checked;
  showView  = document.getElementById('ck-view').checked;
  showProc  = document.getElementById('ck-proc').checked;
  if (VIEW_MODE === 'objects' && FOCUS) drillInto(FOCUS);
}

function reheat() {
  if (sim) {
    sim.alpha = 1.0;
    for (const n of sim.nodes) {
      n.vx = 0; n.vy = 0; n.pinned = false;
      n.x += (Math.random()-0.5)*20;
      n.y += (Math.random()-0.5)*20;
    }
  }
}

// ── CAMERA ────────────────────────────────────────────────────────────────────
function resetCamera() {
  cam.x = 0; cam.y = 0; cam.scale = 1;
  // fit all nodes in view after a short delay (nodes might not be placed yet)
  setTimeout(() => {
    if (!sim || !sim.nodes.length) return;
    let minX=Infinity, minY=Infinity, maxX=-Infinity, maxY=-Infinity;
    for (const n of sim.nodes) {
      minX=Math.min(minX,n.x); minY=Math.min(minY,n.y);
      maxX=Math.max(maxX,n.x); maxY=Math.max(maxY,n.y);
    }
    const pad = 80;
    const scX = (canvas.width  - pad*2) / Math.max(maxX-minX, 1);
    const scY = (canvas.height - pad*2) / Math.max(maxY-minY, 1);
    cam.scale = Math.min(scX, scY, 2);
    cam.x = (canvas.width  - (maxX+minX) * cam.scale) / 2;
    cam.y = (canvas.height - (maxY+minY) * cam.scale) / 2;
  }, 400);
}

// ── SELECTION & PANEL ────────────────────────────────────────────────────────
function selectNode(n) {
  selNode = (selNode && selNode.id === n.id) ? null : n;
  updatePanel(selNode);
}

function updatePanel(n) {
  const panel = document.getElementById('panel');
  if (!n) {
    panel.innerHTML = `<p class="hint">Click any node to see its details.<br><br>${VIEW_MODE==='schema' ? '<strong>Double-click</strong> a schema to explore its objects.' : '<strong>Back</strong> returns to Schema Overview.'}</p>`;
    return;
  }
  const outE = sim.edges.filter(e => e.s.id === n.id);
  const inE  = sim.edges.filter(e => e.t.id === n.id);

  const depItems = (arr, which) => arr.map(e => {
    const other = which === 'out' ? e.t : e.s;
    return `<li><span class="dc dc-${e.k}">${e.k.toUpperCase()}</span>${escH(other.label)}</li>`;
  }).join('');

  const schemaStr = n.meta ? (n.meta.s || n.id) : n.id;

  panel.innerHTML = `
<div class="pn-hdr">
  <div class="pn-dot" style="background:${nColor(n.type)}"></div>
  <strong>${escH(n.label)}</strong>
</div>
<div class="pn-meta">
  <span>${n.type.replace(/_/g,' ')}</span>
  <span>${schemaStr !== n.label ? 'Schema: ' + escH(schemaStr) : ''}</span>
  ${VIEW_MODE==='schema' && n.meta ? `<span>${n.meta.t||0} tables &middot; ${n.meta.v||0} views &middot; ${n.meta.p||0} procs</span>` : ''}
  <span>${outE.length} outgoing &middot; ${inE.length} incoming dep${inE.length!==1?'s':''}</span>
</div>
${outE.length ? `<div class="pn-section"><div class="pn-sec-lbl">Depends on (${outE.length})</div><ul class="dep-list">${depItems(outE,'out')}</ul></div>` : ''}
${inE.length  ? `<div class="pn-section"><div class="pn-sec-lbl">Referenced by (${inE.length})</div><ul class="dep-list">${depItems(inE,'in')}</ul></div>` : ''}
${VIEW_MODE==='schema' ? `<button class="drill-btn" onclick="drillInto('${escA(n.id)}')">Explore Objects &#x2192;</button><p class="back-info">Shows all objects in this schema and their direct connections to other schemas.</p>` : ''}
`;
}

function updateStats() {
  const chips = document.getElementById('stat-chips');
  if (!sim) { chips.innerHTML=''; return; }
  const counts = {};
  for (const n of sim.nodes) {
    const g = n.type === 'SCHEMA' ? 'schema' : typeGroup(n.type);
    counts[g] = (counts[g]||0) + 1;
  }
  const dep = sim.edges.filter(e=>e.k==='dep').length;
  const fk  = sim.edges.filter(e=>e.k==='fk').length;
  chips.innerHTML = [
    counts.schema ? `<span class="stat-chip chip-schema">${counts.schema} schemas</span>` : '',
    counts.table  ? `<span class="stat-chip chip-table">${counts.table} tables</span>` : '',
    counts.view   ? `<span class="stat-chip chip-view">${counts.view} views</span>` : '',
    counts.proc   ? `<span class="stat-chip chip-proc">${counts.proc} procs</span>` : '',
    dep ? `<span class="stat-chip chip-dep">${dep} dep edges</span>` : '',
    fk  ? `<span class="stat-chip chip-fk">${fk} FK edges</span>` : '',
  ].join('');
}

// ── HIT DETECTION ─────────────────────────────────────────────────────────────
function worldXY(ex, ey) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: (ex - rect.left - cam.x) / cam.scale,
    y: (ey - rect.top  - cam.y) / cam.scale
  };
}

function nodeAtWorld(wx, wy) {
  if (!sim) return null;
  for (let i = sim.nodes.length-1; i >= 0; i--) {
    const n = sim.nodes[i];
    const r = nodeRadius(n) + 4;
    const dx = n.x - wx, dy = n.y - wy;
    if (dx*dx + dy*dy <= r*r) return n;
  }
  return null;
}

// ── TOOLTIP ───────────────────────────────────────────────────────────────────
const tip = document.getElementById('tooltip');
function showTip(n, px, py) {
  tip.querySelector('.tt-name').textContent = n.label;
  const meta = [];
  if (n.type !== 'SCHEMA') meta.push(n.type.replace(/_/g,' '));
  if (n.meta && n.meta.s && n.meta.s !== n.label) meta.push('Schema: ' + n.meta.s);
  if (n.type === 'SCHEMA' && n.meta) meta.push(`${n.meta.t||0}T ${n.meta.v||0}V ${n.meta.p||0}P`);
  const outE = sim.edges.filter(e => e.s.id === n.id).length;
  const inE  = sim.edges.filter(e => e.t.id === n.id).length;
  if (outE || inE) meta.push(`${outE} out / ${inE} in`);
  tip.querySelector('.tt-meta').textContent = meta.join(' · ');
  tip.style.display = 'block';
  tip.style.left = (px + 12) + 'px';
  tip.style.top  = (py - 10) + 'px';
}
function hideTip() { tip.style.display = 'none'; }

// ── MOUSE EVENTS ──────────────────────────────────────────────────────────────
let clickStart = { x:0, y:0, t:0 };

canvas.addEventListener('mousedown', e => {
  e.preventDefault();
  mouseDown = true;
  lastMX = e.clientX; lastMY = e.clientY;
  clickStart = { x: e.clientX, y: e.clientY, t: Date.now() };
  const { x, y } = worldXY(e.clientX, e.clientY);
  dragNode = nodeAtWorld(x, y);
  if (dragNode) { dragNode.pinned = true; sim.alpha = Math.max(sim.alpha, 0.2); }
  else draggingCam = true;
  document.getElementById('canvas-wrap').classList.toggle('grabbing', !dragNode);
});

window.addEventListener('mousemove', e => {
  if (!mouseDown) {
    // hover
    const { x, y } = worldXY(e.clientX, e.clientY);
    const n = nodeAtWorld(x, y);
    hovNode = n;
    canvas.style.cursor = n ? 'pointer' : 'grab';
    if (n) showTip(n, e.clientX, e.clientY);
    else   hideTip();
    return;
  }
  const dx = e.clientX - lastMX, dy = e.clientY - lastMY;
  if (dragNode) {
    const { x, y } = worldXY(e.clientX, e.clientY);
    dragNode.x = x; dragNode.y = y;
    dragNode.vx = 0; dragNode.vy = 0;
  } else if (draggingCam) {
    cam.x += dx; cam.y += dy;
  }
  lastMX = e.clientX; lastMY = e.clientY;
});

window.addEventListener('mouseup', e => {
  if (!mouseDown) return;
  mouseDown = false;
  const moved = Math.abs(e.clientX-clickStart.x) + Math.abs(e.clientY-clickStart.y);
  const quick = Date.now() - clickStart.t < 300;

  if (moved < 6 && quick) {
    const { x, y } = worldXY(e.clientX, e.clientY);
    const n = nodeAtWorld(x, y);
    if (n) {
      if (e.detail >= 2 && VIEW_MODE === 'schema') {
        drillInto(n.id);
      } else {
        selectNode(n);
      }
    } else {
      selNode = null;
      updatePanel(null);
    }
  }
  draggingCam = false;
  dragNode = null;
  document.getElementById('canvas-wrap').classList.remove('grabbing');
});

canvas.addEventListener('wheel', e => {
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left, my = e.clientY - rect.top;
  const factor = e.deltaY < 0 ? 1.15 : 0.87;
  const ns = Math.max(0.05, Math.min(12, cam.scale * factor));
  cam.x = mx - (mx - cam.x) * (ns / cam.scale);
  cam.y = my - (my - cam.y) * (ns / cam.scale);
  cam.scale = ns;
}, { passive: false });

// touch pan/pinch
let touchLast = [], touchDist0 = 0, camScale0 = 1;
canvas.addEventListener('touchstart', e => {
  touchLast = Array.from(e.touches).map(t => ({ x:t.clientX, y:t.clientY }));
  if (e.touches.length === 2) {
    const a = e.touches[0], b = e.touches[1];
    touchDist0 = Math.hypot(a.clientX-b.clientX, a.clientY-b.clientY);
    camScale0 = cam.scale;
  }
}, { passive:true });
canvas.addEventListener('touchmove', e => {
  e.preventDefault();
  if (e.touches.length === 1 && touchLast.length === 1) {
    cam.x += e.touches[0].clientX - touchLast[0].x;
    cam.y += e.touches[0].clientY - touchLast[0].y;
  } else if (e.touches.length === 2 && touchDist0 > 0) {
    const a = e.touches[0], b = e.touches[1];
    const d = Math.hypot(a.clientX-b.clientX, a.clientY-b.clientY);
    cam.scale = Math.max(0.05, Math.min(12, camScale0 * d / touchDist0));
  }
  touchLast = Array.from(e.touches).map(t => ({ x:t.clientX, y:t.clientY }));
}, { passive:false });

// ── SEARCH ────────────────────────────────────────────────────────────────────
document.getElementById('search').addEventListener('input', function() {
  searchStr = this.value.trim().toLowerCase();
});

// ── ANIMATION LOOP ────────────────────────────────────────────────────────────
function loop() {
  if (sim) {
    for (let i = 0; i < 6; i++) tickSim(sim, canvas.width, canvas.height);
  }
  render();
  requestAnimationFrame(loop);
}

// ── RESIZE ────────────────────────────────────────────────────────────────────
function resizeCanvas() {
  const wrap = document.getElementById('canvas-wrap');
  canvas.width  = wrap.clientWidth;
  canvas.height = wrap.clientHeight;
}
window.addEventListener('resize', resizeCanvas);
resizeCanvas();

// ── HELPERS ───────────────────────────────────────────────────────────────────
function escH(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function escA(s) {
  return String(s||'').replace(/'/g,"\\'").replace(/"/g,'\\"');
}

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
    conn = get_connection()
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

    objects = run("objects",      OBJECTS_SQL)
    deps    = run("dependencies", DEPS_SQL)
    fks     = run("foreign keys", FK_SQL)
    conn.close()

    print("\nBuilding graph data…")
    graph = build_graph(objects, deps, fks)

    cross = len(graph['se'])
    print(f"  Schemas        : {len(graph['schemas'])}")
    print(f"  Object nodes   : {len(graph['objects'])}")
    print(f"  Object edges   : {len(graph['edges'])}")
    print(f"  Cross-schema   : {cross} schema-level edges")

    data_json = json.dumps(graph, separators=(',', ':'))
    html = TEMPLATE.replace('__DATA__', data_json).replace('__TITLE__', DATABASE)

    out = f"/home/thedavidporter/synapse_connections_{datetime.now().strftime('%Y%m%d_%H%M')}.html"
    with open(out, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"\nSaved to: {out}")

if __name__ == "__main__":
    main()
