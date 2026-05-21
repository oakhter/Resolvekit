"""
Rebuild DB — drops the v3.x schemas in DATABASE_URL then recreates them fresh.

  knowledge schema : knowledge_base, knowledge_base_identifier, article_section
  ops schema       : response_cache, retrieval_cache, feedback, draft_run,
                     feedback_label, knowledge_issue, knowledge_patch,
                     experiment, experiment_arm, experiment_result,
                     api_calls, evaluation_results, human_review_queue, run_trace

After running this you MUST re-run the KB loader to reload embeddings.

Run with:
    python scripts/rebuild_db.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import psycopg2
from backend.core import config
from backend.db.schema import _safe_schema_name, ensure_vector_schema, ensure_ops_schema

VECTOR_TABLES = ["article_section", "knowledge_base_identifier", "knowledge_base"]
LEGACY_VECTOR_TABLES = [
    "human_review_queue",
    "evaluation_results",
    "api_calls",
    "feedback",
    "retrieval_cache",
    "response_cache",
    "release_notes",
]
OPS_TABLES = [
    "run_trace",
    "experiment_result",
    "experiment_arm",
    "experiment",
    "knowledge_patch",
    "knowledge_issue",
    "feedback_label",
    "draft_run",
    "human_review_queue",
    "evaluation_results",
    "api_calls",
    "feedback",
    "retrieval_cache",
    "response_cache",
]


def _connect():
    try:
        conn = psycopg2.connect(config.DATABASE_URL)
        conn.autocommit = False
        return conn, []
    except Exception as e:
        return None, [f"DATABASE_URL: {e}"]


def _row_count(conn, table: str) -> str:
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            return str(cur.fetchone()[0])
    except Exception:
        return "?"


def _table_exists(conn, schema: str, table: str) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", (f"{schema}.{table}",))
            return cur.fetchone()[0] is not None
    except Exception:
        return False


def _show_current_state(conn):
    knowledge_schema = _safe_schema_name(config.KNOWLEDGE_SCHEMA)
    ops_schema = _safe_schema_name(config.OPS_SCHEMA)
    print("\n  Current state:")
    print(f"  {'TABLE':<28} {'SCHEMA':<14} {'ROWS':>6}")
    print(f"  {'-'*28} {'-'*14} {'-'*6}")

    for table in VECTOR_TABLES + LEGACY_VECTOR_TABLES:
        if conn and _table_exists(conn, knowledge_schema, table):
            count = _row_count(conn, f"{knowledge_schema}.{table}")
        else:
            count = "missing"
        print(f"  {table:<28} {knowledge_schema:<14} {count:>6}")

    for table in reversed(OPS_TABLES):
        if conn and _table_exists(conn, ops_schema, table):
            count = _row_count(conn, f"{ops_schema}.{table}")
        else:
            count = "missing"
        print(f"  {table:<28} {ops_schema:<14} {count:>6}")
    print()


def _drop_schema(conn, schema: str) -> None:
    schema = _safe_schema_name(schema)
    with conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    conn.commit()
    print(f"  Dropped schema: {schema}")


def main():
    print("\n  ResolveKit — Rebuild DB")
    print("  " + "=" * 44)
    print()
    print("  This script will:")
    print(f"    1. DROP schemas '{config.KNOWLEDGE_SCHEMA}' and '{config.OPS_SCHEMA}' in DATABASE_URL")
    print("    2. Recreate them empty using the current schema")
    print()
    print("  After this, the KB loader MUST be re-run to reload embeddings.")
    print()

    conn, errors = _connect()
    if errors:
        for e in errors:
            print(f"  Connection error — {e}")
    if conn is None:
        print("  Cannot connect to DATABASE_URL. Check .env config.")
        sys.exit(1)

    _show_current_state(conn)

    answer = input("  Type 'rebuild' to confirm, or anything else to cancel: ").strip()
    if answer != "rebuild":
        print("  Cancelled.")
        if conn:
            conn.close()
        return

    print()

    # ── Drop ──────────────────────────────────────────────────
    _drop_schema(conn, config.KNOWLEDGE_SCHEMA)
    _drop_schema(conn, config.OPS_SCHEMA)

    # ── Recreate ──────────────────────────────────────────────
    print()
    try:
        ensure_vector_schema(conn, schema=config.KNOWLEDGE_SCHEMA)
        print(f"  {config.KNOWLEDGE_SCHEMA} schema applied (knowledge_base + retrieval metadata).")
    except Exception as e:
        print(f"  {config.KNOWLEDGE_SCHEMA} schema error: {e}")

    try:
        ensure_ops_schema(conn, schema=config.OPS_SCHEMA)
        print(f"  {config.OPS_SCHEMA} schema applied (6 operational tables).")
    except Exception as e:
        print(f"  {config.OPS_SCHEMA} schema error: {e}")

    print()
    print("  Verifying clean state...")
    _show_current_state(conn)

    conn.close()

    print()
    print("  Rebuild complete. Run the KB loader to reload embeddings:")
    print("  python knowledge_loader/kb_loader.py")
    print()


if __name__ == "__main__":
    main()
