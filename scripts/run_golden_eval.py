"""
Offline golden-set safety checks.

This is intentionally deterministic: it validates the golden-set contract and
can compare stored result JSONL without making LLM calls.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA = ROOT / "eval" / "golden_set" / "schema.json"
DEFAULT_GOLDEN_SET = ROOT / "eval" / "golden_set" / "v3_1_starter.jsonl"
DEFAULT_ALIASES = ROOT / "eval" / "golden_set" / "source_aliases.json"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open() as handle:
        for lineno, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno} is not valid JSON: {exc}") from exc
    return rows


def _load_schema(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _load_aliases(path: Path = DEFAULT_ALIASES) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return {
        str(source_id): [str(alias) for alias in aliases]
        for source_id, aliases in raw.items()
        if isinstance(aliases, list)
    }


def validate_golden_rows(rows: list[dict[str, Any]], schema: dict[str, Any]) -> dict[str, Any]:
    required = schema.get("required", [])
    errors = []
    for index, row in enumerate(rows, 1):
        missing = [field for field in required if field not in row]
        if missing:
            errors.append(f"row {index} missing required fields: {', '.join(missing)}")
        if row.get("expected_confidence_band") not in {"green", "yellow", "red"}:
            errors.append(f"row {index} has invalid expected_confidence_band")
        expected = set(row.get("expected_source_ids") or [])
        forbidden = set(row.get("forbidden_source_ids") or [])
        overlap = expected & forbidden
        if overlap:
            errors.append(f"row {index} expects forbidden source(s): {', '.join(sorted(overlap))}")
    return {
        "case_count": len(rows),
        "schema_errors": errors,
        "schema_valid": not errors,
    }


def evaluate_stored_results(rows: list[dict[str, Any]], result_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_ticket = {row.get("ticket_id"): row for row in rows}
    source_aliases = _load_aliases()
    hard_failures = []
    route_hits = 0
    confidence_hits = 0
    retrieval_recall_values = []
    retrieval_recall_at_1_values = []
    retrieval_recall_at_3_values = []
    retrieval_recall_at_5_values = []
    reciprocal_rank_values = []
    source_precision_values = []
    citation_recall_values = []
    citation_precision_values = []
    latency_values = []
    cost_values = []
    total_token_values = []
    tokens_in_values = []
    tokens_out_values = []
    faithfulness_values = []
    context_relevance_values = []
    answer_relevance_values = []
    required_point_values = []
    forbidden_point_violations = 0
    abstention_hits = 0
    abstention_count = 0
    validation_pass_count = 0
    validation_failure_count = 0
    evaluated = 0

    for result in result_rows:
        ticket_id = result.get("ticket_id")
        expected = by_ticket.get(ticket_id)
        if not expected:
            hard_failures.append(f"{ticket_id or '<missing>'}: no matching golden-set case")
            continue
        evaluated += 1

        cited_source_ids = set(result.get("cited_source_ids") or [])
        forbidden = set(expected.get("forbidden_source_ids") or [])
        if cited_source_ids & forbidden:
            hard_failures.append(f"{ticket_id}: cited forbidden source(s): {', '.join(sorted(cited_source_ids & forbidden))}")
        if result.get("customer_facing_unapproved_citation_count", 0):
            hard_failures.append(f"{ticket_id}: customer-facing unapproved citation")
        if result.get("raw_historical_ticket_citation_count", 0):
            hard_failures.append(f"{ticket_id}: raw historical ticket citation")
        if result.get("unsupported_factual_claim_count", 0):
            hard_failures.append(f"{ticket_id}: unsupported factual claim")
        answer_text = str(result.get("answer_text") or "").strip()
        if result.get("confidence_band") == "red" and answer_text and not bool(result.get("abstained", False)):
            hard_failures.append(f"{ticket_id}: red confidence answer was not abstained")

        if result.get("route") == expected.get("expected_route"):
            route_hits += 1
        if result.get("confidence_band") == expected.get("expected_confidence_band"):
            confidence_hits += 1
        if result.get("validation_passed") is True:
            validation_pass_count += 1
        if bool(result.get("abstained", False)):
            abstention_count += 1
        if bool(result.get("abstained", False)) == bool(expected.get("review_required_expected", False) and expected.get("expected_confidence_band") == "red"):
            abstention_hits += 1
        expected_review = bool(expected.get("review_required_expected", False))
        expected_red = expected.get("expected_confidence_band") == "red"
        if result.get("validation_passed") is False and not (expected_review or expected_red):
            validation_failure_count += 1
        expected_source_groups = _expected_source_groups(expected, source_aliases)
        expected_sources = set().union(*expected_source_groups) if expected_source_groups else set()
        retrieved_source_ids = list(result.get("retrieved_source_ids") or result.get("cited_source_ids") or [])
        retrieved_sources = set(retrieved_source_ids)
        if expected_source_groups:
            retrieval_recall_values.append(_group_recall(expected_source_groups, retrieved_sources))
            retrieval_recall_at_1_values.append(_group_recall(expected_source_groups, set(retrieved_source_ids[:1])))
            retrieval_recall_at_3_values.append(_group_recall(expected_source_groups, set(retrieved_source_ids[:3])))
            retrieval_recall_at_5_values.append(_group_recall(expected_source_groups, set(retrieved_source_ids[:5])))
            reciprocal_rank_values.append(_reciprocal_rank(expected_sources, retrieved_source_ids))
            citation_recall_values.append(_group_recall(expected_source_groups, cited_source_ids))
        if cited_source_ids:
            allowed_sources = expected_sources | (set(result.get("allowed_extra_source_ids") or []))
            if allowed_sources:
                disallowed_sources = cited_source_ids - allowed_sources
                if disallowed_sources:
                    hard_failures.append(f"{ticket_id}: cited unallowed source(s): {', '.join(sorted(disallowed_sources))}")
                citation_precision_values.append(len(cited_source_ids & allowed_sources) / len(cited_source_ids))
        if retrieved_sources:
            allowed_sources = expected_sources | (set(result.get("allowed_extra_source_ids") or []))
            if allowed_sources:
                source_precision_values.append(len(retrieved_sources & allowed_sources) / len(retrieved_sources))
        if result.get("latency_ms") is not None:
            latency_values.append(float(result.get("latency_ms") or 0.0))
        if result.get("cost_usd") is not None:
            cost_values.append(float(result.get("cost_usd") or 0.0))
        if result.get("total_tokens") is not None:
            total_token_values.append(float(result.get("total_tokens") or 0.0))
        if result.get("tokens_in") is not None:
            tokens_in_values.append(float(result.get("tokens_in") or 0.0))
        if result.get("tokens_out") is not None:
            tokens_out_values.append(float(result.get("tokens_out") or 0.0))
        if result.get("faithfulness_score") is not None:
            faithfulness_values.append(float(result.get("faithfulness_score") or 0.0))
        if result.get("context_relevance_score") is not None:
            context_relevance_values.append(float(result.get("context_relevance_score") or 0.0))
        if result.get("answer_relevance_score") is not None:
            answer_relevance_values.append(float(result.get("answer_relevance_score") or 0.0))
        answer_text = str(result.get("answer_text") or "")
        required_points = [str(point) for point in expected.get("must_include_points") or [] if str(point).strip()]
        forbidden_points = [str(point) for point in expected.get("must_not_include_points") or [] if str(point).strip()]
        if answer_text and required_points:
            required_point_values.append(_point_coverage(answer_text, required_points))
        if answer_text and forbidden_points:
            forbidden_point_violations += _forbidden_point_violations(answer_text, forbidden_points)

    return {
        "evaluated_result_count": evaluated,
        "route_accuracy": round(route_hits / evaluated, 4) if evaluated else None,
        "confidence_band_accuracy": round(confidence_hits / evaluated, 4) if evaluated else None,
        "abstention_accuracy": round(abstention_hits / evaluated, 4) if evaluated else None,
        "fallback_rate": round(abstention_count / evaluated, 4) if evaluated else None,
        "validation_pass_rate": round(validation_pass_count / evaluated, 4) if evaluated else None,
        "retrieval_recall": _avg(retrieval_recall_values),
        "retrieval_recall_at_1": _avg(retrieval_recall_at_1_values),
        "retrieval_recall_at_3": _avg(retrieval_recall_at_3_values),
        "retrieval_recall_at_5": _avg(retrieval_recall_at_5_values),
        "mean_reciprocal_rank": _avg(reciprocal_rank_values),
        "source_precision": _avg(source_precision_values),
        "citation_recall": _avg(citation_recall_values),
        "citation_precision": _avg(citation_precision_values),
        "required_point_coverage": _avg(required_point_values),
        "forbidden_point_violation_count": forbidden_point_violations,
        "ragas_faithfulness": _avg(faithfulness_values),
        "rag_triad": {
            "context_relevance": _avg(context_relevance_values),
            "groundedness": _avg(faithfulness_values),
            "answer_relevance": _avg(answer_relevance_values),
        },
        "avg_latency_ms": _avg(latency_values),
        "p50_latency_ms": _percentile(latency_values, 50),
        "p95_latency_ms": _percentile(latency_values, 95),
        "avg_total_tokens": _avg(total_token_values),
        "avg_tokens_in": _avg(tokens_in_values),
        "avg_tokens_out": _avg(tokens_out_values),
        "total_cost_usd": round(sum(cost_values), 6) if cost_values else None,
        "avg_cost_usd": _avg(cost_values),
        "validation_failure_count": validation_failure_count,
        "hard_failures": hard_failures,
        "hard_failure_count": len(hard_failures),
    }


def compare_to_baseline(report: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    metric_names = [
        "retrieval_recall",
        "retrieval_recall_at_1",
        "retrieval_recall_at_3",
        "retrieval_recall_at_5",
        "mean_reciprocal_rank",
        "source_precision",
        "citation_recall",
        "citation_precision",
        "required_point_coverage",
        "route_accuracy",
        "confidence_band_accuracy",
        "avg_latency_ms",
        "p50_latency_ms",
        "p95_latency_ms",
        "avg_total_tokens",
        "avg_cost_usd",
        "total_cost_usd",
        "abstention_accuracy",
        "fallback_rate",
        "validation_pass_rate",
        "ragas_faithfulness",
    ]
    diff = {}
    for metric in metric_names:
        current = report.get(metric)
        previous = baseline.get(metric)
        if current is None or previous is None:
            continue
        diff[metric] = {
            "current": current,
            "baseline": previous,
            "delta": round(float(current) - float(previous), 6),
        }
    return diff


def release_gate_report(
    report: dict[str, Any],
    *,
    baseline_diff: dict[str, Any] | None = None,
    max_avg_latency_ms: float | None = None,
    max_total_cost_usd: float | None = None,
    fail_on_baseline_regression: bool = False,
) -> dict[str, Any]:
    blockers = []
    warnings = []
    if not report.get("schema_valid", False):
        blockers.append("golden schema invalid")
    if int(report.get("evaluated_result_count", 0) or 0) <= 0:
        blockers.append("no evaluated golden results present")
    if report.get("hard_failure_count", 0):
        blockers.append("source-safety hard failures present")
    if report.get("validation_failure_count", 0):
        warnings.append(f"{report['validation_failure_count']} validation/review failures present")
    if max_avg_latency_ms is not None and report.get("avg_latency_ms") is not None:
        if float(report["avg_latency_ms"]) > float(max_avg_latency_ms):
            blockers.append(f"avg latency {report['avg_latency_ms']}ms exceeds budget {max_avg_latency_ms}ms")
    if max_total_cost_usd is not None and report.get("total_cost_usd") is not None:
        if float(report["total_cost_usd"]) > float(max_total_cost_usd):
            blockers.append(f"total cost ${report['total_cost_usd']} exceeds budget ${max_total_cost_usd}")
    if fail_on_baseline_regression:
        for metric, values in (baseline_diff or {}).items():
            delta = float(values.get("delta", 0) or 0)
            if metric in {"avg_latency_ms", "total_cost_usd"} and delta > 0:
                blockers.append(f"{metric} regressed vs baseline by {delta}")
            elif metric not in {"avg_latency_ms", "total_cost_usd"} and delta < 0:
                blockers.append(f"{metric} regressed vs baseline by {delta}")
    elif baseline_diff:
        warnings.append("baseline diff reported; regression blocking disabled")
    return {
        "passed": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "budgets": {
            "max_avg_latency_ms": max_avg_latency_ms,
            "max_total_cost_usd": max_total_cost_usd,
        },
    }


def human_readable_report(report: dict[str, Any]) -> str:
    lines = [
        "# Golden Eval Report",
        "",
        f"Cases: {report.get('case_count', 0)}",
        f"Evaluated results: {report.get('evaluated_result_count', 0)}",
        f"Schema valid: {report.get('schema_valid')}",
        f"Hard failures: {report.get('hard_failure_count', 0)}",
        "",
        "## Metrics",
        f"- Retrieval recall: {_display(report.get('retrieval_recall'))}",
        f"- Retrieval Recall@1: {_display(report.get('retrieval_recall_at_1'))}",
        f"- Retrieval Recall@3: {_display(report.get('retrieval_recall_at_3'))}",
        f"- Retrieval Recall@5: {_display(report.get('retrieval_recall_at_5'))}",
        f"- Mean reciprocal rank: {_display(report.get('mean_reciprocal_rank'))}",
        f"- Source precision: {_display(report.get('source_precision'))}",
        f"- Citation recall: {_display(report.get('citation_recall'))}",
        f"- Citation precision: {_display(report.get('citation_precision'))}",
        f"- Required point coverage: {_display(report.get('required_point_coverage'))}",
        f"- Forbidden point violations: {_display(report.get('forbidden_point_violation_count'))}",
        f"- Route accuracy: {_display(report.get('route_accuracy'))}",
        f"- Confidence band accuracy: {_display(report.get('confidence_band_accuracy'))}",
        f"- Abstention accuracy: {_display(report.get('abstention_accuracy'))}",
        f"- Fallback rate: {_display(report.get('fallback_rate'))}",
        f"- Validation pass rate: {_display(report.get('validation_pass_rate'))}",
        f"- RAGAS-style faithfulness: {_display(report.get('ragas_faithfulness'))}",
        f"- Avg latency ms: {_display(report.get('avg_latency_ms'))}",
        f"- P50 latency ms: {_display(report.get('p50_latency_ms'))}",
        f"- P95 latency ms: {_display(report.get('p95_latency_ms'))}",
        f"- Avg total tokens: {_display(report.get('avg_total_tokens'))}",
        f"- Avg input tokens: {_display(report.get('avg_tokens_in'))}",
        f"- Avg output tokens: {_display(report.get('avg_tokens_out'))}",
        f"- Avg cost USD: {_display(report.get('avg_cost_usd'))}",
        f"- Total cost USD: {_display(report.get('total_cost_usd'))}",
        "",
        "## RAG Triad",
    ]
    triad = report.get("rag_triad") or {}
    lines.extend([
        f"- Context relevance: {_display(triad.get('context_relevance'))}",
        f"- Groundedness: {_display(triad.get('groundedness'))}",
        f"- Answer relevance: {_display(triad.get('answer_relevance'))}",
    ])
    if report.get("baseline_diff"):
        lines.extend(["", "## Baseline Diff"])
        for name, values in report["baseline_diff"].items():
            lines.append(f"- {name}: {values['baseline']} -> {values['current']} ({values['delta']:+})")
    if report.get("hard_failures"):
        lines.extend(["", "## Hard Failures"])
        lines.extend(f"- {failure}" for failure in report["hard_failures"])
    gate = report.get("release_gate") or {}
    if gate:
        lines.extend(["", "## Release Gate", f"- Passed: {gate.get('passed')}"])
        if gate.get("blockers"):
            lines.extend(f"- Blocker: {item}" for item in gate["blockers"])
        if gate.get("warnings"):
            lines.extend(f"- Warning: {item}" for item in gate["warnings"])
    return "\n".join(lines) + "\n"


def _display(value: Any) -> str:
    return "n/a" if value is None else str(value)


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 4)
    rank = (len(ordered) - 1) * (percentile / 100)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    value = ordered[lower] * (1 - weight) + ordered[upper] * weight
    return round(value, 4)


def _expand_expected_source_ids(row: dict[str, Any], source_aliases: dict[str, list[str]] | None = None) -> set[str]:
    expanded = set()
    for group in _expected_source_groups(row, source_aliases):
        expanded.update(group)
    return expanded


def _expected_source_groups(row: dict[str, Any], source_aliases: dict[str, list[str]] | None = None) -> list[set[str]]:
    groups = []
    row_aliases = row.get("expected_source_aliases") or {}
    for source_id in row.get("expected_source_ids") or []:
        source_id = str(source_id)
        group = {source_id}
        if isinstance(row_aliases, dict):
            group.update(str(alias) for alias in row_aliases.get(source_id, []) or [])
        group.update((source_aliases or {}).get(source_id, []))
        groups.append(group)
    return groups


def _group_recall(expected_source_groups: list[set[str]], retrieved_sources: set[str]) -> float:
    if not expected_source_groups:
        return 0.0
    hits = sum(1 for group in expected_source_groups if group & retrieved_sources)
    return hits / len(expected_source_groups)


def _recall_at_k(expected_sources: set[str], retrieved_source_ids: list[str], k: int) -> float:
    top_k = set(retrieved_source_ids[:k])
    return len(expected_sources & top_k) / len(expected_sources)


def _reciprocal_rank(expected_sources: set[str], retrieved_source_ids: list[str]) -> float:
    for index, source_id in enumerate(retrieved_source_ids, 1):
        if source_id in expected_sources:
            return 1 / index
    return 0.0


def _normalize_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _point_coverage(answer_text: str, required_points: list[str]) -> float:
    normalized_answer = _normalize_text(answer_text)
    hits = sum(1 for point in required_points if _normalize_text(point) in normalized_answer)
    return hits / len(required_points) if required_points else 0.0


def _forbidden_point_violations(answer_text: str, forbidden_points: list[str]) -> int:
    normalized_answer = _normalize_text(answer_text)
    return sum(1 for point in forbidden_points if _normalize_text(point) in normalized_answer)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--golden-set", type=Path, default=DEFAULT_GOLDEN_SET)
    parser.add_argument("--results", type=Path, default=None)
    parser.add_argument("--baseline", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--markdown-output", type=Path, default=None)
    parser.add_argument("--fail-on-baseline-regression", action="store_true")
    parser.add_argument("--max-avg-latency-ms", type=float, default=None)
    parser.add_argument("--max-total-cost-usd", type=float, default=None)
    parser.add_argument("--release-gate", action="store_true")
    args = parser.parse_args()

    schema = _load_schema(args.schema)
    rows = _read_jsonl(args.golden_set)
    report = validate_golden_rows(rows, schema)
    if args.results:
        report.update(evaluate_stored_results(rows, _read_jsonl(args.results)))
    else:
        report.update({
            "evaluated_result_count": 0,
            "route_accuracy": None,
            "confidence_band_accuracy": None,
            "abstention_accuracy": None,
            "fallback_rate": None,
            "validation_pass_rate": None,
            "retrieval_recall": None,
            "retrieval_recall_at_1": None,
            "retrieval_recall_at_3": None,
            "retrieval_recall_at_5": None,
            "mean_reciprocal_rank": None,
            "source_precision": None,
            "citation_recall": None,
            "citation_precision": None,
            "required_point_coverage": None,
            "forbidden_point_violation_count": 0,
            "ragas_faithfulness": None,
            "rag_triad": {"context_relevance": None, "groundedness": None, "answer_relevance": None},
            "avg_latency_ms": None,
            "p50_latency_ms": None,
            "p95_latency_ms": None,
            "avg_total_tokens": None,
            "avg_tokens_in": None,
            "avg_tokens_out": None,
            "total_cost_usd": None,
            "avg_cost_usd": None,
            "validation_failure_count": 0,
            "hard_failures": [],
            "hard_failure_count": 0,
        })
    if args.baseline:
        report["baseline_diff"] = compare_to_baseline(report, json.loads(args.baseline.read_text()))
    if args.release_gate or args.fail_on_baseline_regression or args.max_avg_latency_ms is not None or args.max_total_cost_usd is not None:
        report["release_gate"] = release_gate_report(
            report,
            baseline_diff=report.get("baseline_diff"),
            max_avg_latency_ms=args.max_avg_latency_ms,
            max_total_cost_usd=args.max_total_cost_usd,
            fail_on_baseline_regression=args.fail_on_baseline_regression,
        )

    print(json.dumps(report, indent=2, sort_keys=True))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(human_readable_report(report))
    gate_failed = bool(report.get("release_gate") and not report["release_gate"].get("passed", False))
    return 1 if report["schema_errors"] or report["hard_failure_count"] or gate_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
