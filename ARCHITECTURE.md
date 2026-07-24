# IDOH Metadata Marketplace — Architecture & Pipeline Documentation

**Last updated:** 2026-07-24  
**Author:** David Porter

---

## Overview

The Metadata Marketplace is a daily-automated reporting platform that collects metadata from 18+ Azure services across the IDOH Azure environment, generates self-contained HTML reports, publishes them to Azure Blob Storage, and serves them through a FastAPI Databricks App with Entra ID SSO. No VDI or VPN is required to access reports.

**App URL:** `https://idoh-metadata-marketplace-5757046586469840.0.azure.databricksapps.com`

---

## System Components

```
┌──────────────────────────────────────────────────────────────────┐
│  WSL2 / Debian (Windows Machine — daily cron at 2am)             │
│                                                                  │
│  publish_synapse_metadata.sh                                     │
│    │                                                             │
│    ├── 22 Python sub-scripts (report generators)                 │
│    │     └── resilient.py  (shared retry / backoff library)      │
│    │                                                             │
│    └── az storage blob upload  →  Azure Blob Storage ($web)      │
│                                                                  │
│  capture_help_screenshots.py  (monthly cron, 1st @ 6am)          │
│    └── Playwright/Chromium  →  Azure Blob Storage ($web/         │
│                                screenshots/)                     │
└──────────────────────────────────────────────────────────────────┘
                             │
                     Azure Blob Storage
                     ($web static site)
                             │
                    ┌────────┴────────┐
                    │                 │
            Direct blob URL     FastAPI Databricks App
                              (Entra ID SSO — no VDI)
                              idoh_metadata_marketplace/
                                app.py  (FastAPI)
                                reports/*.html.gz
                                screenshots/*.png
```

---

## 1. The Orchestrator — `publish_synapse_metadata.sh`

### Lock File
```bash
LOCKFILE=/tmp/publish_synapse_metadata.lock
```
Writes its own PID to the lock file. On next invocation, checks `kill -0 $pid` to detect a stale lock — if the PID is dead, clears the lock and proceeds. Prevents concurrent publishes from stomping each other.

### Environment
- `VENV="/home/thedavidporter/.venv/bin/python"` — isolated virtualenv so cron picks up the right packages (azure-identity, azure-mgmt-*, requests, pyodbc, playwright)
- `export PUBLISH_RUNNING=1` — sub-scripts check this env var and skip their own index updates when running inside the publish pipeline
- `STORAGE_ACCOUNT="zus1idohdevv2dbrkdl"`, `CONTAINER="\$web"` — all HTML uploads go here via `az storage blob upload --auth-mode login`

### Step 0 — In-Progress Banner
Before any report is generated, `index.html` is immediately regenerated with an in-progress banner and a `<meta http-equiv="refresh" content="30">` tag. Anyone hitting the site during the publish sees "Update in progress — X of 22 reports complete" with a live progress bar that auto-refreshes every 30 seconds. The refresh tag is absent from the final index so the page stops reloading when done.

### `run_step()` Function
For each of the 22 reports:
1. Runs the Python script, captures exit code
2. On success, uploads the HTML file to blob storage
3. Calls `advance_progress()` — regenerates the in-progress banner with the updated step count and re-uploads it

### `FAILED_STEPS` Array
All failures are collected without stopping the run. The script exits 1 at the end if any step failed, so the cron log captures the failure without aborting the remaining reports.

### Report Execution Order
```
Synapse DEV → Synapse PRD → Synapse DEV delta → Synapse PRD delta
→ ADLS → ADF DEV → ADF PRD
→ Logic Apps DEV → Logic Apps PRD
→ APIM DEV → APIM PRD
→ Key Vault DEV → Key Vault PRD
→ ADO → SQL DW DEV → SQL DW PRD → VNet → AVD
→ Data Catalog → Security Groups → Azure Cost
→ Databricks (last — timeout 900s, walks 3 workspaces)
→ Index (final — generates the nav grid)
```

Databricks runs last because it is the slowest (parallel notebook tree walk across 3 workspaces, up to 15 minutes). The Synapse delta reports run immediately after their full counterparts so they can diff against freshly-written HTML.

### Databricks Deploy (end of script)
After all reports are generated:
1. All HTML files are gzip-compressed (`gzip -k`) so they stay under the Databricks Apps 10MB per-file limit
2. `databricks bundle deploy` syncs the compressed reports into the app bundle
3. `databricks apps deploy` pushes to the live app
4. Screenshots are synced from blob storage into the bundle's `screenshots/` directory before every deploy so the bundled copies stay current with the monthly refresh

---

## 2. Authentication — Three Patterns

All scripts run in the WSL2/Debian environment using your cached `az login` session (`~/.azure/`). Three auth patterns are in use:

### Pattern A — `az account get-access-token` (subprocess)
**Used by:** Synapse, ADLS, Databricks, ADO, Logic Apps, APIM, Key Vault, VNet

Shells out to `az` and captures the bearer token as a string:
```python
token = subprocess.check_output(
    ["az", "account", "get-access-token",
     "--resource", "<resource-uri>",
     "--query", "accessToken", "-o", "tsv"]
).decode().strip()
```

For **Synapse SQL** specifically, the token is packed into a struct for the ODBC driver:
```python
token_bytes  = token.encode("utf-16-le")
token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
conn_attrs   = {SQL_COPT_SS_ACCESS_TOKEN: token_struct}   # attribute 1256
conn = pyodbc.connect(conn_str, attrs_before=conn_attrs)
```
ODBC attribute `1256` (`SQL_COPT_SS_ACCESS_TOKEN`) tells the ODBC driver to use an Entra ID token instead of a password. The connection string has no credentials — just server name and database.

### Pattern B — `DefaultAzureCredential` (azure-identity SDK)
**Used by:** ADF, Security Groups (ARM calls)

```python
from azure.identity import DefaultAzureCredential
from azure.mgmt.datafactory import DataFactoryManagementClient

cred    = DefaultAzureCredential()   # falls through to CLI cache in WSL2
client  = DataFactoryManagementClient(cred, SUBSCRIPTION_ID)
pipelines = list(client.pipelines.list_by_factory(rg, factory_name))
```
The SDK handles token refresh transparently — when a token nears its 1-hour expiry it auto-renews without any script intervention.

### Pattern C — Microsoft Graph via `requests` + bearer token
**Used by:** Security Groups (group/member enumeration), ADO

Calls `az account get-access-token --resource https://graph.microsoft.com/` then uses `requests.Session` with `Authorization: Bearer <token>`. Pagination is handled manually by following `@odata.nextLink` until absent:
```python
while url:
    resp = session.get(url, headers=headers)
    items += resp.json().get("value", [])
    url   = resp.json().get("@odata.nextLink")
```

---

## 3. Retry Logic — `resilient.py`

All external API calls use the shared `resilient.py` library. The design is **linear backoff** (not exponential) for predictable, readable logs:

| Attempt | Wait |
|---|---|
| 1st retry | `backoff_base × 1` |
| 2nd retry | `backoff_base × 2` |
| 3rd retry | `backoff_base × 3` |

### Pre-built Policies

| Policy | max_retries | backoff_base | retry_status | Notes |
|---|---|---|---|---|
| `AZURE_POLICY` | 4 | 5s | 429, 502, 503 | + body-sniff for 200-OK error responses |
| `DATABRICKS_POLICY` | 4 | 8s | 429, 502, 503 | longer backoff for rate limits |
| `GRAPH_POLICY` | 4 | 6s | 429, 503 | Graph throttles aggressively on batch calls |
| `REDCAP_POLICY` | 3 | 3s | 429, 503 | REDCap is reliable; fewer retries |
| `DEFAULT_POLICY` | 4 | 5s | 429, 502, 503 | generic fallback |

### Azure Cost Management Special Case
`AZURE_POLICY` includes a `retry_if` callable (`_azure_body_retry`) that inspects 200-OK response bodies. Azure Cost Management (and occasionally other ARM APIs) returns HTTP 200 with an error body during transient RBAC flaps:
```python
def _azure_body_retry(resp):
    err  = resp.json().get("error", {})
    code = err.get("code", "")
    # retries on "RBACAccessDenied", "NoHttpContext", "IndirectCostDisabled", etc.
    return any(x in code for x in ("RBAC", "NoHttp", "Indirect", "BillingScope"))
```

### `retry_call()` — Non-HTTP SDK Calls
Wraps any callable (pyodbc queries, ARM SDK paginator iterations, subprocess calls) with the same retry logic but without HTTP response inspection — retries only on configured exception types.

---

## 4. The Sub-Scripts — What Each Collects

### `synapse_metadata_report_prd.py` / `_dev.py`
- Connects via `pyodbc` + Entra ID token to Synapse Dedicated SQL Pool
- Queries `sys.schemas`, `sys.objects WHERE type='U'`, `INFORMATION_SCHEMA.COLUMNS`
- Generates hierarchical HTML: schemas → tables → columns
- DDL generation: `sys.pdw_table_distribution_properties` + `sys.indexes` for DISTRIBUTION and index type
- Delta report: diffs current HTML against previous run to show changed/added/removed objects

### `adf_metadata_report.py`
- Uses `DataFactoryManagementClient` (azure-mgmt-datafactory)
- Collects: pipelines, triggers (schedule/recurrence/type), datasets, linked services, 30 days of run history
- Runs twice: `--env dev` and `--env prd`
- Produces 4-tab HTML: Pipelines · Datasets · Linked Services · Monitor

### `adls_metadata_report.py`
- `az storage account list` to find all HNS-enabled (ADLS Gen2) accounts
- Walks file systems using ADLS Gen2 REST API (`*.dfs.core.windows.net`)
- `MAX_DEPTH=6` directory levels, `MAX_PATHS=500` items per listing call
- Skips infrastructure containers: Databricks temp, ADF staged copy, audit logs

### `databricks_metadata_report.py`
- Covers 3 workspaces: IZ-DEV, DEV, PRD
- Token resource ID: `2ff814a6-3304-4ab8-85cb-cd0e6f879c1d` (Databricks)
- Parallel notebook walk: `ThreadPoolExecutor` for directory listing (prevents serial timeout)
- Collects: notebook tree, clusters, jobs, DBFS top-level, Unity Catalog, Lakeview dashboards, SQL Alerts, Apps
- Handles 400/404/429 per-directory without crashing the full walk

### `azure_security_groups_report.py`
- Enumerates all Azure role assignments across 7 subscriptions via ARM REST
- Filters to `Group` principal type
- Graph API to resolve group display names and membership (`/groups/{id}/members`)
- Databricks SCIM API to find users added directly without an Azure group
- `ThreadPoolExecutor` for parallel subscription enumeration and Graph calls
- Excel export via SheetJS (Members + Role Assignments sheets per group)

### `ado_metadata_report.py`
- ADO REST APIs under `dev.azure.com/in-idoh-oda`
- Repos, branches (ahead/behind via refs API), build pipelines, last 25 runs each
- Pull requests, branch policies, variable groups, environments
- Token via `az account get-access-token --resource 499b84ac-...` (ADO resource GUID)
- Active branch threshold: <60 days; stale: >180 days

### `keyvault_metadata_report.py`
- Lists secrets/keys/certs by name and metadata only — **never values**
- ARM REST for vault enumeration, Key Vault data-plane API for object listings

### `apim_metadata_report.py`
- Lists APIs, operations, subscription counts, backend URLs
- ARM management plane via azure-mgmt-apimanagement

### `logic_apps_metadata_report.py`
- Lists workflow definitions and trigger types (HTTP, recurrence, event)
- ARM REST for discovery, Logic Apps management API for definitions

### `avd_metadata_report.py`
- `azure.mgmt.desktopvirtualization` SDK
- 142 AVD host pools in ECAE Shared Production subscription
- Collects session hosts, assigned users, pool configs

### `generate_data_catalog.py`
- Reads `data_catalog_datasets.json` (34-dataset registry, maintained manually)
- Calls REDCap API for live submission counts
- Generates catalog HTML: search, domain chips (multi-select), dataset cards, access-request form
- REDCap form submission posts directly to IDOH's REDCap system

### `azure_cost_report.py`
- Azure Cost Management REST API (`/providers/Microsoft.CostManagement/query`)
- MTD spend, projected month-end, 6-month trend, spend by business category, savings opportunities
- Covers: ECAE IDOH Production + ECAE Shared Production (with `Agency-Name` tag filter for IDOH/ISDH share)
- Uses `resilient_post` with `AZURE_POLICY` (catches 200-OK error bodies)
- API calls interleaved with 3s sleeps to avoid RBACAccessDenied cascade under concurrent load

### `generate_metadata_index.py`
- Generates `index.html` (the nav grid) and calls `generate_help.py`
- `--running` flag embeds step count into in-progress banner
- Without flag: builds final nav grid with all report links and refresh timestamps
- Refresh age computed client-side at page load: green (<25h), yellow (<1 week), red (>1 week)

---

## 5. Screenshot Capture — `capture_help_screenshots.py`

A separate monthly cron job (1st of each month at 6am) captures viewport screenshots of each report and embeds them in the Help page Q&A answers.

### How It Works
1. Launches headless Playwright/Chromium at `VIEWPORT = {width: 1440, height: 860}`
2. Navigates to each report using `file://` URLs (local HTML files — no auth or network needed)
3. Waits `networkidle` + 2s additional settle time for JS to finish rendering tabs/trees/tables
4. Screenshots the viewport as PNG
5. Uploads each PNG to `$web/screenshots/ss_*.png` via `az storage blob upload`

### How Screenshots Appear in Help Page (`generate_help.py`)
- `hotspots.json` maps Q&A demo IDs to screenshot filenames (e.g., `'adfmon': 'ss_adf_dev.png'`)
- `generate_help.py` reads the hotspot map and injects `<img class="qa-ss-img" src="/screenshots/ss_*.png">` tags into Q&A answer blocks
- Screenshots are served from the Databricks app's `/screenshots/{filename}` route (same-origin) to avoid Cross-Origin/CSP issues — **not** direct blob URLs
- `deploy_databricks_app.sh` syncs the latest PNGs from blob storage into the bundle's `screenshots/` directory before every deploy so bundled copies stay current between the monthly cron runs

### Cron Schedule
```
0  2 * * *  publish_synapse_metadata.sh      # daily at 2am
0  6 1 * *  capture_help_screenshots.py      # monthly, 1st at 6am
```

---

## 6. The Databricks App — `idoh_metadata_marketplace/app.py`

FastAPI application serving the reports and handling feedback persistence.

### Report Serving
- Reports stored as `.html.gz` (gzip-compressed to stay under 10MB per-file Apps limit)
- `_serve()` tries exact match → `.html` appended → `.gz` variant → plain HTML
- Catch-all route `GET /{path:path}` handles all report URLs without extension

### Feedback Persistence
- Delta table: `default.marketplace_feedback`
- Statement Execution REST API (not JDBC — avoids warehouse cold-start hangs)
- `wait_timeout: 30s` + `on_wait_timeout: CANCEL`
- Endpoints: `GET/POST /api/feedback`, `PATCH /api/feedback/{entry_id}`
- PAT injected via `app.yaml` env var (`DATABRICKS_SQL_TOKEN`)

### Screenshots Route
```python
@app.get("/screenshots/{filename}")
def screenshot(filename: str):
    path = SCREENSHOTS_DIR / filename   # bundled PNG files
    return Response(path.read_bytes(), media_type="image/png")
```
Serves PNGs from the bundle's `screenshots/` directory (same-origin, avoids CSP/CORS issues with blob storage).

---

## 7. WSL2 / Debian / Cron Environment

- **No GUI, no interactive prompts**: `az login` done once interactively; credentials cached at `~/.azure/` and shared across all scripts
- **PATH**: Crontab sets `PATH` explicitly to include `/usr/bin`, `~/.venv/bin`, and Azure CLI location
- **Logging**: All cron stdout/stderr → `/tmp/publish_synapse_metadata.log`; screenshot cron → `~/capture_screenshots.log`
- **Lock file**: Prevents concurrent runs even if WSL2 is slow to start
- **Windows audio from WSL2** (demo scripts only): `powershell.exe` is accessible from WSL2 via `/mnt/c/Windows/System32/`; demo script calls `System.Media.SoundPlayer.PlaySync()` for synchronous WAV playback on Windows audio

---

## 8. Future — ADF Pipeline Migration

The current architecture depends on your personal Windows machine being on. The migration plan moves execution into Azure:

| Current (WSL2 cron) | Future (ADF) |
|---|---|
| `publish_synapse_metadata.sh` bash script | ADF pipeline with sequential Execute Pipeline activities |
| Personal `az login` token | Service Principal with RBAC roles (Managed Identity) |
| pyodbc + personal Entra token | pyodbc + Managed Identity (no token fetch) |
| `az storage blob upload` | ADF Copy Activity |
| `FAILED_STEPS` bash array | ADF failure paths + email notification actions |
| Lock file | ADF trigger concurrency control settings |
| Monthly screenshot cron | ADF sub-pipeline on schedule trigger |

The Python sub-scripts are already structured to be easily wrapped as ADF Web Activities or Azure Functions — each is a standalone script with argparse for environment selection (`--env dev|prd`), `PUBLISH_RUNNING` env var support, and clean exit codes. The `resilient.py` retry library ports directly.

---

## 9. Demo Scripts

### `marketplace_demo.py`
Full ~4-minute narrated walkthrough. Playwright + edge-tts + ffmpeg for video+audio MP4 output. Covers all 18 reports with real clicks, schema expansion, ADF 4-tab walk, Data Catalog chip filtering and search, dataset modals.

### `marketplace_sizzle.py`
~75-second high-energy sizzle reel variant. Large centered stat callouts, flash-to-black transitions, optional background music via ffmpeg `aloop`+`amix`. Reuses SSO cookies from the demo profile.
