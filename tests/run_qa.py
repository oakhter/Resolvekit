#!/usr/bin/env python3
"""
ResolveKit QA Runner

Runs the pytest suite against the local API server (http://127.0.0.1:8000).
The API server is started automatically by start.py as a background process.

Usage:
  python tests/run_qa.py                   # API smoke suite (localhost)
  python tests/run_qa.py --url http://...  # override base URL
  python tests/run_qa.py -v                # verbose
  python tests/run_qa.py -k TestHealth     # filter tests
"""
import sys
import subprocess
import os
import argparse
import httpx
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _check_server(url: str) -> bool:
    try:
        r = httpx.get(f"{url}/health", timeout=4.0)
        return r.status_code == 200
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="ResolveKit QA")
    parser.add_argument("--url", help="Override base URL (default: http://127.0.0.1:8000)")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-k", help="Run only tests matching expression (pytest -k)")
    args = parser.parse_args()

    target = (args.url or "http://127.0.0.1:8000").rstrip("/")
    print(f"Target: {target}")

    if not _check_server(target):
        print(f"\n  Server not reachable at {target}")
        print("  Start the app first: run start.py via VS Code (F5) or python start.py")
        sys.exit(1)

    os.environ["QA_BASE_URL"] = target

    test_file = "test_resolvekit.py"
    suite_label = "API Smoke QA"

    results_dir = PROJECT_ROOT / "tests" / "results"
    results_dir.mkdir(exist_ok=True)

    print("\n" + "=" * 60)
    print(f"  ResolveKit — {suite_label}")
    print(f"  Target: {target}")
    print("=" * 60 + "\n")

    test_filter = args.k or "TestHealth or TestAuth or TestResolve or TestFeedback or TestEndToEnd"

    pytest_args = [
        sys.executable, "-m", "pytest",
        str(PROJECT_ROOT / "tests" / test_file),
        "-k",
        test_filter,
        "--tb=short",
        "-p", "no:warnings",
        f"--rootdir={PROJECT_ROOT}",
        f"--junitxml={results_dir / 'last_run.xml'}",
    ]

    if args.verbose:
        pytest_args.append("-v")
    else:
        pytest_args.append("-q")

    result = subprocess.run(pytest_args, cwd=str(PROJECT_ROOT))
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
