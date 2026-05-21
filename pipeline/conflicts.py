from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import re
from typing import Any

from backend.core.evidence import _parse_dt


@dataclass(frozen=True)
class SourceConflict:
    conflict_type: str
    source_a: str
    source_b: str
    recommended_handling: str
    severity: str = "medium"

    def to_dict(self) -> dict:
        return asdict(self)


def _source_label(chunk: dict[str, Any]) -> str:
    return str(
        chunk.get("source_id")
        or chunk.get("source_ref")
        or chunk.get("source_file")
        or chunk.get("id")
        or ""
    )


def _source_type(chunk: dict[str, Any]) -> str:
    return str(chunk.get("source_type") or chunk.get("doc_type") or "").strip()


def _status(chunk: dict[str, Any]) -> str:
    return str(chunk.get("status") or chunk.get("known_issue_status") or "").strip().lower()


def _versions(chunk: dict[str, Any]) -> set[str]:
    text = " ".join(
        str(chunk.get(key) or "")
        for key in ("version", "product_version", "embedding_text", "display_text", "content", "title")
    )
    return set(re.findall(r"\bv(?:ersion)?\s*(\d+\.\d+(?:\.\d+)?)\b", text, flags=re.I))


def _is_stale(chunk: dict[str, Any], now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    stale_at = _parse_dt(chunk.get("expires_at")) or _parse_dt(chunk.get("needs_review_at"))
    return bool(stale_at and stale_at < now)


def detect_source_conflicts(chunks: list[dict[str, Any]]) -> list[SourceConflict]:
    conflicts: list[SourceConflict] = []
    typed = [chunk for chunk in chunks if _source_type(chunk)]

    for left_index, left in enumerate(typed):
        for right in typed[left_index + 1:]:
            left_type = _source_type(left)
            right_type = _source_type(right)
            left_label = _source_label(left)
            right_label = _source_label(right)
            pair = {left_type, right_type}

            if pair & {"policy"} and pair & {"faq", "knowledge_base", "official_help_article"}:
                conflicts.append(SourceConflict(
                    conflict_type="policy_vs_faq",
                    source_a=left_label,
                    source_b=right_label,
                    recommended_handling="Prefer the policy source and require review if answer text relies on the lower-tier source.",
                    severity="high",
                ))

            if pair & {"release_note"} and pair & {"knowledge_base", "official_help_article"}:
                stale_side = left if _is_stale(left) else right if _is_stale(right) else None
                if stale_side:
                    conflicts.append(SourceConflict(
                        conflict_type="stale_kb_vs_release_note",
                        source_a=left_label,
                        source_b=right_label,
                        recommended_handling="Use the current release note only if it directly supports the answer; otherwise escalate.",
                        severity="high",
                    ))

            if left_type == "known_issue" and right_type == "known_issue":
                left_status = _status(left)
                right_status = _status(right)
                if left_status and right_status and left_status != right_status:
                    conflicts.append(SourceConflict(
                        conflict_type="known_issue_status_conflict",
                        source_a=left_label,
                        source_b=right_label,
                        recommended_handling="Do not silently choose one known-issue status; show the conflict to the support agent.",
                        severity="high",
                    ))

            left_versions = _versions(left)
            right_versions = _versions(right)
            if left_versions and right_versions and left_versions.isdisjoint(right_versions):
                conflicts.append(SourceConflict(
                    conflict_type="version_specific_behavior_conflict",
                    source_a=left_label,
                    source_b=right_label,
                    recommended_handling="Ask for product version or route to review before applying version-specific guidance.",
                    severity="medium",
                ))

    deduped = []
    seen = set()
    for conflict in conflicts:
        key = (conflict.conflict_type, conflict.source_a, conflict.source_b)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(conflict)
    return deduped
