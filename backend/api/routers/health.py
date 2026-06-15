from __future__ import annotations

import psycopg2
from fastapi import APIRouter

from backend.core import config
from backend.db.schema import _safe_schema_name


router = APIRouter()


def provider_configured() -> bool:
    if config.ACTIVE_PROVIDER == "mock":
        return True
    return bool(config.OPENAI_API_KEY if config.ACTIVE_PROVIDER == "openai" else config.GEMINI_API_KEY)


@router.get("/health")
def health():
    db_reachable = False
    kb_present = False
    try:
        with psycopg2.connect(config.DATABASE_URL, connect_timeout=2) as conn:
            db_reachable = True
            schema = _safe_schema_name(config.KNOWLEDGE_SCHEMA)
            with conn.cursor() as cur:
                cur.execute(f'SET search_path TO "{schema}", public;')
                cur.execute("SELECT COUNT(*) FROM knowledge_base_identifier")
                kb_present = int(cur.fetchone()[0] or 0) > 0
    except Exception:
        db_reachable = False
    return {
        "status": "ok",
        "service": "ResolveKit",
        "version": "1.0.0",
        "db_reachable": db_reachable,
        "provider_configured": provider_configured(),
        "kb_present": kb_present,
    }
