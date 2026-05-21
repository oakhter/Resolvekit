from sentence_transformers import CrossEncoder
from backend.core.logger import get_logger
from backend.core import config

logger = get_logger(__name__)

_cross_encoder = None


def _get_cross_encoder():
    global _cross_encoder
    if _cross_encoder is None:
        _cross_encoder = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
    return _cross_encoder


def warmup() -> None:
    _get_cross_encoder().predict([["warmup query", "warmup passage"]], batch_size=1)


def run(context: dict) -> dict:
    """
    Fragment 4 — Reranker
    Reranks retrieved chunks using a cross-encoder model.

    Input:  context["retrieved_chunks"]
            context["search_query"]
    Output: context["top_chunks"]
    """
    try:
        if not context:
            raise ValueError("Context is missing")

        chunks = context.get("retrieved_chunks")
        if chunks is None:
            raise ValueError("Retrieved chunks missing — run retriever first")

        query = context.get("search_query")
        if not query:
            raise ValueError("Search query missing from context")

        # ── Handle Empty KB ──────────────────────────────────
        if not chunks:
            logger.warning("No chunks to rerank — knowledge base may be empty")
            context["top_chunks"] = []
            return context

        # ── Limit Input Size (Safety) ─────────────────────────
        MAX_RERANK_INPUT = config.MAX_RERANK_INPUT
        chunks = chunks[:MAX_RERANK_INPUT]

        total_input = len(chunks)
        logger.debug(f"Reranker received {total_input} chunks to score")

        # ── Prepare Input Safely ─────────────────────────────
        pairs = [[query, chunk.get("content", "")] for chunk in chunks]

        # ── Batch Scoring ───────────────────────────────────
        scores = _get_cross_encoder().predict(pairs, batch_size=32)

        # ── Build New List (no mutation) ─────────────────────
        reranked = []
        for i, chunk in enumerate(chunks):
            authority = float(chunk.get("source_authority") or 1.0)
            policy_boost = float(chunk.get("policy_boost") or 0.0)
            reranked.append({
                **chunk,
                "rerank_score": (float(scores[i]) * max(authority, 0.0)) + policy_boost
            })

        # ── Sort by Score ───────────────────────────────────
        reranked = sorted(
            reranked,
            key=lambda x: x["rerank_score"],
            reverse=True
        )

        # ── Take Top K (respect planner route hint if present) ─
        top_k = context.get("route_hints", {}).get("top_k_rerank", config.TOP_K_RERANK)
        top_chunks = reranked[:top_k]

        # ── Logging Insights ─────────────────────────────────
        if reranked:
            best  = reranked[0]["rerank_score"]
            worst = reranked[-1]["rerank_score"]

            logger.debug(
                f"Reranker complete — "
                f"scored {total_input} chunks, "
                f"kept top {len(top_chunks)}, "
                f"score range: {worst:.4f} → {best:.4f}"
            )

            logger.debug(f"Top rerank score: {best:.4f}")

        context["top_chunks"] = top_chunks
        return context

    except ValueError:
        raise
    except Exception as e:
        logger.error(f"Reranker failed: {e}")
        raise ValueError(f"Reranker failed: {e}")


# ── Test Block ───────────────────────────────────────────────
if __name__ == "__main__":
    test_context = {
        "search_query": "demo app login error 403 mobile after update",
        "retrieved_chunks": [
            {
                "id": "chunk_001",
                "content": "Error 403 on mobile login usually occurs after a permission change or failed update. Clear app cache and retry.",
                "metadata": {"source": "troubleshooting.txt"},
                "rrf_score": 0.032
            },
            {
                "id": "chunk_002",
                "content": "The demo mobile app requires version 4.2 or higher. Older versions may return authentication errors after updates.",
                "metadata": {"source": "product_guide.txt"},
                "rrf_score": 0.028
            },
            {
                "id": "chunk_003",
                "content": "Desktop and mobile sessions are handled independently. A working desktop session does not indicate mobile auth is healthy.",
                "metadata": {"source": "handbook.txt"},
                "rrf_score": 0.021
            },
            {
                "id": "chunk_004",
                "content": "When users report login issues on mobile only, first check if the session token has expired. Force logout and back in.",
                "metadata": {"source": "troubleshooting.txt"},
                "rrf_score": 0.019
            },
            {
                "id": "chunk_005",
                "content": "403 errors indicate a forbidden request — typically a permission or auth scope issue, not a network problem.",
                "metadata": {"source": "handbook.txt"},
                "rrf_score": 0.015
            },
        ]
    }

    result = run(test_context)
    top = result["top_chunks"]

    print(f"\nTop {len(top)} chunks after reranking:\n")
    for i, chunk in enumerate(top):
        print(f"Rank {i+1}:")
        print(f"  ID:           {chunk['id']}")
        print(f"  RRF Score:    {chunk['rrf_score']:.4f}")
        print(f"  Rerank Score: {chunk['rerank_score']:.4f}")
        print(f"  Source:       {chunk.get('source_file', '')}")
        print(f"  Preview:      {chunk['content'][:80]}...")

    print("\n✅ Reranker working correctly")
