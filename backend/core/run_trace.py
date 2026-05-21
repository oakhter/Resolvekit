from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import re
import time
import uuid
from typing import Any

from backend.core import project_config
from backend.core.prompts import prompt_versions


_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}(?!\d)")
_SECRET_RE = re.compile(r"\b(?:sk|pk|rk|api|key|token|secret)[_-]?[A-Za-z0-9_-]{12,}\b", re.I)
_PAYMENT_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
_ACCOUNT_ID_RE = re.compile(
    r"\b((?:account|acct|customer|workspace|tenant)[ _-]?(?:id|number|#)?[:\s#-]*)([A-Z0-9][A-Z0-9_-]{5,})\b",
    re.I,
)
_ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.'-]+(?:\s+[A-Za-z0-9.'-]+){0,5}\s+"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Way|Suite|Ste|Unit|Apt)\b"
    r"(?:[.,]?\s*(?:Suite|Ste|Unit|Apt)\s*[A-Za-z0-9-]+)?",
    re.I,
)
_NAME_CONTEXT_RE = re.compile(
    r"\b((?i:hi|hello|dear|from|this is|name is|customer|user|requester|contact)\s+)"
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})(?=\b|[,.;:])"
)


def redaction_settings() -> dict[str, Any]:
    policy = project_config.load_config("retrieval_policy")
    return policy.get("privacy", {}).get("pii_redaction", {})


def _redaction_entities() -> set[str]:
    settings = redaction_settings()
    if not settings.get("enabled", True):
        return set()
    return {str(entity).upper() for entity in settings.get("entities", [])}


def redact_text(value: str, max_len: int | None = None) -> str:
    text = value or ""
    entities = _redaction_entities()
    text = _EMAIL_RE.sub("[redacted_email]", text)
    text = _PHONE_RE.sub("[redacted_phone]", text)
    text = _SECRET_RE.sub("[redacted_secret]", text)
    text = _PAYMENT_RE.sub("[redacted_payment_identifier]", text)
    if "ACCOUNT_ID" in entities:
        text = _ACCOUNT_ID_RE.sub(lambda match: f"{match.group(1)}[redacted_account_id]", text)
    if "ADDRESS" in entities:
        text = _ADDRESS_RE.sub("[redacted_address]", text)
    if "PERSON" in entities:
        text = _NAME_CONTEXT_RE.sub(lambda match: f"{match.group(1)}[redacted_name]", text)
    if max_len is not None:
        return text[:max_len]
    return text


def redaction_status(original: str, redacted: str) -> dict[str, Any]:
    return {
        "redaction_applied": original != redacted,
        "redaction_status": "redacted" if original != redacted else "checked_no_sensitive_data_found",
        "redaction_provider": redaction_settings().get("provider", "deterministic"),
    }


def redact_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(chunk)
    changed = False
    for key in ("content", "display_text", "embedding_text"):
        if isinstance(redacted.get(key), str):
            before = redacted[key]
            redacted[key] = redact_text(before)
            changed = changed or before != redacted[key]
    redacted["redaction_applied"] = bool(redacted.get("redaction_applied") or changed)
    redacted["redaction_status"] = (
        "redacted" if redacted["redaction_applied"]
        else redacted.get("redaction_status") or "checked_no_sensitive_data_found"
    )
    return redacted


def hash_ticket(ticket_text: str) -> str:
    return hashlib.sha256((ticket_text or "").encode()).hexdigest()


def _safe_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _compact_chunks(chunks: list[dict], limit: int = 12) -> list[dict]:
    compacted = []
    for chunk in chunks[:limit]:
        compacted.append({
            "id": str(chunk.get("id", "")),
            "source_id": str(chunk.get("source_id", "")),
            "document_id": str(chunk.get("document_id", "")),
            "document_version": int(chunk.get("document_version") or 1),
            "chunk_version": int(chunk.get("chunk_version") or 1),
            "source_type": str(chunk.get("source_type", "")),
            "source_ref": str(chunk.get("source_ref") or chunk.get("source_file") or ""),
            "is_approved": bool(chunk.get("is_approved", False)),
            "is_active": bool(chunk.get("is_active", True)),
            "is_customer_facing_allowed": bool(chunk.get("is_customer_facing_allowed", False)),
            "expires_at": str(chunk.get("expires_at") or ""),
            "needs_review_at": str(chunk.get("needs_review_at") or ""),
            "audience_allowed": chunk.get("audience_allowed") or [],
            "score": float(chunk.get("score") or 0.0),
            "rrf_score": float(chunk.get("rrf_score") or 0.0),
            "rerank_score": float(chunk.get("rerank_score") or 0.0),
            "retrieval_reason": str(chunk.get("retrieval_reason", "")),
            "redaction_status": str(chunk.get("redaction_status") or ""),
            "redaction_applied": bool(chunk.get("redaction_applied", False)),
            "content_preview": redact_text(str(chunk.get("display_text") or chunk.get("content") or ""), 220),
        })
    return compacted


@dataclass(frozen=True)
class RunTrace:
    conversation_id: str
    draft_id: str
    trace_id: str
    timestamp: str
    ticket_text_hash: str
    redacted_ticket_preview: str
    config_hash: str
    model_provider: str
    workflow_mode: str
    product: str
    platform: str
    role: str
    planner_output: dict = field(default_factory=dict)
    query_builder_output: dict = field(default_factory=dict)
    vector_results: list[dict] = field(default_factory=list)
    bm25_results: list[dict] = field(default_factory=list)
    rrf_results: list[dict] = field(default_factory=list)
    source_type_merge_results: list[dict] = field(default_factory=list)
    retrieval_per_question: list[dict] = field(default_factory=list)
    retrieval_strategy: dict = field(default_factory=dict)
    context_expansions: list[dict] = field(default_factory=list)
    reranked_results: list[dict] = field(default_factory=list)
    evidence_sent_to_llm: dict = field(default_factory=dict)
    evidence_table: dict = field(default_factory=dict)
    raw_responder_output: str = ""
    scorer_output: dict = field(default_factory=dict)
    source_conflicts: list[dict] = field(default_factory=list)
    evaluator_output: dict = field(default_factory=dict)
    validation_output: dict = field(default_factory=dict)
    final_response: dict = field(default_factory=dict)
    latency_by_stage: dict = field(default_factory=dict)
    stage_events: list[dict] = field(default_factory=list)
    trace_size: dict = field(default_factory=dict)
    token_usage_by_stage: dict = field(default_factory=dict)
    cache_status: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    prompt_versions: dict = field(default_factory=dict)
    redaction_applied: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def build_run_trace(
    context: dict,
    resolution: dict,
    *,
    started_at: float,
    errors: list[str] | None = None,
) -> RunTrace:
    ticket_text = context.get("ticket", {}).get("cleaned") or context.get("ticket_raw", "")
    request_meta = _safe_dict(context.get("request_meta"))
    usage = _safe_dict(resolution.get("usage") or context.get("usage"))
    evidence_bundle = context.get("evidence_bundle")
    workflow = project_config.workflow_settings()

    top_chunks = context.get("top_chunks", [])
    retrieved_chunks = context.get("retrieved_chunks", [])
    expanded = context.get("context_expansions") or [
        {
            "id": str(chunk.get("id", "")),
            "expanded_from": str(chunk.get("expanded_from", "")),
            "retrieval_reason": str(chunk.get("retrieval_reason", "")),
        }
        for chunk in retrieved_chunks
        if chunk.get("expanded_from") or "parent_section" in str(chunk.get("retrieval_reason", ""))
    ]

    final_response = dict(resolution)
    if final_response.get("draft_email"):
        final_response["draft_email"] = redact_text(final_response["draft_email"], 1200)
    if final_response.get("raw"):
        final_response["raw"] = redact_text(final_response["raw"], 1600)
    final_response.setdefault("agent_action", "pending")
    final_response.setdefault("final_sent_text", "")
    final_response.setdefault("edit_distance_ratio", 0)
    final_response.setdefault("edit_distance_tokens", 0)
    final_response.setdefault("citations_kept", [])

    provider_label = str(
        resolution.get("provider")
        or (usage.get("responder") or {}).get("provider")
        or (usage.get("responder") or {}).get("model")
        or ""
    )

    total_ms = int((time.time() - started_at) * 1000)
    current_errors = errors or []
    stage_events = [
        {
            "stage": "total",
            "status": "fail" if current_errors else "ok",
            "started_at": "",
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": total_ms,
            "error_code": "pipeline_error" if current_errors else "",
            "recovery_action": "review_trace" if current_errors else "",
        }
    ]
    trace_size = {
        "estimated_bytes": len(redact_text(str(final_response), 10000)),
        "truncated": False,
        "max_bytes": 200000,
    }

    return RunTrace(
        conversation_id=str(request_meta.get("conversation_id") or f"conv_{uuid.uuid4().hex[:16]}"),
        draft_id=str(request_meta.get("draft_id") or ""),
        trace_id=f"trace_{uuid.uuid4().hex}",
        timestamp=datetime.now(timezone.utc).isoformat(),
        ticket_text_hash=hash_ticket(ticket_text),
        redacted_ticket_preview=redact_text(ticket_text, 360),
        config_hash=request_meta.get("runtime_config_version") or project_config.runtime_fingerprint(),
        model_provider=provider_label,
        workflow_mode=str(workflow.get("mode", "")),
        product=str(request_meta.get("product", "")),
        platform=str(request_meta.get("access_channel", "")),
        role=str(request_meta.get("permission_level", "")),
        planner_output={
            "routing_strategy": context.get("routing_strategy", ""),
            "route_hints": context.get("route_hints", {}),
            "planner_output": context.get("planner_output", {}),
            "metadata_filter": context.get("metadata_filter", {}),
            "request_meta": request_meta,
        },
        query_builder_output=context.get("query_builder_output") or {"search_query": context.get("search_query", "")},
        vector_results=_compact_chunks(context.get("semantic_results", [])),
        bm25_results=_compact_chunks(context.get("keyword_results", [])),
        rrf_results=_compact_chunks(context.get("rrf_results", [])),
        source_type_merge_results=_compact_chunks(context.get("source_type_merge_results", [])),
        retrieval_per_question=context.get("retrieval_per_question", []),
        retrieval_strategy=context.get("retrieval_strategy", {}),
        context_expansions=expanded[:20],
        reranked_results=_compact_chunks(top_chunks),
        evidence_sent_to_llm=evidence_bundle.to_dict() if evidence_bundle else {},
        evidence_table=context.get("evidence_table", {}),
        raw_responder_output=redact_text(str(resolution.get("raw", "")), 1600),
        scorer_output=_safe_dict(resolution.get("confidence_scorer")),
        source_conflicts=context.get("source_conflicts", []),
        evaluator_output=_safe_dict(resolution.get("eval_score") or context.get("eval_score")),
        validation_output=_safe_dict(resolution.get("validation")),
        final_response=final_response,
        latency_by_stage={"total_ms": total_ms},
        stage_events=stage_events,
        trace_size=trace_size,
        token_usage_by_stage=usage,
        cache_status={
            "retrieval_cache_hit": bool(context.get("retrieval_cache_hit", False)),
            "response_cache_hit": bool(resolution.get("from_cache", False)),
        },
        errors=current_errors,
        prompt_versions=prompt_versions(provider_label),
    )
