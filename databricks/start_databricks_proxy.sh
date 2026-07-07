#!/bin/bash
echo "Fetching Databricks token..."
export DATABRICKS_TOKEN=$(az account get-access-token \
  --resource 2ff814a6-3304-4ab8-85cb-cd0e6f879c1d \
  --subscription 57493fde-eff8-432f-8574-4f1281bd2ce3 \
  --query accessToken -o tsv 2>/dev/null)

if [ -z "$DATABRICKS_TOKEN" ]; then
  echo "ERROR: Could not get token. Are you logged in? Run: az login"
  exit 1
fi

echo "Token acquired. Starting LiteLLM proxy on port 4000..."
echo "Point Claude Code at: http://localhost:4000"
echo "Token expires in ~1 hour — re-run this script to refresh."
echo ""
litellm --config ~/litellm_config.yaml --port 4000
