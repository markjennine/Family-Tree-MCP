import sqlite3
import os
import sys


def get_conn() -> sqlite3.Connection:
    path = os.environ.get("FAMILYTREE_DB")
    if not path:
        raise RuntimeError("FAMILYTREE_DB environment variable not set")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def query(sql: str, params: tuple = ()) -> list[dict]:
    try:
        with get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]
    except Exception as e:
        print(f"db.query error: {e}", file=sys.stderr)
        return []


def query_one(sql: str, params: tuple = ()) -> dict | None:
    try:
        with get_conn() as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None
    except Exception as e:
        print(f"db.query_one error: {e}", file=sys.stderr)
        return None


def introspect() -> list[str]:
    rows = query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    return [r["name"] for r in rows]
