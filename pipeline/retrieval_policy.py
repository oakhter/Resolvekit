from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from backend.core import project_config
from backend.core.evidence import _parse_dt
from backend.core.logger import get_logger
from backend.core.evidence import FORBIDDEN_CUSTOMER_SOURCE_TYPES

logger = get_logger(__name__)

DEFAULT_PER_SOURCE_TYPE_K = 3
CUSTOMER_ALLOWED_SOURCE_TYPES = {
    "knowledge_base",
    "official_help_article",
    "faq",
    "policy",
    "release_note",
    "known_issue",
}


def get_route_policy(route: str) -> dict:
    policies = project_config.load_config("retrieval_policy").get("route_policies", {})
    return deepcopy(policies.get(route) or policies.get("general") or {})


def score_candidate_with_policy(candidate: dict, route: str) -> dict:
    policy = get_route_policy(route)
    policy_config = project_config.load_config("retrieval_policy")
    source_type = candidate.get("source_type") or candidate.get("doc_type") or "knowledge_base"
    chunk_type = candidate.get("chunk_type") or "concept"

    result = dict(candidate)
    base_score = float(result.get("rrf_score") or result.get("score") or 0.0)
    authority = float(result.get("source_authority") or 1.0)
    source_type_weight = _source_type_weight(source_type, policy_config)
    boost = float(policy.get("boost", 0.0) or 0.0)

    policy_boost = 0.0
    if source_type in policy.get("disallowed_source_types", []):
        result["policy_disallowed"] = True
        result["policy_score"] = -1.0
        return result
    if source_type in policy.get("preferred_source_types", []):
        policy_boost += boost
    if chunk_type in policy.get("preferred_chunk_types", []):
        policy_boost += boost

    result["policy_disallowed"] = False
    result["policy_boost"] = round(policy_boost, 6)
    result["source_type_weight"] = source_type_weight
    result["policy_score"] = round((base_score * max(authority, 0.0) * source_type_weight) + policy_boost, 6)
    return result


def _source_type_weight(source_type: str, policy_config: dict | None = None) -> float:
    retrieval = (policy_config or project_config.load_config("retrieval_policy")).get("retrieval", {})
    weights = retrieval.get("source_type_weights") or {}
    try:
        value = float(weights.get(source_type, 1.0))
    except (TypeError, ValueError):
        value = 1.0
    return max(value, 0.0)


def customer_retrieval_allowed(candidate: dict) -> bool:
    source_type = candidate.get("source_type") or candidate.get("doc_type") or "knowledge_base"
    if not candidate.get("source_id") or not source_type:
        return False
    if source_type in FORBIDDEN_CUSTOMER_SOURCE_TYPES:
        return False
    if source_type not in CUSTOMER_ALLOWED_SOURCE_TYPES:
        return False
    if candidate.get("disabled"):
        return False
    if candidate.get("is_approved") is not True:
        return False
    if candidate.get("is_customer_facing_allowed") is not True:
        return False
    if candidate.get("is_internal_only") or candidate.get("is_future_only"):
        return False
    return True


def merge_by_source_type(
    candidates: list[dict],
    route: str,
    top_k: int,
    *,
    per_source_type_k: int = DEFAULT_PER_SOURCE_TYPE_K,
) -> list[dict]:
    """Keep retrieval diverse while preserving route-critical source slots."""
    allowed = [candidate for candidate in candidates if customer_retrieval_allowed(candidate)]
    buckets: dict[str, list[dict]] = {}
    for candidate in allowed:
        source_type = candidate.get("source_type") or candidate.get("doc_type") or "knowledge_base"
        buckets.setdefault(source_type, []).append(candidate)

    for source_type, bucket in buckets.items():
        buckets[source_type] = sorted(
            bucket,
            key=lambda item: (
                _is_stale(item),
                -float(item.get("policy_score") or item.get("rrf_score") or item.get("score") or 0.0),
            ),
        )[:per_source_type_k]

    selected: list[dict] = []
    route_slots = {
        "policy": ["policy"],
        "billing": ["policy"],
        "access": ["policy", "official_help_article"],
        "bug": ["known_issue", "release_note"],
        "release_change": ["release_note", "known_issue"],
    }
    for source_type in route_slots.get(route, []):
        bucket = buckets.get(source_type) or []
        if bucket:
            selected.append(bucket.pop(0))

    remaining = [item for bucket in buckets.values() for item in bucket]
    remaining = sorted(
        remaining,
        key=lambda item: (
            _is_stale(item),
            -float(item.get("policy_score") or item.get("rrf_score") or item.get("score") or 0.0),
        ),
    )

    seen = {item.get("id") for item in selected}
    seen_parent_sections = {item.get("parent_section_id") for item in selected if item.get("parent_section_id")}
    for item in remaining:
        if len(selected) >= top_k:
            break
        if item.get("id") in seen:
            continue
        parent_section_id = item.get("parent_section_id")
        if parent_section_id and parent_section_id in seen_parent_sections:
            continue
        selected.append(item)
        seen.add(item.get("id"))
        if parent_section_id:
            seen_parent_sections.add(parent_section_id)

    return selected[:top_k]


def _is_stale(candidate: dict) -> bool:
    stale_at = _parse_dt(candidate.get("expires_at")) or _parse_dt(candidate.get("needs_review_at"))
    return bool(stale_at and stale_at < datetime.now(timezone.utc))
