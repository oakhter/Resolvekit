from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover - supports fresh system Python before deps install
    yaml = None

ROOT = Path(__file__).resolve().parent.parent
CONFIG_ROOT = ROOT / "configs" / "ab"


GLOBAL_GUARDRAILS = {
    "hard_failures_max": 0,
    "red_confidence_must_abstain": True,
    "inactive_chunks_must_not_retrieve": True,
    "recall_at_3_regression_max_pct": 0,
    "recall_at_5_regression_max_pct": 0,
    "p95_latency_regression_max_pct": 10,
    "cost_regression_max_pct": 15,
}


STAGES: list[dict[str, Any]] = [
    {
        "number": 1,
        "slug": "input_normalization",
        "stage": "input_normalization",
        "primary_metric": "validation_warning_reduction",
        "variants": [
            ("v1_minimal", "Minimal cleanup", "trim whitespace, normalize punctuation, preserve original text", "Reduces pointless query noise", {"route_accuracy_regression_max_pct": 0}),
            ("v2_redaction_first", "Redaction-first", "redact PII before routing/querying", "Reduces privacy risk without hurting retrieval", {"recall_regression_max_pct": 0}),
            ("v3_issue_hints", "Issue-hint extraction", "extract error codes, product area, version hints", "Improves routing/retrieval precision", {"recall_regression_max_pct": 0}),
            ("v4_structured_ticket", "Structured ticket parse", "convert input to summary/symptoms/environment/attempted_steps", "Improves multi-step troubleshooting", {"p95_latency_regression_max_pct": 10}),
            ("v5_noise_filter", "Noise filter", "remove greetings, signatures, repeated non-error logs", "Improves query quality", {"missing_critical_terms_max": 0}),
        ],
    },
    {
        "number": 2,
        "slug": "kb_loading",
        "stage": "kb_loading",
        "primary_metric": "source_validation_pass_rate",
        "variants": [
            ("v1_csv_baseline", "CSV-only", "use only CSV demo KB", "Establish clean structured baseline", {"coverage_tracked": True}, {"source_format_filter": ["csv"]}),
            ("v2_xlsx_baseline", "XLSX-only", "use only XLSX demo KB", "Tests spreadsheet import quality", {"row_level_errors_max": 0}, {"source_format_filter": ["xlsx"]}),
            ("v3_pdf_baseline", "PDF-only", "use only born-digital PDF demo KB", "Tests PDF extraction quality", {"scanned_pdf_accepted_max": 0}, {"source_format_filter": ["pdf"], "pdf_text_required": True}),
            ("v4_strict_metadata", "Strict metadata", "reject any source missing optional-but-useful metadata", "Cleaner metadata improves precision", {"coverage_floor_tracked": True}, {"strict_metadata": True}),
            ("v5_format_weighted", "Format-weighted retrieval", "apply source-type weights during retrieval", "Certain formats may be more reliable", {"recall_regression_max_pct": 0}, {"source_type_weights": {"csv": 1.0, "xlsx": 0.95, "pdf": 0.9}}),
        ],
    },
    {
        "number": 3,
        "slug": "chunking",
        "stage": "chunking",
        "primary_metric": "source_precision",
        "variants": [
            ("v1_256_40", "256/40", "256 tokens, 40 overlap", "Small chunks improve precision", {"recall_at_5_regression_max_pct": 0}, {"chunk_size_tokens": 256, "chunk_overlap_tokens": 40}),
            ("v2_512_80", "512/80", "512 tokens, 80 overlap", "Balanced default", {"p95_latency_regression_max_pct": 0}, {"chunk_size_tokens": 512, "chunk_overlap_tokens": 80}),
            ("v3_768_100", "768/100", "768 tokens, 100 overlap", "Longer support procedures stay intact", {"source_precision_regression_max_pct": 0}, {"chunk_size_tokens": 768, "chunk_overlap_tokens": 100}),
            ("v4_section_aware", "Section-aware", "split by headings/rows before token chunking", "Better semantic boundaries", {"chunk_count_tracked": True}, {"section_aware": True}),
            ("v5_format_aware", "Format-aware", "different chunking per CSV/XLSX/PDF", "Each format needs different chunking", {"complexity_documented": True}, {"format_aware": True}),
        ],
    },
    {
        "number": 4,
        "slug": "metadata_validation",
        "stage": "metadata_validation",
        "primary_metric": "source_precision",
        "variants": [
            ("v1_safety_only_required", "Safety-only required", "require only approval, active, customer-facing flags", "Maximizes coverage", {"hard_failures_max": 0}, {"required_metadata_profile": "safety_only"}),
            ("v2_alpha_strict", "Alpha strict", "require all alpha metadata fields", "Cleaner retrieval", {"fallback_rate_tracked": True}, {"required_metadata_profile": "alpha_strict"}),
            ("v3_authority_required", "Authority required", "require source authority and doc type", "Better conflict handling", {"coverage_floor_tracked": True}, {"required_fields": ["source_authority", "doc_type"]}),
            ("v4_freshness_required", "Freshness required", "require needs_review_at", "Reduces stale source use", {"coverage_tracked": True}, {"required_fields": ["needs_review_at"]}),
            ("v5_issue_class_required", "Issue-class required", "require issue_class/product_area", "Improves routing and retrieval", {"row_rejection_rate_tracked": True}, {"required_fields": ["issue_class", "product_area"]}),
        ],
    },
    {
        "number": 5,
        "slug": "routing",
        "stage": "routing",
        "primary_metric": "route_accuracy",
        "variants": [
            ("v1_always_standard", "Always standard", "every case uses standard mode", "Simpler and stable", {"p95_latency_tracked": True}, {"routing_mode": "standard"}),
            ("v2_rule_based_fast_standard_strict", "Rule-based fast/standard/strict", "keyword/metadata rules select mode", "Cuts latency for easy cases", {"reviewer_ready_regression_max_pct": 0}, {"routing_mode": "rule_based"}),
            ("v3_llm_classifier", "LLM classifier", "LLM selects route from fixed enum", "Better complex route choice", {"cost_regression_max_pct": 15, "p95_latency_regression_max_pct": 10}, {"routing_mode": "llm_classifier"}),
            ("v4_issue_class_route_map", "Issue-class route map", "issue class maps to route", "Product-agnostic support ontology helps", {"unknown_fallback_safe": True}, {"routing_mode": "issue_class_map"}),
            ("v5_strict_biased", "Strict-biased", "sensitive categories default to strict", "Improves safety", {"fallback_rate_tracked": True}, {"strict_bias": True}),
        ],
    },
    {
        "number": 6,
        "slug": "query_construction",
        "stage": "query_construction",
        "primary_metric": "source_precision",
        "variants": [
            ("v1_raw_user_query", "Raw user query", "no rewriting", "Simple baseline", {"source_precision_tracked": True}, {"query_mode": "raw"}),
            ("v2_cleaned_query", "Cleaned query", "normalized text plus extracted terms", "Removes noise", {"recall_regression_max_pct": 0}, {"query_mode": "cleaned"}),
            ("v3_metadata_boosted_query", "Metadata-boosted query", "adds issue_class/product_area/version hints", "Improves match quality", {"p95_latency_regression_max_pct": 0}, {"query_mode": "metadata_boosted"}),
            ("v4_multi_query_offline", "Multi-query offline", "query decomposition only in offline variant", "Helps complex cases", {"cost_tracked": True, "p95_latency_tracked": True}, {"query_mode": "multi_query"}),
            ("v5_sparse_dense_split", "Sparse+dense split", "separate BM25 terms from dense natural query", "Better hybrid retrieval", {"complexity_documented": True}, {"query_mode": "sparse_dense_split"}),
        ],
    },
    {
        "number": 7,
        "slug": "retrieval",
        "stage": "retrieval",
        "primary_metric": "retrieval_recall_at_5",
        "variants": [
            ("v1_bm25_only", "BM25-only", "keyword retrieval only", "Strong for error codes/API terms", {"source_precision_tracked": True}, {"retrieval_mode": "bm25"}),
            ("v2_dense_only", "Dense-only", "vector retrieval only", "Strong for paraphrases", {"source_precision_tracked": True}, {"retrieval_mode": "dense"}),
            ("v3_hybrid_equal_weight", "Hybrid equal-weight", "BM25 and dense equal weight", "Balanced", {"recall_regression_max_pct": 0}, {"retrieval_mode": "hybrid", "bm25_weight": 0.5, "dense_weight": 0.5}),
            ("v4_hybrid_bm25_heavy", "Hybrid BM25-heavy", "weight BM25 higher", "Better technical exact matching", {"paraphrase_recall_tracked": True}, {"retrieval_mode": "hybrid", "bm25_weight": 0.7, "dense_weight": 0.3}),
            ("v5_hybrid_metadata_heavy", "Hybrid metadata-heavy", "boost authority/doc_type/product_area/issue_class", "Cleaner evidence", {"coverage_tracked": True}, {"retrieval_mode": "hybrid", "metadata_boost": True}),
        ],
    },
    {
        "number": 8,
        "slug": "reranking",
        "stage": "reranking",
        "primary_metric": "citation_precision",
        "variants": [
            ("v1_no_reranker", "No reranker", "remove reranker", "Measures cost of reranking", {"citation_precision_floor_tracked": True}, {"reranker_enabled": False}),
            ("v2_top_20_to_5", "Top-20 -> 5", "rerank 20, keep 5", "Faster, precise", {"recall_tracked": True}, {"rerank_candidates": 20, "keep": 5}),
            ("v3_top_30_to_7", "Top-30 -> 7", "rerank 30, keep 7", "Balanced", {"p95_latency_tracked": True}, {"rerank_candidates": 30, "keep": 7}),
            ("v4_top_50_to_10", "Top-50 -> 10", "larger candidate pool", "Better recall for hard cases", {"cost_regression_max_pct": 15, "p95_latency_regression_max_pct": 10}, {"rerank_candidates": 50, "keep": 10}),
            ("v5_authority_aware_rerank", "Authority-aware rerank", "reranker score plus source authority", "Safer citations", {"recall_regression_max_pct": 0}, {"authority_aware": True}),
        ],
    },
    {
        "number": 9,
        "slug": "context_packing",
        "stage": "context_packing",
        "primary_metric": "citation_precision",
        "variants": [
            ("v1_top_k_only", "Top-K only", "pack reranked chunks in order", "Simple baseline", {"completeness_tracked": True}, {"packing_mode": "top_k"}),
            ("v2_source_deduped", "Source-deduped", "limit repeated chunks from same source", "Reduces evidence flooding", {"recall_tracked": True}, {"source_dedupe": True}),
            ("v3_citation_minimal", "Citation-minimal", "pack only chunks likely to be cited", "Improves draft focus", {"completeness_regression_max_pct": 0}, {"packing_mode": "citation_minimal"}),
            ("v4_conflict_aware", "Conflict-aware", "include conflicting source summaries separately", "Better abstention/conflict handling", {"false_abstentions_tracked": True}, {"conflict_aware": True}),
            ("v5_format_diverse", "Format-diverse", "ensure CSV/XLSX/PDF diversity when relevant", "Cross-format support improves", {"context_size_tracked": True}, {"format_diverse": True}),
        ],
    },
    {
        "number": 10,
        "slug": "drafting_template",
        "stage": "drafting_template",
        "primary_metric": "reviewer_ready_proxy",
        "variants": [
            ("v1_concise_support_reply", "Concise support reply", "short answer plus steps plus citations", "Easier for agents to use", {"completeness_tracked": True}, {"template": "concise_support_reply"}),
            ("v2_structured_support_schema", "Structured support schema", "issue/facts/steps/why/escalate/citations", "More inspectable", {"p95_latency_tracked": True}, {"template": "structured_support_schema"}),
            ("v3_troubleshooting_first", "Troubleshooting-first", "prioritizes ordered steps", "Better for how-to/support cases", {"citation_precision_tracked": True}, {"template": "troubleshooting_first"}),
            ("v4_policy_first", "Policy-first", "prioritizes policy boundaries/caveats", "Better for billing/permissions", {"fallback_rate_tracked": True}, {"template": "policy_first"}),
            ("v5_generate_review_refine", "Generate-review-refine", "one self-check/refine pass", "Reduces unsupported claims", {"cost_regression_max_pct": 15, "p95_latency_regression_max_pct": 10}, {"self_refine_passes": 1}),
        ],
    },
    {
        "number": 11,
        "slug": "citation_policy",
        "stage": "citation_policy",
        "primary_metric": "citation_precision",
        "variants": [
            ("v1_cite_every_step", "Cite every step", "each recommended step must cite", "Improves grounding", {"readability_tracked": True}, {"citation_mode": "every_step"}),
            ("v2_max_3_citations", "Max 3 citations", "cap customer-facing citations", "Reduces clutter/noise", {"citation_recall_tracked": True}, {"max_citations": 3}),
            ("v3_source_dedup_citations", "Source-dedup citations", "avoid repeated same-source citations", "Cleaner response", {"citation_precision_regression_max_pct": 0}, {"dedupe_source_citations": True}),
            ("v4_evidence_first_citations", "Evidence-first citations", "only cite directly supported claims", "Reduces unsupported claims", {"completeness_tracked": True}, {"citation_mode": "evidence_first"}),
            ("v5_internal_citation_blocker", "Internal-citation blocker", "hard block internal/non-customer-facing sources", "Improves safety", {"fallback_tracked": True}, {"block_internal_citations": True}),
        ],
    },
    {
        "number": 12,
        "slug": "validation",
        "stage": "validation",
        "primary_metric": "hard_failures",
        "variants": [
            ("v1_outcome_taxonomy", "Outcome taxonomy", "clean/clean_with_caveats/corrected/abstained/hard_failure", "More decision-useful", {"hard_failures_max": 0}, {"outcome_taxonomy": True}),
            ("v2_strict_citation_validation", "Strict citation validation", "hard-fail phantom/unapproved/internal citations", "Improves safety", {"fallback_tracked": True}, {"strict_citation_validation": True}),
            ("v3_warning_class_taxonomy", "Warning class taxonomy", "split warnings into source/citation/completeness/conflict", "Easier to fix failures", {"hidden_failures_max": 0}, {"warning_taxonomy": True}),
            ("v4_llm_claim_support_check", "LLM claim support check", "judge claim-to-citation support", "Reduces unsupported claims", {"cost_tracked": True, "p95_latency_tracked": True}, {"llm_claim_support_check": True}),
            ("v5_self_correction_before_final_validation", "Self-correction before final validation", "one correction pass before outcome", "More clean drafts", {"hard_failures_max": 0}, {"self_correction_passes": 1}),
        ],
    },
    {
        "number": 13,
        "slug": "confidence_abstention",
        "stage": "confidence_abstention",
        "primary_metric": "abstention_accuracy",
        "variants": [
            ("v1_conservative", "Conservative", "more yellow/red", "Fewer unsafe drafts", {"false_abstentions_tracked": True}, {"threshold_profile": "conservative"}),
            ("v2_balanced", "Balanced", "moderate thresholds", "Best alpha default", {"reviewer_ready_tracked": True}, {"threshold_profile": "balanced"}),
            ("v3_aggressive", "Aggressive", "more green drafts", "Higher coverage", {"unsupported_claims_tracked": True}, {"threshold_profile": "aggressive"}),
            ("v4_conflict_sensitive", "Conflict-sensitive", "red/yellow when source conflict found", "Safer conflict handling", {"reviewer_ready_tracked": True}, {"conflict_sensitive": True}),
            ("v5_freshness_sensitive", "Freshness-sensitive", "lower confidence for stale sources", "Reduces stale citations", {"fallback_tracked": True}, {"freshness_sensitive": True}),
        ],
    },
    {
        "number": 14,
        "slug": "feedback_capture",
        "stage": "feedback_capture",
        "primary_metric": "reviewer_ready_signal_completeness",
        "variants": [
            ("v1_three_button_feedback", "Three-button feedback", "send_as_is / edited / rejected", "Low friction", {"signal_quality_tracked": True}, {"feedback_mode": "three_button"}),
            ("v2_reason_code_required", "Reason-code required", "require reason for edited/rejected", "Better diagnostics", {"completion_rate_tracked": True}, {"require_reason_code": True}),
            ("v3_edit_distance_auto_score", "Edit-distance auto-score", "compute edit distance plus citations kept", "Better reviewer-ready proxy", {"privacy_guardrail": True}, {"edit_distance_score": True}),
            ("v4_knowledge_issue_prompt", "Knowledge-issue prompt", "suggest issue for repeated rejects", "Improves knowledge loop", {"false_positives_tracked": True}, {"knowledge_issue_prompt": True}),
            ("v5_post_submit_triage", "Post-submit triage", "ask one follow-up only after rejection", "Better labels without friction", {"completion_rate_tracked": True}, {"post_submit_triage": True}),
        ],
    },
    {
        "number": 15,
        "slug": "replay_export",
        "stage": "replay_export",
        "primary_metric": "replay_reproducibility",
        "variants": [
            ("v1_same_config_replay", "Same-config replay", "replay exact config hash", "Reproducibility baseline", {"secrets_excluded": True}, {"replay_mode": "same_config"}),
            ("v2_current_config_replay", "Current-config replay", "replay against current config", "Shows drift", {"audit_logs_required": True}, {"replay_mode": "current_config"}),
            ("v3_experiment_config_replay", "Experiment-config replay", "replay against variant config", "Supports A/B debugging", {"no_mutation": True}, {"replay_mode": "experiment_config"}),
            ("v4_minimal_support_bundle", "Minimal support bundle", "trace + config hash + metrics", "Smaller exports are safer", {"redaction_pass": True}, {"bundle_profile": "minimal"}),
            ("v5_full_redacted_bundle", "Full redacted bundle", "trace JSONL + source validation + eval context", "Better debugging", {"export_size_tracked": True, "privacy_guardrail": True}, {"bundle_profile": "full_redacted"}),
        ],
    },
]


def _hash_config(data: dict[str, Any]) -> str:
    payload = _dump(data, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _dump(data: dict[str, Any], *, sort_keys: bool = False) -> str:
    if yaml:
        return yaml.safe_dump(data, sort_keys=sort_keys, allow_unicode=False)
    import json

    return json.dumps(data, indent=2, sort_keys=sort_keys) + "\n"


def _variant_config(stage: dict[str, Any], variant: tuple[Any, ...]) -> dict[str, Any]:
    variant_id, name, changed_lever, expected_effect, guardrails, *rest = variant
    config = {
        "variant_id": f"{stage['number']:02d}_{stage['slug']}_{variant_id}",
        "stage": stage["stage"],
        "stage_number": stage["number"],
        "name": name,
        "experiment_mode": "offline_replay_only",
        "changed_lever": changed_lever,
        "expected_effect": expected_effect,
        "primary_metric": stage["primary_metric"],
        "guardrails": {**GLOBAL_GUARDRAILS, **guardrails},
        "runtime": {
            "live_rollout_allowed": False,
            "default_candidate": False,
            "record_config_hash": True,
            "record_prompt_versions": True,
            "record_source_index_version": True,
            "record_code_commit": True,
        },
    }
    if rest:
        config["variant_settings"] = rest[0]
    config["config_hash"] = _hash_config(config)
    return config


def materialize() -> list[Path]:
    CONFIG_ROOT.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    control = {
        "variant_id": "control",
        "stage": "all",
        "experiment_mode": "offline_replay_only",
        "changed_lever": "none",
        "expected_effect": "baseline",
        "primary_metric": "stage_default",
        "guardrails": GLOBAL_GUARDRAILS,
        "runtime": {"live_rollout_allowed": False, "default_candidate": False},
    }
    control["config_hash"] = _hash_config(control)
    control_path = CONFIG_ROOT / "control.yaml"
    control_path.write_text(_dump(control), encoding="utf-8")
    written.append(control_path)

    for stage in STAGES:
        stage_dir = CONFIG_ROOT / f"{stage['number']:02d}_{stage['slug']}"
        stage_dir.mkdir(parents=True, exist_ok=True)
        for variant in stage["variants"]:
            config = _variant_config(stage, variant)
            path = stage_dir / f"{variant[0]}.yaml"
            path.write_text(_dump(config), encoding="utf-8")
            written.append(path)
    return written


def main() -> int:
    written = materialize()
    print(f"Wrote {len(written)} A/B config files under {CONFIG_ROOT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
