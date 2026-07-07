#!/usr/bin/env python3
"""
Read-only MCP server for Azure Synapse using Entra (Azure AD) auth.
Uses az account get-access-token for authentication.
"""

import json
import sys
import subprocess
import struct
import pyodbc

SERVER = "zus1-idoh-dev-v2-sql-server.database.windows.net"
DATABASE = "zus1-idoh-dev-v2-sql-dw"
DRIVER = "{ODBC Driver 18 for SQL Server}"


def get_access_token():
    result = subprocess.run(
        ["az", "account", "get-access-token", "--resource", "https://database.windows.net", "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"az login required: {result.stderr.strip()}")
    return result.stdout.strip()


def get_connection():
    token = get_access_token()
    token_bytes = token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
    conn_str = f"Driver={DRIVER};Server=tcp:{SERVER},1433;Database={DATABASE};Encrypt=yes;TrustServerCertificate=no;"
    conn = pyodbc.connect(conn_str, attrs_before={1256: token_struct})
    return conn


def run_query(sql):
    # Block any non-SELECT statements
    normalized = sql.strip().upper()
    allowed = ("SELECT", "WITH", "SHOW", "DESCRIBE", "EXEC SP_", "EXEC SYS.")
    if not any(normalized.startswith(k) for k in allowed):
        raise ValueError("Only SELECT/WITH queries are allowed in read-only mode.")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(sql)
    columns = [col[0] for col in cursor.description]
    rows = cursor.fetchmany(500)  # limit to 500 rows
    conn.close()
    return {"columns": columns, "rows": [list(r) for r in rows], "row_count": len(rows)}


def list_tables():
    sql = """
        SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE
        FROM INFORMATION_SCHEMA.TABLES
        ORDER BY TABLE_SCHEMA, TABLE_NAME
    """
    return run_query(sql)


def describe_table(schema, table):
    sql = f"""
        SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = '{schema}' AND TABLE_NAME = '{table}'
        ORDER BY ORDINAL_POSITION
    """
    return run_query(sql)


# --- MCP protocol helpers ---

def send(obj):
    msg = json.dumps(obj)
    sys.stdout.write(f"Content-Length: {len(msg)}\r\n\r\n{msg}")
    sys.stdout.flush()


def read_message():
    headers = {}
    while True:
        line = sys.stdin.readline()
        if line == "":
            raise EOFError
        if line in ("\r\n", "\n"):
            break
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip()] = v.strip()
    length = int(headers.get("Content-Length", 0))
    if length == 0:
        raise EOFError
    body = sys.stdin.read(length)
    return json.loads(body)


def handle(msg):
    method = msg.get("method")
    msg_id = msg.get("id")

    if method == "initialize":
        send({
            "jsonrpc": "2.0", "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "synapse-mcp", "version": "1.0.0"},
                "capabilities": {"tools": {}}
            }
        })

    elif method == "tools/list":
        send({
            "jsonrpc": "2.0", "id": msg_id,
            "result": {
                "tools": [
                    {
                        "name": "list_tables",
                        "description": "List all tables in the Synapse database",
                        "inputSchema": {"type": "object", "properties": {}}
                    },
                    {
                        "name": "describe_table",
                        "description": "Describe columns of a specific table",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "schema": {"type": "string", "description": "Table schema, e.g. dbo"},
                                "table": {"type": "string", "description": "Table name"}
                            },
                            "required": ["schema", "table"]
                        }
                    },
                    {
                        "name": "run_query",
                        "description": "Run a read-only SELECT query against Synapse (max 500 rows)",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "sql": {"type": "string", "description": "SELECT query to execute"}
                            },
                            "required": ["sql"]
                        }
                    }
                ]
            }
        })

    elif method == "tools/call":
        tool = msg["params"]["name"]
        args = msg["params"].get("arguments", {})
        try:
            if tool == "list_tables":
                result = list_tables()
            elif tool == "describe_table":
                result = describe_table(args["schema"], args["table"])
            elif tool == "run_query":
                result = run_query(args["sql"])
            else:
                raise ValueError(f"Unknown tool: {tool}")

            send({
                "jsonrpc": "2.0", "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result, default=str)}]
                }
            })
        except Exception as e:
            send({
                "jsonrpc": "2.0", "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {str(e)}"}],
                    "isError": True
                }
            })

    elif method == "notifications/initialized":
        pass  # no response needed

    else:
        if msg_id:
            send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Method not found: {method}"}})


def main():
    while True:
        try:
            msg = read_message()
            if msg:
                handle(msg)
        except EOFError:
            break
        except Exception as e:
            sys.stderr.write(f"Error: {e}\n")
            sys.stderr.flush()


if __name__ == "__main__":
    main()
