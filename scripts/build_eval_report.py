from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_GOLDEN_REPORT = Path("eval/golden_set/last_report.json")
DEFAULT_JSON_OUTPUT = Path("eval/reports/latest.json")
DEFAULT_MARKDOWN_OUTPUT = Path("eval/reports/latest.md")
README_START = "<!-- eval-report:start -->"
README_END = "<!-- eval-report:end -->"


def _display(value: Any, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        text = f"{value:.4f}".rstrip("0").rstrip(".")
    else:
        text = str(value)
    return f"{text}{suffix}"


def _row(label: str, value: Any, note: str, suffix: str = "") -> str:
    return f"| {label} | {_display(value, suffix)} | {note} |"


def build_markdown(report: dict[str, Any]) -> str:
    gate = report.get("release_gate") or {}
    lines = [
        "# Evaluation Report",
        "",
        "Generated from `eval/golden_set/last_report.json`.",
        "",
        "## Summary",
        "| Metric | Result | Notes |",
        "| --- | ---: | --- |",
        _row("Golden cases", report.get("case_count"), "Manual support-style cases in the golden set."),
        _row("Evaluated results", report.get("evaluated_result_count"), "Stored outputs evaluated by the release gate."),
        _row("Schema valid", report.get("schema_valid"), "Golden-set contract validity."),
        _row("Hard safety failures", report.get("hard_failure_count"), "Forbidden, raw, unapproved, or unsupported citations."),
        _row("Readiness profile", gate.get("profile") if gate else None, "Evaluation profile used for this report."),
        _row("Demo readiness passed", gate.get("passed") if gate else None, "Developer-preview readiness status."),
        "",
        "## Retrieval",
        "| Metric | Result | Notes |",
        "| --- | ---: | --- |",
        _row("Recall@1", report.get("retrieval_recall_at_1"), "Expected source present first."),
        _row("Recall@3", report.get("retrieval_recall_at_3"), "Expected source present in top three."),
        _row("Recall@5", report.get("retrieval_recall_at_5"), "Expected source present in top five."),
        _row("Recall", report.get("retrieval_recall"), "Expected source present anywhere retrieved/cited."),
        _row("MRR", report.get("mean_reciprocal_rank"), "First correct source rank quality."),
        _row("Source precision", report.get("source_precision"), "Retrieved sources matching expected/allowed sources."),
        "",
        "## Answer And Safety",
        "| Metric | Result | Notes |",
        "| --- | ---: | --- |",
        _row("Citation recall", report.get("citation_recall"), "Expected sources cited by the answer."),
        _row("Citation precision", report.get("citation_precision"), "Citations matching expected/allowed sources."),
        _row("Required point coverage", report.get("required_point_coverage"), "Deterministic coverage of expected answer points."),
        _row("Forbidden point violations", report.get("forbidden_point_violation_count"), "Disallowed answer points found."),
        _row("Route accuracy", report.get("route_accuracy"), "Planner route match."),
        _row("Confidence accuracy", report.get("confidence_band_accuracy"), "Green/yellow/red calibration match."),
        _row("Abstention accuracy", report.get("abstention_accuracy"), "Expected red/missing-context behavior."),
        _row("Validation pass rate", report.get("validation_pass_rate"), "Validator passed stored outputs."),
        _row("Fallback rate", report.get("fallback_rate"), "Outputs that abstained or used fallback behavior."),
        "",
        "## Operations",
        "| Metric | Result | Notes |",
        "| --- | ---: | --- |",
        _row("Avg latency", report.get("avg_latency_ms"), "Mean response latency.", " ms"),
        _row("P50 latency", report.get("p50_latency_ms"), "Median response latency.", " ms"),
        _row("P95 latency", report.get("p95_latency_ms"), "Tail response latency.", " ms"),
        _row("Avg total tokens", report.get("avg_total_tokens"), "Mean total tokens per evaluated result."),
        _row("Avg input tokens", report.get("avg_tokens_in"), "Mean input tokens per evaluated result."),
        _row("Avg output tokens", report.get("avg_tokens_out"), "Mean output tokens per evaluated result."),
        _row("Avg cost", report.get("avg_cost_usd"), "Mean reported LLM cost per evaluated result.", " USD"),
        _row("Total cost", report.get("total_cost_usd"), "Total reported LLM cost for the stored run.", " USD"),
    ]
    if gate.get("warnings"):
        lines.extend(["", "## Readiness Warnings"])
        lines.extend(f"- {warning}" for warning in gate["warnings"])
    if gate.get("blockers"):
        lines.extend(["", "## Readiness Blockers"])
        lines.extend(f"- {blocker}" for blocker in gate["blockers"])
    return "\n".join(lines) + "\n"


def build_readme_block(report: dict[str, Any]) -> str:
    gate = report.get("release_gate") or {}
    lines = [
        README_START,
        "| Metric | Current value |",
        "| --- | ---: |",
        f"| Demo readiness | {'passed' if gate.get('passed') else 'not ready'} |",
        f"| Golden cases | {_display(report.get('case_count'))} |",
        f"| Source-safety hard failures | {_display(report.get('hard_failure_count'))} |",
        f"| Validation/review warnings | {_display(report.get('validation_failure_count'))} |",
        f"| Recall@3/5 | {_display(report.get('retrieval_recall_at_3'))} |",
        f"| Source precision | {_display(report.get('source_precision'))} |",
        f"| Citation precision | {_display(report.get('citation_precision'))} |",
        f"| Required-point coverage | {_display(report.get('required_point_coverage'))} |",
        f"| Total eval cost | {_display(report.get('total_cost_usd'), ' USD')} |",
        "| Production readiness | not approved |",
        README_END,
    ]
    return "\n".join(lines)


def update_readme(readme_path: Path, block: str) -> None:
    text = readme_path.read_text()
    if README_START in text and README_END in text:
        before = text.split(README_START, 1)[0]
        after = text.split(README_END, 1)[1]
        readme_path.write_text(before + block + after)
        return
    marker = "Current stored golden-eval report:\n\n"
    if marker in text:
        readme_path.write_text(text.replace(marker, marker + block + "\n\n", 1))
        return
    readme_path.write_text(text.rstrip() + "\n\n## Current Evaluation\n\n" + block + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build benchmark report artifacts from the golden eval report.")
    parser.add_argument("--golden-report", type=Path, default=DEFAULT_GOLDEN_REPORT)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN_OUTPUT)
    parser.add_argument("--readme", type=Path, default=None)
    args = parser.parse_args()

    report = json.loads(args.golden_report.read_text())
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    args.markdown_output.write_text(build_markdown(report))
    if args.readme:
        update_readme(args.readme, build_readme_block(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
