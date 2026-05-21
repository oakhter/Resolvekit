from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.cache import get_conn


def export_audit(limit: int = 200) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT trace_id, created_at, confidence_band, severity, source_issue_type, status FROM human_review_queue ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            review_queue = [
                {
                    "trace_id": row[0],
                    "created_at": str(row[1]),
                    "confidence_band": row[2],
                    "severity": row[3],
                    "source_issue_type": row[4],
                    "status": row[5],
                }
                for row in cur.fetchall()
            ]
            cur.execute(
                "SELECT trace_id, created_at, config_hash, model_provider, workflow_mode, product, platform, role FROM run_trace ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            traces = [
                {
                    "trace_id": row[0],
                    "created_at": str(row[1]),
                    "config_hash": row[2],
                    "model_provider": row[3],
                    "workflow_mode": row[4],
                    "product": row[5],
                    "platform": row[6],
                    "role": row[7],
                }
                for row in cur.fetchall()
            ]
    return {"review_queue": review_queue, "traces": traces}


def main() -> int:
    parser = argparse.ArgumentParser(description="Export redacted admin audit metadata.")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = export_audit(args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
