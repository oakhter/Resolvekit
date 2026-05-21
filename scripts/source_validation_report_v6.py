from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from knowledge_loader.source_contract import source_validation_report


def default_paths() -> list[Path]:
    paths = [ROOT / "demo_data" / "csv" / "resolvekit_demo_kb.csv", ROOT / "demo_data" / "xlsx" / "resolvekit_demo_kb.xlsx"]
    pdf_dir = ROOT / "demo_data" / "pdf"
    paths.extend(sorted(path for path in pdf_dir.glob("*.pdf") if path.name != "pdf_manifest.csv"))
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate ResolveKit v6 source validation report.")
    parser.add_argument("--output", type=Path, default=ROOT / "experiments" / "reports" / "source_validation_latest.json")
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args()
    report = source_validation_report(args.paths or default_paths())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report["validation_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
