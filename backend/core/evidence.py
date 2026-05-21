from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


FORBIDDEN_CUSTOMER_SOURCE_TYPES = {
    "raw_ticket_history",
    "raw_ticket",
    "raw_chat_transcript",
    "raw_call_transcript",
    "raw_email",
    "raw_ticket_chat_call",
    "similar_resolved_ticket",
}

APPROVED_CUSTOMER_SOURCE_TYPES = {
    "knowledge_base",
    "official_help_article",
    "faq",
    "policy",
    "release_note",
    "known_issue",
}

REQUIRED_CUSTOMER_SOURCE_FIELDS = (
    "source_id",
    "source_type",
    "source_category",
    "tier",
    "source_ref",
    "lineage_ref",
    "reviewed_by",
    "approved_at",
    "audience_allowed",
    "source_url",
    "document_hash",
    "updated_at",
    "ingested_at",
    "loader_version",
    "config_hash",
)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "approved"}
    return bool(value)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _base_source_type(source_type: str) -> str:
    return str(source_type or "").strip().split("/", 1)[0]


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    source_type: str
    is_approved: bool
    tier: str
    source_ref: str
    lineage_ref: str = ""
    reviewed_by: str = ""
    approved_at: str = ""
    expires_at: str = ""
    needs_review_at: str = ""
    audience_allowed: list[str] = field(default_factory=list)
    source_category: str = ""
    is_customer_facing_allowed: bool = False
    is_internal_only: bool = False
    is_future_only: bool = False
    source_url: str = ""
    document_hash: str = ""
    updated_at: str = ""
    ingested_at: str = ""
    loader_version: str = ""
    config_hash: str = ""
    disabled: bool = False
    redaction_status: str = ""
    redaction_applied: bool = False

    @classmethod
    def from_chunk(cls, chunk: dict[str, Any]) -> "SourceRecord":
        source_type = str(chunk.get("source_type") or chunk.get("doc_type") or "").strip()
        source_id = str(chunk.get("source_id") or chunk.get("source_file") or chunk.get("article_id") or "").strip()
        source_ref = str(chunk.get("source_ref") or chunk.get("url") or chunk.get("source_file") or "").strip()
        audience = chunk.get("audience_allowed")
        if isinstance(audience, str):
            audience_allowed = [a.strip() for a in audience.strip("[]").replace('"', "").split(",") if a.strip()]
        elif isinstance(audience, list):
            audience_allowed = [str(a).strip() for a in audience if str(a).strip()]
        else:
            audience_allowed = []

        return cls(
            source_id=source_id,
            source_type=source_type,
            is_approved=_truthy(chunk.get("is_approved")),
            tier=str(chunk.get("tier") or chunk.get("source_tier") or "").strip(),
            source_ref=source_ref,
            lineage_ref=str(chunk.get("lineage_ref") or chunk.get("article_id") or "").strip(),
            reviewed_by=str(chunk.get("reviewed_by") or "").strip(),
            approved_at=str(chunk.get("approved_at") or "").strip(),
            expires_at=str(chunk.get("expires_at") or "").strip(),
            needs_review_at=str(chunk.get("needs_review_at") or "").strip(),
            audience_allowed=audience_allowed,
            source_category=str(chunk.get("source_category") or chunk.get("doc_type") or "").strip(),
            is_customer_facing_allowed=_truthy(chunk.get("is_customer_facing_allowed")),
            is_internal_only=_truthy(chunk.get("is_internal_only")),
            is_future_only=_truthy(chunk.get("is_future_only")),
            source_url=str(chunk.get("source_url") or chunk.get("url") or "").strip(),
            document_hash=str(chunk.get("document_hash") or "").strip(),
            updated_at=str(chunk.get("updated_at") or "").strip(),
            ingested_at=str(chunk.get("ingested_at") or "").strip(),
            loader_version=str(chunk.get("loader_version") or "").strip(),
            config_hash=str(chunk.get("config_hash") or "").strip(),
            disabled=_truthy(chunk.get("disabled")),
            redaction_status=str(chunk.get("redaction_status") or "").strip(),
            redaction_applied=_truthy(chunk.get("redaction_applied")),
        )

    def is_stale(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        expiry = _parse_dt(self.expires_at) or _parse_dt(self.needs_review_at)
        return bool(expiry and expiry < now)

    def customer_citation_allowed(self) -> bool:
        if any(not getattr(self, field_name) for field_name in REQUIRED_CUSTOMER_SOURCE_FIELDS):
            return False
        if self.disabled or self.is_future_only or self.is_internal_only:
            return False
        base_source_type = _base_source_type(self.source_type)
        if base_source_type in FORBIDDEN_CUSTOMER_SOURCE_TYPES:
            return False
        if base_source_type not in APPROVED_CUSTOMER_SOURCE_TYPES:
            return False
        if self.is_stale():
            return False
        if not self.is_approved or not self.is_customer_facing_allowed:
            return False
        if "customer" not in {a.lower() for a in self.audience_allowed}:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceChunk:
    evidence_id: str
    source: SourceRecord
    content: str
    display_text: str = ""
    chunk_hash: str = ""
    redaction_status: str = ""
    redaction_applied: bool = False
    score: float = 0.0
    citation_label: str = ""

    @classmethod
    def from_chunk(cls, chunk: dict[str, Any], label: str = "") -> "EvidenceChunk":
        return cls(
            evidence_id=str(chunk.get("id") or "").strip(),
            source=SourceRecord.from_chunk(chunk),
            content=str(chunk.get("content") or "").strip(),
            display_text=str(chunk.get("display_text") or chunk.get("content") or "").strip(),
            chunk_hash=str(chunk.get("chunk_hash") or "").strip(),
            redaction_status=str(chunk.get("redaction_status") or "").strip(),
            redaction_applied=_truthy(chunk.get("redaction_applied")),
            score=float(chunk.get("rerank_score") or chunk.get("score") or 0.0),
            citation_label=label,
        )

    def can_cite_customer(self) -> bool:
        return bool(
            self.evidence_id
            and self.content
            and self.chunk_hash
            and self.source.customer_citation_allowed()
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source"] = self.source.to_dict()
        return data


@dataclass(frozen=True)
class Citation:
    citation_id: str
    evidence_id: str
    source_id: str
    source_type: str
    source_ref: str
    audience_allowed: list[str]

    @classmethod
    def from_evidence(cls, evidence: EvidenceChunk) -> "Citation":
        return cls(
            citation_id=evidence.citation_label or evidence.evidence_id,
            evidence_id=evidence.evidence_id,
            source_id=evidence.source.source_id,
            source_type=evidence.source.source_type,
            source_ref=evidence.source.source_ref,
            audience_allowed=evidence.source.audience_allowed,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceBundle:
    chunks: list[EvidenceChunk]
    citations: list[Citation]
    blocked: list[dict[str, str]]

    @classmethod
    def from_chunks(cls, chunks: list[dict[str, Any]], audience: str = "customer") -> "EvidenceBundle":
        evidence_chunks: list[EvidenceChunk] = []
        citations: list[Citation] = []
        blocked: list[dict[str, str]] = []

        for index, chunk in enumerate(chunks, 1):
            evidence = EvidenceChunk.from_chunk(chunk, label=f"KB-{index}")
            if audience == "customer" and not evidence.can_cite_customer():
                blocked.append({
                    "evidence_id": evidence.evidence_id,
                    "source_id": evidence.source.source_id,
                    "source_type": evidence.source.source_type,
                    "reason": "source is missing metadata, unapproved, disabled, forbidden, stale, or not customer-facing",
                })
                continue
            evidence_chunks.append(evidence)
            citations.append(Citation.from_evidence(evidence))

        return cls(evidence_chunks, citations, blocked)

    def approved_chunks(self) -> list[EvidenceChunk]:
        return self.chunks

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunks": [chunk.to_dict() for chunk in self.chunks],
            "citations": [citation.to_dict() for citation in self.citations],
            "blocked": self.blocked,
        }
