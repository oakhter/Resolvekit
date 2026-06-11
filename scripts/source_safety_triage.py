from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.run_golden_eval import (
    DEFAULT_ALIASES,
    DEFAULT_GOLDEN_SET,
    _expected_source_groups,
    _load_aliases,
    _read_jsonl,
)


DEFAULT_RESULTS = Path("eval/golden_set/last_results.jsonl")
DEFAULT_OUTPUT = Path("experiments/reports/source_safety_failure_triage.md")


def _allowed_sources(golden_row: dict[str, Any], source_aliases: dict[str, list[str]]) -> set[str]:
    groups = _expected_source_groups(golden_row, source_aliases)
    expected = set().union(*groups) if groups else set()
    return expected | set(golden_row.get("allowed_extra_source_ids") or [])


def _bucket(
    expected_sources: set[str],
    retrieved_sources: set[str],
    evidence_sources: set[str],
    customer_sources: set[str],
) -> str:
    if customer_sources - expected_sources:
        return "customer_over_citation"
    if evidence_sources - expected_sources and not (customer_sources - expected_sources):
        return "context_only_over_breadth"
    if expected_sources and not (retrieved_sources & expected_sources):
        return "missing_expected_retrieval"
    return "clean"


def build_triage_report(
    golden_rows: list[dict[str, Any]],
    result_rows: list[dict[str, Any]],
    source_aliases: dict[str, list[str]],
) -> dict[str, Any]:
    golden_by_id = {row.get("ticket_id"): row for row in golden_rows}
    cases = []
    bucket_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()

    for result in result_rows:
        ticket_id = result.get("ticket_id", "")
        golden = golden_by_id.get(ticket_id, {})
        expected_sources = _allowed_sources(golden, source_aliases) if golden else set()
        retrieved_sources = set(result.get("retrieved_source_ids") or [])
        evidence_sources = set(result.get("evidence_context_source_ids") or result.get("cited_source_ids") or [])
        if bool(result.get("abstained", False)) or result.get("fallback_reason"):
            customer_sources = set()
        else:
            customer_sources = set(
                result.get("customer_facing_cited_source_ids")
                if result.get("customer_facing_cited_source_ids") is not None
                else result.get("cited_source_ids") or []
            )
        unallowed_customer = sorted(customer_sources - expected_sources) if expected_sources else sorted(customer_sources)
        unallowed_context = sorted(evidence_sources - expected_sources) if expected_sources else sorted(evidence_sources)
        bucket = _bucket(expected_sources, retrieved_sources, evidence_sources, customer_sources)
        bucket_counts[bucket] += 1
        for source_id in unallowed_customer:
            source_counts[source_id] += 1
        cases.append({
            "ticket_id": ticket_id,
            "bucket": bucket,
            "expected_or_allowed": sorted(expected_sources),
            "retrieved": sorted(retrieved_sources),
            "evidence_context": sorted(evidence_sources),
            "customer_facing": sorted(customer_sources),
            "unallowed_customer_facing": unallowed_customer,
            "unallowed_context_only": unallowed_context,
        })

    return {
        "summary": {
            "case_count": len(cases),
            "bucket_counts": dict(sorted(bucket_counts.items())),
            "top_unallowed_customer_sources": source_counts.most_common(25),
        },
        "cases": cases,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Source Safety Failure Triage",
        "",
        "## Summary",
        "",
    ]
    for bucket, count in report["summary"]["bucket_counts"].items():
        lines.append(f"- {bucket}: {count}")
    lines.extend(["", "## Top Unallowed Customer Sources", ""])
    for source_id, count in report["summary"]["top_unallowed_customer_sources"]:
        lines.append(f"- {source_id}: {count}")
    lines.extend(["", "## Cases", ""])
    for case in report["cases"]:
        if case["bucket"] == "clean":
            continue
        lines.append(f"### {case['ticket_id']} — {case['bucket']}")
        lines.append(f"- Expected/allowed: {', '.join(case['expected_or_allowed']) or 'none'}")
        lines.append(f"- Customer-facing unallowed: {', '.join(case['unallowed_customer_facing']) or 'none'}")
        lines.append(f"- Context-only unallowed: {', '.join(case['unallowed_context_only']) or 'none'}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Triage golden source-safety failures by source and failure type.")
    parser.add_argument("--golden-set", type=Path, default=DEFAULT_GOLDEN_SET)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--aliases", type=Path, default=DEFAULT_ALIASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--json-output", type=Path, default=None)
    args = parser.parse_args()

    report = build_triage_report(
        _read_jsonl(args.golden_set),
        _read_jsonl(args.results),
        _load_aliases(args.aliases),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_markdown(report))
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
