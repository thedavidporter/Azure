#!/usr/bin/env python3
"""
Notion Home Sensor Reader
Authenticates to api.getnotion.com and lists all sensors and their current state.
Uses the v1 API directly (requests-based, no aionotion dependency).
"""

import requests
import json

EMAIL    = "dbporter2011@gmail.com"
PASSWORD = "BallState2015!"

BASE_URL = "https://api.getnotion.com/api"

def auth(email, password):
    resp = requests.post(
        f"{BASE_URL}/users/sign_in",
        json={"sessions": {"email": email, "password": password}},
        timeout=15
    )
    resp.raise_for_status()
    token = resp.json()["session"]["authentication_token"]
    return token

def api_get(path, token):
    resp = requests.get(
        f"{BASE_URL}/{path}",
        headers={"Authorization": f"Token token={token}"},
        timeout=15
    )
    resp.raise_for_status()
    return resp.json()

def main():
    print("=" * 60)
    print(" Notion Sensor Reader")
    print("=" * 60)

    print("\n[1] Authenticating...")
    try:
        token = auth(EMAIL, PASSWORD)
        print(f"  OK — token obtained.")
    except Exception as e:
        print(f"  FAILED: {e}")
        return

    print("\n[2] Fetching systems (locations)...")
    try:
        data = api_get("systems", token)
        systems = data.get("systems", [])
        sys_map = {s["id"]: s["name"] for s in systems}
        for s in systems:
            print(f"  - {s['name']}  (ID: {s['id']})")
    except Exception as e:
        print(f"  Error: {e}")
        sys_map = {}

    print("\n[3] Fetching bridges (base stations)...")
    try:
        data = api_get("devices", token)
        devices = data.get("devices", [])
        dev_map = {d["id"]: d for d in devices}
        for d in devices:
            loc = sys_map.get(d.get("system_id"), "Unknown location")
            print(f"  - {d.get('name', d.get('hardware_id', 'Bridge'))}  |  {loc}  |  online: {d.get('firmware', {}).get('wifi_ssid','?')}")
    except Exception as e:
        print(f"  Error: {e}")

    print("\n[4] Fetching sensor details...")
    try:
        data = api_get("sensors", token)
        sensors = data.get("sensors", [])
        sensor_map = {s["id"]: s for s in sensors}

        # Fetch full detail for each sensor to get surface_type + missing_at
        detailed = []
        for s in sensors:
            try:
                d = api_get(f"sensors/{s['id']}", token)
                detailed.append(d.get("sensors", s))
            except Exception:
                detailed.append(s)

        print(f"  Found {len(detailed)} sensor(s).\n")
        online_count  = 0
        offline_count = 0
        for s in detailed:
            loc         = sys_map.get(s.get("system_id"), "Unknown")
            surface     = s.get("surface_type", {}).get("name", "unknown") if s.get("surface_type") else "unknown"
            missing_at  = s.get("missing_at")
            last_seen   = s.get("last_reported_at", "never")
            firmware    = s.get("firmware_version", "?")
            status_icon = "OFFLINE" if missing_at else "ONLINE "
            if missing_at:
                offline_count += 1
            else:
                online_count += 1

            print(f"  [{status_icon}] {s.get('name', 'Unnamed')}")
            print(f"           Type      : {surface}")
            print(f"           Location  : {loc}")
            print(f"           Last seen : {last_seen[:10] if last_seen else 'never'}")
            if missing_at:
                print(f"           Went offline: {missing_at[:10]}")
            print(f"           Firmware  : {firmware}")
            print()

        print(f"  Summary: {online_count} online / {offline_count} offline")

    except Exception as e:
        print(f"  Error: {e}")
        sensors = []
        detailed = []

    print("\n[5] Checking per-sensor listeners...")
    if detailed:
        s = detailed[0]
        sid = s["id"]
        sname = s.get("name", str(sid))
        for path in [f"sensors/{sid}/listeners", f"sensors/{sid}/tasks",
                     f"sensors/{sid}/notifications", f"listeners?sensor_id={sid}"]:
            try:
                data = api_get(path, token)
                print(f"  /{path} — OK: {json.dumps(data)[:200]}")
            except Exception as e:
                print(f"  /{path} — {e}")

    print("=" * 60)
    print(" Done.")
    print("=" * 60)

if __name__ == "__main__":
    main()
