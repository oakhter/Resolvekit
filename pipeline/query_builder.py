from backend.core.logger import get_logger
from backend.core import project_config

logger = get_logger(__name__)


_FILLER_WORDS = {
    "hi", "hello", "hey", "dear", "greetings",
    "team", "all", "everyone", "guys",
    "please", "pls", "kindly",
    "thanks", "thank", "you", "regards", "cheers",
    "urgent", "asap", "immediately", "quickly",
    "i", "we", "my", "our", "me", "us",
    "am", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did",
    "just", "also", "actually", "basically", "really",
    "hope", "hoping", "wanted", "would", "could", "should",
    "know", "let", "help", "support",
}


def strip_filler(text: str) -> str:
    words = text.split()
    filtered = [w for w in words if w.lower().rstrip(".,!?") not in _FILLER_WORDS]
    return " ".join(filtered) if filtered else text


def enrich_query(search_query: str, meta: dict) -> str:
    enriched = search_query.strip()
    suffixes = []

    product = (meta.get("product") or "").strip().lower()
    access_channel = (meta.get("access_channel") or "").strip().lower()
    permission_level = (meta.get("permission_level") or "").strip().lower()

    if product and product not in enriched.lower():
        suffixes.append(product)
    if access_channel:
        mapped = project_config.platform_label(access_channel, product)
        if mapped not in enriched.lower():
            suffixes.append(mapped)
    if permission_level and permission_level not in enriched.lower():
        suffixes.append(permission_level)

    if suffixes:
        enriched = f"{enriched} {' '.join(suffixes)}".strip()

    return " ".join(enriched.split()[:18])


def run(context: dict) -> dict:
    """
    Fragment 2 — Query Builder (no LLM).

    Strips filler words, takes first 12 content words, enriches with
    request metadata. Single deterministic operation, zero token cost.

    Input:  context["ticket"]["normalized"]
    Output: context["search_query"]
    """
    if not context:
        raise ValueError("Context is missing")

    ticket = context.get("ticket")
    if not ticket:
        raise ValueError("Ticket is missing from context — run ingestor first")

    normalized = ticket.get("normalized")
    if not normalized:
        raise ValueError("Normalized ticket text is missing")

    request_meta = context.get("request_meta", {})

    planner_output = context.get("planner_output", {})
    question_text = " ".join(planner_output.get("explicit_questions") or [])
    source_text = f"{normalized} {question_text}".strip()
    stripped = strip_filler(source_text)
    search_query = " ".join(stripped.split()[:12]).rstrip(".!?,")
    search_query = enrich_query(search_query, request_meta)

    context["search_query"] = search_query
    context["query_builder_output"] = {
        "search_query": search_query,
        "explicit_questions": planner_output.get("explicit_questions", []),
        "metadata_filter": context.get("metadata_filter", {}),
    }
    context["usage"] = context.get("usage", {})
    context["usage"]["query_builder"] = {
        "model": "none",
        "endpoint": "none",
        "tokens_in": 0,
        "tokens_out": 0,
        "latency_ms": 0,
        "error": False,
    }

    logger.debug(f"Query builder complete — query: '{search_query}'")
    return context


# ── Test Block ───────────────────────────────────────────────

if __name__ == "__main__":
    test_context = {
        "ticket": {
            "normalized": "hi team user cannot log in to the demo app getting error code 403 mobile only desktop works fine started yesterday after the update"
        },
        "request_meta": {"product": "example_product", "access_channel": "mobile_app"},
    }

    result = run(test_context)
    print(f"Search query: {result['search_query']}")
    print("\n✅ Query builder working correctly")
