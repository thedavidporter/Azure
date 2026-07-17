#!/bin/bash
# Deploy HTML reports to Databricks App (idoh-metadata-marketplace)

DBKS_TOKEN_FILE="$HOME/.databricks_app_token"
DBKS_HOST="https://adb-5757046586469840.0.azuredatabricks.net"
DBKS_CLI="$HOME/.local/bin/databricks"
DBKS_PROJECT="/home/thedavidporter/idoh_metadata_marketplace"

echo "=============================================="
echo " Databricks App Deploy — idoh-metadata-marketplace"
echo "=============================================="

if [ ! -f "$DBKS_TOKEN_FILE" ]; then
  echo "ERROR: Token file not found: $DBKS_TOKEN_FILE"
  echo "       To enable: echo '<your-pat>' > $DBKS_TOKEN_FILE && chmod 600 $DBKS_TOKEN_FILE"
  exit 1
fi

if [ ! -x "$DBKS_CLI" ]; then
  echo "ERROR: Databricks CLI not found or not executable: $DBKS_CLI"
  exit 1
fi

DBKS_TOKEN=$(cat "$DBKS_TOKEN_FILE")

echo ""
echo "Syncing screenshots to bundle..."
for png_file in /home/thedavidporter/idoh_metadata_marketplace/screenshots/*.png; do
  blob_name="screenshots/$(basename "$png_file")"
  az storage blob download \
    --account-name zus1idohdevv2dbrkdl \
    --container-name '$web' \
    --name "$blob_name" \
    --file "$png_file" \
    --auth-mode login \
    --overwrite 2>&1 | grep -iE "error" || echo "  $(basename "$png_file")"
done

echo ""
echo "Compressing and copying reports to bundle..."
for html_file in /home/thedavidporter/*.html; do
  gzip -c "$html_file" > "$DBKS_PROJECT/reports/$(basename "$html_file").gz"
  echo "  $(basename "$html_file")"
done

echo ""
echo "Deploying bundle..."
if (cd "$DBKS_PROJECT" && DATABRICKS_HOST="$DBKS_HOST" DATABRICKS_TOKEN="$DBKS_TOKEN" \
    "$DBKS_CLI" bundle deploy 2>&1); then
  echo ""
  echo "Bundle deploy complete — redeploying app..."
  if DATABRICKS_HOST="$DBKS_HOST" DATABRICKS_TOKEN="$DBKS_TOKEN" \
      "$DBKS_CLI" apps deploy idoh-metadata-marketplace \
      --source-code-path /Workspace/Users/j430074@health.in.gov/.bundle/idoh-metadata-marketplace/default/files \
      2>&1; then
    echo ""
    echo "  App redeployed successfully."
    echo "  App: https://idoh-metadata-marketplace-5757046586469840.0.azure.databricksapps.com"
  else
    echo "  WARN: app redeploy failed — bundle was deployed but app may be stale"
    exit 1
  fi
else
  echo "ERROR: bundle deploy failed — reports not updated"
  exit 1
fi
