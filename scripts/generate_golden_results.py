from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.run_golden_eval import DEFAULT_GOLDEN_SET, _read_jsonl

DEFAULT_OUTPUT = ROOT / "eval" / "golden_set" / "last_results.jsonl"


def _citation_source_ids(resolution: dict[str, Any]) -> list[str]:
    citations = (resolution.get("validation") or {}).get("citations") or []
    source_ids = []
    for citation in citations:
        source_id = citation.get("source_id") or citation.get("evidence_id") or citation.get("citation_id")
        if source_id:
            source_ids.append(str(source_id))
    return sorted(set(source_ids))


def _customer_facing_citation_source_ids(resolution: dict[str, Any]) -> list[str]:
    if resolution.get("draft_unavailable_reason"):
        return []
    validation = resolution.get("validation") or {}
    explicit = validation.get("customer_facing_citations") or []
    if explicit:
        source_ids = [
            str(citation.get("source_id") or "")
            for citation in explicit
            if citation.get("source_id")
        ]
        return sorted(set(source_ids))

    citations_by_label = {
        str(citation.get("citation_id") or ""): str(citation.get("source_id") or "")
        for citation in (validation.get("citations") or [])
        if citation.get("citation_id") and citation.get("source_id")
    }
    answer_text = "\n".join(str(resolution.get(key) or "") for key in (
        "root_cause",
        "resolution_steps",
        "draft_email",
        "rendered_reply",
    ))
    used_labels = sorted(set(re.findall(r"\[?(KB-\d+)\]?", answer_text)))
    return sorted(set(
        citations_by_label[label]
        for label in used_labels
        if citations_by_label.get(label)
    ))


def _retrieved_source_ids(resolution: dict[str, Any], fallback: list[str]) -> list[str]:
    signals = resolution.get("retrieval_signals") or {}
    bundles = signals.get("support_context_bundles") or []
    source_ids = []
    for bundle in bundles:
        source_id = bundle.get("source_id")
        if source_id:
            source_ids.append(str(source_id))
    return list(dict.fromkeys(source_ids or fallback))


_REVIEW_ONLY_UNSUPPORTED_PREFIXES = (
    "Response produced a customer draft despite red confidence.",
    "Response answered directly despite red confidence.",
    "Missing context was not acknowledged:",
    "Source conflicts were not surfaced in the response.",
)


def _unsupported_factual_claims(claims: list[Any], answer_text: str = "", abstained: bool = False) -> list[str]:
    factual = []
    has_answer_citation = bool(re.search(r"\[KB-\d+\]", answer_text or ""))
    for claim in claims:
        text = str(claim or "").strip()
        if not text:
            continue
        if abstained and text == "Factual answer fields did not cite approved evidence.":
            continue
        if any(text.startswith(prefix) for prefix in _REVIEW_ONLY_UNSUPPORTED_PREFIXES):
            continue
        if text == "Factual answer fields did not cite approved evidence." and has_answer_citation:
            continue
        factual.append(text)
    return factual


def _cost_usd(resolution: dict[str, Any]) -> float:
    usage_summary = resolution.get("usage_summary") or {}
    if usage_summary.get("total_cost_usd") is not None:
        return round(float(usage_summary.get("total_cost_usd") or 0.0), 8)
    usage = resolution.get("usage") or {}
    total = 0.0
    for stage in usage.values():
        if isinstance(stage, dict):
            total += float(stage.get("cost_usd", 0) or 0)
    return round(total, 8)


def _answer_text(resolution: dict[str, Any]) -> str:
    parts = [
        resolution.get("issue_classification"),
        resolution.get("diagnosis"),
        resolution.get("root_cause"),
        resolution.get("resolution_steps"),
        resolution.get("draft_email"),
        resolution.get("draft_unavailable_reason"),
    ]
    if not resolution.get("draft_unavailable_reason"):
        parts.append(resolution.get("raw"))
    return "\n\n".join(str(part).strip() for part in parts if str(part or "").strip())


def _token_usage(resolution: dict[str, Any]) -> dict[str, int]:
    summary = resolution.get("usage_summary") or {}
    tokens_in = (
        int(summary.get("query_tokens_in", 0) or 0)
        + int(summary.get("response_tokens_in", 0) or 0)
        + int(summary.get("eval_tokens_in", 0) or 0)
    )
    tokens_out = (
        int(summary.get("query_tokens_out", 0) or 0)
        + int(summary.get("response_tokens_out", 0) or 0)
        + int(summary.get("eval_tokens_out", 0) or 0)
    )
    total = int(summary.get("total_tokens", 0) or 0) or tokens_in + tokens_out
    return {"tokens_in": tokens_in, "tokens_out": tokens_out, "total_tokens": total}


def _result_from_resolution(case: dict[str, Any], resolution: dict[str, Any], latency_ms: int) -> dict[str, Any]:
    validation = resolution.get("validation") or {}
    scorer = resolution.get("confidence_scorer") or {}
    eval_score = resolution.get("eval_score") or {}
    evidence_citations = _citation_source_ids(resolution)
    customer_citations = _customer_facing_citation_source_ids(resolution)
    retrieved_source_ids = _retrieved_source_ids(resolution, evidence_citations)
    unsupported_claims = validation.get("unsupported_claims") or []
    answer_text = _answer_text(resolution)
    abstained = bool(resolution.get("draft_unavailable_reason")) or scorer.get("confidence_band") == "red"
    factual_unsupported_claims = _unsupported_factual_claims(unsupported_claims, answer_text, abstained)
    blocked = validation.get("blocked_citations") or []
    raw_blocked = [
        item for item in blocked
        if isinstance(item, dict) and str(item.get("source_type", "")).startswith("raw_")
    ]
    token_usage = _token_usage(resolution)
    return {
        "ticket_id": case["ticket_id"],
        "route": (validation.get("strategist") or {}).get("route") or scorer.get("route") or "",
        "confidence_band": scorer.get("confidence_band", ""),
        "abstained": abstained,
        "fallback_reason": resolution.get("draft_unavailable_reason", ""),
        "validation_passed": validation.get("passed"),
        "cited_source_ids": customer_citations,
        "customer_facing_cited_source_ids": customer_citations,
        "evidence_context_source_ids": evidence_citations,
        "retrieved_source_ids": retrieved_source_ids,
        "answer_text": answer_text,
        "customer_facing_unapproved_citation_count": len(blocked),
        "raw_historical_ticket_citation_count": len(raw_blocked),
        "unsupported_factual_claim_count": len(factual_unsupported_claims),
        "validation_review_finding_count": max(0, len(unsupported_claims) - len(factual_unsupported_claims)),
        "unsupported_claims": factual_unsupported_claims,
        "latency_ms": latency_ms,
        "tokens_in": token_usage["tokens_in"],
        "tokens_out": token_usage["tokens_out"],
        "total_tokens": token_usage["total_tokens"],
        "cost_usd": _cost_usd(resolution),
        "llm_calls_used": int((resolution.get("llm_workflow") or {}).get("llm_calls_used", 0) or 0),
        "faithfulness_score": 1.0 if eval_score.get("faithfulness") == "HIGH" else None,
        "context_relevance_score": 1.0 if retrieved_source_ids else 0.0,
        "answer_relevance_score": 1.0 if validation.get("passed") is True else None,
        "trace_id": resolution.get("trace_id", ""),
    }


def _run_case(client: httpx.Client, base_url: str, api_key: str, case: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "ticket": case["ticket_text"],
        "mode": "suggest",
        "product": case.get("product", ""),
        "access_channel": case.get("platform", ""),
        "permission_level": case.get("role", ""),
    }
    started = time.perf_counter()
    response = client.post(
        f"{base_url.rstrip('/')}/resolve",
        headers={"x-api-key": api_key},
        json=payload,
        timeout=180,
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    response.raise_for_status()
    resolution = response.json().get("resolution", {})
    return _result_from_resolution(case, resolution, latency_ms)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run golden cases against a live ResolveKit API and store result JSONL.")
    parser.add_argument("--golden-set", type=Path, default=DEFAULT_GOLDEN_SET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--base-url", default=os.getenv("BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--api-key", default=os.getenv("API_KEY", ""))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--delay-seconds", type=float, default=2.2)
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    api_key = args.api_key or os.getenv("API_KEY", "")
    if not api_key:
        raise SystemExit("API_KEY is required. Set API_KEY or pass --api-key.")

    rows = _read_jsonl(args.golden_set)
    if args.limit:
        rows = rows[:args.limit]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client() as client, args.output.open("w", encoding="utf-8") as fh:
        for index, case in enumerate(rows, 1):
            result = _run_case(client, args.base_url, api_key, case)
            fh.write(json.dumps(result, sort_keys=True) + "\n")
            fh.flush()
            print(f"{index}/{len(rows)} {case['ticket_id']} {result['confidence_band']} {result['route']}")
            if index < len(rows) and args.delay_seconds > 0:
                time.sleep(args.delay_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
