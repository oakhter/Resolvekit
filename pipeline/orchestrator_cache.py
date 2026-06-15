"""
Ticket-level response cache.

Canonical orchestrator cache: request-fingerprint cache for whole ticket outputs.

Keyed by the normalized ticket text alone (SHA256), giving an early-exit
path that skips query_builder, retriever, and reranker for repeat tickets.
This is a separate lookup from the chunk-keyed response cache in responder.py.
"""
import json
import re
from pipeline.cache import get_conn, hash_key
from backend.core import project_config
from backend.core.logger import get_logger

logger = get_logger(__name__)

CACHE_SCHEMA_VERSION = "ticket-cache-v3-redaction-query-experiment-arm-fix"


def normalize_ticket_for_cache(ticket_text: str) -> str:
    text = (ticket_text or "").lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def build_request_fingerprint(ticket_text: str, request_meta: dict | None = None) -> str:
    meta = request_meta or {}
    payload = {
        "ticket": normalize_ticket_for_cache(ticket_text),
        "product": (meta.get("product") or "").strip().lower(),
        "permission_level": (meta.get("permission_level") or "").strip().lower(),
        "access_channel": (meta.get("access_channel") or "").strip().lower(),
        "experiment_arm": (meta.get("experiment_arm") or "").strip().lower(),
        "runtime_config": meta.get("runtime_config_version") or project_config.runtime_fingerprint(),
        "cache_schema_version": CACHE_SCHEMA_VERSION,
    }
    return hash_key(json.dumps(payload, sort_keys=True))


def _ticket_key(normalized_ticket: str, request_meta: dict | None = None) -> str:
    return "tkt:" + build_request_fingerprint(normalized_ticket, request_meta)


def get_ticket_cache(normalized_ticket: str, request_meta: dict | None = None):
    """Return cached resolution dict if this exact ticket was seen before, else None."""
    key = _ticket_key(normalized_ticket, request_meta)
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT response FROM response_cache WHERE key = %s", (key,)
                )
                row = cur.fetchone()
        if row:
            logger.info("⚡ TICKET-LEVEL CACHE HIT — skipping full pipeline")
            data = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            data["from_cache"] = True
            return data
    except Exception as e:
        logger.warning(f"Ticket cache lookup failed: {e}")
    return None


def save_ticket_cache(normalized_ticket: str, resolution: dict, request_meta: dict | None = None) -> None:
    """Persist resolution under the ticket-level key for future short-circuits."""
    key = _ticket_key(normalized_ticket, request_meta)
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO response_cache (key, response)
                    VALUES (%s, %s)
                    ON CONFLICT (key) DO NOTHING
                    """,
                    (key, json.dumps(resolution)),
                )
    except Exception as e:
        logger.warning(f"Ticket cache save failed: {e}")
