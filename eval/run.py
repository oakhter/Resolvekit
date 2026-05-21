from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts.run_golden_eval import _load_schema, _read_jsonl, evaluate_stored_results, validate_golden_rows


def _outcome_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"clean": 0, "clean_with_caveats": 0, "corrected": 0, "abstained": 0, "hard_failure": 0}
    for row in results:
        outcome = str(row.get("validation_outcome") or row.get("outcome") or "").strip()
        if outcome in counts:
            counts[outcome] += 1
    return counts


def run_eval(golden: Path, output: Path, schema: Path, results: Path | None = None, config_path: Path | None = None) -> dict[str, Any]:
    rows = _read_jsonl(golden)
    schema_data = _load_schema(schema)
    report = validate_golden_rows(rows, schema_data)
    result_rows = _read_jsonl(results) if results and results.exists() else []
    if result_rows:
        report.update(evaluate_stored_results(rows, result_rows))
    else:
        report.update({
            "evaluated_result_count": 0,
            "retrieval_recall_at_3": None,
            "retrieval_recall_at_5": None,
            "mean_reciprocal_rank": None,
            "source_precision": None,
            "citation_precision": None,
            "fallback_rate": None,
            "p50_latency_ms": None,
            "p95_latency_ms": None,
            "total_cost_usd": None,
            "hard_failures": [],
            "hard_failure_count": 0,
        })
    report["config"] = str(config_path or "")
    report["validation_outcome_counts"] = _outcome_counts(result_rows)
    report["reviewer_ready_proxy"] = report.get("validation_pass_rate")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(report, sort_keys=True) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="ResolveKit alpha eval runner.")
    parser.add_argument("--config", type=Path, default=Path("configs/baseline.yaml"))
    parser.add_argument("--golden", type=Path, default=Path("eval/golden/resolvekit_v0_1.jsonl"))
    parser.add_argument("--schema", type=Path, default=Path("eval/golden_set/schema.json"))
    parser.add_argument("--results", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_eval(args.golden, args.output, args.schema, args.results, args.config)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report.get("schema_errors") or report.get("hard_failure_count") else 0


if __name__ == "__main__":
    raise SystemExit(main())
