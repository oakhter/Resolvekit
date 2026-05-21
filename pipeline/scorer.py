import json
from pipeline.cache import get_conn
from backend.core.logger import get_logger

logger = get_logger(__name__)


def _get_gold_chunks(cache_key: str) -> set:
    """Return chunk IDs from thumbs-up feedback for this response cache_key."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT retrieved_chunk_ids FROM feedback "
                    "WHERE cache_key = %s AND rating = 'thumbs_up'",
                    (cache_key,)
                )
                rows = cur.fetchall()
        gold = set()
        for row in rows:
            raw = row[0]
            ids = json.loads(raw) if isinstance(raw, str) else (raw or [])
            gold.update(ids)
        return gold
    except Exception as e:
        logger.debug(f"Gold chunk lookup failed: {e}")
        return set()


def compute(retrieved_ids: list, cache_key: str) -> dict:
    if not retrieved_ids or not cache_key:
        return {"precision": None, "recall": None, "gold_set_size": 0}

    gold = _get_gold_chunks(cache_key)
    if not gold:
        return {"precision": None, "recall": None, "gold_set_size": 0}

    retrieved = set(retrieved_ids)
    overlap = retrieved & gold
    return {
        "precision": round(len(overlap) / len(retrieved), 4) if retrieved else 0.0,
        "recall":    round(len(overlap) / len(gold), 4)     if gold else 0.0,
        "gold_set_size": len(gold),
    }


def run(context: dict) -> dict:
    top_chunks = context.get("top_chunks", [])
    cache_key  = context.get("resolution", {}).get("cache_key", "")
    retrieved_ids = [str(c.get("id", "")) for c in top_chunks]

    pr = compute(retrieved_ids, cache_key)
    context["precision_recall"] = pr

    if pr["precision"] is not None:
        logger.info(
            f"P&R — precision: {pr['precision']} | "
            f"recall: {pr['recall']} | gold_set: {pr['gold_set_size']}"
        )
    return context
