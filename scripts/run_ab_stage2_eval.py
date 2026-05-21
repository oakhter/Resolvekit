from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.core import project_config
from knowledge_loader.connectors import ConnectorError, get_connector_for_path
from scripts.run_golden_eval import (
    DEFAULT_GOLDEN_SET,
    DEFAULT_SCHEMA,
    _load_schema,
    _read_jsonl,
    compare_to_baseline,
    evaluate_stored_results,
    validate_golden_rows,
)

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

CONFIG_DIR = ROOT / "configs" / "ab" / "02_kb_loading"
RUNS_DIR = ROOT / "experiments" / "runs"
REPORTS_DIR = ROOT / "experiments" / "reports"
DECISIONS_DIR = ROOT / "experiments" / "decisions"
SUPPORTED_FORMATS = {"csv", "xlsx", "pdf"}
STRICT_METADATA_FIELDS = {
    "source_id",
    "source_title",
    "source_type",
    "source_authority",
    "is_approved",
    "is_active",
    "is_customer_facing_allowed",
    "approved_at",
    "reviewed_by",
    "needs_review_at",
    "doc_type",
    "product_area",
    "issue_class",
    "version_scope",
    "escalation_risk",
    "body",
}


def _read_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if yaml:
        data = yaml.safe_load(text) or {}
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping")
    return data


def load_stage2_variants(config_dir: Path = CONFIG_DIR) -> list[dict[str, Any]]:
    configs = [_read_yaml(path) for path in sorted(config_dir.glob("v*.yaml"))]
    if len(configs) != 5:
        raise ValueError(f"Expected 5 Stage 2 configs in {config_dir}, found {len(configs)}")
    return configs


def config_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def source_index_version(paths: list[Path]) -> str:
    payload = []
    for path in sorted(paths):
        if path.exists():
            payload.append(f"{path.relative_to(ROOT)}:{path.stat().st_size}:{path.stat().st_mtime_ns}")
    return hashlib.sha256("\n".join(payload).encode("utf-8")).hexdigest()[:16]


def code_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return "unavailable"
    return result.stdout.strip() or "unavailable"


def discover_source_files() -> list[Path]:
    paths: list[Path] = []
    paths.extend(sorted((ROOT / "knowledge_loader" / "processed").glob("demo_*.csv")))
    for directory in (ROOT / "demo_data" / "csv", ROOT / "demo_data" / "xlsx", ROOT / "demo_data" / "pdf"):
        if directory.exists():
            paths.extend(
                path for path in sorted(directory.iterdir())
                if path.name != "pdf_manifest.csv" and path.suffix.lower().lstrip(".") in SUPPORTED_FORMATS
            )
    return sorted({path.resolve() for path in paths})


def _source_format(path: Path) -> str:
    return path.suffix.lower().lstrip(".")


def _raw_rows(path: Path) -> list[dict[str, str]]:
    if path.suffix.lower() != ".csv":
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def _strict_metadata_errors(path: Path) -> list[str]:
    errors = []
    for index, row in enumerate(_raw_rows(path), 2):
        missing = sorted(field for field in STRICT_METADATA_FIELDS if not str(row.get(field, "")).strip())
        if missing:
            errors.append(f"{path.name}:row {index} missing strict metadata: {', '.join(missing)}")
    return errors


def inspect_source(path: Path, *, strict_metadata: bool) -> dict[str, Any]:
    fmt = _source_format(path)
    result = {
        "path": str(path.relative_to(ROOT)),
        "format": fmt,
        "loaded_records": 0,
        "rejected_records": 0,
        "chunked_count": 0,
        "validation_errors": [],
        "warnings": [],
    }
    try:
        connector = get_connector_for_path(path)
        documents, preview = connector.parse(path, source_key=fmt, source_type=fmt, sample_limit=500)
        errors = _strict_metadata_errors(path) if strict_metadata else []
        if errors:
            result["rejected_records"] = max(len(errors), len(documents))
            result["validation_errors"].extend(errors)
            return result
        result["loaded_records"] = len(documents)
        result["chunked_count"] = sum(max(1, len(document.sections)) for document in documents if document.body.strip())
        result["warnings"].extend(preview.get("warnings") or [])
    except ConnectorError as exc:
        result["rejected_records"] = 1
        result["validation_errors"].append(f"{path.name}: {exc.code}: {exc}")
        result["warnings"].extend(exc.warnings)
    except Exception as exc:
        result["rejected_records"] = 1
        result["validation_errors"].append(f"{path.name}: {type(exc).__name__}: {exc}")
    return result


def inventory_for_variant(variant: dict[str, Any], source_files: list[Path]) -> dict[str, Any]:
    settings = variant.get("variant_settings") or {}
    allowed_formats = set(settings.get("source_format_filter") or SUPPORTED_FORMATS)
    strict_metadata = bool(settings.get("strict_metadata"))
    selected = [path for path in source_files if _source_format(path) in allowed_formats]
    inspected = [inspect_source(path, strict_metadata=strict_metadata) for path in selected]
    by_format: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "loaded_records": 0,
        "rejected_records": 0,
        "chunked_count": 0,
        "validation_errors": [],
        "warnings": [],
    })
    for item in inspected:
        bucket = by_format[item["format"]]
        bucket["loaded_records"] += item["loaded_records"]
        bucket["rejected_records"] += item["rejected_records"]
        bucket["chunked_count"] += item["chunked_count"]
        bucket["validation_errors"].extend(item["validation_errors"])
        bucket["warnings"].extend(item["warnings"])
    return {
        "selected_source_files": [str(path.relative_to(ROOT)) for path in selected],
        "source_counts_by_format": dict(sorted(by_format.items())),
        "total_loaded_records": sum(item["loaded_records"] for item in inspected),
        "total_rejected_records": sum(item["rejected_records"] for item in inspected),
        "total_chunked_count": sum(item["chunked_count"] for item in inspected),
        "source_validation_errors_by_format": {
            fmt: values["validation_errors"]
            for fmt, values in sorted(by_format.items())
            if values["validation_errors"]
        },
        "source_warnings_by_format": {
            fmt: values["warnings"]
            for fmt, values in sorted(by_format.items())
            if values["warnings"]
        },
    }


def _parse_result_files(items: list[str]) -> dict[str, Path]:
    parsed = {}
    for item in items:
        if "=" not in item:
            raise ValueError("--result-file values must be variant_id=path")
        variant_id, path = item.split("=", 1)
        parsed[variant_id.strip()] = Path(path)
    return parsed


def metrics_for_variant(
    variant_id: str,
    *,
    result_files: dict[str, Path],
    golden_rows: list[dict[str, Any]],
    schema: dict[str, Any],
) -> dict[str, Any]:
    report = validate_golden_rows(golden_rows, schema)
    result_file = result_files.get(variant_id)
    if result_file:
        report.update(evaluate_stored_results(golden_rows, _read_jsonl(result_file)))
    else:
        report.update({
            "evaluated_result_count": 0,
            "source_precision": None,
            "citation_precision": None,
            "retrieval_recall_at_5": None,
            "fallback_rate": None,
            "hard_failure_count": 0,
        })
    return report


def _metric(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return str(round(value, 4)).rstrip("0").rstrip(".")
    return str(value)


def markdown_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Stage 2 KB Loading A/B Report",
        "",
        f"Run ID: `{summary['run_id']}`",
        f"Source index version: `{summary['source_index_version']}`",
        f"Code commit: `{summary['code_commit']}`",
        "",
        "| Variant | Loaded | Rejected | Chunks | Source precision | Citation precision | Recall@5 | Fallback | Validation errors |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for variant_id, result in summary["variants"].items():
        inventory = result["inventory"]
        metrics = result["metrics"]
        error_count = sum(len(v) for v in inventory["source_validation_errors_by_format"].values())
        lines.append(
            f"| `{variant_id}` | {inventory['total_loaded_records']} | {inventory['total_rejected_records']} | "
            f"{inventory['total_chunked_count']} | {_metric(metrics.get('source_precision'))} | "
            f"{_metric(metrics.get('citation_precision'))} | {_metric(metrics.get('retrieval_recall_at_5'))} | "
            f"{_metric(metrics.get('fallback_rate'))} | {error_count} |"
        )
    lines.extend(["", "## Example Successes And Failures", ""])
    for variant_id, result in summary["variants"].items():
        inventory = result["inventory"]
        errors = [
            error
            for values in inventory["source_validation_errors_by_format"].values()
            for error in values
        ][:3]
        lines.append(f"- `{variant_id}`: files={len(inventory['selected_source_files'])}, loaded={inventory['total_loaded_records']}, rejected={inventory['total_rejected_records']}")
        for error in errors:
            lines.append(f"- `{variant_id}` failure: {error}")
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    variants = load_stage2_variants(args.config_dir)
    source_files = discover_source_files()
    golden_rows = _read_jsonl(args.golden_set)
    schema = _load_schema(args.schema)
    result_files = _parse_result_files(args.result_file or [])
    run_id = datetime.now(timezone.utc).strftime("stage2_kb_loading_%Y%m%dT%H%M%SZ")
    output_dir = args.output_dir or RUNS_DIR / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    DECISIONS_DIR.mkdir(parents=True, exist_ok=True)

    summary = {
        "run_id": run_id,
        "stage": "kb_loading",
        "source_index_version": source_index_version(source_files),
        "code_commit": code_commit(),
        "source_files": [str(path.relative_to(ROOT)) for path in source_files],
        "variants": {},
    }
    baseline_metrics = None
    for variant in variants:
        variant_id = str(variant["variant_id"])
        config_path = args.config_dir / f"{variant_id.split('_', 3)[-1]}.yaml"
        metrics = metrics_for_variant(variant_id, result_files=result_files, golden_rows=golden_rows, schema=schema)
        if baseline_metrics is None:
            baseline_metrics = metrics
        summary["variants"][variant_id] = {
            "name": variant.get("name"),
            "config_hash": config_hash(config_path) if config_path.exists() else variant.get("config_hash", ""),
            "changed_lever": variant.get("changed_lever"),
            "expected_effect": variant.get("expected_effect"),
            "primary_metric": variant.get("primary_metric"),
            "guardrails": variant.get("guardrails", {}),
            "variant_settings": variant.get("variant_settings", {}),
            "inventory": inventory_for_variant(variant, source_files),
            "metrics": metrics,
            "diff_vs_first_variant": compare_to_baseline(metrics, baseline_metrics or metrics),
        }

    text = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    report = markdown_report(summary)
    (output_dir / "summary.json").write_text(text, encoding="utf-8")
    (output_dir / "summary.md").write_text(report, encoding="utf-8")
    (REPORTS_DIR / "stage2_kb_loading_latest.md").write_text(report, encoding="utf-8")
    (REPORTS_DIR / "stage2_kb_loading_latest.json").write_text(text, encoding="utf-8")
    print(report, end="")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run offline Stage 2 KB loading A/B inventory and optional golden metrics.")
    parser.add_argument("--config-dir", type=Path, default=CONFIG_DIR)
    parser.add_argument("--golden-set", type=Path, default=DEFAULT_GOLDEN_SET)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--result-file", action="append", default=[], help="variant_id=path to stored golden result JSONL")
    parser.add_argument("--output-dir", type=Path)
    run(parser.parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
