#!/usr/bin/env python3
"""
Generates two fake Synapse snapshots with deliberate differences
then runs the delta report so you can preview what it looks like.
Run this once — it does not connect to Synapse.
"""

import json
import os
import subprocess
from datetime import datetime, timedelta

SNAP_DIR = "/home/thedavidporter/snapshots"
os.makedirs(SNAP_DIR, exist_ok=True)

yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
today     = datetime.now().strftime("%Y%m%d")

# ── YESTERDAY snapshot ────────────────────────────────────────────────────────
prev = {
    "generated": (datetime.now() - timedelta(days=1)).isoformat(),
    "database": "zus1-idoh-dev-v2-sql-dw",
    "objects": [
        {"schema_name": "dbo",     "object_name": "FactClaims",       "object_type": "USER_TABLE",            "created": "2024-01-10", "modified": "2026-06-01"},
        {"schema_name": "dbo",     "object_name": "DimProvider",      "object_type": "USER_TABLE",            "created": "2024-01-10", "modified": "2026-06-01"},
        {"schema_name": "dbo",     "object_name": "DimMember",        "object_type": "USER_TABLE",            "created": "2024-01-10", "modified": "2026-06-01"},
        {"schema_name": "dbo",     "object_name": "OldStagingTable",  "object_type": "USER_TABLE",            "created": "2024-03-01", "modified": "2026-05-01"},
        {"schema_name": "rpt",     "object_name": "vw_ClaimSummary",  "object_type": "VIEW",                  "created": "2024-02-01", "modified": "2026-06-01"},
        {"schema_name": "rpt",     "object_name": "vw_ProviderList",  "object_type": "VIEW",                  "created": "2024-02-01", "modified": "2026-06-01"},
        {"schema_name": "etl",     "object_name": "usp_LoadClaims",   "object_type": "SQL_STORED_PROCEDURE",  "created": "2024-01-15", "modified": "2026-06-01"},
    ],
    "columns": [
        {"schema_name": "dbo", "table_name": "FactClaims",  "COLUMN_NAME": "ClaimID",       "DATA_TYPE": "int",          "max_length": None,  "IS_NULLABLE": "NO"},
        {"schema_name": "dbo", "table_name": "FactClaims",  "COLUMN_NAME": "MemberID",      "DATA_TYPE": "int",          "max_length": None,  "IS_NULLABLE": "NO"},
        {"schema_name": "dbo", "table_name": "FactClaims",  "COLUMN_NAME": "ClaimAmount",   "DATA_TYPE": "decimal",      "max_length": "10",  "IS_NULLABLE": "YES"},
        {"schema_name": "dbo", "table_name": "FactClaims",  "COLUMN_NAME": "ClaimDate",     "DATA_TYPE": "date",         "max_length": None,  "IS_NULLABLE": "YES"},
        {"schema_name": "dbo", "table_name": "DimProvider", "COLUMN_NAME": "ProviderID",    "DATA_TYPE": "int",          "max_length": None,  "IS_NULLABLE": "NO"},
        {"schema_name": "dbo", "table_name": "DimProvider", "COLUMN_NAME": "ProviderName",  "DATA_TYPE": "varchar",      "max_length": "100", "IS_NULLABLE": "YES"},
        {"schema_name": "dbo", "table_name": "DimProvider", "COLUMN_NAME": "ProviderNPI",   "DATA_TYPE": "varchar",      "max_length": "10",  "IS_NULLABLE": "YES"},
        {"schema_name": "dbo", "table_name": "DimMember",   "COLUMN_NAME": "MemberID",      "DATA_TYPE": "int",          "max_length": None,  "IS_NULLABLE": "NO"},
        {"schema_name": "dbo", "table_name": "DimMember",   "COLUMN_NAME": "MemberName",    "DATA_TYPE": "varchar",      "max_length": "200", "IS_NULLABLE": "YES"},
    ],
    "row_counts": [
        {"schema_name": "dbo", "table_name": "FactClaims",  "row_count": 1200000},
        {"schema_name": "dbo", "table_name": "DimProvider", "row_count": 45000},
        {"schema_name": "dbo", "table_name": "DimMember",   "row_count": 320000},
        {"schema_name": "dbo", "table_name": "OldStagingTable", "row_count": 5000},
    ],
}

# ── TODAY snapshot — with deliberate differences ───────────────────────────────
curr = {
    "generated": datetime.now().isoformat(),
    "database": "zus1-idoh-dev-v2-sql-dw",
    "objects": [
        {"schema_name": "dbo",     "object_name": "FactClaims",       "object_type": "USER_TABLE",            "created": "2024-01-10", "modified": "2026-06-11"},  # modified
        {"schema_name": "dbo",     "object_name": "DimProvider",      "object_type": "USER_TABLE",            "created": "2024-01-10", "modified": "2026-06-01"},
        {"schema_name": "dbo",     "object_name": "DimMember",        "object_type": "USER_TABLE",            "created": "2024-01-10", "modified": "2026-06-01"},
        # OldStagingTable removed
        {"schema_name": "dbo",     "object_name": "DimDate",          "object_type": "USER_TABLE",            "created": "2026-06-11", "modified": "2026-06-11"},  # new
        {"schema_name": "rpt",     "object_name": "vw_ClaimSummary",  "object_type": "VIEW",                  "created": "2024-02-01", "modified": "2026-06-11"},  # modified
        {"schema_name": "rpt",     "object_name": "vw_ProviderList",  "object_type": "VIEW",                  "created": "2024-02-01", "modified": "2026-06-01"},
        {"schema_name": "etl",     "object_name": "usp_LoadClaims",   "object_type": "SQL_STORED_PROCEDURE",  "created": "2024-01-15", "modified": "2026-06-01"},
        {"schema_name": "etl",     "object_name": "usp_LoadMembers",  "object_type": "SQL_STORED_PROCEDURE",  "created": "2026-06-11", "modified": "2026-06-11"},  # new
    ],
    "columns": [
        {"schema_name": "dbo", "table_name": "FactClaims",  "COLUMN_NAME": "ClaimID",       "DATA_TYPE": "int",          "max_length": None,  "IS_NULLABLE": "NO"},
        {"schema_name": "dbo", "table_name": "FactClaims",  "COLUMN_NAME": "MemberID",      "DATA_TYPE": "int",          "max_length": None,  "IS_NULLABLE": "NO"},
        {"schema_name": "dbo", "table_name": "FactClaims",  "COLUMN_NAME": "ClaimAmount",   "DATA_TYPE": "decimal",      "max_length": "18",  "IS_NULLABLE": "YES"},  # max_length changed
        {"schema_name": "dbo", "table_name": "FactClaims",  "COLUMN_NAME": "ClaimDate",     "DATA_TYPE": "date",         "max_length": None,  "IS_NULLABLE": "YES"},
        {"schema_name": "dbo", "table_name": "FactClaims",  "COLUMN_NAME": "AdjudicatedDate","DATA_TYPE": "date",        "max_length": None,  "IS_NULLABLE": "YES"},  # new column
        {"schema_name": "dbo", "table_name": "DimProvider", "COLUMN_NAME": "ProviderID",    "DATA_TYPE": "int",          "max_length": None,  "IS_NULLABLE": "NO"},
        {"schema_name": "dbo", "table_name": "DimProvider", "COLUMN_NAME": "ProviderName",  "DATA_TYPE": "varchar",      "max_length": "100", "IS_NULLABLE": "YES"},
        # ProviderNPI removed
        {"schema_name": "dbo", "table_name": "DimProvider", "COLUMN_NAME": "NPI",           "DATA_TYPE": "char",         "max_length": "10",  "IS_NULLABLE": "YES"},  # renamed/new
        {"schema_name": "dbo", "table_name": "DimMember",   "COLUMN_NAME": "MemberID",      "DATA_TYPE": "int",          "max_length": None,  "IS_NULLABLE": "NO"},
        {"schema_name": "dbo", "table_name": "DimMember",   "COLUMN_NAME": "MemberName",    "DATA_TYPE": "varchar",      "max_length": "200", "IS_NULLABLE": "YES"},
        {"schema_name": "dbo", "table_name": "DimDate",     "COLUMN_NAME": "DateKey",       "DATA_TYPE": "int",          "max_length": None,  "IS_NULLABLE": "NO"},
        {"schema_name": "dbo", "table_name": "DimDate",     "COLUMN_NAME": "FullDate",      "DATA_TYPE": "date",         "max_length": None,  "IS_NULLABLE": "NO"},
        {"schema_name": "dbo", "table_name": "DimDate",     "COLUMN_NAME": "CalendarYear",  "DATA_TYPE": "smallint",     "max_length": None,  "IS_NULLABLE": "NO"},
    ],
    "row_counts": [
        {"schema_name": "dbo", "table_name": "FactClaims",  "row_count": 1385000},   # +15% increase
        {"schema_name": "dbo", "table_name": "DimProvider", "row_count": 45000},
        {"schema_name": "dbo", "table_name": "DimMember",   "row_count": 298000},    # -7% decrease
        {"schema_name": "dbo", "table_name": "DimDate",     "row_count": 3652},
    ],
}

# ── write snapshots ────────────────────────────────────────────────────────────
prev_file = f"{SNAP_DIR}/synapse_dev_{yesterday}.json"
curr_file = f"{SNAP_DIR}/synapse_dev_{today}.json"

with open(prev_file, "w") as f:
    json.dump(prev, f, indent=2)
with open(curr_file, "w") as f:
    json.dump(curr, f, indent=2)

print(f"Created demo snapshots:")
print(f"  Yesterday : {prev_file}")
print(f"  Today     : {curr_file}")
print()
print("Running delta report...")

result = subprocess.run(
    ["python", "/home/thedavidporter/synapse_metadata_delta_dev.py"],
    capture_output=False
)

if result.returncode == 0:
    print()
    print("Open this file in your browser to preview:")
    print("  /home/thedavidporter/synapse_metadata_delta_dev.html")
    print()
    print("Windows path: \\\\wsl$\\Ubuntu\\home\\thedavidporter\\synapse_metadata_delta_dev.html")
