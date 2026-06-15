from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from knowledge_loader.source_contract import source_validation_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run ResolveKit source contract validation.")
    parser.add_argument("paths", nargs="+", help="CSV or XLSX source file(s) to validate.")
    args = parser.parse_args()

    supported = {".csv", ".xlsx"}
    unsupported = [path for path in args.paths if Path(path).suffix.lower() not in supported]
    if unsupported:
        print("Public preview ingest supports CSV and XLSX.")
        print("Convert unsupported files using demo_data/onboarding/source_manifest_template.csv")
        for path in unsupported:
            print(f"- {path}")
        return 2

    report = source_validation_report(args.paths)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report.get("validation_errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
