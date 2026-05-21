from backend.core.logger import get_logger
from backend.core.run_trace import hash_ticket, redact_text, redaction_status
from pipeline.orchestrator_cache import normalize_ticket_for_cache

logger = get_logger(__name__)


def run(context: dict) -> dict:
    """
    Fragment 1 — Ingestor
    Takes raw ticket text and cleans + structures it.

    Input:  context["ticket_raw"]
    Output: context["ticket"]
    """
    try:
        if not context:
            raise ValueError("Context is missing")

        raw = context.get("ticket_raw", "")

        # ── Validate ─────────────────────────────────────────
        if not raw or not raw.strip():
            raise ValueError("Ticket is empty — nothing to ingest")

        # ── Clean ────────────────────────────────────────────
        cleaned = redact_text(raw.strip())
        cleaned = cleaned.replace("\x00", "")
        cleaned = " ".join(cleaned.split())   # collapse whitespace

        # Optional normalization (helps retrieval later)
        normalized = cleaned.lower()

        # ── Structure ────────────────────────────────────────
        ticket = {
            "raw": cleaned,
            "cleaned": cleaned,
            "normalized": normalized,
            "raw_text_hash": hash_ticket(raw.strip()),
            "fingerprint_base": normalize_ticket_for_cache(cleaned),
            "char_count": len(cleaned),
            "word_count": len(cleaned.split()),
            **redaction_status(raw, cleaned),
        }

        # ── Guard Against Too Short ──────────────────────────
        if ticket["word_count"] < 3:
            raise ValueError(f"Ticket too short to process: '{cleaned}'")

        context["ticket"] = ticket

        logger.debug(
            f"Ingestor complete — {ticket['word_count']} words, {ticket['char_count']} chars"
        )

        return context

    except ValueError:
        raise
    except Exception as e:
        logger.error(f"Ingestor failed: {e}")
        raise ValueError(f"Ingestor failed: {e}")


# ── Test Block ───────────────────────────────────────────────

if __name__ == "__main__":
    test_context = {
        "ticket_raw": """
            Hi team,   user cannot log in to the demo app.
            Getting error code 403 on mobile only.
            Desktop works fine. Started yesterday after the update.
        """
    }

    result = run(test_context)

    print(f"Cleaned:    {result['ticket']['cleaned']}")
    print(f"Normalized: {result['ticket']['normalized']}")
    print(f"Word count: {result['ticket']['word_count']}")
    print(f"Char count: {result['ticket']['char_count']}")
    print("\n✅ Ingestor working correctly")
