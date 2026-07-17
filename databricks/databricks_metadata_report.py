import os
#!/usr/bin/env python3
"""
Azure Databricks Metadata Report — all 3 workspaces in one file.

Usage:
  python3 databricks_metadata_report.py
"""

import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from datetime import datetime

import requests

# ── config ─────────────────────────────────────────────────────────────────────

SUBSCRIPTION_ID = "57493fde-eff8-432f-8574-4f1281bd2ce3"

WORKSPACES = [
    {
        "key":           "iz-dev",
        "label":         "IZ-DEV",
        "resource_name": "zus1-idoh-iz-dev-v2-dbrk",
        "url":           "adb-612192313963696.16.azuredatabricks.net",
        "sku":           "Premium",
        "rg":            "zus1-idoh-dev-v2-rg",
    },
    {
        "key":           "dev",
        "label":         "DEV",
        "resource_name": "zus1-idoh-dev-v2-dbrk",
        "url":           "adb-5757046586469840.0.azuredatabricks.net",
        "sku":           "Premium",
        "rg":            "zus1-idoh-dev-v2-rg",
    },
    {
        "key":           "prd",
        "label":         "PRD",
        "resource_name": "zus1-idoh-prd-v1-dbrk",
        "url":           "adb-5323951998838804.4.azuredatabricks.net",
        "sku":           "Premium",
        "rg":            "zus1-idoh-prd-v1-rg",
    },
]

OUT_FILE = "/home/thedavidporter/databricks_metadata_report.html"

# ── auth ───────────────────────────────────────────────────────────────────────

def get_token():
    return subprocess.check_output(
        ["az", "account", "get-access-token",
         "--resource", "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d",
         "--subscription", SUBSCRIPTION_ID,
         "--query", "accessToken", "-o", "tsv"],
        stderr=subprocess.DEVNULL,
    ).decode().strip()


def api(token, ws_url, path, params=None):
    try:
        resp = requests.get(
            f"https://{ws_url}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params or {},
            timeout=30,
        )
    except requests.exceptions.Timeout:
        print(f"\n    [WARN] timeout {ws_url}{path}", flush=True)
        return {}
    except requests.exceptions.RequestException as exc:
        print(f"\n    [WARN] request error {ws_url}{path}: {exc}", flush=True)
        return {}
    if resp.status_code == 200:
        return resp.json()
    print(f"\n    [WARN] {resp.status_code} {ws_url}{path}", flush=True)
    return {}


_JUPYTER_LANG_MAP = {"python": "PYTHON", "r": "R", "scala": "SCALA", "sql": "SQL", "sparksql": "SQL"}

def _notebook_language(token, base, path):
    """Get notebook language via JUPYTER export — authoritative source for default language.
    The workspace/list and get-status APIs can return stale/incorrect language values."""
    import base64
    try:
        data = api(token, base, "/api/2.0/workspace/export", {"path": path, "format": "JUPYTER"})
        content = data.get("content", "")
        if content:
            import json as _json
            nb = _json.loads(base64.b64decode(content))
            lang = nb.get("metadata", {}).get("language_info", {}).get("name", "")
            if lang:
                return _JUPYTER_LANG_MAP.get(lang.lower(), lang.upper())
    except Exception:
        pass
    return ""

# ── repo URL helpers ────────────────────────────────────────────────────────────

_CRED_RE = re.compile(r"https?://[^@]+@")

def clean_url(url):
    """Strip embedded credentials from a repo URL."""
    if not url:
        return ""
    return _CRED_RE.sub("https://", url)


def repo_name(url):
    """Extract the short repo name from an ADO/GitHub URL."""
    if not url:
        return "(no url)"
    url = clean_url(url)
    # ADO: .../ODA/_git/RepoName  or  .../ODA-DataScience/_git/RepoName
    m = re.search(r"/_git/([^/]+)$", url)
    if m:
        return m.group(1)
    # GitHub: .../owner/repo.git
    m = re.search(r"/([^/]+?)(?:\.git)?$", url)
    if m:
        return m.group(1)
    return url

# ── collect ────────────────────────────────────────────────────────────────────

def _walk_notebooks(token, base, root="/", skip_prefixes=("/Repos",)):
    """BFS walk of workspace tree, returning all NOTEBOOK objects (excluding /Repos).
    Phase 1: walk directories (fast). Phase 2: enrich notebooks in parallel (JUPYTER language + creator)."""
    # Phase 1: directory walk — one API call per directory, no per-notebook calls yet
    raw = []
    queue = [root]
    visited = set()
    while queue:
        path = queue.pop(0)
        if path in visited:
            continue
        visited.add(path)
        if any(path.startswith(p) for p in skip_prefixes):
            continue
        objects = api(token, base, "/api/2.0/workspace/list", {"path": path}).get("objects", [])
        for obj in objects:
            t = obj.get("object_type", "")
            if t == "NOTEBOOK":
                raw.append(obj)
            elif t == "DIRECTORY":
                queue.append(obj.get("path", ""))

    # Phase 2: fetch authoritative language + creator in parallel
    def enrich(obj):
        p        = obj.get("path", "")
        creator  = obj.get("creator_user_name", "")
        language = _notebook_language(token, base, p)
        if not language:
            language = obj.get("language", "")
        if not creator:
            status  = api(token, base, "/api/2.0/workspace/get-status", {"path": p})
            creator = status.get("creator_user_name", "")
        return {
            "path":        p,
            "name":        p.split("/")[-1],
            "language":    language,
            "modified_at": obj.get("modified_at"),
            "owner":       creator,
        }

    results = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(enrich, obj): obj for obj in raw}
        done, not_done = wait(futures, timeout=300)
        for future in done:
            try:
                results.append(future.result())
            except Exception:
                pass
        if not_done:
            print(f"\n    [WARN] {len(not_done)} notebook enrichments timed out after 300s — skipped", flush=True)
    return results


def collect_workspace(ws, token):
    label = ws["label"]
    base  = ws["url"]
    print(f"  [{label}] clusters…", end="", flush=True)

    # clusters
    raw_clusters = api(token, base, "/api/2.0/clusters/list").get("clusters", [])
    clusters = [
        {
            "name":               c.get("cluster_name", ""),
            "state":              c.get("state", ""),
            "spark_version":      c.get("spark_version", ""),
            "node_type":          c.get("node_type_id", ""),
            "autoterminate_mins": c.get("autotermination_minutes", 0),
            "source":             c.get("cluster_source", ""),
            "driver_node_type":   c.get("driver_node_type_id", ""),
            "num_workers":        c.get("num_workers", 0),
            "autoscale":          c.get("autoscale"),
        }
        for c in raw_clusters
    ]
    print(f"{len(clusters)}", end="  ", flush=True)

    # jobs
    print("jobs…", end="", flush=True)
    raw_jobs = api(token, base, "/api/2.1/jobs/list", {"limit": 200}).get("jobs", [])
    jobs = []
    for j in raw_jobs:
        s    = j.get("settings", {})
        sched = s.get("schedule", {})
        jobs.append({
            "job_id":   j.get("job_id"),
            "name":     s.get("name", ""),
            "schedule": sched.get("quartz_cron_expression", ""),
            "timezone": sched.get("timezone_id", ""),
            "paused":   sched.get("pause_status", "") == "PAUSED",
        })
    print(f"{len(jobs)}", end="  ", flush=True)

    # repos — paginate
    print("repos…", end="", flush=True)
    repos_raw = []
    next_page = None
    while True:
        params = {}
        if next_page:
            params["next_page_token"] = next_page
        d = api(token, base, "/api/2.0/repos", params)
        repos_raw.extend(d.get("repos", []))
        next_page = d.get("next_page_token")
        if not next_page:
            break
    repos = [
        {
            "id":         r.get("id"),
            "url":        clean_url(r.get("url") or ""),
            "repo_name":  repo_name(r.get("url") or ""),
            "branch":     r.get("branch") or "",
            "path":       r.get("path") or "",
            "provider":   r.get("provider") or "",
        }
        for r in repos_raw
    ]
    print(f"{len(repos)}", end="  ", flush=True)

    # warehouses
    print("warehouses…", end="", flush=True)
    raw_wh = api(token, base, "/api/2.0/sql/warehouses").get("warehouses", [])
    warehouses = [
        {
            "name":          w.get("name", ""),
            "state":         w.get("state", ""),
            "size":          w.get("cluster_size", ""),
            "type":          w.get("warehouse_type", ""),
            "auto_stop":     w.get("auto_stop_mins", 0),
            "spot_instance": w.get("spot_instance_policy", ""),
        }
        for w in raw_wh
    ]
    print(f"{len(warehouses)}", end="  ", flush=True)

    # instance pools
    print("pools…", end="", flush=True)
    raw_pools = api(token, base, "/api/2.0/instance-pools/list").get("instance_pools", [])
    pools = [
        {
            "name":         p.get("instance_pool_name", ""),
            "node_type":    p.get("node_type_id", ""),
            "min_idle":     p.get("min_idle_instances", 0),
            "max_capacity": p.get("max_capacity"),
            "state":        p.get("state", ""),
        }
        for p in raw_pools
    ]
    print(f"{len(pools)}", end="  ", flush=True)

    # DLT pipelines
    print("pipelines…", end="", flush=True)
    raw_pipelines = api(token, base, "/api/2.0/pipelines").get("statuses", [])
    pipelines = [
        {
            "pipeline_id": p.get("pipeline_id", ""),
            "name":        p.get("name", ""),
            "state":       p.get("state", ""),
            "target":      (p.get("spec") or {}).get("target", ""),
            "continuous":  (p.get("spec") or {}).get("continuous", False),
            "development": (p.get("spec") or {}).get("development", False),
        }
        for p in raw_pipelines
    ]
    print(f"{len(pipelines)}", end="  ", flush=True)

    # SQL queries — paginate
    print("queries…", end="", flush=True)
    queries = []
    q_page = 1
    while True:
        d = api(token, base, "/api/2.0/sql/queries", {"page_size": 250, "page": q_page})
        batch = d.get("results", [])
        if not batch:
            break
        for q_item in batch:
            queries.append({
                "id":          q_item.get("id", ""),
                "name":        q_item.get("name", ""),
                "description": q_item.get("description") or "",
                "tags":        q_item.get("tags") or [],
                "updated_at":  q_item.get("updated_at", ""),
            })
        if len(queries) >= (d.get("count") or 0):
            break
        q_page += 1
    print(f"{len(queries)}", end="  ", flush=True)

    # SQL dashboards — paginate
    print("dashboards…", end="", flush=True)
    dashboards = []
    d_page = 1
    while True:
        d = api(token, base, "/api/2.0/sql/dashboards", {"page_size": 250, "page": d_page})
        batch = d.get("results", [])
        if not batch:
            break
        for dash in batch:
            dashboards.append({
                "id":         dash.get("id", ""),
                "name":       dash.get("name", ""),
                "slug":       dash.get("slug", ""),
                "tags":       dash.get("tags") or [],
                "updated_at": dash.get("updated_at", ""),
            })
        if len(dashboards) >= (d.get("count") or 0):
            break
        d_page += 1
    print(f"{len(dashboards)}", end="  ", flush=True)

    # apps
    print("apps…", end="", flush=True)
    apps_raw = api(token, base, "/api/2.0/apps").get("apps", [])
    apps = [
        {
            "name":        a.get("name", ""),
            "description": a.get("description", ""),
            "state":       (a.get("status") or {}).get("state", ""),
            "message":     (a.get("status") or {}).get("message", ""),
            "creator":     a.get("creator", ""),
            "url":         a.get("url", ""),
            "create_time": a.get("create_time", ""),
            "update_time": a.get("update_time", ""),
        }
        for a in apps_raw
    ]
    print(f"{len(apps)}", end="  ", flush=True)

    # model serving endpoints
    print("serving…", end="", flush=True)
    raw_endpoints = api(token, base, "/api/2.0/serving-endpoints").get("endpoints", [])
    serving = [
        {
            "name":  e.get("name", ""),
            "ready": (e.get("state") or {}).get("ready", ""),
        }
        for e in raw_endpoints
    ]
    print(f"{len(serving)}", end="  ", flush=True)

    # cluster policies
    raw_pol = api(token, base, "/api/2.0/policies/clusters/list").get("policies", [])
    policies = [
        {"name": p.get("name", ""), "description": p.get("description", "")}
        for p in raw_pol
    ]

    # secret scopes (names only — no keys or values)
    raw_scopes = api(token, base, "/api/2.0/secrets/scopes/list").get("scopes", [])
    secret_scopes = [s.get("name", "") for s in raw_scopes]

    # workspace root dirs
    raw_ws = api(token, base, "/api/2.0/workspace/list", {"path": "/"}).get("objects", [])
    workspace_dirs = [
        {"type": o.get("object_type", ""), "path": o.get("path", "")}
        for o in raw_ws
    ]

    # notebooks (recursive walk, excluding /Repos)
    print("notebooks…", end="", flush=True)
    notebooks = _walk_notebooks(token, base)
    print(f"{len(notebooks)}", end="  ", flush=True)

    # lakeview / AI dashboards
    print("lakeview…", end="", flush=True)
    lakeview_raw = []
    lv_token = None
    while True:
        params = {"page_size": 200}
        if lv_token:
            params["page_token"] = lv_token
        d = api(token, base, "/api/2.0/lakeview/dashboards", params)
        lakeview_raw.extend(d.get("dashboards", []))
        lv_token = d.get("next_page_token")
        if not lv_token:
            break
    lakeview_dashboards = [
        {
            "dashboard_id":  d.get("dashboard_id", ""),
            "display_name":  d.get("display_name", ""),
            "lifecycle_state": d.get("lifecycle_state", ""),
            "create_time":   d.get("create_time", ""),
            "update_time":   d.get("update_time", ""),
            "parent_path":   d.get("parent_path", ""),
        }
        for d in lakeview_raw
    ]
    print(f"{len(lakeview_dashboards)}", end="  ", flush=True)

    # SQL alerts
    print("alerts…", end="", flush=True)
    alerts_resp = api(token, base, "/api/2.0/sql/alerts")
    raw_alerts = alerts_resp if isinstance(alerts_resp, list) else alerts_resp.get("results", [])
    alerts = [
        {
            "id":         a.get("id", ""),
            "name":       a.get("name", ""),
            "state":      a.get("state", ""),
            "created_at": a.get("created_at", ""),
            "updated_at": a.get("updated_at", ""),
            "rearm":      a.get("rearm"),
            "query_name": (a.get("query") or {}).get("name", ""),
            "condition":  "{} {} {}".format(
                (a.get("options") or {}).get("column", "?"),
                (a.get("options") or {}).get("op", "?"),
                (a.get("options") or {}).get("value", "?"),
            ),
        }
        for a in raw_alerts
    ]
    print(f"{len(alerts)}", end="  ", flush=True)

    # Unity Catalog
    print("uc…", end="", flush=True)
    catalogs_raw = api(token, base, "/api/2.1/unity-catalog/catalogs").get("catalogs", [])
    uc_catalogs = []
    for cat in catalogs_raw:
        cat_name = cat.get("name", "")
        schemas_raw = api(token, base, "/api/2.1/unity-catalog/schemas",
                          {"catalog_name": cat_name}).get("schemas", [])
        schemas = []
        for sch in schemas_raw:
            sch_name = sch.get("name", "")
            tables_raw = []
            page_token = None
            while True:
                params = {"catalog_name": cat_name, "schema_name": sch_name, "max_results": 200}
                if page_token:
                    params["page_token"] = page_token
                d = api(token, base, "/api/2.1/unity-catalog/tables", params)
                batch = d.get("tables", [])
                tables_raw.extend(batch)
                page_token = d.get("next_page_token")
                if not page_token:
                    break
            schemas.append({
                "name": sch_name,
                "owner": sch.get("owner", ""),
                "comment": sch.get("comment") or "",
                "tables": [
                    {
                        "name": t.get("name", ""),
                        "table_type": t.get("table_type", ""),
                        "owner": t.get("owner", ""),
                        "comment": t.get("comment") or "",
                        "columns": len(t.get("columns") or []),
                        "updated_at": t.get("updated_at"),
                    }
                    for t in tables_raw
                ],
            })
        uc_catalogs.append({
            "name": cat_name,
            "owner": cat.get("owner", ""),
            "comment": cat.get("comment") or "",
            "schemas": schemas,
        })
    ext_locs = [
        {
            "name": e.get("name", ""),
            "url": e.get("url", ""),
            "credential_name": e.get("credential_name", ""),
            "owner": e.get("owner", ""),
        }
        for e in api(token, base, "/api/2.1/unity-catalog/external-locations").get("external_locations", [])
    ]
    uc_table_count = sum(len(s["tables"]) for c in uc_catalogs for s in c["schemas"])
    print(f"{len(uc_catalogs)} catalogs/{uc_table_count} tables", end="  ", flush=True)

    print("done")
    return {
        "key":            ws["key"],
        "label":          ws["label"],
        "resource_name":  ws["resource_name"],
        "url":            ws["url"],
        "sku":            ws["sku"],
        "rg":             ws["rg"],
        "clusters":       clusters,
        "jobs":           jobs,
        "repos":          repos,
        "warehouses":     warehouses,
        "pools":          pools,
        "pipelines":      pipelines,
        "queries":        queries,
        "dashboards":     dashboards,
        "apps":           apps,
        "serving":        serving,
        "policies":       policies,
        "secret_scopes":  secret_scopes,
        "workspace_dirs": workspace_dirs,
        "notebooks":           notebooks,
        "lakeview_dashboards": lakeview_dashboards,
        "alerts":              alerts,
        "unity_catalog": {"catalogs": uc_catalogs, "external_locations": ext_locs},
    }


def collect():
    results = []
    for ws in WORKSPACES:
        token = get_token()  # fresh token per workspace so it doesn't expire mid-collection
        print(f"  Collecting {ws['label']}…", end=" ", flush=True)
        results.append(collect_workspace(ws, token))
    return results

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
.sidebar{width:200px;background:var(--sur);border-right:1px solid var(--brd);
  display:flex;flex-direction:column;flex-shrink:0;overflow:hidden}
.sb-hdr{padding:14px 14px 10px;font-size:13px;font-weight:700;border-bottom:1px solid var(--brd)}
.sb-hdr small{display:block;font-size:10px;color:var(--mut);font-weight:400;margin-top:2px}
.sb-body{padding:8px 0;overflow-y:auto}
.sb-section{font-size:10px;font-weight:700;color:var(--mut);padding:10px 14px 4px;
  text-transform:uppercase;letter-spacing:.5px}
.sb-item{padding:6px 14px;cursor:pointer;font-size:12px;border-left:3px solid transparent;
  color:var(--txt);display:flex;align-items:center;gap:6px}
.sb-item:hover{background:var(--sur2)}
.sb-item.active{background:var(--sur2);border-left-color:var(--acc);color:var(--acc)}
.ws-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}

/* main */
.main{flex:1;display:flex;flex-direction:column;overflow:hidden}
.main-hdr{padding:14px 24px 10px;border-bottom:1px solid var(--brd);flex-shrink:0}
.main-hdr h1{font-size:17px;font-weight:800}
.sub{font-size:11px;color:var(--mut);margin-top:2px}

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
tr:hover td{background:var(--sur)}
.mono{font-family:'Cascadia Code','Fira Code',monospace;font-size:11px}
h2{font-size:13px;font-weight:700;margin:14px 0 8px}

/* chips */
.chip{display:inline-block;font-size:10px;padding:2px 7px;border-radius:3px;font-weight:700;white-space:nowrap}
.st-running  {background:#1a3a2a;color:#4ade80}
.st-stopped  {background:var(--sur2);color:var(--mut)}
.st-terminated{background:var(--sur2);color:var(--mut)}
.st-starting {background:#2a2a0a;color:#fbbf24}
.st-error    {background:#3a1a1a;color:#f87171}
.chip-pro    {background:#2d1e5f;color:#c084fc}
.chip-classic{background:#1e2a4a;color:#6c8eff}
.chip-sl     {background:#1a3a2a;color:#4ade80}
.chip-sched  {background:#1e2a4a;color:#6c8eff}
.chip-nosched{background:var(--sur2);color:var(--mut)}
.chip-paused {background:#2a2a0a;color:#fbbf24}

/* overview cards */
.ov-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px;margin-bottom:18px}
.ov-card{background:var(--sur);border:1px solid var(--brd);border-radius:8px;padding:12px 14px}
.ov-card h3{font-size:12px;font-weight:700;margin-bottom:8px;color:var(--mut);text-transform:uppercase;letter-spacing:.4px}
.ov-card .row{display:flex;justify-content:space-between;font-size:12px;padding:2px 0}
.ov-card .row b{color:var(--txt)}

/* repo groups */
.repo-group{background:var(--sur);border:1px solid var(--brd);border-radius:6px;margin-bottom:8px}
.repo-group-hdr{padding:8px 12px;font-weight:700;font-size:12px;cursor:pointer;
  display:flex;align-items:center;justify-content:space-between;user-select:none}
.repo-group-hdr:hover{background:var(--sur2);border-radius:6px}
.repo-group-body{display:none;border-top:1px solid var(--brd)}
.repo-group-body.open{display:block}
.repo-row{padding:5px 14px;font-size:11px;border-bottom:1px solid var(--brd);
  display:flex;align-items:center;gap:8px}
.repo-row:last-child{border-bottom:none}

/* scope list */
.scope-list{display:flex;flex-wrap:wrap;gap:8px;margin-top:6px}
.scope-pill{background:var(--sur2);border:1px solid var(--brd);border-radius:6px;
  padding:5px 12px;font-size:12px;font-family:monospace}

/* workspace dir list */
.ws-dir{padding:5px 0;display:flex;align-items:center;gap:8px;font-size:12px;
  border-bottom:1px solid var(--brd)}
.ws-dir:last-child{border-bottom:none}

/* language chips */
.lang-py  {background:#1a2a4a;color:#6c8eff}
.lang-sql {background:#1a3a2a;color:#4ade80}
.lang-sc  {background:#2d1e0a;color:#fb923c}
.lang-r   {background:#2a1a3a;color:#c084fc}

/* unity catalog tree */
.uc-catalog{background:var(--sur);border:1px solid var(--brd);border-radius:6px;margin-bottom:8px}
.uc-cat-hdr{padding:10px 14px;font-size:12px;cursor:pointer;display:flex;align-items:center;
  justify-content:space-between;user-select:none}
.uc-cat-hdr:hover{background:var(--sur2);border-radius:6px}
.uc-cat-body{display:none;border-top:1px solid var(--brd)}
.uc-cat-body.open{display:block}
.uc-schema{border-bottom:1px solid var(--brd)}
.uc-schema:last-child{border-bottom:none}
.uc-sch-hdr{padding:7px 14px 7px 28px;font-size:12px;cursor:pointer;display:flex;
  align-items:center;justify-content:space-between;user-select:none}
.uc-sch-hdr:hover{background:var(--sur2)}
.uc-sch-body{display:none;padding-left:14px;border-top:1px solid var(--brd)}
.uc-sch-body.open{display:block}
.uc-section-hdr{font-size:12px;font-weight:700;margin:14px 0 8px;color:var(--mut);
  text-transform:uppercase;letter-spacing:.4px}
"""

# ── JS ─────────────────────────────────────────────────────────────────────────

JS = """
const WORKSPACES = __WORKSPACES__;
const WS_COLORS  = ['#6c8eff','#fb923c','#4ade80'];

let activeWS = WORKSPACES[0].key;

function esc(s){
  if(s==null) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function stateChip(s){
  const sl = (s||'').toUpperCase();
  const cls = sl==='RUNNING'?'st-running':sl==='STOPPED'?'st-stopped':sl==='TERMINATED'?'st-terminated':sl.includes('START')?'st-starting':'st-error';
  return `<span class="chip ${cls}">${esc(sl)}</span>`;
}
function whTypeChip(t){
  if(t==='PRO')        return '<span class="chip chip-pro">PRO</span>';
  if(t==='CLASSIC')    return '<span class="chip chip-classic">CLASSIC</span>';
  if(t==='SERVERLESS') return '<span class="chip chip-sl">SERVERLESS</span>';
  return `<span class="chip chip-classic">${esc(t)}</span>`;
}

// ── workspace switcher ─────────────────────────────────────────────────────────
function selectWS(key){
  activeWS = key;
  document.querySelectorAll('.sb-item').forEach(e=>e.classList.remove('active'));
  document.getElementById('sb-'+key)?.classList.add('active');
  const ws = WORKSPACES.find(w=>w.key===key);
  document.getElementById('ws-header-name').textContent = 'Azure Databricks — ' + ws.label;
  document.getElementById('ws-header-sub').textContent  = ws.resource_name + '  ·  ' + ws.url;
  // re-render active tab
  const activePanel = document.querySelector('.panel.active');
  if(activePanel) renderPanel(activePanel.id.replace('p-',''));
  renderStats(ws);
}

function renderStats(ws){
  document.getElementById('stat-clusters').textContent   = ws.clusters.length;
  document.getElementById('stat-jobs').textContent       = ws.jobs.length;
  document.getElementById('stat-repos').textContent      = ws.repos.length;
  document.getElementById('stat-warehouses').textContent = ws.warehouses.length;
  document.getElementById('stat-pools').textContent      = ws.pools.length;
  document.getElementById('stat-pipelines').textContent  = ws.pipelines.length;
  document.getElementById('stat-queries').textContent    = ws.queries.length;
  document.getElementById('stat-dashboards').textContent = ws.dashboards.length;
  document.getElementById('stat-apps').textContent       = ws.apps.length;
  document.getElementById('stat-serving').textContent    = ws.serving.length;
  const uc = ws.unity_catalog;
  const ucTables = uc ? uc.catalogs.reduce((a,c)=>a+c.schemas.reduce((b,s)=>b+s.tables.length,0),0) : 0;
  document.getElementById('stat-uc').textContent        = ucTables;
  document.getElementById('stat-notebooks').textContent = ws.notebooks.length;
  document.getElementById('stat-lakeview').textContent  = ws.lakeview_dashboards.length;
  document.getElementById('stat-alerts').textContent    = ws.alerts.length;
}

// ── tabs ───────────────────────────────────────────────────────────────────────
function showTab(id){
  document.querySelectorAll('.tab,.panel').forEach(e=>e.classList.remove('active'));
  document.getElementById('tab-'+id)?.classList.add('active');
  document.getElementById('p-'+id)?.classList.add('active');
  renderPanel(id);
}
function renderPanel(id){
  const ws = WORKSPACES.find(w=>w.key===activeWS);
  if(!ws) return;
  if(id==='overview')    renderOverview(ws);
  if(id==='clusters')    renderClusters(ws);
  if(id==='jobs')        renderJobs(ws);
  if(id==='repos')       renderRepos(ws);
  if(id==='warehouses')  renderWarehouses(ws);
  if(id==='pools')       renderPools(ws);
  if(id==='pipelines')   renderPipelines(ws);
  if(id==='queries')     renderQueries(ws);
  if(id==='dashboards')  renderDashboards(ws);
  if(id==='apps')        renderApps(ws);
  if(id==='serving')     renderServing(ws);
  if(id==='policies')    renderPolicies(ws);
  if(id==='scopes')      renderScopes(ws);
  if(id==='notebooks')   renderNotebooks(ws);
  if(id==='lakeview')    renderLakeview(ws);
  if(id==='alerts')      renderAlerts(ws);
  if(id==='uc')          renderUC(ws);
}

// ── OVERVIEW ──────────────────────────────────────────────────────────────────
function renderOverview(ws){
  // cluster state summary
  const states = {};
  ws.clusters.forEach(c=>{ states[c.state]=(states[c.state]||0)+1; });
  document.getElementById('ov-clusters').innerHTML = Object.entries(states)
    .sort((a,b)=>b[1]-a[1])
    .map(([s,n])=>`<div class="row"><span>${stateChip(s)}</span><b>${n}</b></div>`).join('');

  // warehouse state summary
  const whStates = {};
  ws.warehouses.forEach(w=>{ whStates[w.state]=(whStates[w.state]||0)+1; });
  document.getElementById('ov-warehouses').innerHTML = Object.entries(whStates)
    .sort((a,b)=>b[1]-a[1])
    .map(([s,n])=>`<div class="row"><span>${stateChip(s)}</span><b>${n}</b></div>`).join('');

  // unique repos
  const uniqueRepos = {};
  ws.repos.forEach(r=>{ uniqueRepos[r.repo_name]=(uniqueRepos[r.repo_name]||0)+1; });
  document.getElementById('ov-repos').innerHTML = [
    `<div class="row"><span class="mut">Total clones</span><b>${ws.repos.length}</b></div>`,
    `<div class="row"><span class="mut">Unique repos</span><b>${Object.keys(uniqueRepos).length}</b></div>`,
  ].join('');

  // secret scopes
  document.getElementById('ov-scopes').innerHTML = ws.secret_scopes
    .map(s=>`<div class="row"><span class="mono">${esc(s)}</span></div>`).join('') || '<div class="mut" style="font-size:11px">None</div>';

  // workspace dirs
  document.getElementById('ov-ws-dirs').innerHTML = ws.workspace_dirs
    .map(d=>`<div class="ws-dir">
      <span class="chip chip-classic" style="font-size:9px">${esc(d.type)}</span>
      <span class="mono">${esc(d.path)}</span>
    </div>`).join('');
}

// ── CLUSTERS ──────────────────────────────────────────────────────────────────
function renderClusters(ws){
  const q = (document.getElementById('cluster-search')?.value||'').toLowerCase();
  document.getElementById('cluster-search').oninput = ()=>renderClusters(ws);
  let clusters = ws.clusters;
  if(q) clusters = clusters.filter(c=>c.name.toLowerCase().includes(q)||c.state.toLowerCase().includes(q)||c.spark_version.toLowerCase().includes(q)||c.node_type.toLowerCase().includes(q));
  document.getElementById('cluster-tbody').innerHTML = clusters.map(c=>`<tr>
    <td><b>${esc(c.name)}</b></td>
    <td>${stateChip(c.state)}</td>
    <td class="mono mut">${esc(c.spark_version)}</td>
    <td class="mono mut">${esc(c.node_type)}</td>
    <td class="mut">${c.autoterminate_mins ? c.autoterminate_mins+'m' : '—'}</td>
    <td class="mut">${c.autoscale ? `${c.autoscale.min_workers}–${c.autoscale.max_workers}` : (c.num_workers||'1')}</td>
  </tr>`).join('') || '<tr><td colspan="6" class="mut" style="padding:12px">No clusters.</td></tr>';
  document.getElementById('cluster-count').textContent = `${clusters.length} of ${ws.clusters.length}`;
}

// ── JOBS ──────────────────────────────────────────────────────────────────────
function renderJobs(ws){
  const q = (document.getElementById('job-search')?.value||'').toLowerCase();
  document.getElementById('job-search').oninput = ()=>renderJobs(ws);
  let jobs = ws.jobs;
  if(q) jobs = jobs.filter(j=>j.name.toLowerCase().includes(q));
  document.getElementById('job-tbody').innerHTML = jobs.map(j=>`<tr>
    <td class="mut" style="font-size:10px">${esc(j.job_id)}</td>
    <td><b>${esc(j.name)}</b></td>
    <td>${j.schedule
      ? (j.paused
        ? `<span class="chip chip-paused">PAUSED</span> <span class="mono mut" style="font-size:10px">${esc(j.schedule)}</span>`
        : `<span class="chip chip-sched">SCHEDULED</span> <span class="mono mut" style="font-size:10px">${esc(j.schedule)}</span>`)
      : '<span class="chip chip-nosched">Manual</span>'}</td>
    <td class="mono mut">${esc(j.timezone)}</td>
  </tr>`).join('') || '<tr><td colspan="4" class="mut" style="padding:12px">No jobs configured.</td></tr>';
  document.getElementById('job-count').textContent = `${jobs.length}`;
}

// ── REPOS ─────────────────────────────────────────────────────────────────────
function renderRepos(ws){
  const q = (document.getElementById('repo-search')?.value||'').toLowerCase();
  document.getElementById('repo-search').oninput = ()=>renderRepos(ws);

  // group by repo_name
  const groups = {};
  ws.repos.forEach(r=>{
    const k = r.repo_name || '(no url)';
    if(!groups[k]) groups[k]=[];
    groups[k].push(r);
  });

  let html = '';
  const sorted = Object.entries(groups).sort((a,b)=>b[1].length-a[1].length);
  let shown = 0;
  for(const [name, clones] of sorted){
    if(q && !name.toLowerCase().includes(q) && !clones.some(c=>c.branch.toLowerCase().includes(q)||c.url.toLowerCase().includes(q))) continue;
    shown++;
    const gid = 'rg-'+name.replace(/[^a-zA-Z0-9]/g,'-');
    html += `<div class="repo-group">
      <div class="repo-group-hdr" onclick="toggleGroup('${gid}')">
        <span><b>${esc(name)}</b> <span class="mut" style="font-weight:400;font-size:11px">${clones.length} clone${clones.length!==1?'s':''}</span></span>
        <span class="mut" style="font-size:11px">▶</span>
      </div>
      <div class="repo-group-body" id="${gid}">
        ${clones.map(c=>`<div class="repo-row">
          <span class="chip chip-classic" style="font-size:9px">${esc(c.branch||'no branch')}</span>
          <span class="mono mut" style="font-size:10px;flex:1;overflow:hidden;text-overflow:ellipsis">${esc(c.url||'(no url)')}</span>
        </div>`).join('')}
      </div>
    </div>`;
  }
  document.getElementById('repo-groups').innerHTML = html || '<div class="mut" style="padding:12px">No repos.</div>';
  document.getElementById('repo-count').textContent = `${shown} repo${shown!==1?'s':''} (${ws.repos.length} total clones)`;
}
function toggleGroup(id){
  const el = document.getElementById(id);
  const tog = el.previousElementSibling.querySelector('span:last-child');
  el.classList.toggle('open');
  if(tog) tog.textContent = el.classList.contains('open') ? '▼' : '▶';
}

// ── WAREHOUSES ─────────────────────────────────────────────────────────────────
function renderWarehouses(ws){
  document.getElementById('wh-tbody').innerHTML = ws.warehouses.map(w=>`<tr>
    <td><b>${esc(w.name)}</b></td>
    <td>${stateChip(w.state)}</td>
    <td>${whTypeChip(w.type)}</td>
    <td class="mut">${esc(w.size)}</td>
    <td class="mut">${w.auto_stop ? w.auto_stop+'m' : '—'}</td>
  </tr>`).join('') || '<tr><td colspan="5" class="mut" style="padding:12px">No SQL warehouses.</td></tr>';
}

// ── INSTANCE POOLS ────────────────────────────────────────────────────────────
function renderPools(ws){
  document.getElementById('pools-tbody').innerHTML = ws.pools.map(p=>`<tr>
    <td><b>${esc(p.name)}</b></td>
    <td>${stateChip(p.state)}</td>
    <td class="mono mut">${esc(p.node_type)}</td>
    <td class="mut">${p.min_idle}</td>
    <td class="mut">${p.max_capacity != null ? p.max_capacity : '—'}</td>
  </tr>`).join('') || '<tr><td colspan="5" class="mut" style="padding:12px">No instance pools.</td></tr>';
}

// ── DLT PIPELINES ─────────────────────────────────────────────────────────────
function renderPipelines(ws){
  const q = (document.getElementById('pipeline-search')?.value||'').toLowerCase();
  document.getElementById('pipeline-search').oninput = ()=>renderPipelines(ws);
  let pipelines = ws.pipelines;
  if(q) pipelines = pipelines.filter(p=>p.name.toLowerCase().includes(q)||(p.target||'').toLowerCase().includes(q));
  document.getElementById('pipeline-tbody').innerHTML = pipelines.map(p=>`<tr>
    <td><b>${esc(p.name)}</b></td>
    <td>${stateChip(p.state)}</td>
    <td class="mono mut">${esc(p.target)||'—'}</td>
    <td>${p.continuous
      ? '<span class="chip st-running">Continuous</span>'
      : '<span class="chip chip-nosched">Triggered</span>'}
      ${p.development ? '<span class="chip chip-paused" style="margin-left:4px">Dev</span>' : ''}
    </td>
  </tr>`).join('') || '<tr><td colspan="4" class="mut" style="padding:12px">No DLT pipelines.</td></tr>';
  document.getElementById('pipeline-count').textContent = `${pipelines.length} of ${ws.pipelines.length}`;
}

// ── SQL QUERIES ───────────────────────────────────────────────────────────────
function renderQueries(ws){
  const q = (document.getElementById('query-search')?.value||'').toLowerCase();
  document.getElementById('query-search').oninput = ()=>renderQueries(ws);
  let queries = ws.queries;
  if(q) queries = queries.filter(x=>x.name.toLowerCase().includes(q)||(x.description||'').toLowerCase().includes(q));
  document.getElementById('query-tbody').innerHTML = queries.map(x=>`<tr>
    <td><b>${esc(x.name)}</b>${x.description?`<br><span class="mut" style="font-size:10px">${esc(x.description)}</span>`:''}</td>
    <td>${(x.tags||[]).map(t=>`<span class="chip chip-classic" style="margin:1px">${esc(t)}</span>`).join('')||'<span class="mut">—</span>'}</td>
    <td class="mut" style="white-space:nowrap;font-size:11px">${esc((x.updated_at||'').slice(0,10))}</td>
  </tr>`).join('') || '<tr><td colspan="3" class="mut" style="padding:12px">No saved queries.</td></tr>';
  document.getElementById('query-count').textContent = `${queries.length} of ${ws.queries.length}`;
}

// ── SQL DASHBOARDS ────────────────────────────────────────────────────────────
function renderDashboards(ws){
  const q = (document.getElementById('dashboard-search')?.value||'').toLowerCase();
  document.getElementById('dashboard-search').oninput = ()=>renderDashboards(ws);
  let dashboards = ws.dashboards;
  if(q) dashboards = dashboards.filter(d=>d.name.toLowerCase().includes(q)||(d.slug||'').toLowerCase().includes(q));
  document.getElementById('dashboard-tbody').innerHTML = dashboards.map(d=>`<tr>
    <td><b>${esc(d.name)}</b></td>
    <td class="mono mut" style="font-size:11px">${esc(d.slug)||'—'}</td>
    <td>${(d.tags||[]).map(t=>`<span class="chip chip-classic" style="margin:1px">${esc(t)}</span>`).join('')||'<span class="mut">—</span>'}</td>
    <td class="mut" style="white-space:nowrap;font-size:11px">${esc((d.updated_at||'').slice(0,10))}</td>
  </tr>`).join('') || '<tr><td colspan="4" class="mut" style="padding:12px">No dashboards.</td></tr>';
  document.getElementById('dashboard-count').textContent = `${dashboards.length} of ${ws.dashboards.length}`;
}

// ── APPS ──────────────────────────────────────────────────────────────────────
function renderApps(ws){
  document.getElementById('apps-tbody').innerHTML = ws.apps.map(a=>{
    const running = a.state === 'RUNNING';
    const chip = running
      ? '<span class="chip st-running">RUNNING</span>'
      : `<span class="chip st-error">${esc(a.state||'UNKNOWN')}</span>`;
    const link = a.url
      ? `<a href="${esc(a.url)}" target="_blank" style="color:var(--acc);font-size:11px">${esc(a.url)}</a>`
      : '<span class="mut">—</span>';
    return `<tr>
    <td><b>${esc(a.name)}</b>${a.description ? `<div class="mut" style="font-size:11px">${esc(a.description)}</div>` : ''}</td>
    <td>${chip}${a.message ? `<div class="mut" style="font-size:10px;margin-top:3px">${esc(a.message)}</div>` : ''}</td>
    <td>${link}</td>
    <td class="mut" style="font-size:11px">${esc(a.creator||'—')}</td>
    <td class="mut" style="white-space:nowrap;font-size:11px">${esc((a.update_time||'').slice(0,10))}</td>
  </tr>`;
  }).join('') || '<tr><td colspan="5" class="mut" style="padding:12px">No apps deployed.</td></tr>';
}

// ── MODEL SERVING ─────────────────────────────────────────────────────────────
function renderServing(ws){
  document.getElementById('serving-tbody').innerHTML = ws.serving.map(e=>`<tr>
    <td><b>${esc(e.name)}</b></td>
    <td>${e.ready==='READY'
      ? '<span class="chip st-running">READY</span>'
      : `<span class="chip st-error">${esc(e.ready||'UNKNOWN')}</span>`}</td>
  </tr>`).join('') || '<tr><td colspan="2" class="mut" style="padding:12px">No serving endpoints.</td></tr>';
}

// ── POLICIES ──────────────────────────────────────────────────────────────────
function renderPolicies(ws){
  document.getElementById('policy-tbody').innerHTML = ws.policies.map(p=>`<tr>
    <td><b>${esc(p.name)}</b></td>
    <td class="mut">${esc(p.description)}</td>
  </tr>`).join('') || '<tr><td colspan="2" class="mut" style="padding:12px">No policies.</td></tr>';
}

// ── SECRET SCOPES ─────────────────────────────────────────────────────────────
function renderScopes(ws){
  document.getElementById('scopes-list').innerHTML = ws.secret_scopes.length
    ? ws.secret_scopes.map(s=>`<div class="scope-pill">${esc(s)}</div>`).join('')
    : '<span class="mut">No secret scopes.</span>';
}

// ── NOTEBOOKS ─────────────────────────────────────────────────────────────────
function langChip(lang){
  const l = (lang||'').toUpperCase();
  if(l==='PYTHON') return '<span class="chip lang-py">PY</span>';
  if(l==='SQL')    return '<span class="chip lang-sql">SQL</span>';
  if(l==='SCALA')  return '<span class="chip lang-sc">SCALA</span>';
  if(l==='R')      return '<span class="chip lang-r">R</span>';
  return `<span class="chip chip-nosched">${esc(l||'?')}</span>`;
}
function renderNotebooks(ws){
  const q = (document.getElementById('nb-search')?.value||'').toLowerCase();
  document.getElementById('nb-search').oninput = ()=>renderNotebooks(ws);
  const lang = document.getElementById('nb-lang')?.value||'';
  document.getElementById('nb-lang').onchange = ()=>renderNotebooks(ws);
  let nbs = ws.notebooks;
  if(q)    nbs = nbs.filter(n=>n.path.toLowerCase().includes(q)||n.name.toLowerCase().includes(q)||(n.owner||'').toLowerCase().includes(q));
  if(lang) nbs = nbs.filter(n=>n.language===lang);
  const sorted = [...nbs].sort((a,b)=>(b.modified_at||0)-(a.modified_at||0));
  document.getElementById('nb-tbody').innerHTML = sorted.map(n=>`<tr>
    <td>${langChip(n.language)}</td>
    <td><b>${esc(n.name)}</b><br><span class="mono mut" style="font-size:10px">${esc(n.path)}</span></td>
    <td class="mut" style="font-size:11px">${esc(n.owner||'—')}</td>
    <td class="mut" style="font-size:11px;white-space:nowrap">${n.modified_at?new Date(n.modified_at).toLocaleDateString():'—'}</td>
  </tr>`).join('')||'<tr><td colspan="4" class="mut" style="padding:12px">No notebooks found.</td></tr>';
  document.getElementById('nb-count').textContent = `${sorted.length} of ${ws.notebooks.length}`;
}

// ── LAKEVIEW DASHBOARDS ───────────────────────────────────────────────────────
function lvStateChip(s){
  if(s==='ACTIVE')    return '<span class="chip st-running">ACTIVE</span>';
  if(s==='TRASHED')   return '<span class="chip st-error">TRASHED</span>';
  if(s==='DRAFT')     return '<span class="chip chip-paused">DRAFT</span>';
  return `<span class="chip chip-nosched">${esc(s||'—')}</span>`;
}
function renderLakeview(ws){
  const q = (document.getElementById('lv-search')?.value||'').toLowerCase();
  document.getElementById('lv-search').oninput = ()=>renderLakeview(ws);
  let dashboards = ws.lakeview_dashboards;
  if(q) dashboards = dashboards.filter(d=>d.display_name.toLowerCase().includes(q)||(d.parent_path||'').toLowerCase().includes(q));
  document.getElementById('lv-tbody').innerHTML = dashboards.map(d=>`<tr>
    <td><b>${esc(d.display_name)}</b></td>
    <td>${lvStateChip(d.lifecycle_state)}</td>
    <td class="mono mut" style="font-size:11px">${esc(d.parent_path)||'—'}</td>
    <td class="mut" style="font-size:11px;white-space:nowrap">${d.update_time?new Date(d.update_time).toLocaleDateString():'—'}</td>
  </tr>`).join('')||'<tr><td colspan="4" class="mut" style="padding:12px">No Lakeview dashboards.</td></tr>';
  document.getElementById('lv-count').textContent = `${dashboards.length} of ${ws.lakeview_dashboards.length}`;
}

// ── SQL ALERTS ────────────────────────────────────────────────────────────────
function alertStateChip(s){
  if(s==='ok')        return '<span class="chip st-running">OK</span>';
  if(s==='triggered') return '<span class="chip st-error">TRIGGERED</span>';
  if(s==='unknown')   return '<span class="chip chip-nosched">UNKNOWN</span>';
  return `<span class="chip chip-nosched">${esc(s||'—')}</span>`;
}
function renderAlerts(ws){
  const q = (document.getElementById('alert-search')?.value||'').toLowerCase();
  document.getElementById('alert-search').oninput = ()=>renderAlerts(ws);
  let alerts = ws.alerts;
  if(q) alerts = alerts.filter(a=>a.name.toLowerCase().includes(q)||(a.query_name||'').toLowerCase().includes(q));
  document.getElementById('alert-tbody').innerHTML = alerts.map(a=>`<tr>
    <td><b>${esc(a.name)}</b></td>
    <td>${alertStateChip(a.state)}</td>
    <td class="mut">${esc(a.query_name)||'—'}</td>
    <td class="mono mut" style="font-size:11px">${esc(a.condition)}</td>
    <td class="mut" style="font-size:11px;white-space:nowrap">${a.rearm!=null?a.rearm+'s rearm':'—'}</td>
    <td class="mut" style="font-size:11px;white-space:nowrap">${a.updated_at?(a.updated_at+'').slice(0,10):'—'}</td>
  </tr>`).join('')||'<tr><td colspan="6" class="mut" style="padding:12px">No alerts configured.</td></tr>';
  document.getElementById('alert-count').textContent = `${alerts.length} of ${ws.alerts.length}`;
}

// ── UNITY CATALOG ─────────────────────────────────────────────────────────────
function tableTypeChip(t){
  if(t==='MANAGED')           return '<span class="chip chip-classic">MANAGED</span>';
  if(t==='EXTERNAL')          return '<span class="chip chip-sched">EXTERNAL</span>';
  if(t==='VIEW')              return '<span class="chip chip-pro">VIEW</span>';
  if(t==='MATERIALIZED_VIEW') return '<span class="chip st-running">MAT VIEW</span>';
  if(t==='STREAMING_TABLE')   return '<span class="chip chip-paused">STREAM</span>';
  return `<span class="chip chip-nosched">${esc(t||'—')}</span>`;
}

function toggleUC(id){
  const el = document.getElementById(id);
  el.classList.toggle('open');
  const tog = el.previousElementSibling.querySelector('.uc-tog');
  if(tog) tog.textContent = el.classList.contains('open') ? '▼' : '▶';
}

function renderUC(ws){
  const uc = ws.unity_catalog;
  if(!uc || !uc.catalogs.length){
    document.getElementById('uc-tree').innerHTML = '<div class="mut" style="padding:12px">Unity Catalog not available for this workspace.</div>';
    document.getElementById('uc-ext-locs').innerHTML = '';
    return;
  }
  const q = (document.getElementById('uc-search')?.value||'').toLowerCase();
  document.getElementById('uc-search').oninput = ()=>renderUC(ws);

  let totalTables = 0;
  let html = '';
  for(const cat of uc.catalogs){
    const catTables = cat.schemas.reduce((a,s)=>a+s.tables.length,0);
    totalTables += catTables;
    const matchesCat = !q || cat.name.toLowerCase().includes(q) ||
      cat.schemas.some(s=>s.name.toLowerCase().includes(q)||s.tables.some(t=>t.name.toLowerCase().includes(q)));
    if(!matchesCat) continue;
    const catId = 'uc-c-'+cat.name.replace(/[^a-zA-Z0-9]/g,'-');
    html += `<div class="uc-catalog">
      <div class="uc-cat-hdr" onclick="toggleUC('${catId}')">
        <span><span class="chip chip-pro" style="font-size:9px;margin-right:6px">CATALOG</span>
          <b>${esc(cat.name)}</b>${cat.comment?` <span class="mut" style="font-size:10px">${esc(cat.comment)}</span>`:''}
          ${cat.owner?`<span class="mut" style="font-size:10px;margin-left:8px">owner: ${esc(cat.owner)}</span>`:''}
        </span>
        <span class="mut" style="font-size:11px">${cat.schemas.length} schemas · ${catTables} tables &nbsp;<span class="uc-tog">▶</span></span>
      </div>
      <div class="uc-cat-body" id="${catId}">
        ${cat.schemas.map(sch=>{
          const schId = catId+'-'+sch.name.replace(/[^a-zA-Z0-9]/g,'-');
          const matchesSch = !q || sch.name.toLowerCase().includes(q)||sch.tables.some(t=>t.name.toLowerCase().includes(q));
          if(!matchesSch) return '';
          return `<div class="uc-schema">
            <div class="uc-sch-hdr" onclick="toggleUC('${schId}')">
              <span><span class="chip chip-classic" style="font-size:9px;margin-right:6px">SCHEMA</span>
                <b>${esc(sch.name)}</b>${sch.comment?` <span class="mut" style="font-size:10px">${esc(sch.comment)}</span>`:''}
              </span>
              <span class="mut" style="font-size:11px">${sch.tables.length} tables &nbsp;<span class="uc-tog">▶</span></span>
            </div>
            <div class="uc-sch-body" id="${schId}">
              ${sch.tables.length?`<table style="margin:0">
                <thead><tr><th>Table</th><th>Type</th><th>Columns</th><th>Owner</th><th>Updated</th></tr></thead>
                <tbody>${sch.tables
                  .filter(t=>!q||t.name.toLowerCase().includes(q))
                  .map(t=>`<tr>
                  <td><b>${esc(t.name)}</b>${t.comment?`<br><span class="mut" style="font-size:10px">${esc(t.comment)}</span>`:''}</td>
                  <td>${tableTypeChip(t.table_type)}</td>
                  <td class="mut">${t.columns||'—'}</td>
                  <td class="mono mut" style="font-size:11px">${esc(t.owner)||'—'}</td>
                  <td class="mut" style="font-size:11px;white-space:nowrap">${t.updated_at?new Date(t.updated_at).toLocaleDateString():'—'}</td>
                </tr>`).join('')}</tbody>
              </table>`:'<div class="mut" style="padding:8px 14px;font-size:11px">No tables.</div>'}
            </div>
          </div>`;
        }).join('')}
      </div>
    </div>`;
  }
  document.getElementById('uc-tree').innerHTML = html||'<div class="mut" style="padding:12px">No matching catalogs.</div>';
  document.getElementById('uc-summary').textContent = `${uc.catalogs.length} catalogs · ${totalTables} tables`;

  document.getElementById('uc-ext-locs').innerHTML = uc.external_locations.length
    ? `<table><thead><tr><th>Name</th><th>URL</th><th>Credential</th><th>Owner</th></tr></thead><tbody>
        ${uc.external_locations.map(e=>`<tr>
          <td><b>${esc(e.name)}</b></td>
          <td class="mono mut" style="font-size:11px;word-break:break-all">${esc(e.url)}</td>
          <td class="mut">${esc(e.credential_name)}</td>
          <td class="mut">${esc(e.owner)}</td>
        </tr>`).join('')}
      </tbody></table>`
    : '<div class="mut" style="font-size:11px">No external locations.</div>';
}

document.addEventListener('DOMContentLoaded',()=>{
  selectWS(WORKSPACES[0].key);
  showTab('overview');
});
"""

# ── HTML ───────────────────────────────────────────────────────────────────────

WS_COLORS = ["#6c8eff", "#fb923c", "#4ade80"]

def build_html(workspaces_data, generated):
    data_json = json.dumps(workspaces_data, ensure_ascii=False, default=str)
    js = JS.replace("__WORKSPACES__", data_json)

    first = workspaces_data[0]

    # sidebar items
    sb_items = ""
    for i, ws in enumerate(workspaces_data):
        color = WS_COLORS[i % len(WS_COLORS)]
        sb_items += (
            f'<div class="sb-item" id="sb-{ws["key"]}" onclick="selectWS(\'{ws["key"]}\')">'
            f'<div class="ws-dot" style="background:{color}"></div>'
            f'{ws["label"]}</div>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>Databricks Metadata</title>
<style>{CSS}.help-fab{{position:fixed;bottom:22px;right:22px;width:38px;height:38px;border-radius:50%;background:var(--acc);color:#fff;font-size:17px;font-weight:700;display:flex;align-items:center;justify-content:center;text-decoration:none;z-index:9999;box-shadow:0 2px 10px rgba(0,0,0,.5);opacity:.8;transition:opacity .15s;line-height:1}}.help-fab:hover{{opacity:1}}</style>
</head>
<body>
<a href="help.html" class="help-fab" title="Help &amp; Guide">?</a>
<div class="layout">

<!-- SIDEBAR -->
<div class="sidebar">
  <div class="sb-hdr">Databricks<small>3 Workspaces</small></div>
  <div class="sb-body">
    <div class="sb-section">Workspace</div>
    {sb_items}
  </div>
</div>

<!-- MAIN -->
<div class="main">
  <div class="main-hdr">
    <h1 id="ws-header-name">Azure Databricks — {first["label"]}</h1>
    <p class="sub" id="ws-header-sub">{first["resource_name"]} · {first["url"]}</p>
    <p class="sub">Generated: <span id="gen-ts" data-ts="{generated}">&#x21BB; {generated}</span><script>(function(){{var s=document.getElementById(\'gen-ts\'),h=(Date.now()-new Date(s.dataset.ts.replace(\' \',\'T\')))/36e5;s.style.color=h<25?'var(--grn)':h<168?'var(--yel)':'var(--red)';s.style.fontWeight='700';}})();</script></p>

    <div class="stats">
      <div class="sc" onclick="showTab('clusters')" title="Interactive and job clusters. Interactive clusters are long-running and shared for notebook development. Job clusters are ephemeral — spun up for a specific job run and terminated when done. Fewer running clusters = lower cost.">
        <div class="sc-n" id="stat-clusters" style="color:var(--acc)">—</div>
        <div class="sc-l">Clusters</div></div>
      <div class="sc" onclick="showTab('jobs')" title="Databricks Jobs are scheduled or triggered automation tasks that run notebooks, Python scripts, JARs, or Delta Live Tables pipelines on a cluster. Jobs replace manual notebook execution for production workloads.">
        <div class="sc-n" id="stat-jobs" style="color:var(--pur)">—</div>
        <div class="sc-l">Jobs</div></div>
      <div class="sc" onclick="showTab('repos')" title="Databricks Repos sync notebooks and code files directly from a Git repository (Azure DevOps, GitHub, etc.). They enable version control, branching, and CI/CD for notebook-based workflows.">
        <div class="sc-n" id="stat-repos" style="color:var(--cyn)">—</div>
        <div class="sc-l">Repos</div></div>
      <div class="sc" onclick="showTab('warehouses')" title="SQL Warehouses (formerly SQL Endpoints) are compute clusters optimized for SQL analytics. They power Databricks SQL queries, dashboards, and BI tool connections (Power BI, Tableau). Separate from interactive clusters.">
        <div class="sc-n" id="stat-warehouses" style="color:var(--grn)">—</div>
        <div class="sc-l">SQL Warehouses</div></div>
      <div class="sc" onclick="showTab('pools')" title="Instance Pools pre-provision and hold a set of idle VM instances so that clusters can start faster. Instead of waiting for Azure to provision new VMs, clusters from a pool attach to pre-warmed instances, reducing start time from minutes to seconds.">
        <div class="sc-n" id="stat-pools" style="color:var(--org)">—</div>
        <div class="sc-l">Inst. Pools</div></div>
      <div class="sc" onclick="showTab('pipelines')" title="Delta Live Tables (DLT) Pipelines declare data transformation logic as a graph of tables. Databricks manages the execution order, incremental updates, data quality checks, and retry logic automatically. A higher-level alternative to hand-written Spark ETL.">
        <div class="sc-n" id="stat-pipelines" style="color:var(--yel)">—</div>
        <div class="sc-l">DLT Pipelines</div></div>
      <div class="sc" onclick="showTab('queries')" title="Saved SQL queries written in the Databricks SQL editor and run against SQL Warehouses. These can be parameterized, scheduled, and used as data sources for dashboards and alerts.">
        <div class="sc-n" id="stat-queries" style="color:var(--pur)">—</div>
        <div class="sc-l">SQL Queries</div></div>
      <div class="sc" onclick="showTab('dashboards')" title="Legacy Databricks SQL Dashboards built from saved queries using the classic dashboard builder. These are separate from the newer Lakeview (AI/BI) dashboards. See the Lakeview card for the modern equivalent.">
        <div class="sc-n" id="stat-dashboards" style="color:var(--cyn)">—</div>
        <div class="sc-l">Dashboards</div></div>
      <div class="sc" onclick="showTab('apps')" title="Databricks Apps are lightweight web applications hosted directly on the Databricks platform with Entra ID SSO. They run FastAPI, Streamlit, Gradio, or custom Python servers and have access to workspace resources.">
        <div class="sc-n" id="stat-apps" style="color:var(--acc)">—</div>
        <div class="sc-l">Apps</div></div>
      <div class="sc" onclick="showTab('serving')" title="Model Serving endpoints host trained ML models (MLflow, Feature Store, or external) as REST APIs for real-time inference. Each endpoint auto-scales compute independently and can serve multiple model versions simultaneously.">
        <div class="sc-n" id="stat-serving" style="color:var(--red)">—</div>
        <div class="sc-l">Model Serving</div></div>
      <div class="sc" onclick="showTab('uc')" title="Unity Catalog is Databricks' centralized governance layer for data and AI assets. It provides a three-level namespace (catalog → schema → table), fine-grained access control, data lineage, and auditing across all workspaces. Only available on workspaces with Unity Catalog enabled.">
        <div class="sc-n" id="stat-uc" style="color:var(--grn)">—</div>
        <div class="sc-l">UC Tables</div></div>
      <div class="sc" onclick="showTab('notebooks')" title="Notebooks are the primary development interface in Databricks — interactive documents that mix code (Python, SQL, Scala, R), markdown, and output. Notebooks in the workspace (outside /Repos) are not version-controlled unless manually exported.">
        <div class="sc-n" id="stat-notebooks" style="color:var(--yel)">—</div>
        <div class="sc-l">Notebooks</div></div>
      <div class="sc" onclick="showTab('lakeview')" title="Lakeview (AI/BI) Dashboards are the modern Databricks dashboard experience, replacing the legacy SQL Dashboard builder. They support richer visualizations, natural language querying, and are the recommended format for new dashboards going forward.">
        <div class="sc-n" id="stat-lakeview" style="color:var(--cyn)">—</div>
        <div class="sc-l">Lakeview</div></div>
      <div class="sc" onclick="showTab('alerts')" title="SQL Alerts run a saved query on a schedule and notify you (email, webhook, Slack) when the result meets a condition (e.g. row count &gt; 0, value exceeds a threshold). Useful for data quality monitoring and SLA tracking. Currently 0 alerts configured across all workspaces.">
        <div class="sc-n" id="stat-alerts" style="color:var(--red)">—</div>
        <div class="sc-l">SQL Alerts</div></div>
    </div>
  </div>

  <div class="tabs">
    <div class="tab" id="tab-overview"    onclick="showTab('overview')">Overview</div>
    <div class="tab" id="tab-clusters"    onclick="showTab('clusters')">Clusters</div>
    <div class="tab" id="tab-jobs"        onclick="showTab('jobs')">Jobs</div>
    <div class="tab" id="tab-repos"       onclick="showTab('repos')">Repos</div>
    <div class="tab" id="tab-warehouses"  onclick="showTab('warehouses')">SQL Warehouses</div>
    <div class="tab" id="tab-pools"       onclick="showTab('pools')">Instance Pools</div>
    <div class="tab" id="tab-pipelines"   onclick="showTab('pipelines')">DLT Pipelines</div>
    <div class="tab" id="tab-queries"     onclick="showTab('queries')">SQL Queries</div>
    <div class="tab" id="tab-dashboards"  onclick="showTab('dashboards')">SQL Dashboards</div>
    <div class="tab" id="tab-apps"        onclick="showTab('apps')">Apps</div>
    <div class="tab" id="tab-serving"     onclick="showTab('serving')">Model Serving</div>
    <div class="tab" id="tab-policies"    onclick="showTab('policies')">Cluster Policies</div>
    <div class="tab" id="tab-scopes"      onclick="showTab('scopes')">Secret Scopes</div>
    <div class="tab" id="tab-uc"          onclick="showTab('uc')">Unity Catalog</div>
    <div class="tab" id="tab-notebooks"   onclick="showTab('notebooks')">Notebooks</div>
    <div class="tab" id="tab-lakeview"    onclick="showTab('lakeview')">Lakeview</div>
    <div class="tab" id="tab-alerts"      onclick="showTab('alerts')">SQL Alerts</div>
  </div>

  <div class="content">

    <!-- OVERVIEW -->
    <div class="panel" id="p-overview">
      <div class="ov-grid">
        <div class="ov-card">
          <h3>Clusters by State</h3>
          <div id="ov-clusters"></div>
        </div>
        <div class="ov-card">
          <h3>SQL Warehouses</h3>
          <div id="ov-warehouses"></div>
        </div>
        <div class="ov-card">
          <h3>Repos</h3>
          <div id="ov-repos"></div>
        </div>
        <div class="ov-card">
          <h3>Secret Scopes</h3>
          <div id="ov-scopes"></div>
        </div>
      </div>
      <h2>Workspace Root</h2>
      <div id="ov-ws-dirs"></div>
    </div>

    <!-- CLUSTERS -->
    <div class="panel" id="p-clusters">
      <div class="filter-row">
        <input id="cluster-search" placeholder="Search clusters…"/>
        <span id="cluster-count" class="mut"></span>
      </div>
      <table>
        <thead><tr>
          <th>Cluster Name</th><th>State</th><th>Spark Version</th>
          <th>Node Type</th><th>Auto-Terminate</th><th>Workers</th>
        </tr></thead>
        <tbody id="cluster-tbody"></tbody>
      </table>
    </div>

    <!-- JOBS -->
    <div class="panel" id="p-jobs">
      <div class="filter-row">
        <input id="job-search" placeholder="Search jobs…"/>
        <span id="job-count" class="mut"></span>
      </div>
      <table>
        <thead><tr>
          <th>Job ID</th><th>Name</th><th>Schedule</th><th>Timezone</th>
        </tr></thead>
        <tbody id="job-tbody"></tbody>
      </table>
    </div>

    <!-- REPOS -->
    <div class="panel" id="p-repos">
      <div class="filter-row">
        <input id="repo-search" placeholder="Search repos or branches…"/>
        <span id="repo-count" class="mut"></span>
      </div>
      <div id="repo-groups"></div>
    </div>

    <!-- WAREHOUSES -->
    <div class="panel" id="p-warehouses">
      <table>
        <thead><tr>
          <th>Name</th><th>State</th><th>Type</th><th>Size</th><th>Auto-Stop</th>
        </tr></thead>
        <tbody id="wh-tbody"></tbody>
      </table>
    </div>

    <!-- INSTANCE POOLS -->
    <div class="panel" id="p-pools">
      <table>
        <thead><tr>
          <th>Pool Name</th><th>State</th><th>Node Type</th><th>Min Idle</th><th>Max Capacity</th>
        </tr></thead>
        <tbody id="pools-tbody"></tbody>
      </table>
    </div>

    <!-- DLT PIPELINES -->
    <div class="panel" id="p-pipelines">
      <div class="filter-row">
        <input id="pipeline-search" placeholder="Search pipelines…"/>
        <span id="pipeline-count" class="mut"></span>
      </div>
      <table>
        <thead><tr>
          <th>Name</th><th>State</th><th>Target Schema</th><th>Mode</th>
        </tr></thead>
        <tbody id="pipeline-tbody"></tbody>
      </table>
    </div>

    <!-- SQL QUERIES -->
    <div class="panel" id="p-queries">
      <div class="filter-row">
        <input id="query-search" placeholder="Search queries…"/>
        <span id="query-count" class="mut"></span>
      </div>
      <table>
        <thead><tr>
          <th>Name</th><th>Tags</th><th>Last Updated</th>
        </tr></thead>
        <tbody id="query-tbody"></tbody>
      </table>
    </div>

    <!-- SQL DASHBOARDS -->
    <div class="panel" id="p-dashboards">
      <div class="filter-row">
        <input id="dashboard-search" placeholder="Search dashboards…"/>
        <span id="dashboard-count" class="mut"></span>
      </div>
      <table>
        <thead><tr>
          <th>Name</th><th>Slug</th><th>Tags</th><th>Last Updated</th>
        </tr></thead>
        <tbody id="dashboard-tbody"></tbody>
      </table>
    </div>

    <!-- APPS -->
    <div class="panel" id="p-apps">
      <table>
        <thead><tr><th>Name / Description</th><th>State</th><th>URL</th><th>Creator</th><th>Updated</th></tr></thead>
        <tbody id="apps-tbody"></tbody>
      </table>
    </div>

    <!-- MODEL SERVING -->
    <div class="panel" id="p-serving">
      <table>
        <thead><tr><th>Endpoint Name</th><th>Ready</th></tr></thead>
        <tbody id="serving-tbody"></tbody>
      </table>
    </div>

    <!-- POLICIES -->
    <div class="panel" id="p-policies">
      <table>
        <thead><tr><th>Policy Name</th><th>Description</th></tr></thead>
        <tbody id="policy-tbody"></tbody>
      </table>
    </div>

    <!-- SECRET SCOPES -->
    <div class="panel" id="p-scopes">
      <p class="mut" style="font-size:11px;margin-bottom:10px">
        Scope names only — keys and values are never collected.
      </p>
      <div class="scope-list" id="scopes-list"></div>
    </div>

    <!-- UNITY CATALOG -->
    <div class="panel" id="p-uc">
      <div class="filter-row">
        <input id="uc-search" placeholder="Search catalogs, schemas, tables…"/>
        <span id="uc-summary" class="mut"></span>
      </div>
      <div id="uc-tree"></div>
      <h2 class="uc-section-hdr" style="margin-top:18px">External Locations</h2>
      <div id="uc-ext-locs"></div>
    </div>

    <!-- NOTEBOOKS -->
    <div class="panel" id="p-notebooks">
      <div class="filter-row">
        <input id="nb-search" placeholder="Search notebooks by name or path…"/>
        <select id="nb-lang">
          <option value="">All languages</option>
          <option value="PYTHON">Python</option>
          <option value="SQL">SQL</option>
          <option value="SCALA">Scala</option>
          <option value="R">R</option>
        </select>
        <span id="nb-count" class="mut"></span>
      </div>
      <p class="mut" style="font-size:11px;margin-bottom:8px">/Repos notebooks are excluded — see the Repos tab.</p>
      <table>
        <thead><tr><th>Lang</th><th>Name / Path</th><th>Owner</th><th>Last Modified</th></tr></thead>
        <tbody id="nb-tbody"></tbody>
      </table>
    </div>

    <!-- LAKEVIEW DASHBOARDS -->
    <div class="panel" id="p-lakeview">
      <div class="filter-row">
        <input id="lv-search" placeholder="Search Lakeview dashboards…"/>
        <span id="lv-count" class="mut"></span>
      </div>
      <table>
        <thead><tr><th>Name</th><th>State</th><th>Parent Path</th><th>Updated</th></tr></thead>
        <tbody id="lv-tbody"></tbody>
      </table>
    </div>

    <!-- SQL ALERTS -->
    <div class="panel" id="p-alerts">
      <div class="filter-row">
        <input id="alert-search" placeholder="Search alerts…"/>
        <span id="alert-count" class="mut"></span>
      </div>
      <table>
        <thead><tr><th>Alert Name</th><th>State</th><th>Linked Query</th><th>Condition</th><th>Rearm</th><th>Updated</th></tr></thead>
        <tbody id="alert-tbody"></tbody>
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
    print("\n=== Databricks Metadata Report ===")

    workspaces_data = collect()

    print("\nBuilding HTML…")
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    html = build_html(workspaces_data, generated)

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\nSaved: {OUT_FILE}")
    for ws in workspaces_data:
        print(f"  {ws['label']:8s}  clusters={len(ws['clusters'])}  jobs={len(ws['jobs'])}  "
              f"repos={len(ws['repos'])}  warehouses={len(ws['warehouses'])}")



    if not os.environ.get('PUBLISH_RUNNING'):
        try:
            import generate_metadata_index
            generate_metadata_index.main()
            print("  Index updated       : index.html")
        except Exception as exc:
            print(f"  Warning: could not update index.html: {exc}")

if __name__ == "__main__":
    main()
