from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psycopg2

from backend.core import config
from backend.core.run_trace import redact_text
from backend.db.schema import _safe_schema_name
from pipeline.cache import create_review_queue_item


def build_report(days: int = 14) -> dict:
    with psycopg2.connect(config.DATABASE_URL) as conn:
        schema = _safe_schema_name(config.KNOWLEDGE_SCHEMA)
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}", public;')
            cur.execute(
                """
                SELECT source_id, source_type, source_ref, title, expires_at, needs_review_at,
                       COUNT(*) AS chunk_count
                FROM knowledge_base_identifier
                WHERE COALESCE(disabled, FALSE) = FALSE
                  AND (
                    NULLIF(expires_at, '')::timestamp <= NOW() + (%s * INTERVAL '1 day')
                    OR NULLIF(needs_review_at, '')::timestamp <= NOW() + (%s * INTERVAL '1 day')
                  )
                GROUP BY source_id, source_type, source_ref, title, expires_at, needs_review_at
                ORDER BY COALESCE(NULLIF(expires_at, '')::timestamp, NULLIF(needs_review_at, '')::timestamp) ASC
                """,
                (days, days),
            )
            rows = cur.fetchall()

    items = []
    now = datetime.now(timezone.utc)
    for row in rows:
        expires_at = row[4] or ""
        needs_review_at = row[5] or ""
        marker = expires_at or needs_review_at
        stale = _is_past(marker, now)
        items.append({
            "source_id": row[0] or "",
            "source_type": row[1] or "",
            "source_ref": row[2] or "",
            "title": redact_text(row[3] or "", 160),
            "expires_at": str(expires_at),
            "needs_review_at": str(needs_review_at),
            "chunk_count": int(row[6] or 0),
            "status": "stale" if stale else "near_review",
            "warning": "High-citation source is stale or near review." if int(row[6] or 0) >= 3 else "",
        })
    return {
        "generated_at": now.isoformat(),
        "near_review_days": days,
        "stale_count": sum(1 for item in items if item["status"] == "stale"),
        "near_review_count": sum(1 for item in items if item["status"] == "near_review"),
        "items": items,
    }


def queue_high_impact(report: dict, min_chunks: int = 3) -> int:
    queued = 0
    for item in report.get("items", []):
        if item.get("status") != "stale" or int(item.get("chunk_count") or 0) < min_chunks:
            continue
        create_review_queue_item({
            "ticket_preview": f"Stale source review: {item.get('title') or item.get('source_id')}",
            "confidence": "LOW",
            "confidence_band": "red",
            "severity": "high",
            "sla_marker": "source_re_review",
            "gatekeeper_reason": "stale high-impact source",
            "source_issue_type": "stale_source",
            "auditor_flags": item,
            "needs_escalation": False,
            "route": "source_freshness",
            "status": "open",
        })
        queued += 1
    return queued


def _is_past(value: str, now: datetime) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed < now
    except ValueError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Report stale and near-review sources.")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--queue-review", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = build_report(args.days)
    if args.queue_review:
        report["queued_review_items"] = queue_high_impact(report)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    print(text, end="")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
