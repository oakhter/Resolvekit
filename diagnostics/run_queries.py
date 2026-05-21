#!/usr/bin/env python3
"""
Run the ad-hoc diagnostic queries from db_queries.sql directly from Python.

Usage:
    python diagnostics/run_queries.py
    python diagnostics/run_queries.py --query feedback
    python diagnostics/run_queries.py --query counts

Available query names (matched from SQL comment headers):
    tables, feedback, api_calls, response_cache, retrieval_cache, counts
"""
from __future__ import annotations

import os
import sys
import argparse
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
OPS_SCHEMA = os.getenv("OPS_SCHEMA", "ops")
SQL_FILE = Path(__file__).parent / "db_queries.sql"

SEP = "─" * 70


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set in .env")
    conn = psycopg2.connect(DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{OPS_SCHEMA}", public;')
    return conn


def _parse_sql_blocks(path: Path) -> list[tuple[str, str]]:
    """Return [(label, sql), ...] split on '-- ' comment headers."""
    blocks = []
    current_label = "unnamed"
    current_sql_lines: list[str] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            if current_sql_lines:
                sql = "\n".join(current_sql_lines).strip()
                if sql:
                    blocks.append((current_label, sql))
                current_sql_lines = []
            current_label = stripped.lstrip("- ").strip()
        elif stripped:
            current_sql_lines.append(line)

    if current_sql_lines:
        sql = "\n".join(current_sql_lines).strip()
        if sql:
            blocks.append((current_label, sql))

    return blocks


def _run_block(conn, label: str, sql: str) -> None:
    print(f"\n{SEP}")
    print(f"  {label}")
    print(SEP)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql)
            rows = cur.fetchall()
        if not rows:
            print("  (no rows)")
            return
        for i, row in enumerate(rows, 1):
            if len(rows) > 1:
                print(f"\n  [{i}]")
            for k, v in row.items():
                val = str(v)
                if len(val) > 200:
                    val = val[:197] + "..."
                print(f"    {k}: {val}")
    except Exception as e:
        print(f"  ERROR: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run db_queries.sql via Python")
    parser.add_argument("--query", "-q", help="Run only the block whose label contains this string (case-insensitive)")
    args = parser.parse_args()

    if not SQL_FILE.exists():
        print(f"SQL file not found: {SQL_FILE}")
        sys.exit(1)

    blocks = _parse_sql_blocks(SQL_FILE)
    if not blocks:
        print("No SQL blocks found in file.")
        sys.exit(1)

    if args.query:
        term = args.query.lower()
        blocks = [(lbl, sql) for lbl, sql in blocks if term in lbl.lower()]
        if not blocks:
            print(f"No block matching '{args.query}'. Available: {[l for l,_ in _parse_sql_blocks(SQL_FILE)]}")
            sys.exit(1)

    print(f"\nResolveKit — DB Queries ({len(blocks)} block(s))")

    try:
        with get_conn() as conn:
            for label, sql in blocks:
                _run_block(conn, label, sql)
    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit(1)

    print(f"\n{SEP}")


if __name__ == "__main__":
    main()
