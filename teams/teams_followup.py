#!/usr/bin/env python3
"""
Teams Follow-Up Tracker
Track Teams messages you need to respond to within 7 days.

Usage:
  python teams_followup.py add "Person - message description"
  python teams_followup.py check
  python teams_followup.py list
  python teams_followup.py done <id>
  python teams_followup.py remove <id>
"""

import json
import sys
import os
from datetime import datetime, timedelta

DATA_FILE = "/home/thedavidporter/teams_followup.json"
FOLLOWUP_DAYS = 7


def load_data():
    if not os.path.exists(DATA_FILE):
        return []
    with open(DATA_FILE, "r") as f:
        return json.load(f)


def save_data(items):
    with open(DATA_FILE, "w") as f:
        json.dump(items, f, indent=2)


def next_id(items):
    return max((i["id"] for i in items), default=0) + 1


def add_item(description):
    items = load_data()
    item = {
        "id": next_id(items),
        "description": description,
        "added": datetime.now().isoformat(),
        "due": (datetime.now() + timedelta(days=FOLLOWUP_DAYS)).isoformat(),
        "done": False,
    }
    items.append(item)
    save_data(items)
    print(f"  Added #{item['id']}: {description}")
    print(f"  Follow-up due by: {item['due'][:10]}")


def check_items():
    items = load_data()
    now = datetime.now()
    overdue = [i for i in items if not i["done"] and datetime.fromisoformat(i["due"]) <= now]
    upcoming = [i for i in items if not i["done"] and datetime.fromisoformat(i["due"]) > now]

    print("=" * 60)
    print(" Teams Follow-Up Check")
    print(f" {now.strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    if overdue:
        print(f"\n  OVERDUE ({len(overdue)} item{'s' if len(overdue) != 1 else ''}):")
        for i in overdue:
            days_late = (now - datetime.fromisoformat(i["due"])).days
            print(f"  [{i['id']}] {i['description']}")
            print(f"       Added: {i['added'][:10]}  |  Overdue by: {days_late} day{'s' if days_late != 1 else ''}")
    else:
        print("\n  No overdue items.")

    if upcoming:
        print(f"\n  UPCOMING ({len(upcoming)} item{'s' if len(upcoming) != 1 else ''}):")
        for i in upcoming:
            days_left = (datetime.fromisoformat(i["due"]) - now).days
            print(f"  [{i['id']}] {i['description']}")
            print(f"       Added: {i['added'][:10]}  |  Due in: {days_left} day{'s' if days_left != 1 else ''}")

    print()


def list_items():
    items = load_data()
    active = [i for i in items if not i["done"]]
    done = [i for i in items if i["done"]]

    print("=" * 60)
    print(f" Teams Follow-Up List  ({len(active)} active, {len(done)} done)")
    print("=" * 60)

    if not active:
        print("\n  No active items.")
    else:
        print("\n  Active:")
        for i in active:
            due = datetime.fromisoformat(i["due"])
            days_left = (due - datetime.now()).days
            status = f"overdue {abs(days_left)}d" if days_left < 0 else f"due in {days_left}d"
            print(f"  [{i['id']}] {i['description']}  ({status})")

    if done:
        print("\n  Completed:")
        for i in done:
            print(f"  [{i['id']}] {i['description']}  (done: {i.get('completed', 'N/A')[:10]})")
    print()


def mark_done(item_id):
    items = load_data()
    for i in items:
        if i["id"] == item_id:
            i["done"] = True
            i["completed"] = datetime.now().isoformat()
            save_data(items)
            print(f"  Marked #{item_id} as done: {i['description']}")
            return
    print(f"  Item #{item_id} not found.")


def remove_item(item_id):
    items = load_data()
    before = len(items)
    items = [i for i in items if i["id"] != item_id]
    if len(items) < before:
        save_data(items)
        print(f"  Removed #{item_id}.")
    else:
        print(f"  Item #{item_id} not found.")


def usage():
    print(__doc__)


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args:
        check_items()
    elif args[0] == "add" and len(args) >= 2:
        add_item(" ".join(args[1:]))
    elif args[0] == "check":
        check_items()
    elif args[0] == "list":
        list_items()
    elif args[0] == "done" and len(args) == 2:
        mark_done(int(args[1]))
    elif args[0] == "remove" and len(args) == 2:
        remove_item(int(args[1]))
    else:
        usage()
