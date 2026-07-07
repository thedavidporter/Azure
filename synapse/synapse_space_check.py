#!/usr/bin/env python3
"""
Azure Synapse space consumption report — dev + prd
Uses sys.dm_pdw_nodes_db_partition_stats for accurate per-table storage.
"""

import struct
import subprocess

import pyodbc

DRIVER = "{ODBC Driver 18 for SQL Server}"

INSTANCES = [
    ("DEV", "zus1-idoh-dev-v2-sql-server.database.windows.net", "zus1-idoh-dev-v2-sql-dw"),
    ("PRD", "zus1-idoh-prd-v1-sql-server.database.windows.net", "zus1-idoh-prd-v1-sql-dw"),
]

SPACE_SQL = """
SELECT
    s.name                                                    AS schema_name,
    t.name                                                    AS table_name,
    SUM(p.row_count)                                         AS row_count,
    SUM(p.used_page_count)     * 8.0 / 1024 / 1024          AS used_gb,
    SUM(p.reserved_page_count) * 8.0 / 1024 / 1024          AS reserved_gb
FROM sys.dm_pdw_nodes_db_partition_stats p
JOIN sys.tables  t ON t.object_id  = p.object_id
JOIN sys.schemas s ON s.schema_id  = t.schema_id
WHERE p.index_id <= 1
GROUP BY s.name, t.name
ORDER BY used_gb DESC
"""

TOTAL_SQL = """
SELECT
    SUM(used_page_count)     * 8.0 / 1024 / 1024  AS total_used_gb,
    SUM(reserved_page_count) * 8.0 / 1024 / 1024  AS total_reserved_gb
FROM sys.dm_pdw_nodes_db_partition_stats
"""


def get_token():
    result = subprocess.run(
        ["az", "account", "get-access-token",
         "--resource", "https://database.windows.net",
         "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"az login required: {result.stderr.strip()}")
    return result.stdout.strip()


def connect(server, database, token):
    token_bytes  = token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
    conn_str = (f"Driver={DRIVER};Server=tcp:{server},1433;"
                f"Database={database};Encrypt=yes;TrustServerCertificate=no;")
    return pyodbc.connect(conn_str, attrs_before={1256: token_struct})


def query(conn, sql):
    cur = conn.cursor()
    cur.execute(sql)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def hr(label=""):
    print(f"\n{'─'*60}  {label}")


def main():
    print("Fetching Azure token…")
    token = get_token()
    print("Token acquired.\n")

    for env, server, database in INSTANCES:
        hr(f"{env}  ({database})")
        try:
            conn   = connect(server, database, token)
            totals = query(conn, TOTAL_SQL)[0]
            tables = query(conn, SPACE_SQL)
            conn.close()
        except Exception as e:
            print(f"  ERROR connecting: {e}")
            continue

        used_gb     = totals["total_used_gb"]     or 0
        reserved_gb = totals["total_reserved_gb"] or 0
        print(f"  Total used      : {used_gb:>10.2f} GB")
        print(f"  Total reserved  : {reserved_gb:>10.2f} GB")
        print(f"  Tables queried  : {len(tables)}")

        # per-schema rollup
        schema_gb: dict[str, float] = {}
        for t in tables:
            schema_gb[t["schema_name"]] = schema_gb.get(t["schema_name"], 0) + (t["used_gb"] or 0)

        print(f"\n  {'Schema':<30} {'Used (GB)':>10}")
        print(f"  {'─'*30} {'─'*10}")
        for schema, gb in sorted(schema_gb.items(), key=lambda x: -x[1]):
            print(f"  {schema:<30} {gb:>10.2f}")

        # top 10 tables
        top10 = [t for t in tables if (t["used_gb"] or 0) > 0][:10]
        if top10:
            print(f"\n  Top tables by size:")
            print(f"  {'Schema.Table':<50} {'Rows':>14} {'Used (GB)':>10}")
            print(f"  {'─'*50} {'─'*14} {'─'*10}")
            for t in top10:
                name = f"{t['schema_name']}.{t['table_name']}"
                rows = t["row_count"] or 0
                gb   = t["used_gb"]   or 0
                print(f"  {name:<50} {rows:>14,} {gb:>10.2f}")

    print()


if __name__ == "__main__":
    main()
