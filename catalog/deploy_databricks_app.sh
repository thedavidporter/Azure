#!/bin/bash
# Deploy HTML reports to Databricks App (idoh-metadata-marketplace)
# Usage: bash deploy_databricks_app.sh [--changelog]
#   --changelog   prompt for a changelog entry and prepend it to changelog.json

DBKS_TOKEN_FILE="$HOME/.databricks_app_token"
DBKS_HOST="https://adb-5757046586469840.0.azuredatabricks.net"
DBKS_CLI="$HOME/.local/bin/databricks"
DBKS_PROJECT="/home/thedavidporter/idoh_metadata_marketplace"
CHANGELOG_FILE="$HOME/changelog.json"

echo "=============================================="
echo " Databricks App Deploy — idoh-metadata-marketplace"
echo "=============================================="

# ── changelog prompt ──────────────────────────────────────────────────────────
if [[ "$1" == "--changelog" ]]; then
  echo ""
  echo "--- Changelog Entry ---"
  read -rp "  Name (short title): " CL_NAME
  if [[ -z "$CL_NAME" ]]; then
    echo "  Skipping changelog (no name provided)."
  else
    read -rp "  Description: " CL_DESC
    CL_DATE=$(date +"%Y-%m-%d")
    CL_TIME=$(date +"%I:%M %p" | sed 's/^0//')
    # escape double-quotes in user input
    CL_NAME_ESC=${CL_NAME//\"/\\\"}
    CL_DESC_ESC=${CL_DESC//\"/\\\"}
    # build the new entry and prepend it to the existing array
    NEW_ENTRY="{\"date\":\"$CL_DATE\",\"time\":\"$CL_TIME\",\"name\":\"$CL_NAME_ESC\",\"description\":\"$CL_DESC_ESC\"}"
    python3 - <<PYEOF
import json, pathlib
p = pathlib.Path("$CHANGELOG_FILE")
entries = json.loads(p.read_text()) if p.exists() else []
entries.insert(0, json.loads('''$NEW_ENTRY'''))
p.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n")
print(f"  Changelog updated ({len(entries)} entries).")
PYEOF
  fi
fi

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
echo "Regenerating HTML reports..."
python3 "$HOME/generate_metadata_index.py" && echo "  index.html"
python3 "$HOME/generate_help.py"           && echo "  help.html"

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

# ── GitHub sync ───────────────────────────────────────────────────────────────
AZURE_REPO="$HOME/Azure"
if [ -d "$AZURE_REPO/.git" ]; then
  echo ""
  echo "Syncing to GitHub..."
  cp "$HOME/generate_metadata_index.py" "$AZURE_REPO/catalog/"
  cp "$HOME/generate_help.py"           "$AZURE_REPO/catalog/"
  cp "$HOME/deploy_databricks_app.sh"   "$AZURE_REPO/catalog/"
  cp "$HOME/changelog.json"             "$AZURE_REPO/catalog/"
  cp "$HOME/spinner_names.json"         "$AZURE_REPO/catalog/"
  cp "$HOME/hotspots.json"              "$AZURE_REPO/catalog/"
  cp "$HOME/PROJECTS_AND_IDEAS.md"      "$AZURE_REPO/"
  CHANGED=$(git -C "$AZURE_REPO" diff --name-only HEAD; git -C "$AZURE_REPO" ls-files --others --exclude-standard)
  if [[ -z "$CHANGED" ]]; then
    echo "  No changes to push."
  else
    git -C "$AZURE_REPO" add \
      catalog/generate_metadata_index.py \
      catalog/generate_help.py \
      catalog/deploy_databricks_app.sh \
      catalog/changelog.json \
      catalog/spinner_names.json \
      catalog/hotspots.json \
      PROJECTS_AND_IDEAS.md
    git -C "$AZURE_REPO" commit -m "$(printf 'Sync catalog — %s\n\nCo-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>' "$(date '+%Y-%m-%d %H:%M')")"
    if git -C "$AZURE_REPO" push origin main 2>&1; then
      echo "  Pushed to github.com/thedavidporter/Azure"
    else
      echo "  WARN: GitHub push failed — local repo is up to date but remote may be stale"
    fi
  fi
else
  echo "  WARN: Azure repo not found at $AZURE_REPO — skipping GitHub sync"
fi
