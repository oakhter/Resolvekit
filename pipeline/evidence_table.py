from __future__ import annotations

import re
from typing import Any


_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _first_fact(content: str) -> str:
    text = " ".join(str(content or "").split())
    for sentence in _SENTENCE_RE.split(text):
        sentence = sentence.strip()
        if 20 <= len(sentence) <= 240:
            return sentence
    return text[:240]


def build(context: dict[str, Any]) -> dict[str, Any]:
    evidence_bundle = context.get("evidence_bundle")
    citations = {
        citation.evidence_id: citation.citation_id
        for citation in getattr(evidence_bundle, "citations", [])
    }
    top_chunks = context.get("top_chunks", [])
    supported_facts = []
    for index, chunk in enumerate(top_chunks, 1):
        chunk_id = str(chunk.get("id", ""))
        citation = citations.get(chunk_id, f"KB-{index}")
        fact = _first_fact(chunk.get("display_text") or chunk.get("content") or "")
        if not fact:
            continue
        supported_facts.append({
            "claim": fact,
            "citations": [citation],
            "confidence": "high" if float(chunk.get("rerank_score") or 0.0) >= 7 else "medium",
            "source_id": str(chunk.get("source_id", "")),
            "source_type": str(chunk.get("source_type", "")),
        })

    planner_output = context.get("planner_output", {})
    missing_context = list(planner_output.get("required_context") or planner_output.get("missing_context") or [])
    for item in context.get("condition_context", {}).get("missing_context_fields", []):
        if item not in missing_context:
            missing_context.append(item)

    conflicts = []
    for conflict in context.get("source_conflicts", []):
        conflicts.append({
            "topic": conflict.get("topic") or conflict.get("field") or "source conflict",
            "source_a": conflict.get("source_a") or conflict.get("left_source") or "",
            "source_b": conflict.get("source_b") or conflict.get("right_source") or "",
            "severity": conflict.get("severity", "medium"),
            "summary": conflict.get("summary", ""),
        })

    return {
        "supported_facts": supported_facts,
        "missing_context": missing_context,
        "conflicts": conflicts,
        "blocked_evidence": getattr(evidence_bundle, "blocked", []),
    }
