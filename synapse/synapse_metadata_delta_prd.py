#!/usr/bin/env python3
"""
Synapse Metadata Delta Report — Prd
Compares the two most recent daily snapshots and generates an HTML
report showing what changed: new/removed objects, column changes,
and significant row count movements.
"""

import json
import os
import glob
from datetime import datetime

SNAP_DIR   = "/home/thedavidporter/snapshots"
OUT_FILE   = "/home/thedavidporter/synapse_metadata_delta_prd.html"
DATABASE   = "zus1-idoh-prd-v1-sql-dw"
ROW_CHANGE_THRESHOLD = 0.05  # alert on row count changes >= 5%

# ── load snapshots ─────────────────────────────────────────────────────────────

def load_snapshots():
    files = sorted(glob.glob(f"{SNAP_DIR}/synapse_prd_*.json"))
    if len(files) < 1:
        raise RuntimeError("No snapshots found. Run synapse_metadata_report_dev.py first.")
    if len(files) < 2:
        print("Only one snapshot found — no previous day to compare against.")
        print("Run the report again tomorrow for a delta.")
        return None, load_json(files[-1])
    return load_json(files[-2]), load_json(files[-1])

def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

# ── diff helpers ───────────────────────────────────────────────────────────────

def obj_key(o):
    return f"{o['schema_name']}.{o['object_name']}"

def col_key(c):
    return f"{c['schema_name']}.{c['table_name']}.{c['COLUMN_NAME']}"

def diff_objects(prev, curr):
    prev_map = {obj_key(o): o for o in prev["objects"]}
    curr_map = {obj_key(o): o for o in curr["objects"]}

    added   = [curr_map[k] for k in curr_map if k not in prev_map]
    removed = [prev_map[k] for k in prev_map if k not in curr_map]
    modified = [
        curr_map[k] for k in curr_map
        if k in prev_map and curr_map[k].get("modified") != prev_map[k].get("modified")
    ]
    return added, removed, modified

def diff_columns(prev, curr):
    prev_map = {col_key(c): c for c in prev["columns"]}
    curr_map = {col_key(c): c for c in curr["columns"]}

    added   = [curr_map[k] for k in curr_map if k not in prev_map]
    removed = [prev_map[k] for k in prev_map if k not in curr_map]
    changed = []
    for k in curr_map:
        if k in prev_map:
            p, c = prev_map[k], curr_map[k]
            if p.get("DATA_TYPE") != c.get("DATA_TYPE") or p.get("max_length") != c.get("max_length"):
                changed.append({"key": k, "prev": p, "curr": c})
    return added, removed, changed

def diff_row_counts(prev, curr):
    prev_map = {f"{r['schema_name']}.{r['table_name']}": r["row_count"] for r in prev["row_counts"]}
    curr_map = {f"{r['schema_name']}.{r['table_name']}": r["row_count"] for r in curr["row_counts"]}

    changes = []
    for k in curr_map:
        if k in prev_map:
            p, c = prev_map[k] or 0, curr_map[k] or 0
            if p == 0 and c == 0:
                continue
            pct = abs(c - p) / max(p, 1)
            if pct >= ROW_CHANGE_THRESHOLD or abs(c - p) >= 10000:
                changes.append({"table": k, "prev": p, "curr": c,
                                 "delta": c - p, "pct": pct})
    changes.sort(key=lambda x: abs(x["delta"]), reverse=True)
    return changes

# ── HTML builder ───────────────────────────────────────────────────────────────

CSS = """
:root{--bg:#0f1117;--sur:#1a1d27;--sur2:#252836;--brd:#2e3245;
  --txt:#e2e8f0;--mut:#8892a4;--acc:#6c8eff;--grn:#4ade80;
  --red:#f87171;--yel:#fbbf24;--pur:#c084fc;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font:14px/1.5 'Segoe UI',system-ui,sans-serif;
  display:flex;flex-direction:column;height:100vh;overflow:hidden}

/* fixed header */
.hdr{padding:18px 28px 0;flex-shrink:0}
h1{font-size:20px;font-weight:700;margin-bottom:2px}
.sub{color:var(--mut);font-size:12px;margin-bottom:14px}

/* stat cards — clickable nav */
.stats{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.sc{background:var(--sur);border:1px solid var(--brd);border-radius:10px;
  padding:11px 16px;min-width:105px;cursor:pointer;transition:border-color .15s}
.sc:hover{border-color:var(--acc)}
.sc.active-card{border-color:var(--acc);background:var(--sur2)}
.sc-n{font-size:22px;font-weight:700;line-height:1}
.sc-l{font-size:11px;color:var(--mut);margin-top:3px}
.grn{color:var(--grn)} .red{color:var(--red)} .yel{color:var(--yel)} .mut{color:var(--mut)}

/* tab bar */
.tabs{display:flex;gap:2px;border-bottom:2px solid var(--brd);padding:0 28px;
  flex-shrink:0;flex-wrap:wrap}
.tab{padding:7px 13px;cursor:pointer;border-radius:6px 6px 0 0;font-size:12px;font-weight:600;
  color:var(--mut);border:1px solid transparent;border-bottom:none;
  margin-bottom:-2px;user-select:none;white-space:nowrap}
.tab:hover{color:var(--txt);background:var(--sur)}
.tab.active{background:var(--sur);border-color:var(--brd);border-bottom-color:var(--sur);color:var(--txt)}
.tab.zero{opacity:.45}

/* scrollable content area */
.content{flex:1;overflow-y:auto;padding:18px 28px}
.panel{display:none}.panel.active{display:block}

/* search */
.srch{margin-bottom:10px}
.srch input{padding:7px 11px;background:var(--sur);border:1px solid var(--brd);
  border-radius:6px;color:var(--txt);font-size:12px;width:100%;max-width:400px;outline:none}
.srch input:focus{border-color:var(--acc)}

/* tables */
.none{color:var(--mut);font-size:13px;padding:10px 0}
.tw{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:12px}
th{background:var(--sur2);padding:7px 11px;text-align:left;font-weight:700;
  border-bottom:2px solid var(--brd);white-space:nowrap;position:sticky;top:0;z-index:1}
td{padding:5px 11px;border-bottom:1px solid var(--brd);vertical-align:top}
tr:hover td{background:var(--sur)}
.tag{display:inline-block;font-size:10px;padding:1px 6px;border-radius:3px;font-weight:700}
.tag-add{background:#1a3a2a;color:var(--grn)}
.tag-rem{background:#3a1e1e;color:var(--red)}
.tag-mod{background:#3a2e1a;color:var(--yel)}
.arrow-up{color:var(--grn)} .arrow-down{color:var(--red)}
.row-counter{font-size:11px;color:var(--mut);margin-bottom:6px}

/* overview summary cards */
.ov-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:9px;margin-bottom:18px}
.ov-card{background:var(--sur);border:1px solid var(--brd);border-radius:8px;
  padding:13px 15px;cursor:pointer;transition:border-color .15s}
.ov-card:hover{border-color:var(--acc)}
.ov-card-n{font-size:26px;font-weight:700;line-height:1;margin-bottom:4px}
.ov-card-l{font-size:12px;color:var(--mut)}
.ov-card-zero .ov-card-n{color:var(--mut)}
"""

JS = r"""
const PAGE_SIZE = 300;

function escH(s) {
  if (s == null) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── tab switching ─────────────────────────────────────────────────────────────
function showTab(id, triggerEl) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.sc').forEach(c => c.classList.remove('active-card'));
  document.getElementById('p-' + id).classList.add('active');
  if (triggerEl) triggerEl.classList.add('active');
  const card = document.getElementById('card-' + id);
  if (card) card.classList.add('active-card');
  // lazy-init the virtual table the first time a tab is opened
  const state = TABLE_STATES[id];
  if (state && !state.initialized) {
    state.initialized = true;
    _loadMore(id);
  }
}

// ── virtual table engine ──────────────────────────────────────────────────────
const TABLE_STATES = {};

function _registerTable(id, data, renderFn, colspan) {
  TABLE_STATES[id] = {
    data, renderFn, colspan,
    filtered: data,
    loaded: 0,
    initialized: false,
  };
}

function _loadMore(id) {
  const state = TABLE_STATES[id];
  const tbody = document.getElementById(id + '-tbody');
  const sentinel = tbody.querySelector('.sentinel-row');
  if (sentinel) sentinel.remove();

  if (state.filtered.length === 0) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td colspan="${state.colspan}" class="none">No changes in this category.</td>`;
    tbody.appendChild(tr);
    _updateCounter(id);
    return;
  }

  const end = Math.min(state.loaded + PAGE_SIZE, state.filtered.length);
  const frag = document.createDocumentFragment();
  for (let i = state.loaded; i < end; i++) frag.appendChild(state.renderFn(state.filtered[i]));
  tbody.appendChild(frag);
  state.loaded = end;
  _updateCounter(id);

  if (state.loaded < state.filtered.length) {
    const row = document.createElement('tr');
    row.className = 'sentinel-row';
    row.innerHTML = `<td colspan="${state.colspan}" style="text-align:center;padding:14px;color:var(--mut);font-size:12px">` +
      `Loading… (${state.loaded.toLocaleString()} / ${state.filtered.length.toLocaleString()})</td>`;
    tbody.appendChild(row);
    const obs = new IntersectionObserver(entries => {
      if (entries[0].isIntersecting) { obs.disconnect(); _loadMore(id); }
    }, { rootMargin: '300px' });
    obs.observe(row);
  }
}

function _updateCounter(id) {
  const state = TABLE_STATES[id];
  const el = document.getElementById(id + '-counter');
  if (!el) return;
  if (state.filtered.length === 0) {
    el.textContent = '';
  } else if (state.loaded >= state.filtered.length) {
    el.textContent = state.filtered.length.toLocaleString() + ' rows';
    if (state.filtered.length < state.data.length)
      el.textContent += ` (filtered from ${state.data.length.toLocaleString()})`;
  } else {
    el.textContent = `Showing ${state.loaded.toLocaleString()} of ${state.filtered.length.toLocaleString()} — scroll for more`;
  }
}

function filterTable(id) {
  const state = TABLE_STATES[id];
  if (!state) return;
  const inp = document.getElementById(id + '-search');
  const q = inp ? inp.value.toLowerCase().trim() : '';
  state.filtered = q
    ? state.data.filter(d => Object.values(d).join('\0').toLowerCase().includes(q))
    : state.data;
  state.loaded = 0;
  document.getElementById(id + '-tbody').innerHTML = '';
  _loadMore(id);
}

// ── row renderers ─────────────────────────────────────────────────────────────
function renderObjAddedRow(d) {
  const tr = document.createElement('tr');
  tr.innerHTML = `<td>${escH(d.schema)}</td><td>${escH(d.object)}</td>` +
    `<td><span class="tag tag-add">${escH(d.type)}</span></td>` +
    `<td style="color:var(--mut)">${escH(d.created)}</td>`;
  return tr;
}

function renderObjRemovedRow(d) {
  const tr = document.createElement('tr');
  tr.innerHTML = `<td>${escH(d.schema)}</td><td>${escH(d.object)}</td>` +
    `<td><span class="tag tag-rem">${escH(d.type)}</span></td>` +
    `<td style="color:var(--mut)">${escH(d.modified)}</td>`;
  return tr;
}

function renderObjModifiedRow(d) {
  const tr = document.createElement('tr');
  tr.innerHTML = `<td>${escH(d.schema)}</td><td>${escH(d.object)}</td>` +
    `<td><span class="tag tag-mod">${escH(d.type)}</span></td>` +
    `<td style="color:var(--mut)">${escH(d.prev_modified)}</td>` +
    `<td style="color:var(--yel)">${escH(d.curr_modified)}</td>`;
  return tr;
}

function renderColAddedRow(d) {
  const tr = document.createElement('tr');
  tr.innerHTML = `<td>${escH(d.schema)}</td><td>${escH(d.table)}</td>` +
    `<td><span class="tag tag-add">${escH(d.column)}</span></td><td>${escH(d.dtype)}</td>`;
  return tr;
}

function renderColRemovedRow(d) {
  const tr = document.createElement('tr');
  tr.innerHTML = `<td>${escH(d.schema)}</td><td>${escH(d.table)}</td>` +
    `<td><span class="tag tag-rem">${escH(d.column)}</span></td><td>${escH(d.dtype)}</td>`;
  return tr;
}

function renderColChangedRow(d) {
  const tr = document.createElement('tr');
  tr.innerHTML = `<td>${escH(d.schema)}</td><td>${escH(d.table)}</td><td>${escH(d.column)}</td>` +
    `<td class="red">${escH(d.prev_type)}${d.prev_len ? ' (' + escH(d.prev_len) + ')' : ''}</td>` +
    `<td class="grn">${escH(d.curr_type)}${d.curr_len ? ' (' + escH(d.curr_len) + ')' : ''}</td>`;
  return tr;
}

function renderRcRow(d) {
  const arrow = d.delta > 0 ? '<span class="arrow-up">▲</span>' : '<span class="arrow-down">▼</span>';
  const tr = document.createElement('tr');
  tr.innerHTML = `<td>${escH(d.table)}</td>` +
    `<td style="text-align:right">${Number(d.prev).toLocaleString()}</td>` +
    `<td style="text-align:right">${Number(d.curr).toLocaleString()}</td>` +
    `<td style="text-align:right">${arrow} ${Math.abs(d.delta).toLocaleString()}</td>` +
    `<td style="text-align:right">${escH(d.pct)}</td>`;
  return tr;
}

// ── register all tables ───────────────────────────────────────────────────────
_registerTable('obj-added',    OBJ_ADDED_DATA,    renderObjAddedRow,   4);
_registerTable('obj-removed',  OBJ_REMOVED_DATA,  renderObjRemovedRow, 4);
_registerTable('obj-modified', OBJ_MODIFIED_DATA, renderObjModifiedRow,5);
_registerTable('col-added',    COL_ADDED_DATA,    renderColAddedRow,   4);
_registerTable('col-removed',  COL_REMOVED_DATA,  renderColRemovedRow, 4);
_registerTable('col-changed',  COL_CHANGED_DATA,  renderColChangedRow, 5);
_registerTable('row-counts',   RC_DATA,           renderRcRow,         5);
"""

def esc(s):
    if s is None: return ""
    return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def _tab(tid, label, count, color=""):
    zero = " zero" if count == 0 else ""
    cnt_html = f' <span class="{color}">({count:,})</span>' if count else ' <span class="mut">(0)</span>'
    return f'<div class="tab{zero}" id="tab-{tid}" onclick="showTab(\'{tid}\',this)">{label}{cnt_html}</div>'

def _panel(tid, title, thead_html, search=True):
    search_html = (
        f'<div class="srch"><input id="{tid}-search" placeholder="Search…" '
        f'oninput="filterTable(\'{tid}\')"/></div>'
    ) if search else ""
    return f"""
  <div class="panel" id="p-{tid}">
    {search_html}
    <div id="{tid}-counter" class="row-counter"></div>
    <div class="tw"><table>
      <thead><tr>{thead_html}</tr></thead>
      <tbody id="{tid}-tbody"></tbody>
    </table></div>
  </div>"""

def build_html(prev, curr, obj_added, obj_removed, obj_modified,
               col_added, col_removed, col_changed, rc_changes, generated):

    prev_date = prev["generated"][:10] if prev else "N/A"
    curr_date = curr["generated"][:10]
    total_changes = (len(obj_added) + len(obj_removed) + len(obj_modified) +
                     len(col_added) + len(col_removed) + len(col_changed) + len(rc_changes))

    # ── build prev snapshot object map for modified date lookup ────────────────
    prev_obj_map = {f"{o['schema_name']}.{o['object_name']}": o for o in prev.get("objects", [])}

    # ── data arrays ────────────────────────────────────────────────────────────
    obj_added_data = [
        {"schema": o["schema_name"], "object": o["object_name"],
         "type": o.get("object_type",""), "created": o.get("create_date", o.get("created",""))}
        for o in sorted(obj_added, key=lambda x: (x["schema_name"], x["object_name"]))
    ]
    obj_removed_data = [
        {"schema": o["schema_name"], "object": o["object_name"],
         "type": o.get("object_type",""), "modified": o.get("modified","")}
        for o in sorted(obj_removed, key=lambda x: (x["schema_name"], x["object_name"]))
    ]
    obj_modified_data = [
        {"schema": o["schema_name"], "object": o["object_name"],
         "type": o.get("object_type",""),
         "prev_modified": prev_obj_map.get(f"{o['schema_name']}.{o['object_name']}", {}).get("modified",""),
         "curr_modified": o.get("modified","")}
        for o in sorted(obj_modified, key=lambda x: (x["schema_name"], x["object_name"]))
    ]
    col_added_data = [
        {"schema": c["schema_name"], "table": c["table_name"],
         "column": c["COLUMN_NAME"], "dtype": c["DATA_TYPE"]}
        for c in sorted(col_added, key=lambda x: (x["schema_name"], x["table_name"], x["COLUMN_NAME"]))
    ]
    col_removed_data = [
        {"schema": c["schema_name"], "table": c["table_name"],
         "column": c["COLUMN_NAME"], "dtype": c["DATA_TYPE"]}
        for c in sorted(col_removed, key=lambda x: (x["schema_name"], x["table_name"], x["COLUMN_NAME"]))
    ]
    col_changed_data = [
        {"schema":    c["key"].split(".")[0],
         "table":     c["key"].split(".")[1],
         "column":    c["key"].split(".")[2],
         "prev_type": c["prev"].get("DATA_TYPE",""),
         "prev_len":  str(c["prev"].get("max_length","")),
         "curr_type": c["curr"].get("DATA_TYPE",""),
         "curr_len":  str(c["curr"].get("max_length","")),
        }
        for c in sorted(col_changed, key=lambda x: x["key"])
    ]
    rc_data = [
        {"table": r["table"], "prev": r["prev"], "curr": r["curr"],
         "delta": r["delta"], "pct": f"{r['pct']*100:.1f}%"}
        for r in rc_changes
    ]

    # ── overview cards ─────────────────────────────────────────────────────────
    def ov_card(tid, label, count, cls):
        zero_cls = " ov-card-zero" if count == 0 else ""
        return (
            f'<div class="ov-card{zero_cls}" id="card-{tid}" '
            f'onclick="showTab(\'{tid}\',document.getElementById(\'tab-{tid}\'))">'
            f'<div class="ov-card-n {cls}">{count:,}</div>'
            f'<div class="ov-card-l">{label}</div></div>'
        )

    ov_cards = (
        ov_card("obj-added",   "Objects Added",        len(obj_added),   "grn") +
        ov_card("obj-removed", "Objects Removed",       len(obj_removed), "red") +
        ov_card("obj-modified","Objects Modified",      len(obj_modified),"yel") +
        ov_card("col-added",   "Columns Added",         len(col_added),   "grn") +
        ov_card("col-removed", "Columns Removed",       len(col_removed), "red") +
        ov_card("col-changed", "Column Types Changed",  len(col_changed), "yel") +
        ov_card("row-counts",  "Row Count Changes",     len(rc_changes),  "yel")
    )

    # ── stat cards (always-visible header strip) ───────────────────────────────
    def sc(tid, label, count, cls):
        return (
            f'<div class="sc" onclick="showTab(\'{tid}\',document.getElementById(\'tab-{tid}\'))">'
            f'<div class="sc-n {cls}">{count:,}</div>'
            f'<div class="sc-l">{label}</div></div>'
        )

    stat_cards = (
        sc("obj-added",   "Objects Added",        len(obj_added),   "grn") +
        sc("obj-removed", "Objects Removed",       len(obj_removed), "red") +
        sc("obj-modified","Objects Modified",      len(obj_modified),"yel") +
        sc("col-added",   "Columns Added",         len(col_added),   "grn") +
        sc("col-removed", "Columns Removed",       len(col_removed), "red") +
        sc("col-changed", "Column Types Changed",  len(col_changed), "yel") +
        sc("row-counts",  "Row Count Changes",     len(rc_changes),  "yel")
    )

    # ── tab bar ────────────────────────────────────────────────────────────────
    tab_bar = (
        '<div class="tab active" id="tab-overview" onclick="showTab(\'overview\',this)">Overview</div>' +
        _tab("obj-added",   "Objects Added",        len(obj_added),   "grn") +
        _tab("obj-removed", "Objects Removed",       len(obj_removed), "red") +
        _tab("obj-modified","Objects Modified",      len(obj_modified),"yel") +
        _tab("col-added",   "Columns Added",         len(col_added),   "grn") +
        _tab("col-removed", "Columns Removed",       len(col_removed), "red") +
        _tab("col-changed", "Column Types Changed",  len(col_changed), "yel") +
        _tab("row-counts",  "Row Count Changes",     len(rc_changes),  "yel")
    )

    # ── panels ─────────────────────────────────────────────────────────────────
    panels = f"""
  <div class="panel active" id="p-overview">
    <p style="font-size:12px;color:var(--mut);margin-bottom:14px">
      Click any card below or use the tabs to explore each category of change.
    </p>
    <div class="ov-grid">{ov_cards}</div>
  </div>""" + \
    _panel("obj-added",   "Objects Added",
           "<th>Schema</th><th>Object</th><th>Type</th><th>Created</th>") + \
    _panel("obj-removed", "Objects Removed",
           "<th>Schema</th><th>Object</th><th>Type</th><th>Last Modified</th>") + \
    _panel("obj-modified","Objects Modified",
           "<th>Schema</th><th>Object</th><th>Type</th><th>Prev Modified</th><th>Curr Modified</th>") + \
    _panel("col-added",   "Columns Added",
           "<th>Schema</th><th>Table</th><th>Column</th><th>Data Type</th>") + \
    _panel("col-removed", "Columns Removed",
           "<th>Schema</th><th>Table</th><th>Column</th><th>Data Type</th>") + \
    _panel("col-changed", "Column Types Changed",
           "<th>Schema</th><th>Table</th><th>Column</th><th>Previous Type</th><th>New Type</th>") + \
    _panel("row-counts",  "Row Count Changes",
           "<th>Table</th><th style='text-align:right'>Previous</th><th style='text-align:right'>Current</th>"
           "<th style='text-align:right'>Delta</th><th style='text-align:right'>Change %</th>")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Synapse Delta — {esc(DATABASE)}</title>
<style>{CSS}.help-fab{{position:fixed;bottom:22px;right:22px;width:38px;height:38px;border-radius:50%;background:var(--acc);color:#fff;font-size:17px;font-weight:700;display:flex;align-items:center;justify-content:center;text-decoration:none;z-index:9999;box-shadow:0 2px 10px rgba(0,0,0,.5);opacity:.8;transition:opacity .15s;line-height:1}}.help-fab:hover{{opacity:1}}</style>
<script>
const OBJ_ADDED_DATA    = {json.dumps(obj_added_data,    separators=(',',':'))};
const OBJ_REMOVED_DATA  = {json.dumps(obj_removed_data,  separators=(',',':'))};
const OBJ_MODIFIED_DATA = {json.dumps(obj_modified_data, separators=(',',':'))};
const COL_ADDED_DATA    = {json.dumps(col_added_data,    separators=(',',':'))};
const COL_REMOVED_DATA  = {json.dumps(col_removed_data,  separators=(',',':'))};
const COL_CHANGED_DATA  = {json.dumps(col_changed_data,  separators=(',',':'))};
const RC_DATA           = {json.dumps(rc_data,           separators=(',',':'))};
</script>
</head>
<body>
<a href="help.html" class="help-fab" title="Help &amp; Guide">?</a>

<div class="hdr">
  <h1>Synapse Metadata Delta Report</h1>
  <p class="sub">
    Database: <strong>{esc(DATABASE)}</strong>
    &nbsp;|&nbsp; Comparing: <strong>{esc(prev_date)}</strong> → <strong>{esc(curr_date)}</strong>
    &nbsp;|&nbsp; Generated: {esc(generated)}
    &nbsp;|&nbsp; <strong>{total_changes:,}</strong> total changes
  </p>
  <div class="stats">{stat_cards}</div>
</div>

<div class="tabs">{tab_bar}</div>

<div class="content">{panels}</div>

<script>{JS}</script>
</body>
</html>"""

# ── main ───────────────────────────────────────────────────────────────────────

def main():
    print("Loading snapshots…")
    prev, curr = load_snapshots()

    if prev is None:
        return

    print(f"  Previous : {prev['generated'][:10]}")
    print(f"  Current  : {curr['generated'][:10]}")

    print("\nComputing diff…")
    obj_added, obj_removed, obj_modified = diff_objects(prev, curr)
    col_added, col_removed, col_changed  = diff_columns(prev, curr)
    rc_changes                           = diff_row_counts(prev, curr)

    print(f"  Objects  : +{len(obj_added)} added, -{len(obj_removed)} removed, ~{len(obj_modified)} modified")
    print(f"  Columns  : +{len(col_added)} added, -{len(col_removed)} removed, ~{len(col_changed)} type changes")
    print(f"  Row counts: {len(rc_changes)} significant changes")

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    print("\nBuilding HTML…")
    html = build_html(prev, curr, obj_added, obj_removed, obj_modified,
                      col_added, col_removed, col_changed, rc_changes, generated)

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\nDelta report saved to:\n  {OUT_FILE}")


    if not os.environ.get('PUBLISH_RUNNING'):
        try:
            import generate_metadata_index
            generate_metadata_index.main()
            print("  Index updated       : index.html")
        except Exception as exc:
            print(f"  Warning: could not update index.html: {exc}")

if __name__ == "__main__":
    main()
