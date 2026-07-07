#!/bin/bash
# setup_teams_presence_app.sh
# Run this as an Azure AD admin to create the app registration for Teams Presence Monitor.
# Takes about 30 seconds. Outputs the credentials to paste into credentials.json.
#
# Usage (admin runs this):
#   az login
#   bash setup_teams_presence_app.sh

set -e

echo ""
echo "Creating Teams Presence Monitor app registration..."
APP_ID=$(az ad app create \
  --display-name "Teams Presence Monitor" \
  --query appId -o tsv)
echo "  App ID: $APP_ID"

echo "Creating service principal..."
az ad sp create --id "$APP_ID" --output none

echo "Adding Presence.Read.All permission..."
# 00000003-0000-0000-c000-000000000000 = Microsoft Graph
# 9c7a330d-35b3-4aa1-963d-cb2b9f927841 = Presence.Read.All (Application)
az ad app permission add \
  --id "$APP_ID" \
  --api 00000003-0000-0000-c000-000000000000 \
  --api-permissions 9c7a330d-35b3-4aa1-963d-cb2b9f927841=Role \
  --output none

echo "Granting admin consent..."
az ad app permission admin-consent --id "$APP_ID"

echo "Creating client secret (expires 2 years)..."
SECRET=$(az ad app credential reset \
  --id "$APP_ID" \
  --years 2 \
  --query password -o tsv)

TENANT_ID=$(az account show --query tenantId -o tsv)

echo ""
echo "=============================================="
echo " Done! Send these values to the requester:"
echo "=============================================="
echo ""
echo "mkdir -p ~/.teams_presence"
echo "cat > ~/.teams_presence/credentials.json << 'EOF'"
echo "{"
echo "  \"tenant_id\":     \"$TENANT_ID\","
echo "  \"client_id\":     \"$APP_ID\","
echo "  \"client_secret\": \"$SECRET\""
echo "}"
echo "EOF"
echo "chmod 600 ~/.teams_presence/credentials.json"
echo ""
echo "=============================================="
