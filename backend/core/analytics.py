from __future__ import annotations

from collections import Counter
from typing import Any


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _money(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _rate(count: int, total: int) -> float:
    return round(count / total, 4) if total else 0.0


def _top(counter: Counter, limit: int = 10) -> list[dict]:
    return [{"name": str(name), "count": int(count)} for name, count in counter.most_common(limit)]


def _trace_cost(trace: dict) -> float:
    usage = _as_dict(trace.get("token_usage_by_stage"))
    if "cost_usd" in usage:
        return _money(usage.get("cost_usd"))
    total = 0.0
    for stage in usage.values():
        if isinstance(stage, dict):
            total += _money(stage.get("cost_usd"))
    final = _as_dict(trace.get("final_response"))
    summary = _as_dict(final.get("usage_summary"))
    total += _money(summary.get("total_cost_usd"))
    return total


def build_support_intelligence_report(
    *,
    traces: list[dict],
    feedback: list[dict],
    knowledge_issues: list[dict],
    review_items: list[dict],
    api_calls: list[dict],
    events: list[dict],
    days: int,
) -> dict:
    user_ids = {row.get("user_id") for row in traces if row.get("user_id")}
    team_ids = {row.get("team_id") for row in traces if row.get("team_id")}
    products = Counter(row.get("product") or "unknown" for row in traces)
    roles = Counter(row.get("role") or "unknown" for row in traces)
    teams = Counter(row.get("team_id") or "unknown" for row in traces)

    no_answer_count = 0
    low_confidence_count = 0
    review_required_count = 0
    retrieved_sources: Counter = Counter()
    top_scores = []
    trace_cost_total = 0.0
    for row in traces:
        trace = _as_dict(row.get("trace"))
        final = _as_dict(trace.get("final_response"))
        chunks = _as_list(trace.get("reranked_results"))
        trace_cost_total += _trace_cost(trace)
        if not chunks or final.get("draft_unavailable_reason"):
            no_answer_count += 1
        if str(final.get("confidence", "")).upper() in {"LOW", "RED"}:
            low_confidence_count += 1
        validation = _as_dict(final.get("validation")) or _as_dict(trace.get("validation_output"))
        if validation.get("review_required"):
            review_required_count += 1
        for chunk in chunks:
            source_id = chunk.get("source_id") or chunk.get("id") or "unknown"
            retrieved_sources[source_id] += 1
            top_scores.append(_money(chunk.get("score") or chunk.get("rerank_score")))

    ratings = Counter(row.get("rating") or "no_rating" for row in feedback)
    reasons = Counter(row.get("feedback_reason") or row.get("reason_code") or "unspecified" for row in feedback)
    actions = Counter(row.get("agent_action") or "pending" for row in feedback)
    negative = sum(1 for row in feedback if row.get("rating") == "thumbs_down")
    helpful = sum(1 for row in feedback if row.get("rating") == "thumbs_up")
    issue_types = Counter(row.get("issue_type") or "unknown" for row in knowledge_issues)
    open_issues = sum(1 for row in knowledge_issues if row.get("status", "open") == "open")
    escalation_count = sum(1 for row in review_items if row.get("needs_escalation"))
    source_issue_types = Counter(row.get("source_issue_type") or "unspecified" for row in review_items)
    api_cost_total = sum(_money(row.get("cost_usd")) for row in api_calls)
    latencies = sorted(_money(row.get("latency_ms")) for row in api_calls if row.get("latency_ms") is not None)
    event_types = Counter(row.get("event_type") or "unknown" for row in events)

    total_queries = len(traces)
    total_feedback = len(feedback)
    total_cost = round(trace_cost_total if trace_cost_total else api_cost_total, 6)
    p95_latency = latencies[int(0.95 * (len(latencies) - 1))] if latencies else 0

    return {
        "period_days": days,
        "usage": {
            "total_queries": total_queries,
            "active_users": len(user_ids),
            "active_teams": len(team_ids),
            "top_products": _top(products),
            "top_roles": _top(roles),
            "top_teams": _top(teams),
            "event_counts": _top(event_types),
        },
        "retrieval": {
            "no_answer_count": no_answer_count,
            "no_answer_rate": _rate(no_answer_count, total_queries),
            "low_confidence_count": low_confidence_count,
            "low_confidence_rate": _rate(low_confidence_count, total_queries),
            "average_top_score": round(sum(top_scores) / len(top_scores), 4) if top_scores else 0.0,
            "most_retrieved_sources": _top(retrieved_sources),
        },
        "evaluation": {
            "total_feedback": total_feedback,
            "helpful_rate": _rate(helpful, total_feedback),
            "negative_feedback_count": negative,
            "review_required_count": review_required_count,
            "ratings": dict(ratings),
            "agent_actions": dict(actions),
            "feedback_reasons": _top(reasons),
        },
        "knowledge_gaps": {
            "open_issue_count": open_issues,
            "issue_types": _top(issue_types),
            "missing_source_feedback_count": reasons.get("missing_source", 0),
            "wrong_source_feedback_count": reasons.get("wrong_source", 0),
            "stale_source_feedback_count": reasons.get("stale_source", 0),
        },
        "escalations": {
            "needs_escalation_count": escalation_count,
            "review_queue_count": len(review_items),
            "source_issue_types": _top(source_issue_types),
        },
        "costs": {
            "total_cost_usd": total_cost,
            "trace_cost_usd": round(trace_cost_total, 6),
            "api_call_cost_usd": round(api_cost_total, 6),
            "avg_cost_per_query_usd": round(total_cost / total_queries, 6) if total_queries else 0.0,
            "api_call_count": len(api_calls),
            "p95_latency_ms": p95_latency,
        },
    }


def render_support_intelligence_markdown(report: dict) -> str:
    usage = report["usage"]
    retrieval = report["retrieval"]
    evaluation = report["evaluation"]
    gaps = report["knowledge_gaps"]
    escalations = report["escalations"]
    costs = report["costs"]
    return "\n".join([
        "# ResolveKit Usage & Knowledge Gap Report",
        "",
        f"- Period: last {report['period_days']} days",
        f"- Total queries: {usage['total_queries']}",
        f"- Active users: {usage['active_users']}",
        f"- Active teams: {usage['active_teams']}",
        f"- No-answer rate: {retrieval['no_answer_rate']}",
        f"- Low-confidence rate: {retrieval['low_confidence_rate']}",
        f"- Helpful rate: {evaluation['helpful_rate']}",
        f"- Review-required count: {evaluation['review_required_count']}",
        f"- Open knowledge issues: {gaps['open_issue_count']}",
        f"- Escalation signals: {escalations['needs_escalation_count']}",
        f"- Total cost USD: {costs['total_cost_usd']}",
        f"- Average cost/query USD: {costs['avg_cost_per_query_usd']}",
    ])
