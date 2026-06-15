import time
import json
import re
from pipeline import ingestor, query_builder, retriever, reranker, responder
from pipeline import planner, evaluator, scorer
from pipeline import validation
from pipeline.orchestrator_cache import get_ticket_cache, save_ticket_cache, build_request_fingerprint
from pipeline.confidence import compute as compute_confidence, compute_scorer_result
from pipeline.cache import get_conn, save_run_trace, save_draft_run, create_review_queue_item
from backend.core.evidence import EvidenceBundle
from backend.core.logger import get_logger, log_timing
from backend.core.run_trace import build_run_trace, redact_text, redact_chunk
from pipeline.conflicts import detect_source_conflicts
from pipeline import evidence_table
from backend.core import project_config
from backend.core.orchestrator_response import abstention_response as _abstention_response
from backend.core.orchestrator_response import replace_with_abstention as _replace_with_abstention

logger = get_logger(__name__)


def safe_run(step_name, func, context):
    try:
        logger.info(f"Step — {step_name}")
        with log_timing(step_name, logger):
            return func(context)
    except Exception as e:
        logger.error(f"{step_name} failed: {e}", exc_info=True)
        raise ValueError(f"{step_name} failed: {e}")


def _collect_retrieval_signals(context: dict) -> dict:
    """Derive retrieval diagnostics from top_chunks for feedback/logging."""
    top_chunks = context.get("top_chunks", [])
    scores = [c.get("rerank_score", 0.0) for c in top_chunks]
    top_score = scores[0] if scores else 0.0
    score_gap = top_score - scores[1] if len(scores) >= 2 else top_score
    bundles = []
    for index, chunk in enumerate(top_chunks, 1):
        bundles.append({
            "label": f"KB-{index}",
            "id": str(chunk.get("id", "")),
            "source_id": str(chunk.get("source_id", "")),
            "title": chunk.get("title", ""),
            "source_file": chunk.get("source_file", ""),
            "source_type": chunk.get("source_type", ""),
            "chunk_type": chunk.get("chunk_type", ""),
            "heading_path": chunk.get("heading_path", ""),
            "condition_flags": chunk.get("condition_flags") or [],
            "retrieval_reason": chunk.get("retrieval_reason", "initial_match"),
            "expanded_from": chunk.get("expanded_from", ""),
            "score": round(float(chunk.get("rerank_score", 0.0) or 0.0), 4),
        })
    return {
        "top_score": round(top_score, 4),
        "score_gap": round(score_gap, 4),
        "rerank_scores": [round(s, 4) for s in scores],
        "retrieved_chunk_ids": [str(c.get("id", "")) for c in top_chunks],
        "support_context_bundles": bundles,
        "source_selection": [
            f"{bundle['label']} {bundle['source_file']} via {bundle['retrieval_reason']}"
            for bundle in bundles
        ],
        "used_retrieval_cache": bool(context.get("retrieval_cache_hit", False)),
        "used_response_cache": False,  # updated after responder
    }


def _select_direct_evidence_chunks(chunks: list[dict], context: dict, max_chunks: int = 3) -> list[dict]:
    """Keep direct, source-diverse evidence for customer-facing drafting."""
    if not chunks:
        return []
    route = str(context.get("routing_strategy") or "").strip().lower()
    route_priority = {
        "policy": ["policy", "official_help_article", "knowledge_base", "release_note", "known_issue"],
        "billing": ["policy", "official_help_article", "knowledge_base"],
        "access": ["official_help_article", "knowledge_base", "policy"],
        "bug": ["known_issue", "official_help_article", "knowledge_base", "release_note"],
        "release_change": ["release_note", "known_issue", "official_help_article", "knowledge_base"],
        "how_to": ["official_help_article", "knowledge_base", "faq", "policy"],
        "general": ["official_help_article", "knowledge_base", "policy", "faq"],
    }.get(route, [])
    priority_index = {source_type: index for index, source_type in enumerate(route_priority)}
    stop_words = {
        "cannot", "find", "need", "needs", "help", "issue", "request", "customer",
        "please", "after", "before", "with", "from", "that", "this", "workspace",
        "mobile", "website", "agent", "admin", "conversation", "conversations",
    }

    def terms(text: str) -> set[str]:
        values = set()
        for raw in re.findall(r"[a-z0-9]+", text.lower().replace("_", " ")):
            if len(raw) < 4 or raw in stop_words:
                continue
            values.add(raw[:-1] if raw.endswith("s") and len(raw) > 4 else raw)
        return values

    ticket_text = str((context.get("ticket") or {}).get("cleaned") or "")
    ticket_terms = terms(ticket_text)

    def label_overlap(chunk: dict) -> int:
        label_text = " ".join(str(chunk.get(key) or "") for key in (
            "source_id",
            "source_file",
            "title",
            "heading_path",
        ))
        return len(ticket_terms & terms(label_text))

    overlap_available = bool(ticket_terms and any(label_overlap(chunk) > 0 for chunk in chunks))
    ordered_chunks = sorted(
        chunks,
        key=lambda chunk: (
            (
                priority_index.get(str(chunk.get("source_type") or chunk.get("doc_type") or ""), len(priority_index))
                if not ticket_terms or label_overlap(chunk) > 0
                else len(priority_index)
            ),
            -label_overlap(chunk),
            -float(chunk.get("rerank_score") or chunk.get("policy_score") or chunk.get("rrf_score") or 0.0),
        ),
    )
    selected = []
    seen_sources = set()
    for chunk in ordered_chunks:
        if overlap_available and label_overlap(chunk) == 0:
            continue
        source_key = str(chunk.get("source_id") or chunk.get("source_file") or chunk.get("id") or "")
        reason = str(chunk.get("retrieval_reason") or "")
        if source_key in seen_sources and any(marker in reason for marker in ("sibling", "neighbor", "parent_section")):
            continue
        if source_key in seen_sources:
            continue
        selected.append(chunk)
        seen_sources.add(source_key)
        if len(selected) >= max_chunks:
            break
    return selected or chunks[:max_chunks]


def _save_human_review(resolution: dict, routing_strategy: str, eval_score: dict, full_ticket: str = "") -> None:
    """Persist gatekeeper-flagged items to the ops.human_review_queue table."""
    reasons, severity, source_issue_type, needs_escalation, escalation_reason = _review_queue_signals(
        resolution, routing_strategy, eval_score
    )
    if not reasons and not needs_escalation:
        return
    try:
        validation_data = resolution.get("validation", {})
        auditor = validation_data.get("auditor", {})
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO human_review_queue
                        (trace_id, cache_key, ticket_preview, full_ticket, confidence, confidence_band,
                         severity, sla_marker, gatekeeper_reason, source_issue_type, auditor_flags, needs_escalation,
                         escalation_reason, route, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        resolution.get("trace_id", ""),
                        resolution.get("cache_key", ""),
                        resolution.get("ticket_preview", ""),
                        redact_text(full_ticket),
                        resolution.get("confidence", ""),
                        resolution.get("confidence_scorer", {}).get("confidence_band", ""),
                        severity,
                        _sla_marker(severity),
                        "; ".join(reasons),
                        source_issue_type,
                        json.dumps(auditor),
                        needs_escalation,
                        escalation_reason,
                        routing_strategy,
                        "open",
                    ),
                )
    except Exception as e:
        logger.error(f"Human review queue save failed: {e}")


def _review_queue_signals(resolution: dict, routing_strategy: str, eval_score: dict | None = None) -> tuple[list[str], str, str, bool, str]:
    eval_score = eval_score or {}
    validation_data = resolution.get("validation", {}) or {}
    scorer_result = resolution.get("confidence_scorer", {}) or {}
    strategist = validation_data.get("strategist", {}) or {}
    flags = eval_score.get("flags", [])
    reasons = []
    source_issue_type = ""
    if resolution.get("confidence") == "LOW":
        reasons.append("low confidence")
    if scorer_result.get("confidence_band") == "red":
        reasons.append("red confidence")
    if scorer_result.get("source_conflict_detected") or validation_data.get("source_conflicts"):
        reasons.append("source conflict")
        source_issue_type = "conflict"
    if validation_data.get("passed") is False:
        reasons.append("validator failure")
        source_issue_type = source_issue_type or "validation"
    if validation_data.get("blocked_citations"):
        source_issue_type = source_issue_type or "citation"
    if routing_strategy in {"billing", "access", "policy"}:
        reasons.append("policy-heavy or sensitive case")
    if eval_score.get("faithfulness") == "LOW":
        reasons.append("low faithfulness")
    if eval_score.get("completeness") == "LOW":
        reasons.append("low completeness")
    if flags:
        reasons.append(f"eval flags: {', '.join(flags)}")
    needs_escalation = bool(strategist.get("needs_escalation"))
    escalation_reason = strategist.get("escalation_reason", "")
    if needs_escalation:
        reasons.append("escalation path")
    severity = "high" if scorer_result.get("confidence_band") == "red" or needs_escalation else "medium"
    return sorted(set(reasons)), severity, source_issue_type, needs_escalation, escalation_reason


def _sla_marker(severity: str) -> str:
    return "review_today" if severity == "high" else "standard_review"


def _save_review_queue_item(resolution: dict, context: dict, reason: str = "") -> None:
    routing_strategy = context.get("routing_strategy", "general")
    eval_score = context.get("eval_score", {})
    reasons, severity, source_issue_type, needs_escalation, escalation_reason = _review_queue_signals(
        resolution, routing_strategy, eval_score
    )
    if reason:
        reasons.append(reason)
    if not reasons:
        return
    create_review_queue_item({
        "trace_id": resolution.get("trace_id", ""),
        "cache_key": resolution.get("cache_key", ""),
        "ticket_preview": resolution.get("ticket_preview", ""),
        "full_ticket": redact_text(context.get("ticket", {}).get("cleaned", "")),
        "confidence": resolution.get("confidence", ""),
        "confidence_band": resolution.get("confidence_scorer", {}).get("confidence_band", ""),
        "severity": severity,
        "sla_marker": _sla_marker(severity),
        "gatekeeper_reason": "; ".join(sorted(set(reasons))),
        "source_issue_type": source_issue_type,
        "auditor_flags": {"review_reason": reason} if reason else {},
        "needs_escalation": needs_escalation,
        "escalation_reason": escalation_reason,
        "route": routing_strategy,
        "status": "open",
    })


def _attach_and_save_trace(context: dict, resolution: dict, started_at: float, errors: list[str] | None = None) -> dict:
    _redact_resolution_outputs(resolution)
    trace = build_run_trace(context, resolution, started_at=started_at, errors=errors)
    trace_dict = trace.to_dict()
    resolution["trace_id"] = trace.trace_id
    trace_dict["final_response"]["trace_id"] = trace.trace_id
    draft_run_id = save_draft_run({
        "trace_id": trace.trace_id,
        "ticket_text_hash": trace.ticket_text_hash,
        "ticket_preview_redacted": trace.redacted_ticket_preview,
        "final_draft": resolution.get("draft_email") or resolution.get("resolution_steps") or resolution.get("diagnosis") or "",
        "confidence_band": resolution.get("confidence_scorer", {}).get("confidence_band", ""),
        "confidence_score": resolution.get("confidence_scorer", {}).get("score", 0),
        "validation_status": "blocked" if resolution.get("validation", {}).get("gatekeeper_flagged") else "ok",
        "citations_used": resolution.get("sources", []),
        "source_ids": resolution.get("retrieval_signals", {}).get("source_selection", []),
        "config_hash": trace.config_hash,
    })
    resolution["draft_run_id"] = draft_run_id
    resolution["conversation_id"] = trace_dict.get("conversation_id", "")
    trace_dict["final_response"]["draft_run_id"] = draft_run_id
    trace_dict["draft_id"] = draft_run_id
    trace_dict["final_response"]["conversation_id"] = trace_dict.get("conversation_id", "")
    save_run_trace(trace_dict)
    return resolution


def _redact_resolution_outputs(resolution: dict) -> None:
    for key in ("issue_classification", "diagnosis", "root_cause", "resolution_steps", "draft_email", "raw"):
        if isinstance(resolution.get(key), str):
            resolution[key] = redact_text(resolution[key])
    validation_data = resolution.get("validation", {})
    if isinstance(validation_data, dict):
        validation_data["redaction_applied"] = True
    resolution["redaction_applied"] = True


def run(ticket_raw: str, request_meta: dict | None = None) -> dict:
    logger.info("Pipeline started")
    start = time.time()

    request_meta = dict(request_meta or {})
    if request_meta.get("mode", "suggest") != "suggest":
        raise ValueError("Unsupported mode. This v3.x demo is suggest-only.")
    request_meta["runtime_config_version"] = project_config.runtime_fingerprint()
    workflow = project_config.workflow_settings()
    max_llm_calls = int(workflow.get("max_llm_calls", 2) or 0)
    stages = workflow.get("stages", {})
    llm_calls_used = 0

    def stage_allowed(stage_name: str) -> bool:
        stage = stages.get(stage_name, {})
        if not stage.get("enabled", stage_name == "responder"):
            return False
        if stage.get("counts_toward_budget", True):
            return llm_calls_used < max_llm_calls
        return True

    def count_stage(stage_name: str) -> None:
        nonlocal llm_calls_used
        if stages.get(stage_name, {}).get("counts_toward_budget", True):
            llm_calls_used += 1

    # Promote product + platform into the top-level context so every downstream
    # step can read them with a simple context.get(). Platform normalization is
    # config-driven so product setup owns platform vocabulary.
    product_for_retrieval = project_config.normalize_product_for_retrieval(request_meta.get("product", ""))
    context = {
        "ticket_raw":    ticket_raw,
        "request_meta":  request_meta,
        "usage":         {},
        "product":       product_for_retrieval,
        "platform":      project_config.normalize_platform_for_retrieval(
            request_meta.get("access_channel", ""),
            request_meta.get("product", ""),
        ),
    }

    # ── Step 1 — Ingestor ────────────────────────────────────
    context = safe_run("Ingestor", ingestor.run, context)

    # ── Ticket-level early-exit cache ────────────────────────
    normalized_ticket = context.get("ticket", {}).get("cleaned", "").lower().strip()
    request_fingerprint = build_request_fingerprint(normalized_ticket, request_meta)
    if normalized_ticket:
        cached = get_ticket_cache(normalized_ticket, request_meta)
        if cached:
            cached = responder.apply_output_preferences(cached)
            cached["mode"] = "suggest"
            cached["request_fingerprint"] = request_fingerprint
            cached.setdefault("confidence_scorer", {
                "confidence_score": 0.0,
                "uncertainty_score": 1.0,
                "confidence_band": "red" if cached.get("confidence") == "LOW" else "yellow",
                "retrieval_strength": "cached",
                "source_coverage": "unknown",
                "source_conflict_detected": False,
                "missing_context": [],
                "abstention_reason": "",
                "recommended_action": "cautious_answer",
            })
            cached["request_context"] = {
                "product": request_meta.get("product", ""),
                "permission_level": request_meta.get("permission_level", ""),
                "access_channel": request_meta.get("access_channel", ""),
            }
            cached.setdefault("usage_summary", {
                "query_tokens_in": 0, "query_tokens_out": 0,
                "response_tokens_in": 0, "response_tokens_out": 0,
                "eval_tokens_in": 0, "eval_tokens_out": 0,
                "total_tokens": 0,
            })
            cached.setdefault("retrieval_signals", {
                "top_score": 0.0, "score_gap": 0.0,
                "rerank_scores": [], "retrieved_chunk_ids": [],
                "used_retrieval_cache": False, "used_response_cache": False,
            })
            cached["retrieval_signals"]["used_response_cache"] = True
            cached = _attach_and_save_trace(context, cached, start)
            logger.info(f"Pipeline short-circuited in {time.time() - start:.2f}s")
            return cached

    # ── Step 2 — Planner (Conditional Router) ───────────────
    context = safe_run("Planner", planner.run, context)

    # ── Step 3 — Query Builder ───────────────────────────────
    context = safe_run("Query Builder", query_builder.run, context)
    logger.debug(f"Query: {context.get('search_query')}")

    # ── Step 4 — Retriever ───────────────────────────────────
    context = safe_run("Retriever", retriever.run, context)
    retrieved = context.get("retrieved_chunks", [])
    logger.debug(f"Retrieved chunks: {len(retrieved)}")

    if not retrieved:
        logger.warning("No retrieval results — returning fallback")
        scorer_result = compute_scorer_result([], evidence_bundle=EvidenceBundle.from_chunks([])).to_dict()
        fallback = {
            "mode": "suggest",
            "issue_classification": "Unknown",
            "diagnosis": "",
            "root_cause": "No relevant knowledge base results found",
            "resolution_steps": "Manual investigation required",
            "sources": "",
            "confidence": "LOW",
            "confidence_scorer": scorer_result,
            "draft_email": "",
            "draft_unavailable_reason": "Draft unavailable because no relevant approved knowledge base results were found.",
            "retrieval_signals": {
                "top_score": 0.0, "score_gap": 0.0,
                "rerank_scores": [], "retrieved_chunk_ids": [],
                "used_retrieval_cache": bool(context.get("retrieval_cache_hit", False)),
                "used_response_cache": False,
            },
            "request_context": {
                "product": request_meta.get("product", ""),
                "permission_level": request_meta.get("permission_level", ""),
                "access_channel": request_meta.get("access_channel", ""),
            },
            "ticket_preview": context.get("ticket", {}).get("cleaned", "")[:240],
            "usage_summary": {
                "query_tokens_in": 0, "query_tokens_out": 0,
                "response_tokens_in": 0, "response_tokens_out": 0,
                "eval_tokens_in": 0, "eval_tokens_out": 0,
                "total_tokens": 0,
            },
            "request_fingerprint": request_fingerprint,
        }
        fallback = responder.apply_output_preferences(fallback)
        fallback = _attach_and_save_trace(context, fallback, start)
        _save_review_queue_item(fallback, context, "no retrieval results")
        return fallback

    # ── Step 5 — Reranker ────────────────────────────────────
    context = safe_run("Reranker", reranker.run, context)

    # ── Dynamic top-k: trim to 3 when signals are strong and clear ──
    top_chunks = context.get("top_chunks", [])
    top_chunks = _apply_support_ops_retrieval_controls(top_chunks, context)
    top_chunks = _select_direct_evidence_chunks(top_chunks, context)
    context["top_chunks"] = top_chunks
    if len(top_chunks) > 3:
        scores = [c.get("rerank_score", 0.0) for c in top_chunks]
        top_score = scores[0] if scores else 0.0
        score_gap = top_score - scores[1] if len(scores) >= 2 else top_score
        if top_score >= 7.0 and score_gap >= 3.0:
            context["top_chunks"] = top_chunks[:3]
            logger.debug(f"Trimmed to top 3 chunks (score={top_score:.2f}, gap={score_gap:.2f})")

    logger.debug(f"Top chunks for responder: {len(context.get('top_chunks', []))}")

    evidence_bundle = EvidenceBundle.from_chunks(context.get("top_chunks", []), audience="customer")
    context["evidence_bundle"] = evidence_bundle
    if evidence_bundle.blocked:
        logger.warning(f"Source safety blocked {len(evidence_bundle.blocked)} candidate chunk(s)")
    if not evidence_bundle.citations:
        scorer_result = compute_scorer_result(
            context.get("top_chunks", []),
            evidence_bundle=evidence_bundle,
        ).to_dict()
        fallback = _abstention_response(
            scorer_result.get("abstention_reason") or "No approved customer-facing source supports the answer.",
            scorer_result,
            context,
        )
        fallback["request_fingerprint"] = request_fingerprint
        fallback.setdefault("usage_summary", {
            "query_tokens_in": 0, "query_tokens_out": 0,
            "response_tokens_in": 0, "response_tokens_out": 0,
            "eval_tokens_in": 0, "eval_tokens_out": 0,
            "total_tokens": 0,
        })
        fallback = _attach_and_save_trace(context, fallback, start)
        _save_review_queue_item(fallback, context, "no approved customer-facing evidence")
        return fallback
    approved_ids = {chunk.evidence_id for chunk in evidence_bundle.chunks}
    context["top_chunks"] = [
        chunk for chunk in context.get("top_chunks", [])
        if str(chunk.get("id", "")) in approved_ids
    ]
    context["top_chunks"] = [redact_chunk(chunk) for chunk in context["top_chunks"]]
    evidence_bundle = EvidenceBundle.from_chunks(context["top_chunks"], audience="customer")
    context["evidence_bundle"] = evidence_bundle
    source_conflicts = [conflict.to_dict() for conflict in detect_source_conflicts(context["top_chunks"])]
    context["source_conflicts"] = source_conflicts
    condition_context = validation._condition_context(context.get("top_chunks", []), request_meta)
    context["condition_context"] = condition_context
    if project_config.experiment_settings("advanced_reasoning").get("evidence_table", False):
        context["evidence_table"] = evidence_table.build(context)

    # ── Step 6 — Responder (LLM call 1) ─────────────────────
    if not stage_allowed("responder"):
        raise ValueError("Workflow configuration disabled the responder LLM stage")
    context = safe_run("Responder", responder.run, context)

    resolution = context["resolution"]
    if not resolution.get("from_cache"):
        count_stage("responder")

    # ── Computed confidence (fresh runs only) ────────────────
    if not resolution.get("from_cache"):
        resolution["confidence"] = compute_confidence(context.get("top_chunks", []))
    resolution["mode"] = "suggest"

    # ── Step 6b — Scorer (Precision & Recall, no LLM) ───────
    context = safe_run("Scorer", scorer.run, context)
    condition_context = validation._condition_context(context.get("top_chunks", []), request_meta)
    scorer_result = compute_scorer_result(
        context.get("top_chunks", []),
        evidence_bundle=evidence_bundle,
        missing_context=condition_context.get("missing_context_fields", []),
        source_conflicts=context.get("source_conflicts", []),
        route=context.get("routing_strategy", "general"),
    )
    resolution["confidence_scorer"] = scorer_result.to_dict()
    if scorer_result.confidence_band == "red":
        _replace_with_abstention(
            resolution,
            _abstention_response(
                scorer_result.abstention_reason,
                scorer_result.to_dict(),
                context,
            ),
        )
        context["resolution"] = resolution

    # ── Step 7 — Evaluator (LLM call 2, fresh runs only) ────
    if (
        not resolution.get("from_cache")
        and resolution.get("confidence_scorer", {}).get("confidence_band") != "red"
        and stage_allowed("evaluator")
    ):
        context = safe_run("Evaluator", evaluator.run, context)
        resolution["eval_score"] = context.get("eval_score", {})
        if resolution["eval_score"].get("evaluation_skipped"):
            logger.info("Evaluator skipped by deterministic pre-check; no evaluator LLM call counted")
        else:
            count_stage("evaluator")

        # Feedback loop: if faithfulness LOW, widen retrieval and retry responder
        # (LLM call 3 max). Evaluator does NOT re-run to stay within call budget.
        if (resolution["eval_score"].get("faithfulness") == "LOW"
                and not context.get("_retried")
                and stage_allowed("responder_retry")):
            logger.info("Feedback loop triggered — retrying with top_k_rerank=10")
            context["_retried"] = True
            context["route_hints"] = {**context.get("route_hints", {}), "top_k_rerank": 10}
            context = safe_run("Reranker [retry]", reranker.run, context)
            context = safe_run("Responder [retry]", responder.run, context)
            count_stage("responder_retry")
            resolution = context["resolution"]
            resolution["confidence"] = compute_confidence(context.get("top_chunks", []))
            context = safe_run("Scorer [retry]", scorer.run, context)
            resolution["retry_triggered"] = True
    else:
        resolution.setdefault("eval_score", {
            "faithfulness": "SKIPPED",
            "completeness": "SKIPPED",
            "tone": "SKIPPED",
            "flags": [],
            "summary": "Evaluator skipped by workflow mode or LLM budget.",
            "evaluation_skipped": True,
            "usage": {"tokens_in": 0, "tokens_out": 0},
        })
        context["eval_score"] = resolution["eval_score"]

    # ── Step 8 — Validation (Human Validation, no LLM) ──────
    context = safe_run("Validation", validation.run, context)
    resolution = context["resolution"]

    # ── Usage summary ─────────────────────────────────────────
    usage = resolution.get("usage", context.get("usage", {}))
    query_usage     = usage.get("query_builder", {})
    responder_usage = usage.get("responder", {})
    eval_usage      = resolution.get("eval_score", {}).get("usage", {})
    resolution["usage"] = usage
    resolution["usage_summary"] = {
        "query_tokens_in":      int(query_usage.get("tokens_in", 0) or 0),
        "query_tokens_out":     int(query_usage.get("tokens_out", 0) or 0),
        "response_tokens_in":   int(responder_usage.get("tokens_in", 0) or 0),
        "response_tokens_out":  int(responder_usage.get("tokens_out", 0) or 0),
        "eval_tokens_in":       int(eval_usage.get("tokens_in", 0) or 0),
        "eval_tokens_out":      int(eval_usage.get("tokens_out", 0) or 0),
        "total_tokens":         int(responder_usage.get("tokens_in", 0) or 0)
                                + int(responder_usage.get("tokens_out", 0) or 0)
                                + int(eval_usage.get("tokens_in", 0) or 0)
                                + int(eval_usage.get("tokens_out", 0) or 0),
        "total_cost_usd":       round(float(query_usage.get("cost_usd", 0.0) or 0.0)
                                + float(responder_usage.get("cost_usd", 0.0) or 0.0)
                                + float(eval_usage.get("cost_usd", 0.0) or 0.0), 8),
    }
    resolution["llm_workflow"] = {
        "mode": workflow.get("mode", ""),
        "preset": workflow.get("llm_budget_preset", "balanced"),
        "max_llm_calls": max_llm_calls,
        "llm_calls_used": llm_calls_used,
        "stages": stages,
    }

    # ── Retrieval signals ─────────────────────────────────────
    signals = _collect_retrieval_signals(context)
    signals["used_response_cache"] = bool(resolution.get("from_cache", False))
    signals["per_question"] = context.get("retrieval_per_question", [])
    signals["retrieval_strategy"] = context.get("retrieval_strategy", {})
    resolution["retrieval_signals"] = signals

    # ── Precision & Recall ────────────────────────────────────
    resolution["precision_recall"] = context.get("precision_recall", {})

    # ── Request context ───────────────────────────────────────
    resolution["request_context"] = {
        "product":          request_meta.get("product", ""),
        "permission_level": request_meta.get("permission_level", ""),
        "access_channel":   request_meta.get("access_channel", ""),
        "support_ops_mode": request_meta.get("support_ops_mode", "query"),
    }
    if request_meta.get("support_ops_mode") == "chat":
        resolution["draft_email"] = ""
        resolution["draft_unavailable_reason"] = "Chat mode is internal support guidance only in v3.x and is not customer-facing proof."
    resolution["request_fingerprint"] = request_fingerprint
    resolution = _attach_and_save_trace(context, resolution, start)
    context["resolution"] = resolution

    # ── Save flagged items to human review queue ─────────────
    eval_score = context.get("eval_score", {})
    _save_human_review(
        resolution,
        context.get("routing_strategy", "general"),
        eval_score,
        full_ticket=context.get("ticket", {}).get("cleaned", ""),
    )

    elapsed = time.time() - start
    logger.info(f"Pipeline complete — confidence: {resolution['confidence']} | {elapsed:.2f}s")

    # ── Save ticket-level cache ───────────────────────────────
    if normalized_ticket and not resolution.get("from_cache"):
        save_ticket_cache(normalized_ticket, resolution, request_meta)

    return resolution


def _apply_support_ops_retrieval_controls(top_chunks: list[dict], context: dict) -> list[dict]:
    request_meta = context.get("request_meta", {})
    threshold = str(request_meta.get("similarity_threshold") or "none").lower()
    thresholds = {"none": None, "low": 0.5, "medium": 2.0, "high": 7.0}
    min_score = thresholds.get(threshold)
    chunks = list(top_chunks)
    if min_score is not None:
        chunks = [chunk for chunk in chunks if float(chunk.get("rerank_score") or 0.0) >= min_score]

    pinned = {str(item) for item in request_meta.get("pinned_source_ids", []) if item}
    if pinned:
        existing_ids = {str(chunk.get("id", "")) for chunk in chunks}
        for chunk in context.get("retrieved_chunks", []):
            chunk_id = str(chunk.get("id", ""))
            source_id = str(chunk.get("source_id", ""))
            if (chunk_id in pinned or source_id in pinned) and chunk_id not in existing_ids:
                chunks.append({**chunk, "retrieval_reason": "pinned_source"})
                existing_ids.add(chunk_id)
    return chunks


if __name__ == "__main__":
    test_ticket = """
        Hi team, user cannot log in to the Example Product app.
        Getting error code 403 on mobile only.
        Desktop works fine. Started yesterday after the update.
    """

    print("\n── Running Full Pipeline ───────────────────\n")

    try:
        resolution = run(test_ticket)
        print(f"Issue:      {resolution['issue_classification']}")
        print(f"Diagnosis:  {resolution.get('diagnosis', '')[:80]}...")
        print(f"Confidence: {resolution['confidence']}")
        signals = resolution.get("retrieval_signals", {})
        print(f"Top score:  {signals.get('top_score')}, Gap: {signals.get('score_gap')}")
        print("\n✅ Full pipeline working correctly")
    except Exception as e:
        print(f"\n❌ Pipeline failed: {e}")
