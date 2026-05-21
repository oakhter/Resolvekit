from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.core.replay import replay_saved_trace
from pipeline.cache import get_run_trace


def _load_trace(args: argparse.Namespace) -> dict:
    if args.trace_file:
        return json.loads(args.trace_file.read_text())
    trace = get_run_trace(args.trace_id)
    if not trace:
        raise ValueError(f"Trace not found: {args.trace_id}")
    return trace


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay a saved redacted RunTrace deterministically.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--trace-id")
    source.add_argument("--trace-file", type=Path)
    parser.add_argument("--same-config-hash", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = replay_saved_trace(_load_trace(args), use_current_config=not args.same_config_hash)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    print(text, end="")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
