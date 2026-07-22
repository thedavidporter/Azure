# Projects & Ideas

A running list of projects, enhancements, and ideas to revisit.
Add entries at the top of each section so the newest items are first.

---

## In Progress

- **WSL Dual Distro (Work/Home Network Isolation)** — Two Debian WSL2 instances to keep work and home network connections separate. `debian-home.tar` exported to `C:\WSL`. `~/network-select.sh` built and working — prompts to switch between work (default network) and home (WireGuard `wg0`). Debian auto-starts minimized at Windows login via `start_wsl_debian.vbs` in Startup folder. Next: import debian-home distro with `wsl --import`, fix default user.

---

## Up Next

- **Migrate publish_synapse_metadata.sh off laptop to ADF pipeline (daily run)** — Currently runs via laptop cron (`0 9 * * 1-5`), 47-step script generating ~18 metadata reports (Synapse dev/prd+delta, ADF, ADLS, Logic Apps, APIM, Key Vault, ADO, SQL DW, VNet, AVD, Data Catalog, Security Groups, Databricks) then uploading to Azure Blob `$web` + deploying Databricks bundle/App. Goal: run once/day with no laptop dependency. **Decision: compute target = ADF Custom Activity on Azure Batch** (lift-and-shift, minimal rewrite vs. Databricks Notebook Activity option).
  - **Auth:** give the Azure Batch pool a **system-assigned managed identity** instead of an SPN+secret — every script's `az account get-access-token` call keeps working unmodified via `az login --identity` at the top of the job (no secret to store/rotate). Grant that MI: ARM roles (Synapse/ADF/ADLS/KeyVault/VNet/AVD/Storage Blob Data Contributor), SQL/Synapse AAD login on both DW DBs, Microsoft Graph app permissions (Directory.Read.All/GroupMember.Read.All, admin consent) for security groups report, access on all 3 Databricks workspaces. Also found hardcoded secrets to move to Key Vault while touching this anyway: `REDCAP_API_KEY` in `generate_data_catalog.py`, Pushover/Teams webhook creds in `adf_pipeline_failure_check.py`, Databricks PAT file (`~/.databricks_app_token`).
  - **Network:** deploy the Batch pool into a **VNet subnet** and add that subnet as an allowed VNet rule on the storage account (`zus1idohdevv2dbrkdl`, currently `defaultAction: Deny` w/ only laptop IP + ADF/Logic App resource-instance trust rules), Synapse/SQL DW, and Key Vault firewalls — avoids IP churn vs. static IP allow-listing. Alternative for storage specifically: let ADF's own Copy Activity do the final blob upload (already trusted via resource-instance rule) instead of opening firewall to Batch at all — needs Batch task output staged somewhere ADF can read between Custom Activity and Copy Activity.
  - **Porting:** package the 45 `.py` files + `requirements.txt` + bash script as a Batch **Application Package** (versioned zip, auto-deployed to pool nodes). Pool **Start Task** installs az-cli, unixODBC + msodbcsql18 (Driver 18 for SQL Server, needed by pyodbc-based Synapse/SQL DW scripts), Python + venv deps — cached per node. Fix hardcoded `/home/thedavidporter/...` paths (recreate structure on node via Start Task, or swap to `$AZ_BATCH_TASK_WORKING_DIR`-relative paths). Swap interactive `az login` → `az login --identity`. Databricks bundle/app deploy step: use MI-based AAD auth if supported, else pull PAT from Key Vault via MI instead of local token file.
  - **ADF pipeline:** Linked Services for Azure Batch (account/pool/storage) + Key Vault (remaining secrets). Keep as **one Custom Activity** running the existing bash script as-is for v1, including the Databricks bundle/app publish step at the tail end (matches current per-step-failure-tolerant behavior; decided against splitting publish into a separate activity — revisit later if independent retry/monitoring of the publish step is needed). Daily schedule trigger (no longer tied to 9am laptop-on constraint). Failure alerting: reuse Teams webhook + Pushover pattern already in `adf_pipeline_failure_check.py`.
  - **Databricks publish auth (end of script — bundle deploy + apps deploy to idoh-metadata-marketplace App):** currently reads a plaintext PAT from `~/.databricks_app_token`. v1 plan: move PAT into Key Vault, Batch task pulls it at runtime via pool's MI, sets `DATABRICKS_TOKEN` env var, same CLI commands run unchanged. Revisit later: AAD-native auth via the pool's managed identity directly (no PAT at all) if Databricks CLI/workspace config supports it cleanly.
  - **Steps:** 1) create Batch account + pool w/ system-assigned MI + VNet subnet, 2) grant MI parity access (ARM/SQL/Graph/Databricks) + move hardcoded secrets to Key Vault, 3) build Application Package + Start Task, 4) port script (auth swap, path fixes), 5) build ADF Linked Services + Custom Activity pipeline + daily trigger, 6) parallel-run vs laptop cron for a few days to confirm parity (also root-cause known-flaky SQL DW dev/prd generation step), 7) cut over — disable laptop cron, 8) wire up ADF failure alerting.

- **Azure Security Groups & Access report** — Build a metadata report page showing all Azure AD / Entra ID security groups, their members, and what Azure resources they have access to (role assignments). Should cover all subscriptions and surface which groups control access to Synapse, Databricks, ADF, Key Vault, storage accounts, etc.

- **Synapse schema & linked service documentation** — Document all schemas and linked services: what each is used for, how often they are queried, and which ADF pipelines reference them. Enrich the existing Synapse metadata report or build a standalone report pulling ADF pipeline run history to show query frequency per linked service/schema.

- **Azure resource documentation gaps** — Build metadata reports for undocumented resources identified on 2026-06-19:
  - Azure SQL Databases (4 in DEV) — likely storing operational data alongside the DW
  - AKS clusters (3 in PRD) — significant compute, no documentation exists
  - PostgreSQL flexible servers (2 in PRD)
  - Automation Accounts / Runbooks (10 runbooks in PRD)
  - Azure Monitor alerts & action groups — document what's being monitored, thresholds, and who gets notified
  - Log Analytics workspaces (2 DEV, 5 PRD)
  - Container Registries / ACR (2 in each sub)

---

## Ideas & Enhancements

- **Azure Front Door for static website** — Replace IP-allowlist workaround with Front Door Premium + Private Link origin so the metadata reports are accessible from the state network without managing IPs. Requires Front Door Premium (~$330/month base) and Azure Policy approval. Storage account stays VNet-locked; Front Door is the public face with WAF IP restrictions.

- **Databricks metadata report** — Add SQL Alerts configuration (currently 0 alerts across all workspaces — none set up yet)

- **Metadata report access** — ~~Long-term: migrate from Azure Blob static site to Azure Static Web Apps with AAD auth~~ — solved via IDOH Metadata Marketplace Databricks App (see Completed)

- **Claude Code Remote Control** — Explored `--remote-control` flag to share a WSL session with the Claude mobile app. Toggle with `/remote-control` in-session; session name auto-generated from hostname. Connection status indicators unclear — no `--list-remote-control-sessions` flag exists. Worth revisiting once the feature matures for monitoring long-running publish jobs from phone.

- **Azure Monitor alert inventory** — Add a report covering all `smartDetectorAlertRules`, `metricalerts`, and `activityLogAlerts` across subscriptions showing thresholds, action groups, and notification targets

---

## Backlog

- **AVD / Virtual Desktop documentation** — 141 host pools in PRD subscription, not documented anywhere
- **Backup Vaults** — 2 in PRD, document what is being backed up and retention policies
- **Event Grid** — topics and system topics in both subscriptions, document publishers and subscribers
- **Application Insights** — 5 components in DEV, document what applications they are monitoring

---

## Completed

<!-- Done — keep for reference -->

| Date | Item |
|------|------|
| 2026-07-21 | Data Catalog — schema pills in dataset modal now have PRD↗ and DEV↗ links; clicking opens Synapse metadata report filtered directly to that schema (All Objects tab pre-filtered via `?schema=` URL param + `filterBySchema()` on load); deployed |
| 2026-07-21 | All reports — date/time stamps now include timezone label (EDT/EST); switched from naive local time to `zoneinfo.ZoneInfo("America/New_York")` in generate_metadata_index.py, azure_security_groups_report.py, and generate_data_catalog.py; `%Z` format renders EDT in summer and EST in winter automatically; deployed |
| 2026-07-21 | Azure Security Groups report — moved 🟠 workspace cards into the Databricks chip filter (appear alongside 2 security groups when Databricks chip is active); sidebar IZ-DEV/DEV/PRD buttons now clickable — each shows just that workspace card with a Clear link; no always-on cards at top; deployed |
| 2026-07-22 | help.html — updated guide to reflect recent changes: freshness Q&A updated to 3-tier color scheme (green/yellow/red) + EDT timezone note + per-report gen-ts; Security Groups card updated to mention ⬇ Excel export and Databricks Direct users; Data Catalog card updated to mention PRD↗/DEV↗ schema deep-links; two new Q&As added ("How do I export security group members to Excel?" and "How do I open a dataset's schema directly in the Synapse Metadata Report?"); deployed |
| 2026-07-21 | Azure Security Groups report — added three 🟠 Databricks workspace cards (IZ-DEV/DEV/PRD) at top of groups grid; each card shows direct-only user count and clicks open a modal listing users added directly without an Azure group; sidebar "Databricks Direct" toggle also added for full detail view; deployed |
| 2026-07-21 | Azure Security Groups report — added "Databricks Direct" view showing users added to each workspace directly without an Azure group; fetches SCIM users from IZ-DEV/DEV/PRD via AAD token (resource 2ff814a6), diffs against UPNs of members in Azure groups that hold role assignments on each workspace resource; sidebar shows per-workspace direct-only counts; findings: IZ-DEV 5 direct, DEV 69 direct (0 Azure group coverage), PRD 47 direct (0 Azure group coverage) — DEV and PRD have no Azure group role assignments on the workspace resources at all |
| 2026-07-21 | Security Groups report — Excel export button fixed: switched from hand-rolled SpreadsheetML XML (caused Excel format-mismatch warning) to SheetJS lazy-loaded from CDN on first click; produces true `.xlsx` with no warning; fixed Databricks Direct cards not exporting (openDbModal never set _currentIdx — added _currentDbWsKey tracker; Databricks export gets Members sheet only, Azure groups get Members + Role Assignments sheets) |
| 2026-07-21 | Security Groups report — Excel export button added to group detail dialog; clicking "⬇ Excel" downloads members list; two-sheet workbook for Azure groups (Members + Role Assignments), single Members sheet for Databricks Direct cards; filename is the group display name |
| 2026-07-21 | Report generators — gen-ts color-coded timestamps added to remaining 7 reports missing them: `generate_data_catalog.py`, `azure_security_groups_report.py`, `databricks_metadata_report_dev.py`, `_prd.py`, `_iz_dev.py`, `apim_metadata_report.py`; also fixed `avd_metadata_report.py` date-parsing bug (`.replace(' ','T')` without stripping "EDT" → `Invalid Date`; fix: `.replace(/ [A-Z]{2,4}$/,'')` first); data_catalog.html regenerated and deployed |
| 2026-07-21 | Metadata Marketplace — fixed index.html refresh date badges always showing green; server-baked CSS class (`stale`) was correct at publish time but stale on next page load; fix: added `data-ts` (Unix mtime) to each `.card-refresh` span + client-side IIFE recomputes `ageH = (now - ts) / 3600000` on page load and re-applies `stale` (yellow > 25h) class dynamically; deployed |
| 2026-07-21 | Data Catalog — enriched data_catalog_datasets.json from ADO wiki (55 pages, ODA.wiki); updated from 32 → 34 datasets; cadence corrections: CHIRP daily (8:30am), COVID surveillance daily (7:30am, 3-stage NBS pipeline), Wastewater PROD daily (7am), NHSN daily (10am), Vital Records weekly (DRIVE/STEVE systems, annual cert in July); BRFSS schema SM_IDOH_01 → SM_BRFSS_01; status upgrades: ESSENCE verified, HFI verified; new datasets: Poison Control/Syndromic Foundation (SM_IPC_01, 34 tables) and HIV/AIDS Surveillance (EHARS, Databricks gold); enriched notes with vendors, contacts, pipeline names, R:Drive paths, source systems, and NHSN SAMS credential expiry note |
| 2026-07-17 | Databricks token rotation — two PATs exposed in `databricks_metadata_report_dev.py` and `_prd.py` via GitHub push (caught by push protection); revoked 3 dev tokens + 2 prd tokens via Token Management API (`POST /api/2.0/token/delete`); issued new 1-year PATs for both workspaces; repo copies stay as `REDACTED_DATABRICKS_TOKEN`; local scripts hold real tokens only |
| 2026-07-17 | Report generators — all 16 report generators now show `↻ YYYY-MM-DD HH:MM` timestamp badge replacing plain "Generated:" text; color computed client-side at page load: green (<25h), yellow (<1 week), red (>1 week); uses `data-ts` attribute + inline IIFE script; `avd_metadata_report.py` updated separately (already used `<span id="gen-time">`) |
| 2026-07-17 | Feedback widget — "Failed to fetch" retry logic: catches app container sleep (not warehouse cold start); shows "Waking up…" on button/spinner; waits 4s and retries up to 3 times automatically; applies to both Submit and Submission Log load; both index and help pages |
| 2026-07-17 | Feedback widget — removed Export JSON and Export CSV buttons; Show Deleted and Refresh now the only toolbar buttons |
| 2026-07-17 | Metadata Marketplace — feedback button hover tooltip: "…you can also find recent updates to this app in the Changelog"; CSS `::after` pseudo-element with `data-tooltip` attribute; fades in on hover; both index and help pages |
| 2026-07-17 | Metadata Marketplace — Changelog section on Help page: `changelog.json` in `~/` holds dated entries (date, time, name, description); `generate_help.py` reads and renders entries under new "Changelog" sidebar section; `deploy_databricks_app.sh --changelog` flag prompts for name + description, auto-stamps date/time, prepends to JSON, regenerates help page, then deploys; silent by default |
| 2026-07-17 | Metadata Marketplace — animated spinner in feedback widget: spinning ring + rotating word list (Thinking…, Pondering…, Querying… etc.) shown in log pane while loading; Submit button shows inline spinner + "Saving…" while POST is in flight; CSS `@keyframes fb-spin`; both index and help pages |
| 2026-07-17 | Metadata Marketplace feedback persistence — (1) added `GET/POST /api/feedback` + `PATCH /api/feedback/{id}` endpoints to `app.py` backed by `default.marketplace_feedback` Delta table; (2) switched from `databricks-sql-connector` (JDBC hangs on cold warehouse) to `requests` + Statement Execution REST API with `wait_timeout: 30s`; (3) replaced `localStorage` JS in both generators with async `fetch()` calls — all users share a single log; `DATABRICKS_HOST` env var lacks `https://` in Apps runtime — prepended in code; PAT injected via `app.yaml` plain `value:` (nested `valueFrom:` format rejected by Apps runtime) |
| 2026-07-17 | GitHub (thedavidporter/Azure) — pushed all July 2026 work; 18 files updated, 4 new scripts added (capture_help_screenshots.py, deploy_databricks_app.sh, anthropic_databricks_proxy.py, generate_synthetic_inpatient.py); README.md rewritten to reflect current state |
| 2026-07-16 | help.html Report Directory — cards now flow in a fixed 3-column grid; section label moved inside each card as a small blue category header; removes blank whitespace from single-card sections |
| 2026-07-16 | help.html screenshots — fixed stale screenshots in Databricks app (bundle has its own PNG copies separate from blob storage); deploy_databricks_app.sh now syncs screenshots from blob storage into the bundle before every deploy so they stay current automatically |
| 2026-07-16 | help.html screenshots — ran capture_help_screenshots.py manually to refresh all 11 stale screenshots (last updated 2026-06-29); deployed to Databricks app; all current as of today |
| 2026-07-16 | help.html screenshots — capture_help_screenshots.py uses headless Playwright/Chromium to screenshot each report from local file:// URLs and upload to $web/screenshots/; cron runs 1st of each month at 6am; log at ~/capture_screenshots.log |
| 2026-07-16 | ADLS metadata report — sidebar folders now collapsible (recursive lazy-rendered tree, click to expand per level); depth increased from 3 → 6 levels; SKIP_FS list excludes infrastructure/temp containers (insights-logs-workflowruntime, adfstaged*, sqldbauditlogs, databricks-temp, tmpcontainer); skip list noted in the report header |
| 2026-07-16 | ADF metadata report — added Activity Performance tab ranking all activities by duration; COPY badge + proportional duration bar; filter controls for All / Copy only / Failed only; rowsRead, rowsCopied, dataRead, dataWritten columns; published for both dev and prd |
| 2026-07-16 | Synapse metadata reports (dev + prd) — DDL fixes from colleague feedback: removed spurious length from INT/BIGINT/FLOAT datatypes; added DISTRIBUTION & Indexing section to each DDL block showing DISTRIBUTION= and index type pulled from sys.pdw_table_distribution_properties and sys.indexes |
| 2026-07-16 | Synapse metadata report — schema descriptions now open as a full-screen modal dialog instead of a collapsible details dropdown; fixed onclick HTML attribute quote-escaping bug by storing all narratives in SCHEMA_NAR_MAP JS object and using js_esc() in onclick |
| 2026-07-16 | help.html — fixed filter bar (All Questions / Executive / Data Management / Business Analyst / Data Engineer) bleeding into Glossary section when scrolling; fixed filterQA bug where clicking a group caused all questions to disappear (wrapper div had same CSS class as individual Q&A items) |
| 2026-07-16 | deploy_databricks_app.sh — standalone deploy script extracted from publish pipeline; compresses all *.html to .gz, runs databricks bundle deploy + apps deploy; executable at ~/deploy_databricks_app.sh |
| 2026-07-16 | Synthetic CSV — generate_synthetic_inpatient.py generates 100 rows × 284 columns for DM_Hospital_Discharge_01.INPATIENT_ANNUAL_FINAL_2022; realistic Indiana hospitals, ICD-10 codes, dates, charges; output ~/INPATIENT_ANNUAL_FINAL_2022_synthetic.csv |
| 2026-07-16 | Databricks proxy — added 429 retry logic with exponential backoff (up to 5 retries; 2s/4s/8s/16s/32s); respects Retry-After header; covers both streaming and non-streaming paths; surfaces clear error message after all retries exhausted |
| 2026-07-16 | help.html — added Data Catalog glossary section (3 sub-sections: Dataset Status, Refresh Cadence, Access Levels) explaining inference rules behind verified/review/needs-steward/new/requested, cadence derivation from table name patterns + source system knowledge, and self-serve/approval-required/restricted access criteria |
| 2026-07-15 | Data Catalog — 32-dataset registry extracted from Synapse PRD snapshot into data_catalog_datasets.json (14 domains, Synapse schema mapping with row counts per layer); generate_data_catalog.py loads from JSON; modal shows schema layers + notes; search covers schema names; fixed openModal index bug when filtering |
| 2026-07-15 | Synapse metadata reports (dev + prd) — added Select tab to table modal showing full SELECT col1, col2,… FROM schema.table; statement with copy button |
| 2026-07-15 | Metadata Marketplace — verified help.html screenshots now display correctly after the /screenshots/ same-origin route fix |
| 2026-07-15 | Databricks proxy — `~/anthropic_databricks_proxy.py`; FastAPI on port 8082; translates Anthropic API → OpenAI format; forwards to `databricks-claude-sonnet-5` serving endpoint; reads token from `~/.databricks_app_token`; alias `claude-work` added to `~/.bashrc`; key fix: Databricks returns content as list (reasoning + text blocks) — proxy strips reasoning and passes only text |
| 2026-07-15 | Metadata Marketplace — added Apps tab to databricks_metadata_report.py (calls GET /api/2.0/apps); fixed help.html broken images by bundling PNGs in screenshots/ dir with same-origin /screenshots/ route; data catalog domain chips changed to multi-select |
| 2026-07-13 | IDOH Metadata Marketplace — FastAPI Databricks App deployed to adb-5757046586469840.0.azuredatabricks.net/apps/idoh-metadata-marketplace/; proxies blob reports with Entra ID SSO; no VDI required; bundle deploy via databricks.yml in /home/thedavidporter/idoh_metadata_marketplace/ |
| 2026-07-13 | publish_synapse_metadata.sh — banner auto-refreshes browser every 30s during publish via meta http-equiv refresh tag; tag absent in final index so page stops reloading when done |
| 2026-07-13 | publish_synapse_metadata.sh — progress bar in banner: shows "X of 21 reports complete (N%)" with CSS fill bar; advance_progress() called after each of 21 report sections |
| 2026-07-13 | publish_synapse_metadata.sh — fixed in-progress banner being overwritten; added PUBLISH_RUNNING=1 env var; all 14 report scripts now skip index update when running inside publish pipeline |
| 2026-07-10 | HA Fans dashboard — rebuilt view 4 with Mushroom cards; 3 sections (ceiling, outdoor, exhaust); added 7 missing fans; moved MB Becky/David vanity fans to ceiling section |
| 2026-07-10 | HA Rooms dashboard — 21-section Mushroom card view covering all house areas; renamed from Family Room dashboard |
| 2026-07-10 | HA OOO Tracker — Log Entry button now auto-refreshes sensor.ooo_log instantly via homeassistant.update_entity in script |
| 2026-07-10 | HA Motion Sensors dashboard — fully rebuilt with Mushroom cards; added ELK M1 basement motion, Reolink cameras (5), Hubitat/Z-Wave contact sensors, Notion moisture sensors (8), smoke/CO, ecobee occupancy, dryer/washer motion |
| 2026-07-10 | HA OOO Tracker — fixed iOS app rendering by replacing iframe with command_line sensor + markdown card |
| 2026-07-10 | HA sidebar — custom-sidebar v16 installed, OOO Tracker icon golden (#F4A820), positioned after Overview |
| 2026-07-10 | HA Basement Stairs automation — replaced delay with timer helper (survives reboots) |
| 2026-06-19 | Databricks metadata report — added Unity Catalog, Notebooks, Lakeview dashboards, SQL Alerts |
| 2026-06-19 | Fixed publish_synapse_metadata.sh stale lockfile handling |
| 2026-06-19 | Fixed teams_keep_active.py duplicate log entries in crontab |
| 2026-06-18 | Added SQL DW, VNet, Databricks, ADO, Key Vault, Logic Apps, APIM reports to publish pipeline |
| 2026-06-18 | Built publish_synapse_metadata.sh — master publish + Azure Blob static site |
