#!/usr/bin/env python3
"""
teams_presence_monitor.py — Monitor a Microsoft Teams user's presence via Graph API.
Uses a service principal (client credentials) for unattended cron job auth.

SETUP (one-time, done by an Azure AD admin):
  bash setup_teams_presence_app.sh
  The script outputs a credentials.json block — paste it into ~/.teams_presence/credentials.json.

USAGE:
  python3 teams_presence_monitor.py user@state.in.us
  python3 teams_presence_monitor.py user@state.in.us --log /path/to/custom.log

CRON EXAMPLE (every 5 minutes):
  */5 * * * * /home/thedavidporter/.venv/bin/python /home/thedavidporter/teams_presence_monitor.py user@state.in.us

State is persisted in ~/.teams_presence/ so changes are detected across cron runs.
Log file defaults to ~/teams_presence_<email>.log.
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import requests

# ── config ─────────────────────────────────────────────────────────────────────

GRAPH      = "https://graph.microsoft.com/v1.0"
TOKEN_URL  = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
STATE_DIR  = Path("/home/thedavidporter/.teams_presence")
CREDS_FILE = STATE_DIR / "credentials.json"
LOG_DIR    = Path("/home/thedavidporter")

AVAIL_LABELS = {
    "Available":         "Available",
    "AvailableIdle":     "Available (Idle)",
    "Away":              "Away",
    "BeRightBack":       "Be Right Back",
    "Busy":              "Busy",
    "BusyIdle":          "Busy (Idle)",
    "DoNotDisturb":      "Do Not Disturb",
    "Offline":           "Offline",
    "PresenceUnknown":   "Unknown",
}

ACTIVITY_LABELS = {
    "Available":               "Available",
    "Away":                    "Away",
    "BeRightBack":             "Be Right Back",
    "Busy":                    "Busy",
    "DoNotDisturb":            "Do Not Disturb",
    "InACall":                 "In a Call",
    "InAConferenceCall":       "In a Conference Call",
    "Inactive":                "Inactive",
    "InAMeeting":              "In a Meeting",
    "Offline":                 "Offline",
    "OffWork":                 "Off Work",
    "OutOfOffice":             "Out of Office",
    "Presenting":              "Presenting",
    "UrgentInterruptionsOnly": "Urgent Interruptions Only",
    "Unknown":                 "Unknown",
}

# ── credentials & token (client credentials flow) ──────────────────────────────

def load_credentials():
    if not CREDS_FILE.exists():
        raise FileNotFoundError(
            f"Credentials file not found: {CREDS_FILE}\n"
            "Ask your Azure AD admin to run: bash setup_teams_presence_app.sh\n"
            "Then paste the output into ~/.teams_presence/credentials.json"
        )
    return json.loads(CREDS_FILE.read_text())

def get_token(creds):
    r = requests.post(
        TOKEN_URL.format(tenant=creds["tenant_id"]),
        data={
            "grant_type":    "client_credentials",
            "client_id":     creds["client_id"],
            "client_secret": creds["client_secret"],
            "scope":         "https://graph.microsoft.com/.default",
        },
        timeout=15,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Token request failed ({r.status_code}): {r.text[:300]}")
    return r.json()["access_token"]

# ── Graph helpers ───────────────────────────────────────────────────────────────

def graph_get(token, path):
    try:
        r = requests.get(
            f"{GRAPH}{path}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"Request error on {path}: {exc}")
    if r.status_code == 403:
        raise PermissionError(
            f"403 on {path} — Presence.Read.All not yet consented.\n"
            "Re-run --setup and ask an admin to approve the consent prompt."
        )
    if r.status_code == 404:
        raise ValueError(f"404 on {path} — user not found.")
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} on {path}: {r.text[:200]}")
    return r.json()

def resolve_user_id(token, email):
    data = graph_get(token, f"/users/{email}?$select=id,displayName,mail")
    return data["id"], data.get("displayName", email)

def get_presence(token, user_id):
    data = graph_get(token, f"/users/{user_id}/presence")
    return data.get("availability", "Unknown"), data.get("activity", "Unknown")

# ── state persistence ───────────────────────────────────────────────────────────

def state_path(email):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    safe = email.replace("@", "_at_").replace(".", "_")
    return STATE_DIR / f"{safe}.json"

def load_state(email):
    p = state_path(email)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}

def save_state(email, data):
    state_path(email).write_text(json.dumps(data, indent=2))

# ── formatting ──────────────────────────────────────────────────────────────────

def fmt(avail, activity):
    a = AVAIL_LABELS.get(avail, avail)
    b = ACTIVITY_LABELS.get(activity, activity)
    return a if a.lower() == b.lower() else f"{a} / {b}"

# ── main ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Monitor Teams presence and log state changes.")
    parser.add_argument("email", help="Teams user email address to monitor")
    parser.add_argument("--log", help="Log file path (default: ~/teams_presence_<email>.log)")
    args = parser.parse_args()

    email    = args.email.strip().lower()
    safe     = email.replace("@", "_at_").replace(".", "_")
    log_file = args.log or str(LOG_DIR / f"teams_presence_{safe}.log")

    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log = logging.getLogger()

    # ── credentials & token ────────────────────────────────────────────────────
    try:
        creds = load_credentials()
        token = get_token(creds)
    except (FileNotFoundError, RuntimeError) as exc:
        log.error(str(exc))
        sys.exit(1)

    # ── resolve user ID (cached) ───────────────────────────────────────────────
    state   = load_state(email)
    user_id = state.get("user_id")
    display = state.get("display_name", email)

    if not user_id:
        try:
            user_id, display = resolve_user_id(token, email)
            state["user_id"]      = user_id
            state["display_name"] = display
            save_state(email, state)
        except (ValueError, PermissionError, RuntimeError) as exc:
            log.error(str(exc))
            sys.exit(1)

    # ── fetch presence ─────────────────────────────────────────────────────────
    try:
        avail, activity = get_presence(token, user_id)
    except (PermissionError, RuntimeError) as exc:
        log.error(str(exc))
        sys.exit(1)

    now           = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prev_avail    = state.get("availability")
    prev_activity = state.get("activity")
    changed       = (avail != prev_avail or activity != prev_activity)

    if changed:
        if prev_avail:
            log.info(f"{display} ({email})  {fmt(prev_avail, prev_activity)}  →  {fmt(avail, activity)}")
        else:
            log.info(f"{display} ({email})  initial state  →  {fmt(avail, activity)}")

        state["availability"]   = avail
        state["activity"]       = activity
        state["last_change"]    = now
        state["last_heartbeat"] = now
        save_state(email, state)
    else:
        last_hb = state.get("last_heartbeat", "")
        if not last_hb or last_hb[:13] != now[:13]:
            log.info(f"{display} ({email})  still  {fmt(avail, activity)}")
            state["last_heartbeat"] = now
            save_state(email, state)


if __name__ == "__main__":
    main()
