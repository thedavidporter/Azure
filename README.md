# IDOH Azure Tooling — Work Summary

Scripts and tooling built for the Indiana Department of Health (IDOH) Office of Data Analytics (ODA).
All scripts live on the WSL Debian host at `/home/thedavidporter/`.

---

## Table of Contents

1. [Synapse Metadata Tooling](#synapse-metadata-tooling)
2. [Databricks Metadata Reports](#databricks-metadata-reports)
3. [Azure Data Factory (ADF)](#azure-data-factory-adf)
4. [ADLS / Storage](#adls--storage)
5. [Azure Virtual Desktop (AVD)](#azure-virtual-desktop-avd)
6. [Other Azure Services](#other-azure-services)
7. [Catalog, Index & Publishing](#catalog-index--publishing)
8. [IDOH Metadata Marketplace](#idoh-metadata-marketplace)
9. [Teams & Utilities](#teams--utilities)
10. [Home Assistant (Wyze Locks)](#home-assistant-wyze-locks)
11. [Known Issues & Pending Work](#known-issues--pending-work)
12. [Azure Environment Reference](#azure-environment-reference)

---

## Synapse Metadata Tooling

| Script | Purpose |
|---|---|
| `synapse_metadata_report_dev.py` | Metadata report for dev (`zus1-idoh-dev-v2-sql-dw`) |
| `synapse_metadata_report_prd.py` | Metadata report for prod (`zus1-idoh-prd-v1-sql-dw`) |
| `synapse_er_diagram.py` | ER-style logical diagram for dev |
| `synapse_connections.py` | Force-directed connections graph |
| `synapse_mcp.py` | Read-only MCP server for Synapse |
| `synapse_metadata_delta_dev.py` | Delta report — two most recent dev snapshots (HTML, 500 rows/page infinite scroll) |
| `synapse_metadata_delta_prd.py` | Delta report — two most recent prd snapshots (HTML, 500 rows/page infinite scroll) |
| `synapse_missing_ace_timestamp.py` | Finds Synapse tables missing ACE_TIMESTAMP column + lists Databricks notebooks with owner/creator; outputs Excel |
| `synapse_space_check.py` | Space consumption report for dev + prd via `sys.dm_pdw_nodes_db_partition_stats` |
| `sql_dw_metadata_report.py` | Metadata report for the legacy Azure SQL Data Warehouse (ACE warehouse) — schemas, tables, views, procs, columns |
| `demo_delta_report.py` | Generates fake Synapse snapshots to preview delta report output — no live connection needed |

### Recent Enhancements (2026-07)

- **DDL fixes** — `INT`, `BIGINT`, `FLOAT` no longer show spurious length in the DDL block; `DISTRIBUTION=` and index type now shown in a dedicated section pulled from `sys.pdw_table_distribution_properties` and `sys.indexes`
- **Schema narrative modal** — schemas with descriptions now open a full-screen modal dialog instead of a collapsible dropdown; onclick escaping fixed via JS data map pattern
- **SELECT tab** — table modal includes a pre-built `SELECT col1, col2, … FROM schema.table` statement with copy button

**Published HTML (dev):** https://zus1idohdevv2dbrkdl.z13.web.core.windows.net/synapse_metadata_report_dev.html

---

## Databricks Metadata Reports

| Script | Purpose |
|---|---|
| `databricks_metadata_report_dev.py` | Databricks metadata for dev (`adb-5757046586469840.0.azuredatabricks.net`) |
| `databricks_metadata_report_prd.py` | Databricks metadata for prd (`adb-5323951998838804.4.azuredatabricks.net`) |
| `databricks_metadata_report_iz_dev.py` | Databricks metadata for IZ dev (`adb-612192313963696.16.azuredatabricks.net`) |
| `databricks_metadata_report.py` | Combined report for all 3 Databricks workspaces |
| `anthropic_databricks_proxy.py` | FastAPI proxy on port 8082 — translates Anthropic API format → Databricks Claude serving endpoint; alias `claude-work` in `~/.bashrc` |
| `databricks_proxy.py` | Earlier LiteLLM-based proxy (superseded by anthropic_databricks_proxy.py) |
| `claude_chat_notebook.py` | Databricks notebook for Claude chat |
| `start_databricks_proxy.sh` | Starts the legacy Databricks proxy |

### Key Fixes Applied (2026-07-02)

- `api()` now catches `requests.exceptions.Timeout` and `RequestException`
- Non-200 responses logged as `[WARN] <status> <url>` (previously silent)
- `_walk_notebooks` split into two phases: sequential BFS directory walk, then parallel `ThreadPoolExecutor(max_workers=20)` for JUPYTER export + creator lookup
- Token refreshed per workspace via `get_token()` — previously a single token expired before reaching PRD
- 429 retry logic with exponential backoff (up to 5 retries; 2s/4s/8s/16s/32s); respects `Retry-After` header

**Notebook counts after fixes:** PRD 589 (was 0) | DEV 2089 | IZ-DEV 21

### Known API Issues (pre-existing, not script bugs)

| Code | Endpoint | Notes |
|---|---|---|
| 400 | `/api/2.1/jobs/list` | 0 jobs returned on all 3 workspaces — API version or parameter issue |
| 404 | `/api/2.0/sql/dashboards` | SQL dashboards not enabled or endpoint deprecated |
| 429 | `/api/2.0/workspace/export` | Rate limiting from 20 concurrent JUPYTER export calls |
| 400 | `/api/2.0/workspace/export` (some) | Some notebooks don't support JUPYTER format — falls back to `workspace/list` language field |

---

## Azure Data Factory (ADF)

| Script | Purpose |
|---|---|
| `adf_metadata_report.py` | ADF metadata report (`--env dev` or `--env prd`) |
| `adf_child_pipeline_check.py` | Parses existing `adf_metadata_report_{env}.html`; reports pipelines with no trigger and not called by any other pipeline |
| `adf_pipeline_failure_check.py` | Checks ADF pipeline failures — runs as cron job at 7am daily |
| `adf_pipeline_failure_check_test.py` | Test version of the pipeline failure check |
| `pipeline_doc_report.py` | HTML documentation for a single ADF pipeline (sources, execution chains, transformations, destinations) |

### Recent Enhancements (2026-07)

- **Activity Performance tab** — ranks all activities by duration; COPY activities badged; proportional duration bar; filter controls for All / Copy only / Failed only; shows rowsRead, rowsCopied, dataRead, dataWritten columns

### ADF Failure Check Cron — Status

- Runs at **7am daily** from WSL Debian
- Auth: currently `DefaultAzureCredential` (backed by `az login` tokens) — will break when refresh tokens expire (~90 days)
- **Pending:** migrate to `ClientSecretCredential` (service principal) for reliability

**Azure environments:**
- Dev ADF: `zus1-idoh-dev-v2-df` | Resource Group: `zus1-idoh-dev-v2-rg`
- Prd ADF: `zus1-idoh-prd-v1-df` | Resource Group: `zus1-idoh-prd-v1-rg`

---

## ADLS / Storage

| Script | Purpose |
|---|---|
| `adls_metadata_report.py` | ADLS Gen2 metadata report — subscription-wide, walks all HNS-enabled storage accounts up to **6 directory levels** |

### Recent Enhancements (2026-07)

- **Depth increased** from 3 → 6 levels — covers all meaningful data lake containers fully (bronze/silver/gold, recordlinkage, outbound, cost exports)
- **Collapsible sidebar tree** — folders are expandable/collapsible with lazy rendering; click to drill down without leaving the sidebar
- **SKIP_FS list** — infrastructure/temp containers excluded from the walk and report: `insights-logs-workflowruntime`, `adfstagedcommandtempdata`, `adfstagedcopytempdata`, `adfstagedpolybasetempdata`, `sqldbauditlogs`, `zus1-idoh-databricks-temp`, `tmpcontainer`

---

## Azure Virtual Desktop (AVD)

| Script | Purpose |
|---|---|
| `avd_metadata_report.py` | Session host inventory — all 142 host pools in ECAE Shared Production; status, last heartbeat, sessions, assigned user; stale machine identification |

### Key Facts

- **Subscription:** ECAE Shared Production (`5d3a4b9c-0e31-477c-9122-bb3be662e2a9`) — NOT the IDOH Production subscription
- **Host pools:** 142 | **Session hosts:** 137 (as of 2026-07-02)
- **Naming:** `ecae-prd-{n}-0.state.in.us` / `ecae-dev-{n}-0.state.in.us`

**Report tabs:** Overview | All Machines | Stale (90d+) | Active Sessions | By Host Pool

---

## Other Azure Services

| Script | Purpose |
|---|---|
| `ado_metadata_report.py` | Azure DevOps — repos, branches, pipelines, PRs, policies, variable groups, environments |
| `apim_metadata_report.py` | API Management — APIs, operations, products, subscriptions, backends, policies (dev + prd) |
| `keyvault_metadata_report.py` | Key Vault — secrets, keys, certs, access policies (secret values never collected) |
| `logic_apps_metadata_report.py` | Logic Apps — workflows, triggers, actions, connections, run history (dev + prd) |
| `vnet_metadata_report.py` | Virtual Network — VNets, subnets, NSGs, private endpoints, peerings, service endpoints; flags data-exfil risk |
| `GetKeyVaults.sh` | Lists Key Vaults |
| `GetKeyVaultsWithSecrets.sh` | Lists Key Vaults with their secrets |
| `azure_costs_june2026.py` | Azure cost Excel from a Cost Management export CSV for June 2026 |
| `azure_costs_ytd2026.py` | Azure costs YTD 2026 summary Excel |
| `network_scan_isdh.py` | Scans `\\State.in.us\file1\ISDH\Shared\ISDH6\ITS` network share for SAS and data files; generates interactive HTML report |

---

## Catalog, Index & Publishing

| Script | Purpose |
|---|---|
| `generate_data_catalog.py` | Generates `data_catalog.html` — IDOH Data Marketplace catalog (32 datasets, 14 domains, Synapse schema mapping) |
| `generate_descriptions.py` | Generates plain-English descriptions for Synapse objects |
| `generate_help.py` | Generates `help.html` — single-page guide with Q&A by role, Report Directory, Quick Start, and Glossary |
| `generate_metadata_index.py` | Generates `index.html` — central landing page linking to all metadata reports |
| `publish_synapse_metadata.sh` | Master publish script — generates all ~18 HTML reports and uploads to Azure Blob `$web` + deploys Databricks app |
| `deploy_databricks_app.sh` | Standalone deploy script — syncs screenshots from blob, compresses HTML reports, runs `databricks bundle deploy` + `apps deploy` |
| `capture_help_screenshots.py` | Headless Playwright/Chromium script — screenshots each report from local `file://` URLs and uploads to `$web/screenshots/`; runs via cron on the 1st of each month at 6am |

### Publishing Notes

- ~43 steps | lock: `flock -n /tmp/publish_synapse_metadata.lock`
- Log: `/home/thedavidporter/publish_synapse_metadata.log`
- **deploy_databricks_app.sh** pulls latest screenshots from blob into the bundle before every deploy so the Databricks app always shows current images
- **APIM steps always skipped** — APIM not deployed; `ERROR: No APIM service found` is expected
- **If it stalls:** likely hung at Databricks PRD step — `kill <PID>`, `rm -f /tmp/publish_synapse_metadata.lock`, restart

### Cron Jobs

```
0 9 * * 1-5   publish_synapse_metadata.sh       # daily publish (weekdays, 9am)
0 7 * * 1-5   adf_pipeline_failure_check.py     # ADF failure check (weekdays, 7am)
0 6 1 * *     capture_help_screenshots.py        # refresh help.html screenshots (1st of month, 6am)
```

---

## IDOH Metadata Marketplace

FastAPI Databricks App deployed at:
**https://idoh-metadata-marketplace-5757046586469840.0.azure.databricksapps.com**

- Proxies all HTML reports from Azure Blob Storage with Entra ID SSO — no VDI required
- Bundle: `/home/thedavidporter/idoh_metadata_marketplace/`
- Deploy: `~/deploy_databricks_app.sh`
- Reports served as gzip-compressed blobs; screenshots bundled as PNGs in `screenshots/`

### Up Next: Migrate Publish Pipeline off Laptop

Currently runs via laptop cron. Goal: move to **ADF Custom Activity on Azure Batch** so reports generate daily with no laptop dependency.

Key decisions made:
- **Auth:** system-assigned managed identity on Batch pool (no SPN secret to rotate)
- **Network:** Batch pool in VNet subnet; subnet added to storage/Synapse/Key Vault firewall allow-lists
- **Porting:** Application Package (versioned zip); Start Task installs az-cli, pyodbc + msodbcsql18, Python venv
- **Hardcoded secrets to move to Key Vault:** `REDCAP_API_KEY`, Pushover/Teams webhook creds, Databricks PAT

---

## Teams & Utilities

| Script | Purpose |
|---|---|
| `teams_presence_monitor.py` | Monitors a Teams user's presence via Graph API; logs state changes |
| `setup_teams_presence_app.sh` | Admin script — creates Azure AD app registration, grants `Presence.Read.All` |
| `teams_followup.py` | Teams Follow-Up Tracker — tracks messages needing a response within 7 days |
| `teams_keep_active.py` | Keeps Teams active during work hours |
| `test_graph_api.py` | Tests the Microsoft Graph API |
| `notion_sensors.py` | Reads Notion home sensors via `api.getnotion.com` v1 API |
| `generate_synthetic_inpatient.py` | Generates 100-row × 284-column synthetic CSV for `INPATIENT_ANNUAL_FINAL_2022`; realistic Indiana hospitals, ICD-10 codes, dates, charges |
| `save_session.sh` | Saves current Claude Code session |
| `check_and_save_session.sh` | Checks and saves current Claude Code session |

### Teams Presence Monitor — Status

- Script is complete and ready
- **Blocked:** waiting on an admin to run `setup_teams_presence_app.sh` to create the app registration and grant `Presence.Read.All`

---

## Home Assistant (Wyze Locks)

**Status: Fixed and working** — all 4 Wyze locks active.

- HA 2026.7.1 on Raspberry Pi 5 64-bit
- wyzeapi v0.1.38 (HACS, SecKatie)

### Root Cause & Fix

Python 3.14 inside the HA Docker container has an empty default SSL trust store. Fix: monkey-patch `aiohttp.ClientSession.__init__` in `/config/custom_components/wyzeapi/__init__.py` to inject `TCPConnector(ssl=False)`. Uses `type(connector) is _aiohttp.TCPConnector` (exact match) to leave HA's own `HomeAssistantTCPConnector` sessions untouched.

### Lock Entities

| Entity | device_id |
|---|---|
| `lock.front_door` | `354c9357fff6c5e7a726fb860b22e64c` |
| `lock.patio_door` | `f10a4b39f4edc09b95811d5f087eb073` |
| `lock.garage_door` | `d65a25d5e42c8fa68838ac658ed6cf41` |
| `lock.carriage_house_door` | `31d8f85461b209204419f83e780a508a` |

---

## Known Issues & Pending Work

| Item | Status |
|---|---|
| ADF failure check — migrate to service principal (`ClientSecretCredential`) | Pending |
| Teams presence monitor — admin needs to run `setup_teams_presence_app.sh` | Blocked on admin |
| Migrate publish pipeline from laptop cron to ADF Custom Activity on Azure Batch | Up Next |
| Databricks PRD notebook collection — occasional 429 rate limiting | Known, low priority |
| "Lock Doors at 11pm" HA automation | Needs manual re-enable |

---

## Azure Environment Reference

| Resource | Value |
|---|---|
| Tenant | State of Indiana (`2199bfba-a409-4f13-b0c4-18b45933d88d`) |
| Subscription (IDOH) | ECAE IDOH Production - Azure Commercial (`57493fde-eff8-432f-8574-4f1281bd2ce3`) |
| Subscription (AVD) | ECAE Shared Production (`5d3a4b9c-0e31-477c-9122-bb3be662e2a9`) |
| Storage account (publish) | `zus1idohdevv2dbrkdl`, container `$web` |
| Dev Synapse | `zus1-idoh-dev-v2-sql-dw` |
| Prd Synapse | `zus1-idoh-prd-v1-sql-dw` |
| Dev ADF | `zus1-idoh-dev-v2-df` |
| Prd ADF | `zus1-idoh-prd-v1-df` |
| Dev Databricks | `adb-5757046586469840.0.azuredatabricks.net` |
| Prd Databricks | `adb-5323951998838804.4.azuredatabricks.net` |
| IZ Dev Databricks | `adb-612192313963696.16.azuredatabricks.net` |
| Metadata Marketplace | `https://idoh-metadata-marketplace-5757046586469840.0.azure.databricksapps.com` |
