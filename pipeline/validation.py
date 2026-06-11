import re
from backend.core.evidence import EvidenceBundle
from backend.core.logger import get_logger
from pipeline.conflicts import detect_source_conflicts

logger = get_logger(__name__)

_ESCALATION_ROUTES = {"billing", "access"}
_ESCALATION_KEYWORDS = re.compile(
    r"\b(data loss|outage|all users|everyone|urgent|critical|breach|security|gdpr|compliance|legal)\b", re.I
)
_VALID_CITATION = re.compile(r"\[KB-\d+\]")
_INVALID_CITATION_PATTERNS = [
    re.compile(r"\[(?!KB-\d+\])[^]]*\bKB\b[^]]*\]", re.I),
    re.compile(r"\bKB\s*(?:#|-|article\s*)\d+\b", re.I),
    re.compile(r"\bsource\s*:\s*KB\b", re.I),
    re.compile(r"\bsee\s+KB[-#\s]*\d+\b", re.I),
]

_CONTEXT_REQUIREMENTS = {
    "requires_role": ("permission_level", "role or permission level"),
    "requires_permission": ("permission_level", "role or permission level"),
    "platform_specific": ("access_channel", "access channel or platform"),
    "plan_specific": ("plan", "plan or subscription tier"),
    "requires_setting": ("setting_state", "relevant setting state"),
    "requires_feature_enabled": ("feature_state", "feature enabled state"),
    "account_specific": ("account_context", "account or location configuration"),
}


def _condition_context(top_chunks: list, request_meta: dict) -> dict:
    collected = []
    for chunk in top_chunks:
        raw_flags = chunk.get("condition_flags") or []
        if isinstance(raw_flags, str):
            raw_flags = [f.strip() for f in raw_flags.strip("[]").replace('"', "").split(",") if f.strip()]
        collected.extend(flag for flag in raw_flags if isinstance(flag, str))
    flags = sorted(set(collected))
    missing = []
    for flag in flags:
        field_label = _CONTEXT_REQUIREMENTS.get(flag)
        if not field_label:
            continue
        field, label = field_label
        if not request_meta.get(field):
            missing.append(label)
    return {
        "condition_flags": flags,
        "missing_required_context": bool(missing),
        "missing_context_fields": sorted(set(missing)),
    }


def _invalid_citation_syntax(text: str) -> list[str]:
    without_valid = _VALID_CITATION.sub("", text or "")
    invalid = []
    for pattern in _INVALID_CITATION_PATTERNS:
        invalid.extend(match.group(0).strip() for match in pattern.finditer(without_valid))
    return sorted(set(invalid))


def _audit_resolution(resolution: dict) -> dict:
    canonical = resolution.get("canonical_resolution")
    if isinstance(canonical, dict) and any(
        str(canonical.get(key, "")).strip()
        for key in ("root_cause", "resolution_steps", "draft_email", "sources")
    ):
        return canonical
    return resolution


def run(context: dict) -> dict:
    resolution = context.get("resolution", {})
    eval_score = context.get("eval_score", {})
    routing_strategy = context.get("routing_strategy", "general")
    ticket = context.get("ticket", {}).get("cleaned", "")
    top_chunks = context.get("top_chunks", [])
    request_meta = context.get("request_meta", {})

    confidence = resolution.get("confidence", "")
    condition_context = _condition_context(top_chunks, request_meta)
    evaluation_skipped = bool(eval_score.get("evaluation_skipped"))
    if confidence == "HIGH" and condition_context["missing_required_context"]:
        confidence = "MEDIUM"
        resolution["confidence"] = confidence
        logger.info(
            "Confidence capped at MEDIUM because conditional sources require missing context: "
            + ", ".join(condition_context["missing_context_fields"])
        )

    # ── Auditor: structured checklist ────────────────────────────
    audit_resolution = _audit_resolution(resolution)
    root_cause = audit_resolution.get("root_cause", "")
    steps = audit_resolution.get("resolution_steps", "")
    email = audit_resolution.get("draft_email", "")
    sources = audit_resolution.get("sources", "")
    visible_response_text = "\n".join([
        resolution.get("root_cause", ""),
        resolution.get("resolution_steps", ""),
        resolution.get("draft_email", ""),
        resolution.get("sources", ""),
    ])
    evidence_bundle = context.get("evidence_bundle")
    if not evidence_bundle:
        evidence_bundle = EvidenceBundle.from_chunks(top_chunks, audience="customer")
        context["evidence_bundle"] = evidence_bundle

    response_text = "\n".join([root_cause, steps, email, sources])
    factual_text = "\n".join([root_cause, steps])
    cited_labels = set(re.findall(r"\[KB-(\d+)\]", "\n".join([response_text, visible_response_text])))
    visible_cited_labels = set(re.findall(r"\[KB-(\d+)\]", visible_response_text))
    valid_labels = {citation.citation_id.replace("KB-", "") for citation in evidence_bundle.citations}
    citations_by_label = {
        citation.citation_id.replace("KB-", ""): citation
        for citation in evidence_bundle.citations
    }
    customer_facing_citations = [
        citations_by_label[label].to_dict()
        for label in sorted(visible_cited_labels)
        if label in citations_by_label
    ]
    missing_citations = sorted(label for label in cited_labels if label not in valid_labels)
    blocked_citations = evidence_bundle.blocked + [
        {"citation_id": f"KB-{label}", "reason": "citation ID does not resolve to approved evidence"}
        for label in missing_citations
    ]
    redaction_failures = [
        str(chunk.get("id", "unknown"))
        for chunk in top_chunks
        if str(chunk.get("redaction_status") or "").lower() in {"failed", "error", "redaction_failed"}
    ]
    source_validation_passed = not blocked_citations and bool(evidence_bundle.citations)
    source_conflicts = context.get("source_conflicts")
    if source_conflicts is None:
        source_conflicts = [conflict.to_dict() for conflict in detect_source_conflicts(top_chunks)]

    auditor = {
        "has_evidence": bool(sources and sources.strip().lower() != "general technical knowledge"),
        "has_steps": bool(steps and len(steps.split()) > 10),
        "email_complete": bool(
            email
            and "Kind regards" in email
            and "Subject:" in email
            and "Hi" in email
        ),
        "citations_present": bool(
            top_chunks and any(
                f"[KB-{i + 1}]" in root_cause or f"[KB-{i + 1}]" in steps
                for i in range(len(top_chunks))
            )
        ),
    }
    unsupported_claims = []
    invalid_citations = sorted(set(
        _invalid_citation_syntax(response_text) + _invalid_citation_syntax(visible_response_text)
    ))
    if invalid_citations:
        unsupported_claims.append(
            "Invalid citation syntax used; only [KB-N] citations are allowed: "
            + ", ".join(invalid_citations[:6])
        )
    if sources.strip().lower() == "general technical knowledge":
        unsupported_claims.append("Customer-facing response used general technical knowledge instead of approved sources.")
    is_abstention = bool(resolution.get("draft_unavailable_reason"))
    if resolution.get("confidence_scorer", {}).get("confidence_band") == "red" and email:
        unsupported_claims.append("Response produced a customer draft despite red confidence.")
    if resolution.get("confidence_scorer", {}).get("confidence_band") == "red" and (root_cause or steps) and not is_abstention:
        unsupported_claims.append("Response answered directly despite red confidence.")
    evidence_table = context.get("evidence_table", {})
    supported_facts = evidence_table.get("supported_facts", []) if isinstance(evidence_table, dict) else []
    if (root_cause or steps) and supported_facts and not is_abstention and not re.search(r"\[KB-\d+\]", factual_text):
        unsupported_claims.append("Factual answer fields did not cite approved evidence.")
    if isinstance(evidence_table, dict) and evidence_table and top_chunks and not supported_facts:
        unsupported_claims.append("Evidence table has no supported facts for responder grounding.")

    missing_required_context = list(condition_context.get("missing_context_fields", []))
    missing_required_context.extend(
        item for item in (evidence_table.get("missing_context", []) if isinstance(evidence_table, dict) else [])
        if item not in missing_required_context
    )
    if missing_required_context:
        lower_response = visible_response_text.lower()
        unacknowledged = [
            item for item in missing_required_context
            if str(item).lower() not in lower_response and "missing" not in lower_response
        ]
        if unacknowledged:
            unsupported_claims.append(
                "Missing context was not acknowledged: " + ", ".join(sorted(set(unacknowledged))[:6])
            )

    if source_conflicts:
        lower_response = visible_response_text.lower()
        if "conflict" not in lower_response and "review" not in lower_response and "uncertain" not in lower_response:
            unsupported_claims.append("Source conflicts were not surfaced in the response.")

    blocked_reasons = " ".join(item.get("reason", "") for item in blocked_citations).lower()
    if any(term in blocked_reasons for term in ("stale", "internal", "future", "forbidden", "disabled")):
        unsupported_claims.append("Unsafe, stale, internal, future-only, or disabled sources were blocked from citation.")

    # ── Gatekeeper: flag for human review ────────────────────────
    gatekeeper_flagged = (
        confidence == "LOW"
        or (not evaluation_skipped and eval_score.get("faithfulness") == "LOW")
        or (not evaluation_skipped and eval_score.get("completeness") == "LOW")
        or (not evaluation_skipped and bool(eval_score.get("flags")))
        or condition_context["missing_required_context"]
        or bool(blocked_citations)
        or bool(unsupported_claims)
        or bool(redaction_failures)
        or any(conflict.get("severity") == "high" for conflict in source_conflicts)
    )

    # ── Strategist: escalation routing ───────────────────────────
    needs_escalation = (
        routing_strategy in _ESCALATION_ROUTES
        and bool(_ESCALATION_KEYWORDS.search(ticket))
    )

    strategist = {
        "route": routing_strategy,
        "needs_escalation": needs_escalation,
        "escalation_reason": (
            "Ticket contains critical/security keywords on a sensitive route"
            if needs_escalation else ""
        ),
    }

    if gatekeeper_flagged:
        logger.warning(
            f"Gatekeeper flagged — confidence: {confidence} | "
            f"faithfulness: {eval_score.get('faithfulness')} | "
            f"completeness: {eval_score.get('completeness')}"
        )
    if needs_escalation:
        logger.warning(f"Strategist escalation triggered — route: {routing_strategy}")

    passed = source_validation_passed and not unsupported_claims and not gatekeeper_flagged
    warnings = [
        f"Redaction failed for chunk {chunk_id}; human review required."
        for chunk_id in redaction_failures
    ]
    if resolution.get("confidence_scorer", {}).get("confidence_band") == "yellow":
        warnings.append("Yellow confidence requires caveat display.")
    outcome = "clean"
    if not passed and resolution.get("confidence_scorer", {}).get("confidence_band") == "red":
        outcome = "abstained"
    elif blocked_citations or unsupported_claims or redaction_failures:
        outcome = "hard_failure"
    elif warnings or gatekeeper_flagged:
        outcome = "clean_with_caveats"
    elif resolution.get("validation_corrected"):
        outcome = "corrected"

    resolution["validation"] = {
        "passed": passed,
        "outcome": outcome,
        "errors": [
            item.get("reason", "citation blocked")
            for item in blocked_citations
        ],
        "warnings": warnings,
        "redaction_status": "failed" if redaction_failures else "checked",
        "redaction_failures": redaction_failures,
        "blocked_citations": blocked_citations,
        "unsupported_claims": unsupported_claims,
        "source_conflicts": source_conflicts,
        "review_required": gatekeeper_flagged or bool(blocked_citations) or bool(unsupported_claims),
        "gatekeeper_flagged": gatekeeper_flagged,
        "auditor": auditor,
        "strategist": strategist,
        "condition_context": condition_context,
        "evidence_table_checks": {
            "supported_fact_count": len(supported_facts),
            "missing_context_acknowledged": not missing_required_context or not any(
                "Missing context was not acknowledged" in claim for claim in unsupported_claims
            ),
            "conflicts_surfaced": not source_conflicts or not any(
                "Source conflicts were not surfaced" in claim for claim in unsupported_claims
            ),
        },
        "evaluation_skipped": evaluation_skipped,
        "citations": [citation.to_dict() for citation in evidence_bundle.citations],
        "customer_facing_citations": customer_facing_citations,
    }
    context["resolution"] = resolution
    return context
