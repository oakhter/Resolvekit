#!/usr/bin/env python3
"""
Diagnostics database checker.

Usage:
    python diagnostics/db_check.py

Shows:
- every knowledge/ops schema table
- total row count
- total column count
- column names/types
- 10 most recent rows when a sensible ordering column exists
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from dotenv import load_dotenv
except ImportError as e:
    print(f"Missing dependency: {e}")
    sys.exit(1)

load_dotenv(PROJECT_ROOT / ".env")
DATABASE_URL = os.getenv("DATABASE_URL")
KNOWLEDGE_SCHEMA = os.getenv("KNOWLEDGE_SCHEMA", "knowledge")
OPS_SCHEMA = os.getenv("OPS_SCHEMA", "ops")

SEP = "─" * 90
SEP2 = "═" * 90


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set in .env")
    return psycopg2.connect(DATABASE_URL)


def get_tables(conn, schema: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = %s
              AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """,
            (schema,),
        )
        return [row[0] for row in cur.fetchall()]


def get_columns(conn, schema: str, table_name: str) -> list[dict]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = %s
            ORDER BY ordinal_position
            """,
            (schema, table_name),
        )
        return [dict(row) for row in cur.fetchall()]


def get_row_count(conn, schema: str, table_name: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) FROM "{schema}"."{table_name}"')
        return int(cur.fetchone()[0])


def choose_order_column(column_names: list[str]) -> str | None:
    for candidate in ("created_at", "updated_at", "id"):
        if candidate in column_names:
            return candidate
    return None


def get_recent_rows(conn, schema: str, table_name: str, columns: list[dict], limit: int = 10) -> list[dict]:
    column_names = [c["column_name"] for c in columns]
    order_col = choose_order_column(column_names)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        if order_col:
            cur.execute(
                f'SELECT * FROM "{schema}"."{table_name}" ORDER BY "{order_col}" DESC NULLS LAST LIMIT %s',
                (limit,),
            )
        else:
            cur.execute(f'SELECT * FROM "{schema}"."{table_name}" LIMIT %s', (limit,))
        return [dict(row) for row in cur.fetchall()]


def compact(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=True)
    return value


def print_table_report(conn, schema: str, table_name: str) -> None:
    columns = get_columns(conn, schema, table_name)
    rows = get_row_count(conn, schema, table_name)
    recent = get_recent_rows(conn, schema, table_name, columns, limit=10)

    print(f"\n{SEP2}")
    print(f"Table: {schema}.{table_name}")
    print(SEP2)
    print(f"Rows: {rows}")
    print(f"Columns: {len(columns)}")
    print("Column details:")
    for col in columns:
        print(f"  - {col['column_name']}: {col['data_type']}")

    print(SEP)
    print("10 most recent rows:")
    if not recent:
        print("  (no rows)")
        return

    for idx, row in enumerate(recent, start=1):
        print(f"\n  Row {idx}")
        for key, value in row.items():
            rendered = compact(value)
            rendered = str(rendered)
            if len(rendered) > 220:
                rendered = rendered[:217] + "..."
            print(f"    {key}: {rendered}")


def main() -> None:
    print(SEP2)
    print("ResolveKit — Database Diagnostics")
    print(SEP2)

    try:
        with get_conn() as conn:
            found = False
            for schema in (KNOWLEDGE_SCHEMA, OPS_SCHEMA):
                tables = get_tables(conn, schema)
                for table_name in tables:
                    found = True
                    print_table_report(conn, schema, table_name)
            if not found:
                print("No knowledge/ops tables found.")
    except Exception as e:
        print(f"Database connection failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
