#!/usr/bin/env python3
"""
Pipeline Documentation Report
Generates a standalone HTML documentation page for a single ADF pipeline
showing sources, execution chains, transformations, and destinations.

Usage:
  python3 pipeline_doc_report.py --env dev --pipeline "PL_CENSUS_Load_Standardized_Data"
"""

import argparse
import json
import re
import struct
import subprocess
import sys
from collections import defaultdict
from datetime import datetime

import pyodbc

sys.path.insert(0, '/home/thedavidporter')
from adf_metadata_report import fetch_all, ENVIRONMENTS
from azure.identity import AzureCliCredential
from azure.mgmt.datafactory import DataFactoryManagementClient

SUBSCRIPTION_ID = "57493fde-eff8-432f-8574-4f1281bd2ce3"

DW_SERVERS = {
    "dev": ("zus1-idoh-dev-v2-sql-server", "zus1-idoh-dev-v2-sql-dw"),
    "prd": ("zus1-idoh-prd-v1-sql-server", "zus1-idoh-prd-v1-sql-dw"),
}


# ── Synapse DW connection + sproc fetch ───────────────────────────────────────

def dw_connect(env):
    server, database = DW_SERVERS[env]
    print(f"  Connecting to {server}/{database}…", end="", flush=True)
    token_raw = subprocess.check_output(
        ["az", "account", "get-access-token", "--resource", "https://database.windows.net/",
         "--subscription", SUBSCRIPTION_ID, "--query", "accessToken", "-o", "tsv"],
        stderr=subprocess.DEVNULL,
    ).decode().strip()
    tb = token_raw.encode("utf-16-le")
    ts = struct.pack(f"<I{len(tb)}s", len(tb), tb)
    conn_str = (
        f"Driver={{ODBC Driver 18 for SQL Server}};"
        f"Server={server}.database.windows.net,1433;"
        f"Database={database};Encrypt=yes;TrustServerCertificate=no;"
    )
    conn = pyodbc.connect(conn_str, attrs_before={1256: ts})
    print(" connected.")
    return conn


def fetch_sproc_definitions(conn, sproc_names):
    """Return {db_name: definition} for each sproc name found in the DW (deduped by DB name)."""
    results = {}
    cur = conn.cursor()
    for name in sproc_names:
        cur.execute("""
            SELECT o.name, m.definition
            FROM sys.sql_modules m
            JOIN sys.objects o ON m.object_id = o.object_id
            WHERE o.type = 'P' AND o.name = ?
        """, name)
        row = cur.fetchone()
        if row and row.name not in results:
            results[row.name] = row.definition or ""
    return results


def parse_sproc(name, sql):
    """
    Parse a stored procedure definition and return a structured summary dict.
    Extracts: author, description, output tables, dashboards served, and step list.
    """
    info = {
        "name":         name,
        "schema":       "",
        "author":       "",
        "description":  "",
        "output_table": "",
        "operation":    "",
        "indexes_built": [],
        "temp_tables":  [],
        "dashboards":   [],
        "steps":        [],
        "raw_sql":      sql,
    }

    # Schema from CREATE PROC line
    m = re.search(r"CREATE\s+PROC(?:EDURE)?\s+\[?(\w+)\]?\.\[?(\w+)\]?", sql, re.IGNORECASE)
    if m:
        info["schema"] = m.group(1)

    # Author
    m = re.search(r"Author:\s*(.+)", sql)
    if m:
        info["author"] = m.group(1).strip()

    # Description block (multi-line between Description: and next blank line / ***)
    m = re.search(r"Description:\s*\n([\s\S]+?)(?=\n\s*\n\s*(?:TODO|Author|\*{5}|\Z))", sql)
    if m:
        info["description"] = re.sub(r"\n\t", "\n", m.group(1)).strip()

    # Truncate (full-reload pattern)
    truncates = re.findall(r"truncate\s+table\s+([\w\.\[\]]+)", sql, re.IGNORECASE)
    if truncates:
        info["output_table"] = truncates[0].replace("[","").replace("]","")
        info["operation"] = "Full truncate + reload on every run"

    # Conditional index creation
    idx_matches = re.findall(r"create\s+\w*\s*index\s+(\w+)\s+on\s+([\w\.\[\]]+)\s*\(([^)]+)\)",
                             sql, re.IGNORECASE)
    for idx_name, tbl, cols in idx_matches:
        col_list = [c.strip() for c in cols.split(",")]
        info["indexes_built"].append({
            "index": idx_name, "table": tbl.replace("[","").replace("]",""),
            "columns": col_list
        })

    # Temp tables created
    temp_matches = re.findall(r"create\s+table\s+(#\w+)", sql, re.IGNORECASE)
    info["temp_tables"] = list(dict.fromkeys(temp_matches))

    # Dashboard blocks — look for INSERT INTO + 'dashboard_id' literal
    dashboard_ids = re.findall(r"'([a-z]+_[a-z]+)'\s+as\s+dashboard_id", sql, re.IGNORECASE)
    seen = set()
    unique_dashboards = [d for d in dashboard_ids if not (d in seen or seen.add(d))]

    # For each dashboard, try to extract geography, years, and demographics from nearby WHERE
    for did in unique_dashboards:
        # Find the INSERT block for this dashboard
        pattern = re.compile(
            rf"'{re.escape(did)}'\s+as\s+dashboard_id[\s\S]{{0,3000}}?insert\s+into[\s\S]{{0,200}}?"
            rf"select[\s\S]{{0,3000}}?(?=(?:;|/\*\*\*|\Z))",
            re.IGNORECASE
        )
        m = pattern.search(sql)
        block = m.group(0) if m else ""

        # Find the header comment above this dashboard block
        header_match = re.search(
            rf"/\*{{3,}}\n([\s\S]{{0,600}}?)(?=\*{{3,}}/\s*(?:with|insert|/\*|\Z))\*{{3,}}/\s*"
            rf"[\s\S]{{0,500}}?'{re.escape(did)}'",
            sql, re.IGNORECASE
        )
        header_text = header_match.group(1).strip() if header_match else ""

        # Geography
        geo_level = re.findall(r"geography_level\s+in\s+\(([^)]+)\)", block, re.IGNORECASE)
        geo_id    = re.findall(r"geography_id\s+(?:like|=)\s+'([^']+)'", block, re.IGNORECASE)

        # Year range
        yr = re.search(r"population_year\s+between\s+(\d{4})\s+and\s+(\d{4})", block, re.IGNORECASE)
        year_range = f"{yr.group(1)}–{yr.group(2)}" if yr else ""

        # Demographics
        age_groups = re.findall(r"age_group\s+in\s+\(([^)]+)\)", block, re.IGNORECASE)
        sex_filter = re.findall(r"(?:sex|gender)\s*=\s*'([^']+)'", block, re.IGNORECASE)

        # Describe special logic from the header comment
        special = []
        if "syoa" in block.lower() or "single year" in header_text.lower():
            special.append("Derives non-standard age group using state-level Single Year of Age (SYOA) proportions with round-preserve-sum rounding")
        if "ethnicity" in block.lower() and "race" in block.lower() and "concat" in block.lower():
            special.append("Combines ethnicity + race into composite demographic buckets (Hispanic, Non-Hispanic Black, Non-Hispanic White, Other)")
        if "standard_age_group_set" in block.lower():
            special.append("Joins to standard_age_group_set for age-group standardization")
        if "salaried_monarch" in block.lower():
            special.append("Includes 2000 US Standard Population (source_id='salaried_monarch') for age-standardized rate calculations")
        if "string_agg" in block.lower():
            special.append("Aggregates multiple source_row_id_hash and vintage_id values per group to preserve full data lineage")

        # Parse human-readable name from the header comment first line
        human_name = ""
        lines = [l.strip() for l in header_text.split("\n") if l.strip() and not l.strip().startswith("-")]
        if lines:
            first = lines[0].strip("*/ \t")
            if first and len(first) < 60:
                human_name = first

        info["dashboards"].append({
            "id":          did,
            "name":        human_name,
            "header":      header_text,
            "geo_levels":  geo_level,
            "geo_filter":  geo_id,
            "year_range":  year_range,
            "age_groups":  age_groups,
            "sex":         list(set(sex_filter)),
            "special":     special,
        })

    # Build overall step list
    steps = []
    steps.append("Capture current timestamp from sm_idoh_01.vw_std_datetime (@ace_timestamp)")
    if info["indexes_built"]:
        tbl_names = list(dict.fromkeys(i["table"].split(".")[-1] for i in info["indexes_built"]))
        steps.append(f"Create nonclustered indexes (if absent) on: {', '.join(tbl_names)}")
    if info["temp_tables"]:
        for t in info["temp_tables"]:
            if t == "#cld":
                steps.append(
                    "Build #cld temp table: join vw_census → census_demographic → census_source_row; "
                    "filter to resident population counts using best-vintage data only "
                    "(plus 2000 US Standard Population for age standardization)"
                )
            else:
                steps.append(f"Build temp table {t}")
    if truncates:
        steps.append(f"Truncate {info['output_table']} (full reload — all rows replaced each run)")
    for db in info["dashboards"]:
        steps.append(
            f"Insert rows for dashboard '{db['id']}' ({db['name'] or 'see below'})"
            + (f" — years {db['year_range']}" if db["year_range"] else "")
        )
    info["steps"] = steps

    return info


# ── HTML builder ──────────────────────────────────────────────────────────────

def esc(s):
    if s is None: return ""
    return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")


# ── Execution chain builder ────────────────────────────────────────────────────

def build_chains(activities):
    """
    Topological sort activities into parallel execution chains.
    Returns a list of chains, each chain is an ordered list of activity names.
    Respects dependency edges — activities that can run in parallel appear in
    separate chains.
    """
    name_map = {a["name"]: a for a in activities}
    # Build adjacency: successor[A] = list of activities that depend on A
    successors = defaultdict(list)
    in_degree  = defaultdict(int)
    for a in activities:
        for dep in a["depends_on"]:
            predecessors_name = dep["activity"]
            successors[predecessors_name].append(a["name"])
            in_degree[a["name"]] += 1

    # Roots = activities with no dependencies
    roots = [a["name"] for a in activities if in_degree[a["name"]] == 0]

    # BFS level assignment
    levels = {}
    queue  = list(roots)
    for r in roots:
        levels[r] = 0

    visited = set(roots)
    while queue:
        cur  = queue.pop(0)
        lvl  = levels[cur]
        for succ in successors[cur]:
            new_lvl = max(levels.get(succ, 0), lvl + 1)
            levels[succ] = new_lvl
            if succ not in visited:
                visited.add(succ)
                queue.append(succ)

    # Group by level
    max_lvl = max(levels.values()) if levels else 0
    level_groups = defaultdict(list)
    for name, lvl in levels.items():
        level_groups[lvl].append(name)

    # Trace individual chains from each root
    chains = []
    for root in roots:
        chain = [root]
        cur   = root
        while True:
            succs = successors.get(cur, [])
            # Follow a single successor (for linear chains)
            if len(succs) == 1:
                chain.append(succs[0])
                cur = succs[0]
            else:
                break
        chains.append(chain)

    return chains, levels, successors


def activity_icon(act_type):
    t = act_type.lower()
    if "copy"            in t: return ("📋", "Copy")
    if "storedprocedure" in t or "sproc" in t: return ("⚙️", "Stored Proc")
    if "executepipeline" in t: return ("🔁", "Execute Pipeline")
    if "dataflow"        in t: return ("🌊", "Data Flow")
    if "foreach"         in t: return ("🔃", "For Each")
    if "ifcondition"     in t: return ("🔀", "If Condition")
    if "web"             in t: return ("🌐", "Web")
    if "lookup"          in t: return ("🔍", "Lookup")
    if "wait"            in t: return ("⏱", "Wait")
    return ("▶", act_type)


def dataset_format(ds_type):
    t = (ds_type or "").lower()
    if "delimitedtext" in t: return ("CSV", "#1e3a2a", "#4ade80")
    if "parquet"       in t: return ("Parquet", "#1e2a4a", "#6c8eff")
    if "json"          in t: return ("JSON", "#3a2a1e", "#fb923c")
    if "avro"          in t: return ("Avro", "#2a1e3a", "#c084fc")
    if "sqldwtable" in t or "azuresqldw" in t: return ("Synapse Table", "#1e2a4a", "#22d3ee")
    if "sqltable"      in t: return ("SQL Table", "#1e2a4a", "#6c8eff")
    return (ds_type or "File", "#252836", "#94a3b8")


# ── HTML builder ──────────────────────────────────────────────────────────────

CSS = """
:root{--bg:#0f1117;--sur:#1a1d27;--sur2:#252836;--brd:#2e3245;
  --txt:#e2e8f0;--mut:#8892a4;--acc:#6c8eff;--grn:#4ade80;
  --red:#f87171;--yel:#fbbf24;--pur:#c084fc;--cyn:#22d3ee;--org:#fb923c;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font:14px/1.6 'Segoe UI',system-ui,sans-serif;
  min-height:100vh;padding:36px 28px 60px}
.page{max-width:1100px;margin:0 auto}

/* ── HEADER ── */
.hero{margin-bottom:32px;padding-bottom:24px;border-bottom:1px solid var(--brd)}
.hero-top{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap}
.hero h1{font-size:22px;font-weight:800;margin-bottom:6px;word-break:break-word}
.hero .sub{color:var(--mut);font-size:12px;margin-bottom:10px}
.badges{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px}
.badge{font-size:10px;font-weight:700;padding:2px 9px;border-radius:4px}
.badge-env{background:#1e2a4a;color:var(--acc)}
.badge-folder{background:#2d1e5f;color:var(--pur)}
.badge-type{background:#1a3a2a;color:var(--grn)}
.hero-desc{color:var(--mut);font-size:13px;max-width:680px;line-height:1.6;margin-top:6px}
.stat-row{display:flex;gap:10px;flex-wrap:wrap;margin-top:16px}
.stat-box{background:var(--sur);border:1px solid var(--brd);border-radius:8px;
  padding:10px 18px;text-align:center}
.stat-n{font-size:22px;font-weight:700}
.stat-l{font-size:10px;color:var(--mut);margin-top:1px}

/* ── SECTIONS ── */
.section{margin-bottom:36px}
.section-hdr{display:flex;align-items:center;gap:10px;margin-bottom:14px;
  padding-bottom:8px;border-bottom:1px solid var(--brd)}
.section-hdr h2{font-size:15px;font-weight:700}
.section-icon{font-size:17px}

/* ── FLOW DIAGRAM ── */
.flow{display:flex;align-items:stretch;gap:0;overflow-x:auto;padding-bottom:4px}
.flow-col{display:flex;flex-direction:column;gap:8px;min-width:180px}
.flow-col-hdr{font-size:10px;font-weight:700;color:var(--mut);text-transform:uppercase;
  letter-spacing:.06em;padding:0 0 6px 0;border-bottom:1px solid var(--brd);margin-bottom:4px}
.flow-box{background:var(--sur);border:1px solid var(--brd);border-radius:8px;
  padding:10px 14px;font-size:12px}
.flow-box.source{border-color:#1e4a3a;background:#0d1f18}
.flow-box.dest{border-color:#1e3a5f;background:#0d1828}
.flow-box.sproc{border-color:#4a3a1e;background:#1e1808}
.flow-box-label{font-size:10px;font-weight:700;margin-bottom:4px}
.flow-box-items{display:flex;flex-direction:column;gap:3px}
.flow-box-item{font-size:11px;color:var(--mut)}
.flow-arrow{display:flex;align-items:center;justify-content:center;padding:0 16px;
  font-size:22px;color:var(--brd);flex-shrink:0;align-self:center}
.fmt-badge{display:inline-block;font-size:9px;font-weight:700;padding:1px 5px;
  border-radius:3px;margin-right:4px;vertical-align:middle}

/* ── EXECUTION CHAINS ── */
.chains{display:flex;flex-direction:column;gap:10px}
.chain{background:var(--sur);border:1px solid var(--brd);border-radius:8px;padding:12px 16px}
.chain-hdr{font-size:10px;font-weight:700;color:var(--mut);text-transform:uppercase;
  letter-spacing:.06em;margin-bottom:10px}
.chain-steps{display:flex;align-items:center;flex-wrap:wrap;gap:0}
.chain-step{background:var(--sur2);border:1px solid var(--brd);border-radius:6px;
  padding:6px 12px;font-size:11px;white-space:nowrap}
.chain-step.copy{border-color:#1e3a5f}
.chain-step.sproc{border-color:#4a3a1e;color:var(--yel)}
.chain-step.exec{border-color:#2d1e5f;color:var(--pur)}
.chain-arrow{padding:0 8px;color:var(--brd);font-size:14px;flex-shrink:0}
.chain-note{font-size:10px;color:var(--mut);margin-top:6px}
.chain-parallel-note{font-size:11px;color:var(--acc);padding:6px 12px;
  background:#1e2a4a22;border:1px dashed var(--acc);border-radius:6px}
.convergence{background:var(--sur);border:1px solid var(--yel);border-radius:8px;
  padding:12px 16px;text-align:center;margin-top:8px}
.convergence-hdr{font-size:10px;color:var(--yel);font-weight:700;margin-bottom:6px;
  text-transform:uppercase;letter-spacing:.06em}

/* ── TABLES ── */
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;padding:7px 10px;font-size:10px;font-weight:700;color:var(--mut);
  border-bottom:2px solid var(--brd);text-transform:uppercase;letter-spacing:.04em}
td{padding:6px 10px;border-bottom:1px solid var(--brd);vertical-align:top}
tr:last-child td{border-bottom:none}
tr:hover td{background:var(--sur2)}
.mono{font-family:monospace;font-size:11px;color:var(--cyn)}
.mut{color:var(--mut)}

/* ── LS CARDS ── */
.ls-cards{display:flex;gap:12px;flex-wrap:wrap}
.ls-card{background:var(--sur);border:1px solid var(--brd);border-radius:10px;
  padding:16px 20px;min-width:240px;flex:1}
.ls-card h3{font-size:13px;font-weight:700;margin-bottom:8px}
.ls-card .ls-type{font-size:10px;color:var(--pur);font-weight:700;margin-bottom:6px}
.ls-kv{font-size:11px;color:var(--mut);line-height:1.8}
.ls-kv b{color:var(--txt)}

/* ── STORED PROC SYNOPSIS ── */
.sproc-card{background:var(--sur);border:1px solid #4a3a1e;border-radius:10px;
  padding:20px 24px;margin-bottom:20px}
.sproc-card h3{font-size:14px;font-weight:700;margin-bottom:4px;color:var(--yel)}
.sproc-meta{font-size:11px;color:var(--mut);margin-bottom:14px}
.sproc-steps{counter-reset:step;display:flex;flex-direction:column;gap:6px;margin-bottom:18px}
.sproc-step{display:flex;gap:10px;align-items:flex-start;font-size:12px}
.sproc-step::before{counter-increment:step;content:counter(step);
  background:#4a3a1e;color:var(--yel);border-radius:50%;width:20px;height:20px;
  display:flex;align-items:center;justify-content:center;font-size:10px;
  font-weight:700;flex-shrink:0;margin-top:1px}
.sproc-desc{font-size:12px;color:var(--mut);margin-bottom:14px;
  border-left:3px solid #4a3a1e;padding-left:12px;line-height:1.7}
.dashboard-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:10px}
.db-card{background:#1a1208;border:1px solid #4a3a1e;border-radius:8px;padding:14px 16px}
.db-card-id{font-size:9px;font-family:monospace;color:var(--mut);margin-bottom:4px}
.db-card-name{font-size:13px;font-weight:700;color:var(--yel);margin-bottom:8px}
.db-kv{font-size:11px;line-height:1.9;color:var(--mut)}
.db-kv b{color:var(--txt)}
.db-special{margin-top:8px;padding-top:8px;border-top:1px solid #4a3a1e}
.db-special-item{font-size:10px;color:var(--acc);padding:2px 0}
.output-table{display:inline-block;background:#1a1208;border:1px solid #4a3a1e;
  border-radius:5px;padding:2px 10px;font-family:monospace;font-size:11px;
  color:var(--cyn);margin:0 4px}
.index-pill{display:inline-block;background:#252836;border:1px solid var(--brd);
  border-radius:4px;padding:1px 7px;font-size:10px;font-family:monospace;
  color:var(--mut);margin:2px 2px}

/* ── FAN-IN FLOW (sproc transformation) ── */
.fan-flow{display:flex;align-items:stretch;gap:0;overflow-x:auto;padding:12px 0 4px}
.fan-box{background:var(--sur);border:1px solid var(--brd);border-radius:8px;
  padding:12px 16px;min-width:190px;display:flex;flex-direction:column}
.fan-box.fan-staging{border-color:#1e4a3a;background:#0a1812;min-width:270px}
.fan-box.fan-sproc{border-color:#4a3a1e;background:#14100a;min-width:220px}
.fan-box.fan-output{border-color:#1e3a5f;background:#0a1420;min-width:200px}
.fan-box.fan-dash{border-color:#3a1e5f;background:#130a20;min-width:180px}
.fan-box-hdr{font-size:10px;font-weight:700;color:var(--mut);text-transform:uppercase;
  letter-spacing:.06em;margin-bottom:8px;padding-bottom:6px;border-bottom:1px solid var(--brd)}
.fan-items{display:flex;flex-direction:column;gap:3px;flex:1}
.fan-item{font-size:11px;color:var(--mut);line-height:1.5}
.fan-mono{font-family:monospace;font-size:11px;color:var(--cyn);line-height:1.5}
.fan-connector{display:flex;align-items:center;padding:0 10px;flex-shrink:0;align-self:center}
.fan-connector-inner{display:flex;align-items:center;gap:0}
.fan-line{height:2px;width:36px;background:var(--brd)}
.fan-head{color:var(--brd);font-size:14px;line-height:1}
.fan-dash-grid{display:flex;flex-direction:column;gap:6px;margin-top:2px}
.fan-dash-pill{background:#1a0d2a;border:1px solid #3a1e5f;border-radius:6px;padding:6px 10px}
.fan-dash-id{font-size:9px;font-family:monospace;color:var(--mut);margin-bottom:2px}
.fan-dash-name{font-size:11px;font-weight:700;color:var(--pur)}

/* ── FOOTER ── */
.footer{text-align:center;color:var(--mut);font-size:11px;margin-top:48px;
  padding-top:20px;border-top:1px solid var(--brd)}
"""


def fetch_pipeline_dataset_schemas(client, resource_group, factory_name, ds_names):
    """
    Return {ds_name: "schema.table"} for the given dataset names by calling the ADF SDK.
    For AzureSqlDWTableDataset the SQL schema name is in schema_type_properties_schema;
    .schema itself holds column definitions, not the SQL schema.
    Falls back silently — if a dataset can't be fetched the key is simply absent.
    """
    result = {}
    for name in ds_names:
        try:
            ds    = client.datasets.get(resource_group, factory_name, name)
            props = ds.properties
            # AzureSqlDWTableDataset: SQL schema name is schema_type_properties_schema
            schema = getattr(props, 'schema_type_properties_schema', None)
            table  = getattr(props, 'table',  None)
            # Values can be plain strings or Expression objects / dicts
            if isinstance(schema, dict):
                schema = schema.get('value', '')
            if isinstance(table, dict):
                table = table.get('value', '')
            schema = str(schema).strip() if schema else ''
            table  = str(table).strip()  if table  else ''
            if schema and table:
                result[name] = f"{schema}.{table}"
            elif table:
                result[name] = table
        except Exception:
            pass
    return result


# Cytoscape + Dagre initialiser — raw string so JS curly braces don't conflict with f-strings.
_CYTOSCAPE_INIT_JS = r"""
(function () {
  // Register the dagre layout once both libraries are loaded
  if (typeof cytoscape !== 'undefined' && typeof cytoscapeDagre !== 'undefined') {
    cytoscape.use(cytoscapeDagre);
  }

  var els = LINEAGE_ELEMENTS;

  // Node style lookup by type
  var NODE_STYLES = {
    source_CSV:     { bg: '#0a1f14', border: '#4ade80', color: '#a7f3c8' },
    source_Parquet: { bg: '#0a1020', border: '#6c8eff', color: '#b0c4ff' },
    source_JSON:    { bg: '#1a100a', border: '#fb923c', color: '#fcd5a8' },
    source_Avro:    { bg: '#130a20', border: '#c084fc', color: '#e2c4ff' },
    source_File:    { bg: '#151515', border: '#94a3b8', color: '#c0c8d4' },
    dest:           { bg: '#081420', border: '#22d3ee', color: '#9eeeff' },
    sproc:          { bg: '#110d04', border: '#fbbf24', color: '#fde68a' },
    output:         { bg: '#130800', border: '#fb923c', color: '#fed7aa' },
    dashboard:      { bg: '#0e0618', border: '#c084fc', color: '#e9d5ff' },
  };

  function nodeStyle(n) {
    var t = n.data('type');
    var fmt = n.data('format') || '';
    var key = (t === 'source') ? ('source_' + fmt) : t;
    return NODE_STYLES[key] || NODE_STYLES['source_File'];
  }

  var cy = cytoscape({
    container: document.getElementById('cy-lineage'),
    boxSelectionEnabled: false,
    userZoomingEnabled: true,
    userPanningEnabled: true,
    minZoom: 0.15,
    maxZoom: 3,
    elements: els,

    style: [
      /* ── Base node ── */
      {
        selector: 'node',
        style: {
          'label':              'data(label)',
          'font-family':        "'Segoe UI', 'Consolas', monospace",
          'font-size':          '10px',
          'text-valign':        'center',
          'text-halign':        'center',
          'text-wrap':          'wrap',
          'text-max-width':     '180px',
          'padding':            '7px',
          'shape':              'round-rectangle',
          'border-width':       '1px',
          'width':              'label',
          'height':             'label',
          'color':              '#e2e8f0',
          'background-color':   '#1a1d27',
          'border-color':       '#2e3245',
          'transition-property':'border-color, border-width, opacity',
          'transition-duration':'0.12s',
        }
      },
      /* ── Source nodes (files) ── */
      {
        selector: 'node[type="source"][format="CSV"]',
        style: { 'background-color': '#0a1f14', 'border-color': '#4ade80', 'color': '#a7f3c8' }
      },
      {
        selector: 'node[type="source"][format="Parquet"]',
        style: { 'background-color': '#0a1020', 'border-color': '#6c8eff', 'color': '#b0c4ff' }
      },
      {
        selector: 'node[type="source"][format="JSON"]',
        style: { 'background-color': '#1a100a', 'border-color': '#fb923c', 'color': '#fcd5a8' }
      },
      {
        selector: 'node[type="source"][format="Avro"]',
        style: { 'background-color': '#130a20', 'border-color': '#c084fc', 'color': '#e2c4ff' }
      },
      {
        selector: 'node[type="source"]',
        style: { 'font-family': "'Consolas', monospace", 'font-size': '9.5px' }
      },
      /* ── Destination (staging table) nodes ── */
      {
        selector: 'node[type="dest"]',
        style: { 'background-color': '#081420', 'border-color': '#22d3ee', 'color': '#9eeeff',
                 'font-family': "'Consolas', monospace", 'font-size': '9.5px' }
      },
      /* ── Stored procedure nodes ── */
      {
        selector: 'node[type="sproc"]',
        style: { 'background-color': '#110d04', 'border-color': '#fbbf24', 'color': '#fde68a',
                 'shape': 'diamond', 'font-size': '9px', 'text-max-width': '130px',
                 'padding': '20px' }
      },
      /* ── Output table nodes ── */
      {
        selector: 'node[type="output"]',
        style: { 'background-color': '#130800', 'border-color': '#fb923c', 'color': '#fed7aa',
                 'font-family': "'Consolas', monospace", 'font-size': '9.5px',
                 'border-width': '2px' }
      },
      /* ── Dashboard nodes ── */
      {
        selector: 'node[type="dashboard"]',
        style: { 'background-color': '#0e0618', 'border-color': '#c084fc', 'color': '#e9d5ff',
                 'font-size': '9px' }
      },
      /* ── Edges ── */
      {
        selector: 'edge',
        style: {
          'width':                  1.2,
          'line-color':             '#2e3245',
          'target-arrow-color':     '#2e3245',
          'target-arrow-shape':     'triangle',
          'arrow-scale':            0.7,
          'curve-style':            'bezier',
          'opacity':                0.7,
          'transition-property':    'line-color, target-arrow-color, width, opacity',
          'transition-duration':    '0.12s',
        }
      },
      {
        selector: 'edge[type="copy"][format="CSV"]',
        style: { 'line-color': '#1a4a2a', 'target-arrow-color': '#1a4a2a' }
      },
      {
        selector: 'edge[type="copy"][format="Parquet"]',
        style: { 'line-color': '#1a2a4a', 'target-arrow-color': '#1a2a4a' }
      },
      {
        selector: 'edge[type="copy"]',
        style: { 'line-color': '#1e3245', 'target-arrow-color': '#1e3245' }
      },
      {
        selector: 'edge[type="feeds"]',
        style: { 'line-color': '#1a2a36', 'target-arrow-color': '#1a2a36',
                 'line-style': 'dashed', 'line-dash-pattern': [4, 3] }
      },
      {
        selector: 'edge[type="writes"]',
        style: { 'line-color': '#3a2010', 'target-arrow-color': '#3a2010', 'width': 2 }
      },
      {
        selector: 'edge[type="consumes"]',
        style: { 'line-color': '#2a1040', 'target-arrow-color': '#2a1040' }
      },
      /* ── Hover / selected state ── */
      {
        selector: '.highlighted',
        style: { 'border-width': '2px', 'opacity': 1 }
      },
      {
        selector: '.faded',
        style: { 'opacity': 0.12 }
      },
      {
        selector: 'edge.highlighted',
        style: { 'width': 2.5, 'opacity': 1,
                 'line-color': '#6c8eff', 'target-arrow-color': '#6c8eff' }
      },
    ],

    layout: {
      name:       'dagre',
      rankDir:    'LR',
      align:      'UL',
      ranker:     'network-simplex',
      rankSep:    120,
      nodeSep:    6,
      edgeSep:    8,
      padding:    24,
      animate:    false,
    }
  });

  // ── Fit all nodes on first render ──────────────────────────────────────────
  cy.ready(function () { cy.fit(undefined, 24); });

  // ── Hover: highlight neighbourhood, fade the rest ─────────────────────────
  cy.on('mouseover', 'node', function (e) {
    var node      = e.target;
    var connected = node.closedNeighborhood();
    cy.elements().difference(connected).addClass('faded');
    connected.addClass('highlighted');
    connected.edges().addClass('highlighted');
  });
  cy.on('mouseout', 'node', function () {
    cy.elements().removeClass('faded highlighted');
  });

  // ── Tooltip on click ───────────────────────────────────────────────────────
  var tip = document.getElementById('cy-tooltip');
  cy.on('tap', 'node', function (e) {
    var n    = e.target;
    var pos  = e.renderedPosition;
    var cont = document.getElementById('cy-lineage').getBoundingClientRect();
    tip.innerHTML  = '<b>' + n.data('label') + '</b>'
      + (n.data('type') ? '<br><span style="color:#8892a4;font-size:10px">'
         + n.data('type') + (n.data('format') ? ' &middot; ' + n.data('format') : '')
         + '</span>' : '');
    tip.style.left    = (cont.left + pos.x + 12) + 'px';
    tip.style.top     = (cont.top  + pos.y - 10) + 'px';
    tip.style.display = 'block';
  });
  cy.on('tap', function (e) {
    if (e.target === cy) tip.style.display = 'none';
  });
  document.addEventListener('scroll', function () { tip.style.display = 'none'; });

  // ── Legend toggle ──────────────────────────────────────────────────────────
  var fitBtn = document.getElementById('cy-fit-btn');
  if (fitBtn) fitBtn.addEventListener('click', function () { cy.fit(undefined, 24); });

})();
"""


def build_lineage_section_html(pipeline, ds_map, schema_map, sproc_infos):
    """
    Generate the Data Lineage section using Cytoscape.js + Dagre.
    Builds a full node-and-edge graph:
      ADLS source files  →  (Copy)  →  Synapse staging tables
      Staging tables     →  (feeds) →  Stored procedure
      Stored procedure   →  (writes)→  Output table
      Output table       →  (consumes) → Dashboards
    """
    acts      = pipeline["activities"]
    copy_acts = [a for a in acts if "Copy" in a["type"]]

    # ── Build src→dst mappings ────────────────────────────────────────────────
    pairs = []
    for a in sorted(copy_acts, key=lambda x: x["name"]):
        for src in a["inputs"]:
            for dst in a["outputs"]:
                src_ds = ds_map.get(src, {})
                raw    = src.replace("DS_", "")
                ext    = (".csv"     if "csv"     in src.lower()
                          else ".parquet" if "parquet" in src.lower()
                          else ".json"    if "json"    in src.lower()
                          else "")
                fname = raw.replace("_csv","").replace("_parquet","").replace("_json","") + ext
                fmt, _bg, _fg = dataset_format(src_ds.get("type", ""))
                dst_label = schema_map.get(dst) or dst.replace("DS_TBL_","").replace("DS_","")
                pairs.append((src, fname, fmt, dst, dst_label))
    pairs.sort(key=lambda p: p[1])

    # ── Build Cytoscape elements ──────────────────────────────────────────────
    elements = []
    seen_nodes = set()

    def safe_id(s):
        """Turn any string into a valid Cytoscape node id."""
        return re.sub(r"[^a-zA-Z0-9_]", "_", s)

    # Source file nodes
    for _src, fname, fmt, _dst, _dst_label in pairs:
        nid = "src_" + safe_id(fname)
        if nid not in seen_nodes:
            seen_nodes.add(nid)
            elements.append({"data": {"id": nid, "label": fname,
                                      "type": "source", "format": fmt}})

    # Destination (staging table) nodes
    for _src, _fname, _fmt, _dst, dst_label in pairs:
        nid = "dst_" + safe_id(dst_label)
        if nid not in seen_nodes:
            seen_nodes.add(nid)
            elements.append({"data": {"id": nid, "label": dst_label, "type": "dest"}})

    # Copy edges  source → dest
    for _src, fname, fmt, _dst, dst_label in pairs:
        src_id = "src_" + safe_id(fname)
        dst_id = "dst_" + safe_id(dst_label)
        elements.append({"data": {"source": src_id, "target": dst_id,
                                  "type": "copy", "format": fmt}})

    # Stored procedure, output table, and dashboard nodes + edges
    for si_idx, si in enumerate(sproc_infos or []):
        sp_name  = f"{si['schema']}.{si['name']}" if si["schema"] else si["name"]
        sp_short = si["name"]                      # shorter label for the diamond node
        sp_id    = f"sproc_{si_idx}"

        elements.append({"data": {"id": sp_id, "label": sp_short, "type": "sproc"}})

        # All staging tables → sproc (dashed "feeds" edges)
        for _src, _fname, _fmt, _dst, dst_label in pairs:
            dst_id = "dst_" + safe_id(dst_label)
            elements.append({"data": {"source": dst_id, "target": sp_id, "type": "feeds"}})

        # Sproc → output table
        if si.get("output_table"):
            out_label = si["output_table"]
            out_id    = f"output_{si_idx}"
            elements.append({"data": {"id": out_id, "label": out_label, "type": "output"}})
            elements.append({"data": {"source": sp_id, "target": out_id, "type": "writes"}})

            # Output → dashboards
            for d_idx, db in enumerate(si.get("dashboards", [])):
                db_label = db.get("name") or db.get("id", "")
                db_id    = f"dash_{si_idx}_{d_idx}"
                elements.append({"data": {"id": db_id, "label": db_label, "type": "dashboard"}})
                elements.append({"data": {"source": out_id, "target": db_id, "type": "consumes"}})

    elements_json = json.dumps(elements, ensure_ascii=False, separators=(",", ":"))

    # Build the <script> block via concatenation — avoids f-string / JS brace conflicts.
    script_tag = (
        '<script>\nvar LINEAGE_ELEMENTS = '
        + elements_json
        + ';\n'
        + _CYTOSCAPE_INIT_JS
        + '\n</script>'
    )

    # ── Legend ────────────────────────────────────────────────────────────────
    legend_items = [
        ("#4ade80", "Source file (CSV)"),
        ("#6c8eff", "Source file (Parquet)"),
        ("#22d3ee", "Staging table (Synapse DW)"),
        ("#fbbf24", "Stored procedure"),
        ("#fb923c", "Output table"),
        ("#c084fc", "Dashboard"),
    ]
    legend_html = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:5px;margin-right:14px">'
        f'<span style="width:10px;height:10px;border-radius:2px;background:{c};flex-shrink:0"></span>'
        f'<span style="font-size:10px;color:var(--mut)">{lbl}</span></span>'
        for c, lbl in legend_items
    )

    return f"""
    <div class="section" id="section-lineage">
      <div class="section-hdr">
        <span class="section-icon">🗺</span>
        <h2>Data Lineage Diagram</h2>
      </div>
      <p style="font-size:12px;color:var(--mut);margin-bottom:10px">
        Interactive graph — hover a node to highlight its upstream/downstream path.
        Click empty space to reset. Scroll to zoom, drag to pan.
      </p>
      <div style="display:flex;align-items:center;justify-content:space-between;
                  margin-bottom:10px;flex-wrap:wrap;gap:6px">
        <div style="display:flex;flex-wrap:wrap;gap:2px 0">{legend_html}</div>
        <button id="cy-fit-btn"
          style="font-size:10px;padding:4px 12px;border-radius:5px;cursor:pointer;
                 background:var(--sur2);border:1px solid var(--brd);color:var(--mut)">
          ⤢ Fit all
        </button>
      </div>
      <div id="cy-lineage"
           style="width:100%;height:820px;background:#0a0c12;
                  border:1px solid var(--brd);border-radius:10px;position:relative">
      </div>
      <div id="cy-tooltip"
           style="display:none;position:fixed;z-index:9999;pointer-events:none;
                  background:#1a1d27;border:1px solid var(--brd);border-radius:6px;
                  padding:7px 12px;font-size:11px;color:var(--txt);max-width:300px;
                  box-shadow:0 4px 16px rgba(0,0,0,.5)">
      </div>
    </div>
    {script_tag}"""


def build_sproc_synopsis_html(sproc_infos):
    """Render detailed synopsis cards for one or more stored procedures."""
    if not sproc_infos:
        return ""

    cards = ""
    for info in sproc_infos:
        # Steps
        steps_html = "".join(
            f'<div class="sproc-step"><div>{esc(s)}</div></div>' for s in info["steps"]
        )

        # Indexes
        idx_pills = "".join(
            f'<span class="index-pill" title="on {esc(i["table"])} ({esc(", ".join(i["columns"]))})">'
            f'{esc(i["index"])}</span>'
            for i in info["indexes_built"]
        )
        idx_row = f'<div style="margin:4px 0 12px"><b style="font-size:11px;color:var(--mut)">Performance indexes created:</b><br>{idx_pills}</div>' if idx_pills else ""

        # Dashboard cards
        db_cards = ""
        for db in info["dashboards"]:
            geo_levels = db["geo_levels"][0].replace("'","") if db["geo_levels"] else "—"
            geo_filter = db["geo_filter"][0] if db["geo_filter"] else ""
            geo_note   = f" (geography_id LIKE '{geo_filter}')" if geo_filter else ""
            age_note   = db["age_groups"][0].replace("'","") if db["age_groups"] else "All age groups"
            sex_note   = ", ".join(db["sex"]) if db["sex"] else "All sexes"
            special_html = ""
            if db["special"]:
                items = "".join(f'<div class="db-special-item">→ {esc(s)}</div>' for s in db["special"])
                special_html = f'<div class="db-special"><b style="font-size:10px;color:var(--mut)">Special logic:</b>{items}</div>'

            db_cards += f"""
            <div class="db-card">
              <div class="db-card-id">dashboard_id: {esc(db["id"])}</div>
              <div class="db-card-name">{esc(db["name"]) or esc(db["id"])}</div>
              <div class="db-kv">
                <div><b>Geography:</b> {esc(geo_levels)}{esc(geo_note)}</div>
                <div><b>Years:</b> {esc(db["year_range"]) or "—"}</div>
                <div><b>Age groups:</b> {esc(age_note)}</div>
                <div><b>Sex filter:</b> {esc(sex_note)}</div>
              </div>
              {special_html}
            </div>"""

        output_tbl_html = f'<span class="output-table">{esc(info["output_table"])}</span>' if info["output_table"] else ""

        cards += f"""
      <div class="sproc-card">
        <h3>⚙️ {esc(info["schema"])}.{esc(info["name"])}</h3>
        <div class="sproc-meta">
          {f'Author: <b>{esc(info["author"])}</b> &nbsp;·&nbsp;' if info["author"] else ""}
          Output table: {output_tbl_html} &nbsp;·&nbsp;
          {esc(info["operation"])}
        </div>

        {f'<div class="sproc-desc">{esc(info["description"])}</div>' if info["description"] else ""}

        <div style="font-size:11px;font-weight:700;color:var(--mut);margin-bottom:8px;
          text-transform:uppercase;letter-spacing:.05em">Execution Steps</div>
        <div class="sproc-steps">{steps_html}</div>

        {idx_row}

        {f'<div style="font-size:11px;font-weight:700;color:var(--mut);margin-bottom:10px;text-transform:uppercase;letter-spacing:.05em">Dashboards Populated ({len(info["dashboards"])})</div><div class="dashboard-grid">{db_cards}</div>' if db_cards else ""}
      </div>"""

    return f"""
    <div class="section">
      <div class="section-hdr">
        <span class="section-icon">⚙️</span>
        <h2>Stored Procedure Deep Dive</h2>
      </div>
      <p style="font-size:12px;color:var(--mut);margin-bottom:16px">
        The stored procedures below run inside Synapse DW after all Copy activities complete.
        They read from the staging tables loaded by ADF and build the final reporting-layer output.
      </p>
      {cards}
    </div>"""


def build_html(pipeline, ds_map, ls_map, env_label, factory_name, generated,
               sproc_infos=None, schema_map=None):
    name     = pipeline["name"]
    folder   = pipeline["folder"] or "Root"
    desc     = pipeline["description"] or ""
    acts     = pipeline["activities"]
    params   = pipeline["parameters"]

    # Gather unique linked services used
    ls_names_used = set()
    for a in acts:
        if a["linked_service"]:
            ls_names_used.add(a["linked_service"])
        for ds_name in a["inputs"] + a["outputs"]:
            ds = ds_map.get(ds_name, {})
            if ds.get("linked_service"):
                ls_names_used.add(ds["linked_service"])

    # Classify activities
    copy_acts  = [a for a in acts if "Copy" in a["type"]]
    sproc_acts = [a for a in acts if "StoredProcedure" in a["type"] or "sproc" in a["type"].lower()]
    exec_acts  = [a for a in acts if "ExecutePipeline" in a["type"]]
    other_acts = [a for a in acts if a not in copy_acts + sproc_acts + exec_acts]

    # Source / destination datasets
    src_ds  = set()
    dest_ds = set()
    for a in copy_acts:
        for d in a["inputs"]:  src_ds.add(d)
        for d in a["outputs"]: dest_ds.add(d)

    # Build execution chains
    chains, levels, successors = build_chains(acts)

    # ── Summary blurb (auto-generated) ────────────────────────────────────────
    src_formats = defaultdict(int)
    dest_systems = defaultdict(int)
    for dsn in src_ds:
        ds = ds_map.get(dsn, {})
        fmt, _, _ = dataset_format(ds.get("type",""))
        src_formats[fmt] += 1
    for dsn in dest_ds:
        ds = ds_map.get(dsn, {})
        ls = ds.get("linked_service","")
        dest_systems[ls] += 1

    src_fmt_str  = ", ".join(f"{v} {k}" for k,v in sorted(src_formats.items()))
    dest_sys_str = ", ".join(f"{v} table{'s' if v>1 else ''} in {k}" for k,v in dest_systems.items())
    n_parallel   = sum(1 for a in acts if not a["depends_on"])
    auto_desc    = (
        f"Reads {src_fmt_str} file{'s' if sum(src_formats.values())>1 else ''} "
        f"from ADLS Gen2 ({len(src_ds)} source{'s' if len(src_ds)>1 else ''}), "
        f"loads them into {dest_sys_str}, "
        f"then executes {len(sproc_acts)} stored procedure{'s' if len(sproc_acts)>1 else ''} "
        f"to build reporting-layer data. "
        f"The pipeline starts {n_parallel} parallel chain{'s' if n_parallel>1 else ''} "
        f"and converges before the final transformation step."
    ) if sproc_acts else (
        f"Reads {src_fmt_str} file{'s' if sum(src_formats.values())>1 else ''} "
        f"from ADLS Gen2 ({len(src_ds)} source{'s' if len(src_ds)>1 else ''}) "
        f"and loads them into {dest_sys_str}."
    )

    display_desc = desc if desc else auto_desc

    # ── Stat boxes ────────────────────────────────────────────────────────────
    stat_boxes = (
        f'<div class="stat-box"><div class="stat-n">{len(acts)}</div><div class="stat-l">Activities</div></div>'
        f'<div class="stat-box"><div class="stat-n" style="color:var(--grn)">{len(src_ds)}</div><div class="stat-l">Source Files</div></div>'
        f'<div class="stat-box"><div class="stat-n" style="color:var(--cyn)">{len(dest_ds)}</div><div class="stat-l">Target Tables</div></div>'
        f'<div class="stat-box"><div class="stat-n" style="color:var(--yel)">{len(sproc_acts)}</div><div class="stat-l">Stored Procs</div></div>'
        f'<div class="stat-box"><div class="stat-n" style="color:var(--acc)">{n_parallel}</div><div class="stat-l">Parallel Starts</div></div>'
    )

    # ── Flow diagram ──────────────────────────────────────────────────────────
    def fmt_badge(ds_type):
        label, bg, fg = dataset_format(ds_type)
        return f'<span class="fmt-badge" style="background:{bg};color:{fg}">{label}</span>'

    src_items_html = ""
    for dsn in sorted(src_ds):
        ds   = ds_map.get(dsn, {})
        src_items_html += (
            f'<div class="flow-box-item">'
            f'{fmt_badge(ds.get("type",""))} {esc(dsn.replace("DS_","").replace("_csv","").replace("_parquet",""))}'
            f'</div>'
        )

    dest_items_html = ""
    for dsn in sorted(dest_ds):
        ds   = ds_map.get(dsn, {})
        tbl  = dsn.replace("DS_TBL_","").replace("DS_","")
        dest_items_html += (
            f'<div class="flow-box-item">'
            f'{fmt_badge(ds.get("type",""))} {esc(tbl)}'
            f'</div>'
        )

    sproc_items_html = ""
    for a in sproc_acts:
        sproc_items_html += f'<div class="flow-box-item">⚙️ {esc(a["name"])}</div>'
        dep_names = [d["activity"].replace("COPY ","") for d in a["depends_on"]]
        if dep_names:
            sproc_items_html += (
                f'<div class="flow-box-item mut" style="font-size:10px;margin-left:14px">'
                f'Waits for: {esc(", ".join(dep_names))}</div>'
            )

    flow_html = f"""
    <div class="flow">
      <div class="flow-col">
        <div class="flow-col-hdr">Sources (ADLS Gen2)</div>
        <div class="flow-box source">
          <div class="flow-box-label" style="color:var(--grn)">📂 IDOH_ADLSG2</div>
          <div class="flow-box-items">{src_items_html}</div>
        </div>
      </div>
      <div class="flow-arrow">⟶</div>
      <div class="flow-col">
        <div class="flow-col-hdr">ADF Pipeline ({len(copy_acts)} Copy Activities)</div>
        <div class="flow-box" style="border-color:var(--acc);background:#0d1228">
          <div class="flow-box-label" style="color:var(--acc)">🔁 {esc(name)}</div>
          <div class="flow-box-items">
            <div class="flow-box-item">📋 {len(copy_acts)} Copy activities</div>
            <div class="flow-box-item">⚙️ {len(sproc_acts)} Stored procedure{'s' if len(sproc_acts)!=1 else ''}</div>
            <div class="flow-box-item mut">Runs {n_parallel} chains in parallel</div>
          </div>
        </div>
      </div>
      <div class="flow-arrow">⟶</div>
      <div class="flow-col">
        <div class="flow-col-hdr">Staging Tables (Synapse DW)</div>
        <div class="flow-box dest">
          <div class="flow-box-label" style="color:var(--cyn)">🗄 IDOH_SYNAPSE_DW</div>
          <div class="flow-box-items">{dest_items_html}</div>
        </div>
      </div>
      {'<div class="flow-arrow">⟶</div><div class="flow-col"><div class="flow-col-hdr">Transformation</div><div class="flow-box sproc"><div class="flow-box-label" style="color:var(--yel)">⚙️ Stored Procedure</div><div class="flow-box-items">' + sproc_items_html + '</div></div></div>' if sproc_acts else ''}
    </div>"""

    # ── Execution chains ──────────────────────────────────────────────────────
    def step_class(a):
        t = a["type"].lower()
        if "copy" in t:            return "copy"
        if "storedprocedure" in t: return "sproc"
        if "executepipeline" in t: return "exec"
        return ""

    def step_label(a):
        icon, _ = activity_icon(a["type"])
        short = a["name"].replace("COPY ","").replace("Sproc_","")
        return f'{icon} {esc(short)}'

    name_to_act = {a["name"]: a for a in acts}

    chains_html = '<div class="chains">'
    # Show parallel starts first
    parallel_starts = [a for a in acts if not a["depends_on"]]
    if len(parallel_starts) > 1:
        start_names = ", ".join(
            "<b>" + esc(a["name"].replace("COPY ", "")) + "</b>" for a in parallel_starts
        )
        chains_html += (
            f'<div class="chain-parallel-note">'
            f'⚡ {len(parallel_starts)} chains start simultaneously: {start_names}'
            f'</div>'
        )

    # Trace each chain from its root
    shown = set()
    for chain in chains:
        if not chain: continue
        root_act = name_to_act.get(chain[0])
        if not root_act: continue

        steps_html = ""
        for i, aname in enumerate(chain):
            a = name_to_act.get(aname)
            if not a: continue
            cls = step_class(a)
            if i > 0:
                steps_html += '<span class="chain-arrow">→</span>'
            steps_html += f'<div class="chain-step {cls}">{step_label(a)}</div>'
            shown.add(aname)

        # Show what this chain loads (source → dest for copy acts in chain)
        chain_tables = []
        for aname in chain:
            a = name_to_act.get(aname)
            if a and a["outputs"]:
                tbl = a["outputs"][0].replace("DS_TBL_","").replace("DS_","")
                chain_tables.append(tbl)

        note = ""
        if chain_tables:
            note = f'<div class="chain-note">Loads: {esc(", ".join(chain_tables))}</div>'

        chains_html += f"""
        <div class="chain">
          <div class="chain-hdr">Chain — starting from {esc(chain[0].replace("COPY ",""))}</div>
          <div class="chain-steps">{steps_html}</div>
          {note}
        </div>"""

    # Show any activities not captured in chains (e.g. stored procs with multi-deps)
    remaining = [a for a in acts if a["name"] not in shown]
    if remaining:
        dep_blocks = ""
        for a in remaining:
            dep_names = [d["activity"].replace("COPY ","") for d in a["depends_on"]]
            icon, lbl = activity_icon(a["type"])
            dep_blocks += (
                f'<div class="flow-box-item">{icon} <b>{esc(a["name"])}</b>'
                f'{"  —  waits for: " + esc(", ".join(dep_names)) if dep_names else ""}</div>'
            )
        chains_html += f"""
        <div class="convergence">
          <div class="convergence-hdr">⭐ Convergence Point — runs after all chains complete</div>
          <div class="flow-box-items" style="text-align:left;display:inline-block;margin:0 auto">{dep_blocks}</div>
        </div>"""

    chains_html += "</div>"

    # ── Source files table ────────────────────────────────────────────────────
    src_rows = ""
    for dsn in sorted(src_ds):
        ds     = ds_map.get(dsn, {})
        fmt, bg, fg = dataset_format(ds.get("type",""))
        ls_name = ds.get("linked_service","")
        clean   = dsn.replace("DS_","")
        src_rows += (
            f'<tr>'
            f'<td class="mono">{esc(clean)}</td>'
            f'<td><span class="fmt-badge" style="background:{bg};color:{fg}">{fmt}</span></td>'
            f'<td class="mut">{esc(ls_name)}</td>'
            f'</tr>'
        )

    # ── Destination tables table ───────────────────────────────────────────────
    dest_rows = ""
    for dsn in sorted(dest_ds):
        ds     = ds_map.get(dsn, {})
        fmt, bg, fg = dataset_format(ds.get("type",""))
        ls_name = ds.get("linked_service","")
        tbl     = dsn.replace("DS_TBL_","").replace("DS_","")
        # Find which activity writes to this table
        writer = next((a["name"] for a in copy_acts if dsn in a["outputs"]), "")
        dest_rows += (
            f'<tr>'
            f'<td class="mono">{esc(tbl)}</td>'
            f'<td><span class="fmt-badge" style="background:{bg};color:{fg}">{fmt}</span></td>'
            f'<td class="mut">{esc(ls_name)}</td>'
            f'<td class="mut">{esc(writer.replace("COPY ",""))}</td>'
            f'</tr>'
        )

    # ── Stored procedures section ─────────────────────────────────────────────
    sproc_section = ""
    if sproc_acts:
        sproc_rows = ""
        for a in sproc_acts:
            dep_names = [d["activity"].replace("COPY ","") for d in a["depends_on"]]
            sproc_rows += (
                f'<tr>'
                f'<td class="mono">{esc(a["name"].replace("Sproc_",""))}</td>'
                f'<td class="mut">{esc(a["linked_service"])}</td>'
                f'<td class="mut" style="font-size:11px">{esc(", ".join(dep_names))}</td>'
                f'</tr>'
            )
        sproc_section = f"""
    <div class="section">
      <div class="section-hdr">
        <span class="section-icon">⚙️</span>
        <h2>Stored Procedures ({len(sproc_acts)})</h2>
      </div>
      <p style="font-size:12px;color:var(--mut);margin-bottom:10px">
        Stored procedures run inside Synapse DW after all Copy activities complete.
        They typically merge staging data into reporting or data-mart tables.
      </p>
      <table>
        <thead><tr><th>Procedure Name</th><th>Runs On</th><th>Waits For (prerequisites)</th></tr></thead>
        <tbody>{sproc_rows}</tbody>
      </table>
    </div>"""

    # ── Parameters section ────────────────────────────────────────────────────
    param_section = ""
    if params:
        param_rows = ""
        for p in params:
            param_rows += (
                f'<tr>'
                f'<td class="mono">{esc(p["name"])}</td>'
                f'<td class="mut">{esc(p["type"])}</td>'
                f'<td class="mut">{esc(str(p["default"])) if p["default"] else "—"}</td>'
                f'</tr>'
            )
        param_section = f"""
    <div class="section">
      <div class="section-hdr"><span class="section-icon">🔧</span><h2>Parameters ({len(params)})</h2></div>
      <table>
        <thead><tr><th>Name</th><th>Type</th><th>Default</th></tr></thead>
        <tbody>{param_rows}</tbody>
      </table>
    </div>"""

    # ── Connected systems section ─────────────────────────────────────────────
    ls_cards_html = ""
    descriptions = {
        "IDOH_ADLSG2":    ("Azure Data Lake Storage Gen2",
                           "Hierarchical namespace storage account. Source for all raw/standardized files "
                           "(CSV, Parquet, JSON). Files land here from upstream systems before ADF picks them up."),
        "IDOH_SYNAPSE_DW": ("Azure Synapse Analytics (Dedicated SQL Pool)",
                            "SQL Data Warehouse used as the destination for loaded tables. Copy activities "
                            "write directly to staging or SM-layer tables. Stored procedures then transform "
                            "data into DM/reporting layers."),
    }
    for lsn in sorted(ls_names_used):
        ls   = ls_map.get(lsn, {})
        nm, expl = descriptions.get(lsn, (ls.get("type",""), ""))
        conn = ls.get("connection", {})
        conn_html = ""
        for k, v in conn.items():
            conn_html += f'<div><b>{esc(k)}:</b> {esc(str(v))}</div>'
        ls_cards_html += f"""
        <div class="ls-card">
          <div class="ls-type">{esc(ls.get('type',''))}</div>
          <h3>{esc(lsn)}</h3>
          <div class="ls-kv">{conn_html or '<span style="color:var(--mut)">Connection details stored in Azure Key Vault / ADF managed identity.</span>'}</div>
          {('<div style="margin-top:8px;font-size:11px;color:var(--mut)">' + esc(expl) + '</div>') if expl else ''}
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Pipeline Doc: {esc(name)}</title>
<style>{CSS}</style>
<script src="https://cdn.jsdelivr.net/npm/cytoscape@3.27.0/dist/cytoscape.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dagre@0.8.5/dist/dagre.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/cytoscape-dagre@2.5.0/cytoscape-dagre.min.js"></script>
</head>
<body>
<div class="page">

  <!-- HEADER -->
  <div class="hero">
    <div class="hero-top">
      <div>
        <div class="badges">
          <span class="badge badge-env">{esc(env_label.upper())}</span>
          <span class="badge badge-folder">📁 {esc(folder)}</span>
          <span class="badge badge-type">ADF Pipeline</span>
        </div>
        <h1>{esc(name)}</h1>
        <div class="sub">{esc(factory_name)} &nbsp;·&nbsp; Generated: <span id="gen-ts" data-ts="{esc(generated)}">&#x21BB; {esc(generated)}</span><script>(function(){{var s=document.getElementById(\'gen-ts\'),h=(Date.now()-new Date(s.dataset.ts.replace(\' \',\'T\')))/36e5;s.style.color=h<25?'var(--grn)':h<168?'var(--yel)':'var(--red)';s.style.fontWeight='700';}})();</script></div>
        <div class="hero-desc">{esc(display_desc)}</div>
      </div>
    </div>
    <div class="stat-row">{stat_boxes}</div>
  </div>

  <!-- DATA LINEAGE DIAGRAM -->
  {build_lineage_section_html(pipeline, ds_map, schema_map if schema_map else {}, sproc_infos or [])}

  <!-- DATA FLOW (summary boxes) -->
  <div class="section">
    <div class="section-hdr">
      <span class="section-icon">🔀</span>
      <h2>End-to-End Data Flow — Summary</h2>
    </div>
    {flow_html}
  </div>

  <!-- EXECUTION CHAINS -->
  <div class="section">
    <div class="section-hdr">
      <span class="section-icon">⛓</span>
      <h2>Execution Chains &amp; Dependencies</h2>
    </div>
    <p style="font-size:12px;color:var(--mut);margin-bottom:12px">
      Each chain runs independently in parallel. An arrow (→) means the activity must
      complete successfully before the next one starts. Chains converge at the stored
      procedure step, which waits for specific prerequisites from multiple chains.
    </p>
    {chains_html}
  </div>

  <!-- SOURCE FILES -->
  <div class="section">
    <div class="section-hdr">
      <span class="section-icon">📂</span>
      <h2>Source Files — ADLS Gen2 ({len(src_ds)})</h2>
    </div>
    <table>
      <thead><tr><th>Dataset / File</th><th>Format</th><th>Linked Service</th></tr></thead>
      <tbody>{src_rows}</tbody>
    </table>
  </div>

  <!-- DESTINATION TABLES -->
  <div class="section">
    <div class="section-hdr">
      <span class="section-icon">🗄</span>
      <h2>Destination Tables — Synapse DW ({len(dest_ds)})</h2>
    </div>
    <table>
      <thead><tr><th>Table Name</th><th>Type</th><th>Loaded By</th><th>Copy Activity</th></tr></thead>
      <tbody>{dest_rows}</tbody>
    </table>
  </div>

  {sproc_section}

  {build_sproc_synopsis_html(sproc_infos or [])}

  {param_section}

  <!-- CONNECTED SYSTEMS -->
  <div class="section">
    <div class="section-hdr">
      <span class="section-icon">🔗</span>
      <h2>Connected Systems</h2>
    </div>
    <div class="ls-cards">{ls_cards_html}</div>
  </div>

  <div class="footer">
    Auto-generated from Azure Data Factory metadata &nbsp;·&nbsp; {esc(factory_name)}
  </div>

</div>
</body>
</html>"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env",      default="dev", choices=["dev","prd"])
    ap.add_argument("--pipeline", required=True)
    ap.add_argument("--out",      default=None)
    args = ap.parse_args()

    env = ENVIRONMENTS[args.env]
    print(f"Connecting to {env['factory_name']} ({args.env.upper()})...")
    cred   = AzureCliCredential()
    client = DataFactoryManagementClient(cred, env["subscription_id"])
    data   = fetch_all(client, env["resource_group"], env["factory_name"])

    pipeline = next((p for p in data["pipelines"] if p["name"] == args.pipeline), None)
    if not pipeline:
        print(f"ERROR: pipeline '{args.pipeline}' not found.")
        names = [p["name"] for p in data["pipelines"] if args.pipeline.lower() in p["name"].lower()]
        if names:
            print("Did you mean:")
            for n in names[:5]: print(f"  {n}")
        sys.exit(1)

    ds_map = {d["name"]: d for d in data["datasets"]}
    ls_map = {l["name"]: l for l in data["linked_services"]}

    # Fetch schema.table info for destination datasets from ADF SDK
    dest_ds_names = set()
    for a in pipeline["activities"]:
        if "Copy" in a["type"]:
            for d in a["outputs"]:
                dest_ds_names.add(d)
    print(f"  Fetching schema/table for {len(dest_ds_names)} destination datasets…", end="", flush=True)
    schema_map = fetch_pipeline_dataset_schemas(
        client, env["resource_group"], env["factory_name"], sorted(dest_ds_names)
    )
    print(f" got {len(schema_map)} schema entries.")

    # Fetch stored procedure definitions from Synapse DW
    sproc_acts = [a for a in pipeline["activities"]
                  if "StoredProcedure" in a["type"] or "sproc" in a["type"].lower()]
    sproc_infos = []
    if sproc_acts:
        # ADF activity names like "Sproc_Load_Census_Reporting" don't always match the
        # actual proc name. Try several variations: full name, strip prefix, lowercase.
        sproc_names = []
        for a in sproc_acts:
            n = a["name"]
            sproc_names += [n, n.lower(),
                            re.sub(r"^Sproc_", "", n), re.sub(r"^Sproc_", "", n).lower(),
                            "sproc_" + re.sub(r"^Sproc_", "", n).lower()]
        sproc_names = list(dict.fromkeys(sproc_names))  # deduplicate, preserve order
        try:
            conn  = dw_connect(args.env)
            defs  = fetch_sproc_definitions(conn, sproc_names)
            conn.close()
            for sname, sql in defs.items():
                print(f"  Parsing sproc: {sname}…")
                sproc_infos.append(parse_sproc(sname, sql))
        except Exception as e:
            print(f"  Warning: could not fetch sproc definitions — {e}")

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    html = build_html(pipeline, ds_map, ls_map, args.env, env["factory_name"], generated,
                      sproc_infos=sproc_infos, schema_map=schema_map)

    safe_name = args.pipeline.replace("/","_").replace(" ","_")
    out_file  = args.out or f"/home/thedavidporter/pipeline_doc_{safe_name}_{args.env}.html"
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved: {out_file}")


if __name__ == "__main__":
    main()
