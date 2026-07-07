az login

#TOKEN=$(az account get-access-token --query accessToken -o tsv)

#Full Bash Script: List All Key Vault Keys + Expiration Dates
#!/usr/bin/env bash

# -----------------------------
# CONFIGURATION
# -----------------------------
SUBSCRIPTION_ID="57493fde-eff8-432f-8574-4f1281bd2ce3"
#SUBSCRIPTION_ID="5d3a4b9c-0e31-477c-9122-bb3be662e2a9"

# Get Azure access token for ARM (management.azure.com)
ARM_TOKEN=$(az account get-access-token --resource https://management.azure.com/ \
    --query accessToken -o tsv)

# Get Azure access token for Key Vault data plane
KV_TOKEN=$(az account get-access-token --resource https://vault.azure.net/ \
    --query accessToken -o tsv)

# -----------------------------
# 1. LIST ALL KEY VAULTS
# -----------------------------
echo "Fetching Key Vaults in subscription: $SUBSCRIPTION_ID"
VAULTS_JSON=$(curl -s \
  -H "Authorization: Bearer $ARM_TOKEN" \
  "https://management.azure.com/subscriptions/$SUBSCRIPTION_ID/providers/Microsoft.KeyVault/vaults?api-version=2024-11-01")

VAULT_NAMES=$(echo "$VAULTS_JSON" | jq -r '.value[].name')

# -----------------------------
# 2. LOOP THROUGH VAULTS
# -----------------------------
for VAULT in $VAULT_NAMES; do
    echo ""
    echo "=============================================="
    echo "🔐 Vault: $VAULT"
    echo "=============================================="

    # -----------------------------
    # 3. LIST KEYS IN THE VAULT
    # -----------------------------
    KEYS_JSON=$(curl -s \
      -H "Authorization: Bearer $KV_TOKEN" \
      "https://$VAULT.vault.azure.net/keys?api-version=7.4")

    KEY_NAMES=$(echo "$KEYS_JSON" | jq -r '.value[].kid' | awk -F/ '{print $NF}')

    # -----------------------------
    # 4. LOOP THROUGH KEYS
    # -----------------------------
    for KEY in $KEY_NAMES; do
        # Get latest version metadata
        KEY_INFO=$(curl -s \
          -H "Authorization: Bearer $KV_TOKEN" \
          "https://$VAULT.vault.azure.net/keys/$KEY?api-version=7.4")

        EXP_TS=$(echo "$KEY_INFO" | jq -r '.attributes.exp // empty')

        if [[ -n "$EXP_TS" ]]; then
            EXP_HUMAN=$(date -d @"$EXP_TS")
        else
            EXP_HUMAN="No expiration"
        fi

        echo "  • Key: $KEY"
        echo "      Expiration: $EXP_HUMAN"
    done
done
