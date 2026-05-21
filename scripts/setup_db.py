"""
Setup DB — first-run bootstrapper.

Default layout: one Postgres database with two schemas:
  knowledge (retrieval/vector tables)
  ops       (operational tables)

Run once when setting up a new environment:
    python scripts/setup_db.py
"""
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import psycopg2
from backend.core import config
from backend.db.schema import ensure_vector_schema, ensure_ops_schema


def _replace_dbname(url: str, dbname: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(path="/" + dbname))


def _db_exists(url: str, dbname: str) -> bool:
    try:
        maintenance = _replace_dbname(url, "postgres")
        conn = psycopg2.connect(maintenance, connect_timeout=5)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
            exists = cur.fetchone() is not None
        conn.close()
        return exists
    except Exception as e:
        print(f"  Could not query pg_database: {e}")
        return False


def _create_db(url: str, dbname: str) -> None:
    maintenance = _replace_dbname(url, "postgres")
    conn = psycopg2.connect(maintenance, connect_timeout=5)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{dbname}"')
    conn.close()


def _setup_db(url: str, label: str, schema_fn, schema: str) -> bool:
    dbname = urlparse(url).path.lstrip("/")
    print(f"\n  {label}")
    print(f"  Database : {dbname}")

    if _db_exists(url, dbname):
        print(f"  Status   : exists")
    else:
        print(f"  Status   : not found — creating...")
        try:
            _create_db(url, dbname)
            print(f"  Created  : '{dbname}'")
        except Exception as e:
            print(f"  ERROR creating database: {e}")
            return False

    print(f"  Applying schema...")
    try:
        conn = psycopg2.connect(url, connect_timeout=5)
        schema_fn(conn, schema=schema)
        conn.close()
        print(f"  Schema   : applied ({schema})")
        return True
    except Exception as e:
        print(f"  ERROR applying schema: {e}")
        return False


def main():
    print("\n  ResolveKit — DB Setup")
    print("  " + "=" * 44)

    db_url = config.DATABASE_URL

    if not db_url:
        print("  ERROR: DATABASE_URL is not set in .env")
        sys.exit(1)

    vec_ok = _setup_db(db_url, "retrieval/vector store (DATABASE_URL)", ensure_vector_schema, config.KNOWLEDGE_SCHEMA)
    ops_ok = _setup_db(db_url, "operational store (DATABASE_URL)", ensure_ops_schema, config.OPS_SCHEMA)

    print()
    if vec_ok:
        print(f"  {config.KNOWLEDGE_SCHEMA} schema tables: knowledge_base, knowledge_base_identifier,")
        print("                          article_section")
    if ops_ok:
        print(f"  {config.OPS_SCHEMA} schema tables      : response_cache, retrieval_cache, feedback,")
        print("                          draft_run, feedback_label, knowledge_issue, knowledge_patch,")
        print("                          experiment, experiment_arm, experiment_result, api_calls,")
        print("                          evaluation_results, human_review_queue, run_trace")

    if vec_ok and ops_ok:
        print("\n  Database schemas are ready.")
        print("  Next: run the KB loader to populate the knowledge schema.")
        print("  python knowledge_loader/kb_loader.py")
    else:
        print("\n  One or more databases failed — check errors above.")
        sys.exit(1)

    print()


if __name__ == "__main__":
    main()
