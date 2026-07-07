#!/usr/bin/env bash

#az login #You will need to do this once every 24 hours.

# -----------------------------
# CONFIGURATION
# -----------------------------
SUBSCRIPTION_ID="57493fde-eff8-432f-8574-4f1281bd2ce3"

# Get Azure access token for ARM (management.azure.com)
ARM_TOKEN=$(az account get-access-token --resource https://management.azure.com/ \
    --query accessToken -o tsv)

# Get Azure access token for Key Vault data plane
KV_TOKEN=$(az account get-access-token --resource https://vault.azure.net/ \
    --query accessToken -o tsv)

# -----------------------------
# OUTPUT FILE
# -----------------------------
OUTPUT_FILE="KeySecrets.txt"
: > "$OUTPUT_FILE"   # truncate file at start

# -----------------------------
# MAIN OUTPUT BLOCK (tee writes to both stdout and file)
# -----------------------------
{
echo "Fetching Key Vaults in subscription: $SUBSCRIPTION_ID"

VAULTS_JSON=$(curl -s \
  -H "Authorization: Bearer $ARM_TOKEN" \
  "https://management.azure.com/subscriptions/$SUBSCRIPTION_ID/providers/Microsoft.KeyVault/vaults?api-version=2024-11-01")

VAULT_NAMES=$(echo "$VAULTS_JSON" | jq -r '.value[].name')

for VAULT in $VAULT_NAMES; do
    echo ""
    echo "=============================================="
    echo "🔐 Vault: $VAULT"
    echo "=============================================="

    #
    # -----------------------------
    # 3. LIST KEYS
    # -----------------------------
    #
    echo "→ Keys:"
    KEYS_JSON=$(curl -s \
      -H "Authorization: Bearer $KV_TOKEN" \
      "https://$VAULT.vault.azure.net/keys?api-version=7.4")

    KEY_NAMES=$(echo "$KEYS_JSON" | jq -r '.value[].kid' | awk -F/ '{print $NF}')

    for KEY in $KEY_NAMES; do
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

    #
    # -----------------------------
    # 4. LIST SECRETS
    # -----------------------------
    #
    echo ""
    echo "→ Secrets:"
    SECRETS_JSON=$(curl -s \
      -H "Authorization: Bearer $KV_TOKEN" \
      "https://$VAULT.vault.azure.net/secrets?api-version=7.4")

    SECRET_NAMES=$(echo "$SECRETS_JSON" | jq -r '.value[].id' | awk -F/ '{print $NF}')

    for SECRET in $SECRET_NAMES; do
        SECRET_INFO=$(curl -s \
          -H "Authorization: Bearer $KV_TOKEN" \
          "https://$VAULT.vault.azure.net/secrets/$SECRET?api-version=7.4")

        EXP_TS=$(echo "$SECRET_INFO" | jq -r '.attributes.exp // empty')

        if [[ -n "$EXP_TS" ]]; then
            EXP_HUMAN=$(date -d @"$EXP_TS")
        else
            EXP_HUMAN="No expiration"
        fi

        echo "  • Secret: $SECRET"
        echo "      Expiration: $EXP_HUMAN"
    done

done

} | tee -a "$OUTPUT_FILE"
