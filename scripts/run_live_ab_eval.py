from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.generate_golden_results import _result_from_resolution
from scripts.run_golden_eval import (
    DEFAULT_GOLDEN_SET,
    DEFAULT_SCHEMA,
    _load_schema,
    _read_jsonl,
    compare_to_baseline,
    evaluate_stored_results,
    validate_golden_rows,
)

DEFAULT_ARMS = ("current_hybrid_rag", "current_rag_query_decomposition")


def build_payload(case: dict[str, Any], arm: str) -> dict[str, Any]:
    return {
        "ticket": case["ticket_text"],
        "mode": "suggest",
        "product": case.get("product", ""),
        "access_channel": case.get("platform", ""),
        "permission_level": case.get("role", ""),
        "experiment_arm": arm,
    }


def _run_case(
    client: httpx.Client,
    *,
    base_url: str,
    api_key: str,
    case: dict[str, Any],
    arm: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    response = client.post(
        f"{base_url.rstrip('/')}/resolve",
        headers={"x-api-key": api_key},
        json=build_payload(case, arm),
        timeout=180,
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    response.raise_for_status()
    resolution = response.json().get("resolution", {})
    result = _result_from_resolution(case, resolution, latency_ms)
    result["experiment_arm"] = arm
    result["retrieval_strategy"] = (resolution.get("retrieval_signals") or {}).get("retrieval_strategy", {})
    return result


def _report_for_results(rows: list[dict[str, Any]], schema: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    report = validate_golden_rows(rows, schema)
    report.update(evaluate_stored_results(rows, results))
    return report


def _markdown_table(summary: dict[str, Any]) -> str:
    arms = summary["arms"]
    reports = summary["reports"]
    baseline = arms[0]
    lines = [
        "| Metric | " + " | ".join(arms) + " | Delta vs baseline |",
        "| --- | " + " | ".join(["---:"] * len(arms)) + " | ---: |",
    ]
    metrics = [
        ("Evaluated cases", "evaluated_result_count"),
        ("Hard failures", "hard_failure_count"),
        ("Recall@1", "retrieval_recall_at_1"),
        ("Recall@3", "retrieval_recall_at_3"),
        ("Recall@5", "retrieval_recall_at_5"),
        ("MRR", "mean_reciprocal_rank"),
        ("Source precision", "source_precision"),
        ("Citation recall", "citation_recall"),
        ("Citation precision", "citation_precision"),
        ("Confidence accuracy", "confidence_band_accuracy"),
        ("Abstention accuracy", "abstention_accuracy"),
        ("Fallback rate", "fallback_rate"),
        ("P50 latency ms", "p50_latency_ms"),
        ("P95 latency ms", "p95_latency_ms"),
        ("Avg cost USD", "avg_cost_usd"),
        ("Total cost USD", "total_cost_usd"),
    ]
    for label, key in metrics:
        values = [reports[arm].get(key) for arm in arms]
        base = reports[baseline].get(key)
        challenger = values[-1]
        delta = ""
        if base is not None and challenger is not None:
            delta = _format_value(round(float(challenger) - float(base), 6))
        lines.append(
            "| "
            + label
            + " | "
            + " | ".join(_format_value(value) for value in values)
            + " | "
            + delta
            + " |"
        )
    return "\n".join(lines) + "\n"


def _format_value(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return str(round(value, 6)).rstrip("0").rstrip(".")
    return str(value)


def run_live_ab_eval(args: argparse.Namespace) -> dict[str, Any]:
    load_dotenv(ROOT / ".env")
    api_key = args.api_key or os.getenv("API_KEY", "")
    if not api_key:
        raise SystemExit("API_KEY is required. Set API_KEY or pass --api-key.")

    rows = _read_jsonl(args.golden_set)
    if args.limit:
        rows = rows[:args.limit]
    schema = _load_schema(args.schema)
    arms = [arm.strip() for arm in args.arms.split(",") if arm.strip()]
    if len(arms) < 2:
        raise SystemExit("At least two comma-separated arms are required.")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_dir or ROOT / "eval" / "ab" / f"live_ab_{run_id}"
    output_dir.mkdir(parents=True, exist_ok=True)

    reports: dict[str, dict[str, Any]] = {}
    result_files: dict[str, str] = {}
    with httpx.Client() as client:
        for arm in arms:
            results = []
            result_file = output_dir / f"{arm}.jsonl"
            with result_file.open("w", encoding="utf-8") as handle:
                for index, case in enumerate(rows, 1):
                    result = _run_case(client, base_url=args.base_url, api_key=api_key, case=case, arm=arm)
                    results.append(result)
                    handle.write(json.dumps(result, sort_keys=True) + "\n")
                    handle.flush()
                    print(f"{arm} {index}/{len(rows)} {case['ticket_id']} {result['confidence_band']} {result['route']}")
                    if args.delay_seconds > 0:
                        time.sleep(args.delay_seconds)
            reports[arm] = _report_for_results(rows, schema, results)
            result_files[arm] = str(result_file.relative_to(ROOT))

    summary = {
        "run_id": run_id,
        "base_url": args.base_url,
        "arms": arms,
        "result_files": result_files,
        "reports": reports,
        "diff": compare_to_baseline(reports[arms[-1]], reports[arms[0]]),
    }
    summary["markdown"] = _markdown_table(summary)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "summary.md").write_text(summary["markdown"], encoding="utf-8")
    print("\n" + summary["markdown"], end="")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run live ResolveKit golden-set A/B evals across retrieval arms.")
    parser.add_argument("--golden-set", type=Path, default=DEFAULT_GOLDEN_SET)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--base-url", default=os.getenv("BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--api-key", default="")
    parser.add_argument("--arms", default=",".join(DEFAULT_ARMS))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--delay-seconds", type=float, default=2.2)
    parser.add_argument("--output-dir", type=Path)
    run_live_ab_eval(parser.parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
