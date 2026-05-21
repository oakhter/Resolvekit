"""
Check DB — inspect tables, row counts, and column structure in DATABASE_URL.

  knowledge schema: retrieval/vector tables
  ops schema      : operational tables

Run with:
    python scripts/check_db.py
    python scripts/check_db.py --recent   # also show 5 most recent rows per table
"""
import sys
import json
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import psycopg2
from psycopg2.extras import RealDictCursor
from backend.core import config
from backend.db.schema import _safe_schema_name

SEP  = "─" * 80
SEP2 = "═" * 80


def _get_tables(conn, schema: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = %s AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """, (schema,))
        return [row[0] for row in cur.fetchall()]


def _get_columns(conn, schema: str, table: str) -> list[dict]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
        """, (schema, table))
        return [dict(r) for r in cur.fetchall()]


def _row_count(conn, schema: str, table: str) -> int:
    schema = _safe_schema_name(schema)
    with conn.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}"')
        return int(cur.fetchone()[0])


def _recent_rows(conn, schema: str, table: str, columns: list[dict], limit: int = 5) -> list[dict]:
    schema = _safe_schema_name(schema)
    col_names = [c["column_name"] for c in columns]
    order = next((c for c in ("created_at", "id") if c in col_names), None)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        if order:
            cur.execute(f'SELECT * FROM "{schema}"."{table}" ORDER BY "{order}" DESC LIMIT %s', (limit,))
        else:
            cur.execute(f'SELECT * FROM "{schema}"."{table}" LIMIT %s', (limit,))
        return [dict(r) for r in cur.fetchall()]


def _compact(val) -> str:
    if isinstance(val, (dict, list)):
        val = json.dumps(val)
    s = str(val)
    return s[:200] + "..." if len(s) > 200 else s


def _print_schema(conn, schema: str, label: str, show_recent: bool) -> None:
    print(f"\n{SEP2}")
    print(f"  {label}")
    print(SEP2)

    try:
        tables = _get_tables(conn, schema)
    except Exception as e:
        print(f"  ERROR: {e}")
        return

    if not tables:
        print("  No tables found.")
        return

    for table in tables:
        try:
            cols  = _get_columns(conn, schema, table)
            count = _row_count(conn, schema, table)

            print(f"\n  {table}  ({count:,} rows, {len(cols)} cols)")
            print(f"  {SEP[:60]}")
            for col in cols:
                print(f"    {col['column_name']:<30} {col['data_type']}")

            if show_recent:
                rows = _recent_rows(conn, schema, table, cols)
                if rows:
                    print(f"\n  Last {len(rows)} rows:")
                    for i, row in enumerate(rows, 1):
                        print(f"\n    [{i}]")
                        for k, v in row.items():
                            print(f"      {k}: {_compact(v)}")
                else:
                    print("\n  (no rows)")

        except Exception as e:
            print(f"  ERROR reading {table}: {e}")


def main():
    parser = argparse.ArgumentParser(description="Check DATABASE_URL schemas")
    parser.add_argument("--recent", action="store_true", help="Show 5 most recent rows per table")
    args = parser.parse_args()

    print(f"\n{SEP2}")
    print("  ResolveKit — DB Inspector")
    print(SEP2)

    if not config.DATABASE_URL:
        print("\n  WARNING: DATABASE_URL not set.")
    else:
        try:
            conn = psycopg2.connect(config.DATABASE_URL, connect_timeout=5)
            _print_schema(conn, config.KNOWLEDGE_SCHEMA, f"{config.KNOWLEDGE_SCHEMA} schema (retrieval/vector)", args.recent)
            _print_schema(conn, config.OPS_SCHEMA, f"{config.OPS_SCHEMA} schema (operational)", args.recent)
            conn.close()
        except Exception as e:
            print(f"\n  DATABASE_URL connection failed: {e}")

    print()


if __name__ == "__main__":
    main()
