from __future__ import annotations

from pipeline import responder


def abstention_response(reason: str, scorer_result: dict | None = None, context: dict | None = None) -> dict:
    scorer_result = scorer_result or {
        "confidence_score": 0.0,
        "uncertainty_score": 1.0,
        "confidence_band": "red",
        "retrieval_strength": "none",
        "source_coverage": "none",
        "source_conflict_detected": False,
        "missing_context": [],
        "abstention_reason": reason,
        "recommended_action": "escalate",
    }
    ticket_preview = ""
    if context:
        ticket_preview = context.get("ticket", {}).get("cleaned", "")[:240]
    return responder.apply_output_preferences({
        "mode": "suggest",
        "issue_classification": "Insufficient approved information",
        "diagnosis": reason,
        "root_cause": "I do not have enough approved information to answer safely.",
        "resolution_steps": "Escalate for human review or add an approved KB, policy, release note, or known issue source.",
        "sources": "",
        "confidence": "LOW",
        "confidence_scorer": scorer_result,
        "draft_email": "",
        "draft_unavailable_reason": "Draft unavailable because no approved customer-facing source supports a safe answer.",
        "validation": {
            "passed": False,
            "errors": [reason],
            "warnings": [],
            "blocked_citations": [],
            "unsupported_claims": [],
            "review_required": True,
        },
        "retrieval_signals": {
            "top_score": 0.0,
            "score_gap": 0.0,
            "rerank_scores": [],
            "retrieved_chunk_ids": [],
            "used_retrieval_cache": bool(context.get("retrieval_cache_hit", False)) if context else False,
            "used_response_cache": False,
        },
        "request_context": {
            "product": context.get("request_meta", {}).get("product", "") if context else "",
            "permission_level": context.get("request_meta", {}).get("permission_level", "") if context else "",
            "access_channel": context.get("request_meta", {}).get("access_channel", "") if context else "",
        },
        "ticket_preview": ticket_preview,
    })


def replace_with_abstention(resolution: dict, abstention: dict) -> dict:
    preserved = {
        key: resolution[key]
        for key in ("cache_key", "from_cache", "usage")
        if key in resolution
    }
    resolution.clear()
    resolution.update(abstention)
    resolution.update(preserved)
    resolution["canonical_resolution"] = {
        key: value
        for key, value in abstention.items()
        if key not in {"canonical_resolution", "raw", "rendered_reply", "structured_reply"}
    }
    return resolution
