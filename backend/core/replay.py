from __future__ import annotations

from typing import Any

from backend.core import project_config
from backend.core.run_trace import redact_text


def replay_saved_trace(trace: dict[str, Any], *, use_current_config: bool = True) -> dict[str, Any]:
    old_final = trace.get("final_response", {}) or {}
    old_validation = trace.get("validation_output", {}) or {}
    old_scorer = trace.get("scorer_output", {}) or {}
    old_retrieval = _ids(trace.get("reranked_results", []))
    old_citations = _citation_ids(old_validation)

    current_config_hash = project_config.runtime_fingerprint() if use_current_config else trace.get("config_hash", "")
    current = {
        "config_hash": current_config_hash,
        "retrieval_result_ids": old_retrieval,
        "citation_ids": old_citations,
        "confidence_band": old_scorer.get("confidence_band", ""),
        "validation_passed": old_validation.get("passed"),
        "final_response_preview": redact_text(_response_preview(old_final), 800),
        "latency_ms": (trace.get("latency_by_stage") or {}).get("total_ms", 0),
        "cost_usd": _trace_cost(trace),
    }

    old = {
        "config_hash": trace.get("config_hash", ""),
        "retrieval_result_ids": old_retrieval,
        "citation_ids": old_citations,
        "confidence_band": old_scorer.get("confidence_band", ""),
        "validation_passed": old_validation.get("passed"),
        "final_response_preview": redact_text(_response_preview(old_final), 800),
        "latency_ms": (trace.get("latency_by_stage") or {}).get("total_ms", 0),
        "cost_usd": _trace_cost(trace),
    }

    return {
        "trace_id": trace.get("trace_id", ""),
        "mode": "current_config" if use_current_config else "same_config_hash",
        "private_replay": False,
        "redacted_ticket_preview": redact_text(trace.get("redacted_ticket_preview", ""), 360),
        "old": old,
        "current": current,
        "diff": {
            "same_config_hash": old["config_hash"] == current["config_hash"],
            "retrieval_added": [item for item in current["retrieval_result_ids"] if item not in old["retrieval_result_ids"]],
            "retrieval_removed": [item for item in old["retrieval_result_ids"] if item not in current["retrieval_result_ids"]],
            "citation_added": [item for item in current["citation_ids"] if item not in old["citation_ids"]],
            "citation_removed": [item for item in old["citation_ids"] if item not in current["citation_ids"]],
            "confidence_band_changed": old["confidence_band"] != current["confidence_band"],
            "validation_changed": old["validation_passed"] != current["validation_passed"],
            "final_response_changed": old["final_response_preview"] != current["final_response_preview"],
            "latency_ms_delta": int(current["latency_ms"] or 0) - int(old["latency_ms"] or 0),
            "cost_usd_delta": round(float(current["cost_usd"] or 0.0) - float(old["cost_usd"] or 0.0), 6),
        },
    }


def _ids(rows: list[dict[str, Any]]) -> list[str]:
    return [str(row.get("id", "")) for row in rows if row.get("id")]


def _citation_ids(validation: dict[str, Any]) -> list[str]:
    citations = validation.get("citations") or []
    return [
        str(citation.get("source_id") or citation.get("evidence_id") or citation.get("citation_id") or "")
        for citation in citations
        if citation
    ]


def _response_preview(response: dict[str, Any]) -> str:
    return "\n".join(
        str(response.get(key, ""))
        for key in ("issue_classification", "diagnosis", "root_cause", "resolution_steps", "draft_email")
        if response.get(key)
    )


def _trace_cost(trace: dict[str, Any]) -> float:
    usage = trace.get("token_usage_by_stage") or {}
    if isinstance(usage.get("cost_usd"), (int, float)):
        return float(usage["cost_usd"])
    return 0.0
