from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKED_DOCS = [
    ROOT / "docs" / "README.md",
    ROOT / "docs" / "TECHNICAL.md",
    ROOT / "docs" / "DEMO.md",
]
FORBIDDEN = [
    re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I),
    re.compile(r"\b(?:sk|pk|rk|api|key|token|secret)[_-]?[A-Za-z0-9_-]{12,}\b", re.I),
    re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
]


def validate_assets() -> dict:
    missing = [str(path.relative_to(ROOT)) for path in CHECKED_DOCS if not path.exists()]
    unsafe = []
    for path in CHECKED_DOCS:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(pattern.search(text) for pattern in FORBIDDEN):
            unsafe.append(str(path.relative_to(ROOT)))
    return {
        "checked": [str(path.relative_to(ROOT)) for path in CHECKED_DOCS],
        "missing": missing,
        "unsafe": unsafe,
        "ok": not missing and not unsafe,
        "process": [
            "Keep public docs compact.",
            "Run this script to verify compact docs exist and contain no obvious private data.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate refreshable public-demo screenshot assets.")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = validate_assets()
    for line in report["process"]:
        print(line)
    print(f"checked: {', '.join(report['checked'])}")
    if report["missing"]:
        print(f"missing: {', '.join(report['missing'])}")
    if report["unsafe"]:
        print(f"unsafe: {', '.join(report['unsafe'])}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
