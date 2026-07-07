#!/usr/bin/env python3
"""
Quick test to check if your current Azure login has access to Microsoft Graph
Teams chat endpoints. Run this to see what permissions you have.
"""

import subprocess
import json
import urllib.request
import urllib.error

def get_token():
    result = subprocess.run(
        ["az", "account", "get-access-token",
         "--resource", "https://graph.microsoft.com",
         "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"az login required: {result.stderr.strip()}")
    return result.stdout.strip()

def graph_get(token, endpoint):
    url = f"https://graph.microsoft.com/v1.0{endpoint}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read()), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode()}"

def main():
    print("=" * 60)
    print(" Microsoft Graph API Access Test")
    print("=" * 60)

    print("\n[1] Getting token from Azure CLI...")
    try:
        token = get_token()
        print("  Token obtained successfully.")
    except RuntimeError as e:
        print(f"  FAILED: {e}")
        return

    print("\n[2] Testing /me (basic profile)...")
    data, err = graph_get(token, "/me")
    if data:
        print(f"  OK — Logged in as: {data.get('displayName')} ({data.get('mail')})")
    else:
        print(f"  FAILED: {err}")

    print("\n[3] Testing /me/chats (Teams chat list)...")
    data, err = graph_get(token, "/me/chats")
    if data:
        chats = data.get("value", [])
        print(f"  OK — Found {len(chats)} chat(s). Graph API access to Teams is working!")
    else:
        print(f"  FAILED: {err}")
        print("  -> You may need Chat.Read permission consented via an app registration.")

    print("\n[4] Testing /me/chats with messages (sample first chat)...")
    data, err = graph_get(token, "/me/chats?$top=1")
    if data and data.get("value"):
        chat_id = data["value"][0]["id"]
        msgs, err2 = graph_get(token, f"/me/chats/{chat_id}/messages?$top=3")
        if msgs:
            print(f"  OK — Can read messages. Found {len(msgs.get('value', []))} message(s) in first chat.")
        else:
            print(f"  FAILED reading messages: {err2}")
    elif err:
        print(f"  Skipped (chats endpoint failed above).")

    print("\n" + "=" * 60)

if __name__ == "__main__":
    main()
