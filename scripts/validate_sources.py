from __future__ import annotations

import argparse
import json
from pathlib import Path

from knowledge_loader.source_contract import source_validation_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run ResolveKit source contract validation.")
    parser.add_argument("paths", nargs="+", help="CSV source file(s) to validate.")
    args = parser.parse_args()

    non_csv = [path for path in args.paths if Path(path).suffix.lower() != ".csv"]
    if non_csv:
        print("Public preview ingest supports CSV only.")
        print("Convert to CSV using demo_data/onboarding/source_manifest_template.csv")
        for path in non_csv:
            print(f"- {path}")
        return 2

    report = source_validation_report(args.paths)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report.get("validation_errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
