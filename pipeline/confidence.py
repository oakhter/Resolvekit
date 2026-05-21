"""
Confidence scoring — fully computed from retrieval signals.

Signals (priority order):
  1. top_score  — best cross-encoder score
  2. score_gap  — top1 minus top2 (dominance of the best chunk)
  3. positive   — chunks scoring above the meaningful threshold
  4. sources    — unique KB sources in agreement

Thresholds are intentionally conservative: LOW should be the default
when coverage is thin, so MEDIUM and HIGH carry real meaning.
"""

from dataclasses import asdict, dataclass, field

from backend.core.evidence import EvidenceBundle, _parse_dt
from backend.core.logger import get_logger
from pipeline.conflicts import detect_source_conflicts
from pipeline.retrieval_policy import get_route_policy

logger = get_logger(__name__)

# Cross-encoder (ms-marco-MiniLM-L-6-v2). Typical range: ~-10 to +12.
#
# _HIGH_SCORE:   a genuinely strong, direct match — not just related content
# _MEDIUM_SCORE: a meaningful match worth reasoning from (above background noise)
# _NOISE_FLOOR:  below this, even a single chunk carries no diagnostic value
# _MIN_GAP:      gap between top-1 and top-2 indicating a clear dominant signal
#
_HIGH_SCORE   = 7.0   # raised from 5.0 — 5.x is common even for adjacent topics
_MEDIUM_SCORE = 2.0   # raised from 0.5 — 0.5 is effectively noise
_NOISE_FLOOR  = 0.5   # anything below this is discarded
_HIGH_CHUNKS  = 2     # multiple strong chunks required for HIGH
_HIGH_SOURCES = 2     # multi-source corroboration for HIGH
_MIN_GAP      = 3.0   # raised from 2.0 — clear dominant signal

GREEN_MIN_CONFIDENCE = 0.72
YELLOW_MIN_CONFIDENCE = 0.40
RED_MAX_CONFIDENCE = YELLOW_MIN_CONFIDENCE

CONFIDENCE_BAND_THRESHOLDS = {
    "green_min": GREEN_MIN_CONFIDENCE,
    "yellow_min": YELLOW_MIN_CONFIDENCE,
    "red_below": RED_MAX_CONFIDENCE,
}


@dataclass(frozen=True)
class ScorerResult:
    confidence_score: float
    uncertainty_score: float
    confidence_band: str
    retrieval_strength: str
    source_coverage: str
    source_conflict_detected: bool
    top_rerank_score: float = 0.0
    score_gap: float = 0.0
    source_diversity: int = 0
    approved_source_coverage: float = 0.0
    citation_support_ratio: float = 0.0
    route_source_alignment: float = 0.0
    conflict_count: int = 0
    stale_source_count: int = 0
    validator_warning_count: int = 0
    source_conflicts: list[dict] = field(default_factory=list)
    missing_context: list[str] = field(default_factory=list)
    abstention_reason: str = ""
    recommended_action: str = "answer"
    confidence_thresholds: dict[str, float] = field(default_factory=lambda: dict(CONFIDENCE_BAND_THRESHOLDS))

    def to_dict(self) -> dict:
        return asdict(self)


def compute(top_chunks: list, llm_confidence: str = "") -> str:
    """
    Return HIGH / MEDIUM / LOW based on retrieval quality.

    llm_confidence is accepted for API compatibility but not used.
    """
    if not top_chunks:
        logger.debug("Confidence → LOW (no chunks)")
        return "LOW"

    scores    = [c.get("rerank_score", 0.0) for c in top_chunks]
    top       = scores[0]
    score_gap = top - scores[1] if len(scores) >= 2 else top

    # Chunks that are meaningfully relevant (above noise)
    positive = [s for s in scores if s >= _MEDIUM_SCORE]
    sources  = {
        c.get("source_file", "")
        for c in top_chunks
        if c.get("rerank_score", 0.0) >= _MEDIUM_SCORE
    }

    logger.debug(
        f"Confidence signals — top={top:.2f}, gap={score_gap:.2f}, "
        f"positive={len(positive)}, sources={len(sources)}"
    )

    # ── HIGH ─────────────────────────────────────────────────
    # Strong top score + multiple meaningful chunks + multiple KB sources
    if top >= _HIGH_SCORE and len(positive) >= _HIGH_CHUNKS and len(sources) >= _HIGH_SOURCES:
        level = "HIGH"

    # Strong top score + dominant single signal (large gap from next chunk)
    elif top >= _HIGH_SCORE and score_gap >= _MIN_GAP and len(positive) >= 1:
        level = "HIGH"

    # ── MEDIUM ────────────────────────────────────────────────
    # At least one meaningful chunk but doesn't reach HIGH bar
    elif top >= _MEDIUM_SCORE and len(positive) >= 1:
        level = "MEDIUM"

    # ── LOW ───────────────────────────────────────────────────
    # Top chunk is near noise — KB likely doesn't cover this issue
    else:
        level = "LOW"

    logger.debug(f"Confidence → {level}")
    return level


def compute_scorer_result(
    top_chunks: list,
    *,
    evidence_bundle: EvidenceBundle | None = None,
    missing_context: list[str] | None = None,
    validator_failed: bool = False,
    validator_warnings: list[str] | None = None,
    source_conflicts: list[dict] | None = None,
    route: str = "general",
) -> ScorerResult:
    missing_context = missing_context or []
    validator_warnings = validator_warnings or []
    scores = [float(c.get("rerank_score") or 0.0) for c in top_chunks]
    top = scores[0] if scores else 0.0
    score_gap = top - scores[1] if len(scores) >= 2 else top
    approved_count = len(evidence_bundle.chunks) if evidence_bundle else 0
    blocked_count = len(evidence_bundle.blocked) if evidence_bundle else 0
    source_ids = {
        str(c.get("source_id") or c.get("source_ref") or c.get("source_file") or "")
        for c in top_chunks
        if c.get("source_id") or c.get("source_ref") or c.get("source_file")
    }
    source_diversity = len(source_ids)
    approved_source_coverage = round(approved_count / len(top_chunks), 4) if top_chunks else 0.0
    citation_support_ratio = round(len(evidence_bundle.citations) / len(top_chunks), 4) if evidence_bundle and top_chunks else 0.0
    route_policy = get_route_policy(route)
    preferred_types = set(route_policy.get("preferred_source_types") or [])
    aligned = [
        c for c in top_chunks
        if (c.get("source_type") or c.get("doc_type") or "knowledge_base") in preferred_types
    ]
    route_source_alignment = round(len(aligned) / len(top_chunks), 4) if top_chunks else 0.0
    stale_source_count = sum(1 for chunk in top_chunks if _chunk_is_stale(chunk))
    detected_conflicts = source_conflicts
    if detected_conflicts is None:
        detected_conflicts = [conflict.to_dict() for conflict in detect_source_conflicts(top_chunks)]
    source_conflict_detected = bool(detected_conflicts)

    base = 0.0
    if top >= _HIGH_SCORE:
        base += 0.55
    elif top >= _MEDIUM_SCORE:
        base += 0.35
    elif top >= _NOISE_FLOOR:
        base += 0.15
    base += min(len([s for s in scores if s >= _MEDIUM_SCORE]), 3) * 0.08
    if score_gap >= _MIN_GAP:
        base += 0.08
    if approved_count:
        base += 0.13

    caps: list[tuple[float, str]] = []
    if not top_chunks:
        caps.append((0.15, "No retrieval results."))
    if approved_count == 0:
        caps.append((0.20, "No approved customer-facing source supports the answer."))
    if blocked_count:
        caps.append((0.60, "One or more candidate sources were blocked by source safety."))
    if stale_source_count:
        caps.append((0.62, "One or more top sources are stale or need review."))
    if source_conflict_detected:
        caps.append((0.55, "Potential source conflict detected."))
    if any(conflict.get("severity") == "high" for conflict in detected_conflicts):
        caps.append((0.45, "High-severity source conflict detected."))
    if missing_context:
        caps.append((0.62, "Required product, platform, role, or account context is missing."))
    if validator_failed:
        caps.append((0.35, "Validator found unsupported or unsafe output."))
    if validator_warnings:
        caps.append((0.68, "Validator warnings need review."))

    cap_value = min([cap for cap, _ in caps], default=1.0)
    confidence_score = round(max(0.0, min(base, cap_value, 1.0)), 3)
    uncertainty_score = round(1.0 - confidence_score, 3)
    if confidence_score >= GREEN_MIN_CONFIDENCE:
        band = "green"
        recommended = "answer"
    elif confidence_score >= YELLOW_MIN_CONFIDENCE:
        band = "yellow"
        recommended = "ask_clarifying_question" if missing_context else "cautious_answer"
    else:
        band = "red"
        recommended = "escalate" if missing_context else "refuse"

    retrieval_strength = "strong" if top >= _HIGH_SCORE else "weak" if top < _MEDIUM_SCORE else "moderate"
    source_coverage = "approved" if approved_count else "none"
    abstention_reason = ""
    if band == "red":
        abstention_reason = caps[0][1] if caps else "Retrieval confidence is too low."

    return ScorerResult(
        confidence_score=confidence_score,
        uncertainty_score=uncertainty_score,
        confidence_band=band,
        retrieval_strength=retrieval_strength,
        source_coverage=source_coverage,
        source_conflict_detected=source_conflict_detected,
        top_rerank_score=round(top, 4),
        score_gap=round(score_gap, 4),
        source_diversity=source_diversity,
        approved_source_coverage=approved_source_coverage,
        citation_support_ratio=citation_support_ratio,
        route_source_alignment=route_source_alignment,
        conflict_count=len(detected_conflicts),
        stale_source_count=stale_source_count,
        validator_warning_count=len(validator_warnings),
        source_conflicts=detected_conflicts,
        missing_context=missing_context,
        abstention_reason=abstention_reason,
        recommended_action=recommended,
    )


def _chunk_is_stale(chunk: dict) -> bool:
    stale_at = _parse_dt(chunk.get("expires_at")) or _parse_dt(chunk.get("needs_review_at"))
    if not stale_at:
        return False
    from datetime import datetime, timezone

    return stale_at < datetime.now(timezone.utc)
