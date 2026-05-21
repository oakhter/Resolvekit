from __future__ import annotations

from datetime import date, timedelta
import json

from pipeline.cache import get_conn


def aggregate_day(metric_date: date) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*),
                    SUM(CASE WHEN agent_action = 'sent_as_is' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN agent_action = 'edited' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN agent_action = 'rejected' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN agent_action = 'pending' OR agent_action IS NULL THEN 1 ELSE 0 END),
                    AVG(edit_distance_ratio),
                    SUM(CASE WHEN confidence IN ('HIGH', 'MEDIUM') THEN 1 ELSE 0 END),
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY response_time_ms),
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY response_time_ms)
                FROM feedback
                WHERE created_at::date = %s
                """,
                (metric_date,),
            )
            row = cur.fetchone()
            total = int(row[0] or 0)
            sent = int(row[1] or 0)
            edited = int(row[2] or 0)
            rejected = int(row[3] or 0)
            pending = int(row[4] or 0)
            coverage = int(row[6] or 0)

            cur.execute(
                """
                SELECT COALESCE(confidence, ''), COALESCE(agent_action, 'pending'), COUNT(*)
                FROM feedback
                WHERE created_at::date = %s
                GROUP BY confidence, agent_action
                """,
                (metric_date,),
            )
            breakdown: dict[str, dict[str, int]] = {}
            for confidence, action, count in cur.fetchall():
                bucket = confidence or "unknown"
                breakdown.setdefault(bucket, {})[action or "pending"] = int(count)

            cur.execute(
                """
                SELECT AVG(cost_usd)
                FROM api_calls
                WHERE created_at::date = %s
                """,
                (metric_date,),
            )
            cost_row = cur.fetchone()

            snapshot = {
                "metric_date": metric_date,
                "total_feedback": total,
                "sent_as_is_count": sent,
                "edited_count": edited,
                "rejected_count": rejected,
                "pending_count": pending,
                "send_as_is_rate": round(sent / total, 4) if total else 0,
                "reject_rate": round(rejected / total, 4) if total else 0,
                "mean_edit_distance": round(float(row[5] or 0), 4),
                "coverage_rate": round(coverage / total, 4) if total else 0,
                "latency_p50_ms": round(float(row[7] or 0), 2),
                "latency_p95_ms": round(float(row[8] or 0), 2),
                "avg_cost_usd": float(cost_row[0] or 0),
                "confidence_action_breakdown": breakdown,
            }
            cur.execute(
                """
                INSERT INTO metrics_daily (
                    metric_date, total_feedback, sent_as_is_count, edited_count,
                    rejected_count, pending_count, send_as_is_rate, reject_rate,
                    mean_edit_distance, coverage_rate, latency_p50_ms, latency_p95_ms,
                    avg_cost_usd, confidence_action_breakdown, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (metric_date) DO UPDATE SET
                    total_feedback = EXCLUDED.total_feedback,
                    sent_as_is_count = EXCLUDED.sent_as_is_count,
                    edited_count = EXCLUDED.edited_count,
                    rejected_count = EXCLUDED.rejected_count,
                    pending_count = EXCLUDED.pending_count,
                    send_as_is_rate = EXCLUDED.send_as_is_rate,
                    reject_rate = EXCLUDED.reject_rate,
                    mean_edit_distance = EXCLUDED.mean_edit_distance,
                    coverage_rate = EXCLUDED.coverage_rate,
                    latency_p50_ms = EXCLUDED.latency_p50_ms,
                    latency_p95_ms = EXCLUDED.latency_p95_ms,
                    avg_cost_usd = EXCLUDED.avg_cost_usd,
                    confidence_action_breakdown = EXCLUDED.confidence_action_breakdown,
                    updated_at = NOW()
                """,
                (
                    snapshot["metric_date"], snapshot["total_feedback"], snapshot["sent_as_is_count"],
                    snapshot["edited_count"], snapshot["rejected_count"], snapshot["pending_count"],
                    snapshot["send_as_is_rate"], snapshot["reject_rate"], snapshot["mean_edit_distance"],
                    snapshot["coverage_rate"], snapshot["latency_p50_ms"], snapshot["latency_p95_ms"],
                    snapshot["avg_cost_usd"], json.dumps(snapshot["confidence_action_breakdown"]),
                ),
            )
        conn.commit()
    return snapshot


def main() -> int:
    snapshot = aggregate_day(date.today() - timedelta(days=1))
    print(json.dumps({**snapshot, "metric_date": snapshot["metric_date"].isoformat()}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
