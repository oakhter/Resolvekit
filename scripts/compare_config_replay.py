from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.run_golden_eval import _load_schema, _read_jsonl, compare_to_baseline, evaluate_stored_results, validate_golden_rows


def run_report(golden_set: Path, schema_path: Path, results_path: Path | None) -> dict[str, Any]:
    rows = _read_jsonl(golden_set)
    report = validate_golden_rows(rows, _load_schema(schema_path))
    if results_path:
        report.update(evaluate_stored_results(rows, _read_jsonl(results_path)))
    else:
        report.update({
            "evaluated_result_count": 0,
            "route_accuracy": None,
            "confidence_band_accuracy": None,
            "abstention_accuracy": None,
            "retrieval_recall": None,
            "source_precision": None,
            "avg_latency_ms": None,
            "total_cost_usd": None,
            "hard_failures": [],
            "hard_failure_count": 0,
        })
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare config A vs config B on stored golden-set result files.")
    parser.add_argument("--schema", type=Path, default=ROOT / "eval" / "golden_set" / "schema.json")
    parser.add_argument("--golden-set", type=Path, default=ROOT / "eval" / "golden_set" / "v3_1_starter.jsonl")
    parser.add_argument("--results-a", type=Path, required=True)
    parser.add_argument("--results-b", type=Path, required=True)
    parser.add_argument("--config-a", default="config-a")
    parser.add_argument("--config-b", default="config-b")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report_a = run_report(args.golden_set, args.schema, args.results_a)
    report_b = run_report(args.golden_set, args.schema, args.results_b)
    report = {
        "config_a": args.config_a,
        "config_b": args.config_b,
        "report_a": report_a,
        "report_b": report_b,
        "diff": compare_to_baseline(report_b, report_a),
        "hard_failures_delta": report_b.get("hard_failure_count", 0) - report_a.get("hard_failure_count", 0),
    }
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    print(text, end="")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    return 1 if report_b.get("hard_failure_count", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
