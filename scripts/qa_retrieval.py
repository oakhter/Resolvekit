"""
Deterministic QA checks for configurator preview and retrieval packaging.

This script avoids LLM calls. It exercises source preview contracts, context-aware
chunk text construction, evaluator-skipped validation, and retrieval diagnostics
shape. Use --with-db only when local PostgreSQL is available.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.core import project_config
from backend.core.orchestrator import _collect_retrieval_signals
from knowledge_loader.kb_loader import build_chunk_texts, chunk_with_sections
from pipeline import validation


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def check_configurator_preview() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "kb.csv"
        path.write_text(
            "title,content,url\n"
            "Mobile login,Only managers can reset sessions when the mobile app token expires.,https://example.test\n",
            encoding="utf-8",
        )
        preview = project_config.preview_source("knowledge_base", str(path))
    _assert(preview["can_load"], "source preview should load a valid KB CSV")
    chunk = preview["sample_chunk_previews"][0]
    _assert("embedding_text" in chunk, "preview should show embedding_text")
    _assert("display_text" in chunk, "preview should show display_text")


def check_context_aware_chunking() -> None:
    chunks, sections = chunk_with_sections("# Setup\n\nOnly managers can enable mobile clock-in.", "article")
    _assert(sections[0]["heading_path"] == "Setup", "section heading should be inherited")
    texts = build_chunk_texts(
        chunks[0]["content"],
        title="Mobile clock-in",
        source_type="official_help_article",
        heading_path=chunks[0]["heading_path"],
        section_text=sections[0]["section_text"],
    )
    _assert("Title: Mobile clock-in" in texts["embedding_text"], "embedding_text should include title")
    _assert("Section: Setup" in texts["display_text"], "display_text should include section")


def check_evaluator_skipped_validation() -> None:
    context = {
        "resolution": {
            "confidence": "MEDIUM",
            "sources": "kb.csv",
            "resolution_steps": "1. Confirm the relevant setting and retry the affected workflow.",
            "draft_email": "Subject: Test\n\nHi,\n\nKind regards",
        },
        "eval_score": {
            "faithfulness": None,
            "completeness": None,
            "flags": ["ignored because evaluator skipped"],
            "evaluation_skipped": True,
        },
        "request_meta": {"permission_level": "manager", "access_channel": "mobile_app"},
        "ticket": {"cleaned": "Cannot clock in on mobile."},
        "top_chunks": [{
            "id": "kb_approved",
            "content": "Approved mobile troubleshooting guidance.",
            "source_id": "knowledge_base:mobile",
            "source_type": "official_help_article",
            "source_category": "knowledge_base",
            "is_approved": True,
            "tier": "approved_kb",
            "source_ref": "demo_knowledge_base.csv",
            "lineage_ref": "kb_mobile_troubleshooting",
            "reviewed_by": "demo_seed",
            "approved_at": "2026-05-01T00:00:00+00:00",
            "audience_allowed": ["customer", "internal"],
            "is_customer_facing_allowed": True,
            "source_url": "https://example.test/help/mobile-troubleshooting",
            "document_hash": "doc_hash",
            "chunk_hash": "chunk_hash",
            "updated_at": "2026-05-01T00:00:00+00:00",
            "ingested_at": "2026-05-01T00:00:00+00:00",
            "loader_version": "qa-fixture",
            "config_hash": "qa-config",
            "condition_flags": [],
            "rerank_score": 1.0,
        }],
    }
    result = validation.run(context)
    validation_data = result["resolution"]["validation"]
    _assert(validation_data["evaluation_skipped"], "validation should mark evaluator as skipped")
    _assert(not validation_data["gatekeeper_flagged"], "skipped evaluator flags should not fail validation")


def check_retrieval_diagnostics_shape() -> None:
    signals = _collect_retrieval_signals({
        "top_chunks": [
            {
                "id": "kb_article_0",
                "title": "Mobile login",
                "source_file": "kb.csv",
                "source_type": "official_help_article",
                "chunk_type": "troubleshooting",
                "heading_path": "Setup",
                "condition_flags": ["requires_role"],
                "retrieval_reason": "initial_match+condition_neighbor",
                "rerank_score": 8.25,
            }
        ],
        "retrieval_cache_hit": False,
    })
    _assert(signals["support_context_bundles"], "retrieval diagnostics should include support bundles")
    _assert("condition_neighbor" in signals["source_selection"][0], "source selection should explain expansion")


def check_db_retrieval_available() -> None:
    from backend.core import config
    from backend.db.schema import ensure_vector_schema
    import psycopg2

    with psycopg2.connect(config.DATABASE_URL, connect_timeout=5) as conn:
        ensure_vector_schema(conn, schema=config.KNOWLEDGE_SCHEMA)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-db", action="store_true", help="Also verify vector DB schema connectivity.")
    args = parser.parse_args()

    checks = [
        ("configurator preview", check_configurator_preview),
        ("context-aware chunking", check_context_aware_chunking),
        ("evaluator-skipped validation", check_evaluator_skipped_validation),
        ("retrieval diagnostics shape", check_retrieval_diagnostics_shape),
    ]
    if args.with_db:
        checks.append(("vector DB schema", check_db_retrieval_available))

    for label, check in checks:
        check()
        print(f"ok - {label}")
    print("deterministic QA passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
