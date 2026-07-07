#!/bin/bash

STORAGE_ACCOUNT="zus1idohdevv2dbrkdl"
CONTAINER="\$web"
VENV="/home/thedavidporter/.venv/bin/python"
LOCKFILE="/tmp/publish_synapse_metadata.lock"
LOGFILE="/home/thedavidporter/publish_synapse_metadata.log"
FAILED_STEPS=()

# Ensure az CLI is on the PATH (needed when running via cron)
export PATH="$PATH:/usr/bin:/usr/local/bin"

# Prevent duplicate runs — handle stale lockfiles from killed/crashed runs
if [ -f "$LOCKFILE" ]; then
  OLD_PID=$(cat "$LOCKFILE")
  if kill -0 "$OLD_PID" 2>/dev/null; then
    echo "$(date '+%Y-%m-%d %H:%M:%S')  Already running (PID $OLD_PID). Exiting." | tee -a "$LOGFILE"
    exit 0
  else
    echo "$(date '+%Y-%m-%d %H:%M:%S')  Stale lockfile found (PID $OLD_PID no longer running). Removing." | tee -a "$LOGFILE"
    rm -f "$LOCKFILE"
  fi
fi
echo $$ > "$LOCKFILE"
trap "rm -f $LOCKFILE" EXIT

echo "=============================================="
echo " Metadata Report — Publish Script"
echo " (Synapse + ADF + ADLS + Logic Apps + Key Vault + ADO + VNet)"
echo "=============================================="

# Upload a single blob — shared by all steps
upload_blob() {
  local name="$1"
  local file="$2"
  az storage blob upload \
    --account-name "$STORAGE_ACCOUNT" \
    --container-name "$CONTAINER" \
    --name "$name" \
    --file "$file" \
    --content-type "text/html" \
    --auth-mode login \
    --overwrite
}

# Run a report script then upload — logs failure and continues if the script errors
run_step() {
  local gen_label="$1"
  local up_label="$2"
  local desc="$3"
  local blob_name="$4"
  local local_file="$5"
  shift 5

  echo ""
  echo "[$gen_label] Generating $desc..."
  if "$@"; then
    echo ""
    echo "[$up_label] Uploading $desc to Azure Blob Storage..."
    if ! upload_blob "$blob_name" "$local_file"; then
      echo "[$up_label] ERROR: upload failed — continuing"
      FAILED_STEPS+=("[$up_label] Upload: $desc")
    fi
  else
    echo "[$gen_label] ERROR: $desc generation failed — skipping upload, continuing"
    FAILED_STEPS+=("[$gen_label] Generate: $desc")
  fi
}

# ── SYNAPSE DEV ────────────────────────────────────────────────────────────────
run_step "3/28" "4/28" "Synapse DEV HTML report" \
  "synapse_metadata_report_dev.html" "/home/thedavidporter/synapse_metadata_report_dev.html" \
  $VENV /home/thedavidporter/synapse_metadata_report_dev.py

# ── SYNAPSE DEV DELTA ──────────────────────────────────────────────────────────
echo ""
echo "[5/28] Generating Synapse DEV delta report..."
if $VENV /home/thedavidporter/synapse_metadata_delta_dev.py; then
  echo ""
  if [ -f "/home/thedavidporter/synapse_metadata_delta_dev.html" ]; then
    echo "[6/28] Uploading Synapse DEV delta report to Azure Blob Storage..."
    upload_blob "synapse_metadata_delta_dev.html" "/home/thedavidporter/synapse_metadata_delta_dev.html" \
      || { echo "[6/28] ERROR: upload failed — continuing"; FAILED_STEPS+=("[6/28] Upload: Synapse DEV delta"); }
  else
    echo "[6/28] Skipping DEV delta upload — need at least 2 snapshots (run again tomorrow)."
  fi
else
  echo "[5/28] ERROR: Synapse DEV delta generation failed — skipping upload, continuing"
  FAILED_STEPS+=("[5/28] Generate: Synapse DEV delta")
fi

# ── SYNAPSE PRD ────────────────────────────────────────────────────────────────
run_step "7/28" "8/28" "Synapse PRD HTML report" \
  "synapse_metadata_report_prd.html" "/home/thedavidporter/synapse_metadata_report_prd.html" \
  $VENV /home/thedavidporter/synapse_metadata_report_prd.py

# ── SYNAPSE PRD DELTA ──────────────────────────────────────────────────────────
echo ""
echo "[9/28] Generating Synapse PRD delta report..."
if $VENV /home/thedavidporter/synapse_metadata_delta_prd.py; then
  echo ""
  if [ -f "/home/thedavidporter/synapse_metadata_delta_prd.html" ]; then
    echo "[10/28] Uploading Synapse PRD delta report to Azure Blob Storage..."
    upload_blob "synapse_metadata_delta_prd.html" "/home/thedavidporter/synapse_metadata_delta_prd.html" \
      || { echo "[10/28] ERROR: upload failed — continuing"; FAILED_STEPS+=("[10/28] Upload: Synapse PRD delta"); }
  else
    echo "[10/28] Skipping PRD delta upload — need at least 2 snapshots (run again tomorrow)."
  fi
else
  echo "[9/28] ERROR: Synapse PRD delta generation failed — skipping upload, continuing"
  FAILED_STEPS+=("[9/28] Generate: Synapse PRD delta")
fi

# ── ADLS ───────────────────────────────────────────────────────────────────────
run_step "11/28" "12/28" "ADLS Gen2 metadata report" \
  "adls_metadata_report.html" "/home/thedavidporter/adls_metadata_report.html" \
  $VENV /home/thedavidporter/adls_metadata_report.py

# ── ADF DEV ────────────────────────────────────────────────────────────────────
run_step "13/28" "14/28" "ADF DEV HTML report" \
  "adf_metadata_report_dev.html" "/home/thedavidporter/adf_metadata_report_dev.html" \
  $VENV /home/thedavidporter/adf_metadata_report.py --env dev

# ── ADF PRD ────────────────────────────────────────────────────────────────────
run_step "15/28" "16/28" "ADF PRD HTML report" \
  "adf_metadata_report_prd.html" "/home/thedavidporter/adf_metadata_report_prd.html" \
  $VENV /home/thedavidporter/adf_metadata_report.py --env prd

# ── LOGIC APPS DEV ─────────────────────────────────────────────────────────────
run_step "17/28" "18/28" "Logic Apps DEV HTML report" \
  "logic_apps_metadata_report_dev.html" "/home/thedavidporter/logic_apps_metadata_report_dev.html" \
  $VENV /home/thedavidporter/logic_apps_metadata_report.py --env dev

# ── LOGIC APPS PRD ─────────────────────────────────────────────────────────────
run_step "19/28" "20/28" "Logic Apps PRD HTML report" \
  "logic_apps_metadata_report_prd.html" "/home/thedavidporter/logic_apps_metadata_report_prd.html" \
  $VENV /home/thedavidporter/logic_apps_metadata_report.py --env prd

# ── APIM DEV ───────────────────────────────────────────────────────────────────
echo ""
echo "[21/28] Generating APIM DEV HTML report..."
if $VENV /home/thedavidporter/apim_metadata_report.py --env dev; then
  echo ""
  echo "[22/28] Uploading APIM DEV report to Azure Blob Storage..."
  upload_blob "apim_metadata_report_dev.html" "/home/thedavidporter/apim_metadata_report_dev.html" \
    || { echo "[22/28] ERROR: upload failed — continuing"; FAILED_STEPS+=("[22/28] Upload: APIM DEV"); }
else
  echo "[22/28] Skipping APIM DEV upload — no APIM service deployed."
fi

# ── APIM PRD ───────────────────────────────────────────────────────────────────
echo ""
echo "[23/28] Generating APIM PRD HTML report..."
if $VENV /home/thedavidporter/apim_metadata_report.py --env prd; then
  echo ""
  echo "[24/28] Uploading APIM PRD report to Azure Blob Storage..."
  upload_blob "apim_metadata_report_prd.html" "/home/thedavidporter/apim_metadata_report_prd.html" \
    || { echo "[24/28] ERROR: upload failed — continuing"; FAILED_STEPS+=("[24/28] Upload: APIM PRD"); }
else
  echo "[24/28] Skipping APIM PRD upload — no APIM service deployed."
fi

# ── KEY VAULT DEV ──────────────────────────────────────────────────────────────
run_step "25/30" "26/30" "Key Vault DEV HTML report" \
  "keyvault_metadata_report_dev.html" "/home/thedavidporter/keyvault_metadata_report_dev.html" \
  $VENV /home/thedavidporter/keyvault_metadata_report.py --env dev

# ── KEY VAULT PRD ──────────────────────────────────────────────────────────────
run_step "27/30" "28/30" "Key Vault PRD HTML report" \
  "keyvault_metadata_report_prd.html" "/home/thedavidporter/keyvault_metadata_report_prd.html" \
  $VENV /home/thedavidporter/keyvault_metadata_report.py --env prd

# ── AZURE DEVOPS ───────────────────────────────────────────────────────────────
run_step "29/32" "30/32" "Azure DevOps metadata report" \
  "ado_metadata_report.html" "/home/thedavidporter/ado_metadata_report.html" \
  $VENV /home/thedavidporter/ado_metadata_report.py

# ── SQL DW DEV ─────────────────────────────────────────────────────────────────
run_step "31/41" "32/41" "SQL DW DEV metadata report" \
  "sql_dw_metadata_report_dev.html" "/home/thedavidporter/sql_dw_metadata_report_dev.html" \
  $VENV /home/thedavidporter/sql_dw_metadata_report.py --env dev

# ── SQL DW PRD ─────────────────────────────────────────────────────────────────
run_step "33/41" "34/41" "SQL DW PRD metadata report" \
  "sql_dw_metadata_report_prd.html" "/home/thedavidporter/sql_dw_metadata_report_prd.html" \
  $VENV /home/thedavidporter/sql_dw_metadata_report.py --env prd

# ── AZURE NETWORKING ───────────────────────────────────────────────────────────
run_step "35/41" "36/41" "VNet metadata report" \
  "vnet_metadata_report.html" "/home/thedavidporter/vnet_metadata_report.html" \
  $VENV /home/thedavidporter/vnet_metadata_report.py

# ── AVD SESSION HOST INVENTORY ─────────────────────────────────────────────────
run_step "37/41" "38/41" "AVD session host inventory report" \
  "avd_metadata_report.html" "/home/thedavidporter/avd_metadata_report.html" \
  $VENV /home/thedavidporter/avd_metadata_report.py

# ── DATA CATALOG (REDCap live pull) ───────────────────────────────────────────
run_step "39/43" "40/43" "Data Catalog (REDCap data sharing requests)" \
  "data_catalog.html" "/home/thedavidporter/data_catalog.html" \
  $VENV /home/thedavidporter/generate_data_catalog.py

# ── DATABRICKS (last — slow PRD notebook walk; timeout 900s prevents indefinite hang) ──
run_step "41/43" "42/43" "Databricks metadata report (all 3 workspaces)" \
  "databricks_metadata_report.html" "/home/thedavidporter/databricks_metadata_report.html" \
  timeout 900 $VENV /home/thedavidporter/databricks_metadata_report.py

# ── INDEX (last — timestamps reflect this run's freshly generated files) ───────
run_step "43/43" "43/43" "metadata index page" \
  "index.html" "/home/thedavidporter/index.html" \
  $VENV /home/thedavidporter/generate_metadata_index.py

# help.html is generated by generate_metadata_index.py — upload it separately
upload_blob "help.html" "/home/thedavidporter/help.html" \
  || echo "[help] ERROR: help.html upload failed — continuing"

echo ""
echo "=============================================="
echo " Done! Reports published to:"
echo " https://zus1idohdevv2dbrkdl.z13.web.core.windows.net/"
echo " https://zus1idohdevv2dbrkdl.z13.web.core.windows.net/synapse_metadata_report_dev.html"
echo " https://zus1idohdevv2dbrkdl.z13.web.core.windows.net/synapse_metadata_delta_dev.html"
echo " https://zus1idohdevv2dbrkdl.z13.web.core.windows.net/synapse_metadata_report_prd.html"
echo " https://zus1idohdevv2dbrkdl.z13.web.core.windows.net/synapse_metadata_delta_prd.html"
echo " https://zus1idohdevv2dbrkdl.z13.web.core.windows.net/adf_metadata_report_dev.html"
echo " https://zus1idohdevv2dbrkdl.z13.web.core.windows.net/adf_metadata_report_prd.html"
echo " https://zus1idohdevv2dbrkdl.z13.web.core.windows.net/adls_metadata_report.html"
echo " https://zus1idohdevv2dbrkdl.z13.web.core.windows.net/logic_apps_metadata_report_dev.html"
echo " https://zus1idohdevv2dbrkdl.z13.web.core.windows.net/logic_apps_metadata_report_prd.html"
echo " https://zus1idohdevv2dbrkdl.z13.web.core.windows.net/keyvault_metadata_report_dev.html"
echo " https://zus1idohdevv2dbrkdl.z13.web.core.windows.net/keyvault_metadata_report_prd.html"
echo " https://zus1idohdevv2dbrkdl.z13.web.core.windows.net/ado_metadata_report.html"
echo " https://zus1idohdevv2dbrkdl.z13.web.core.windows.net/databricks_metadata_report.html"
echo " https://zus1idohdevv2dbrkdl.z13.web.core.windows.net/sql_dw_metadata_report_dev.html"
echo " https://zus1idohdevv2dbrkdl.z13.web.core.windows.net/sql_dw_metadata_report_prd.html"
echo " https://zus1idohdevv2dbrkdl.z13.web.core.windows.net/vnet_metadata_report.html"
echo " https://zus1idohdevv2dbrkdl.z13.web.core.windows.net/avd_metadata_report.html"
echo " https://zus1idohdevv2dbrkdl.z13.web.core.windows.net/data_catalog.html"
echo " https://zus1idohdevv2dbrkdl.z13.web.core.windows.net/help.html"
echo "=============================================="

# ── FAILURE SUMMARY ────────────────────────────────────────────────────────────
if [ ${#FAILED_STEPS[@]} -gt 0 ]; then
  echo ""
  echo "!! ${#FAILED_STEPS[@]} step(s) failed:"
  for s in "${FAILED_STEPS[@]}"; do
    echo "   $s"
  done
  exit 1
fi
