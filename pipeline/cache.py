import psycopg2
import hashlib
import json
import re
import uuid

# Canonical low-level cache: response-cache and retrieval-cache DB helpers.
from backend.core import config
from backend.core import project_config
from backend.core.logger import get_logger
from backend.db.schema import _safe_schema_name

logger = get_logger(__name__)


def get_conn():
    conn = psycopg2.connect(config.DATABASE_URL)
    schema = _safe_schema_name(config.OPS_SCHEMA)
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema}", public;')
    return conn


def hash_key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def token_edit_distance(original: str, final: str) -> dict:
    left = re.findall(r"\S+", original or "")
    right = re.findall(r"\S+", final or "")
    previous = list(range(len(right) + 1))
    for i, left_token in enumerate(left, 1):
        current = [i]
        for j, right_token in enumerate(right, 1):
            current.append(min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (left_token != right_token),
            ))
        previous = current
    distance = previous[-1] if previous else len(right)
    base = max(len(left), 1)
    return {
        "edit_distance_tokens": distance,
        "edit_distance_ratio": round(distance / base, 4),
    }


def _json_text(value, default):
    if value in (None, ""):
        return json.dumps(default)
    if isinstance(value, str):
        stripped = value.strip()
        try:
            json.loads(stripped)
        except json.JSONDecodeError:
            return json.dumps([value])
        return stripped
    return json.dumps(value)


# ─────────────────────────────────────────
# RESPONSE CACHE (LLM OUTPUT)
# ─────────────────────────────────────────
def get_cached_response(key: str):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT response FROM response_cache WHERE key = %s",
                    (key,)
                )
                row = cur.fetchone()

        if row:
            logger.info("⚡ Response cache hit")
            return row[0] if isinstance(row[0], dict) else json.loads(row[0])

        return None

    except Exception as e:
        logger.error(f"Cache read failed: {e}")
        return None


def save_cached_response(key: str, response: dict, provider: str = ""):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO response_cache (key, response, provider)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (key) DO NOTHING
                    """,
                    (key, json.dumps(response), provider)
                )

    except Exception as e:
        logger.error(f"Cache write failed: {e}")


# ─────────────────────────────────────────
# RETRIEVAL CACHE (TOP CHUNKS)
# ─────────────────────────────────────────
def get_cached_chunks(key: str):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT chunks FROM retrieval_cache WHERE key = %s",
                    (key,)
                )
                row = cur.fetchone()

        if row:
            logger.info("⚡ Retrieval cache hit")
            return row[0] if isinstance(row[0], list) else json.loads(row[0])

        return None

    except Exception as e:
        logger.error(f"Retrieval cache read failed: {e}")
        return None


def save_cached_chunks(key: str, chunks: list, query_text: str = "", chunk_count: int = 0):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO retrieval_cache (key, chunks, query_text, chunk_count)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (key) DO NOTHING
                    """,
                    (key, json.dumps(chunks), query_text, chunk_count or len(chunks))
                )

    except Exception as e:
        logger.error(f"Retrieval cache write failed: {e}")


# ─────────────────────────────────────────
# FEEDBACK
# ─────────────────────────────────────────
def save_feedback(data: dict) -> str:
    try:
        final_sent_text = data.get("final_sent_text") or data.get("edited_email") or data.get("original_email", "")
        edit_metrics = token_edit_distance(data.get("original_email", ""), final_sent_text)
        feedback_id = ""
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO feedback (
                        user_token_hash, user_id, team_id, session_id, cache_key, ticket_preview, confidence,
                        rating, email_was_edited, original_email, edited_email,
                        response_time_ms, from_cache, product, permission_level,
                        access_channel, request_fingerprint, total_tokens,
                        query_tokens_in, query_tokens_out, response_tokens_in, response_tokens_out,
                        retrieved_chunk_ids, rerank_scores, top_score, score_gap,
                        used_retrieval_cache, used_response_cache,
                        routing_strategy, eval_faithfulness, eval_completeness,
                        response_id, trace_id, citations_used, feedback_reason, comment,
                        draft_run_id, reason_code, abstention_correct,
                        agent_action, final_sent_text, edit_distance_ratio, edit_distance_tokens, citations_kept
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    RETURNING id
                    """,
                    (
                        data["user_token_hash"],
                        data.get("user_id", ""),
                        data.get("team_id", ""),
                        data.get("session_id", ""),
                        data.get("cache_key", ""),
                        (data.get("ticket_preview", "") or "")[:200],
                        data.get("confidence", ""),
                        data.get("rating") or None,
                        bool(data.get("email_was_edited", False)),
                        data.get("original_email", ""),
                        data.get("edited_email") if data.get("email_was_edited") else None,
                        int(data.get("response_time_ms", 0)),
                        bool(data.get("from_cache", False)),
                        data.get("product") or None,
                        data.get("permission_level") or None,
                        data.get("access_channel") or None,
                        data.get("request_fingerprint") or None,
                        int(data.get("total_tokens", 0) or 0),
                        int(data.get("query_tokens_in", 0) or 0),
                        int(data.get("query_tokens_out", 0) or 0),
                        int(data.get("response_tokens_in", 0) or 0),
                        int(data.get("response_tokens_out", 0) or 0),
                        data.get("retrieved_chunk_ids", "[]"),
                        data.get("rerank_scores", "[]"),
                        float(data.get("top_score", 0) or 0),
                        float(data.get("score_gap", 0) or 0),
                        bool(data.get("used_retrieval_cache", False)),
                        bool(data.get("used_response_cache", False)),
                        data.get("routing_strategy") or None,
                        data.get("eval_faithfulness") or None,
                        data.get("eval_completeness") or None,
                        data.get("response_id", ""),
                        data.get("trace_id", ""),
                        data.get("citations_used", "[]"),
                        data.get("feedback_reason", ""),
                        data.get("comment", ""),
                        data.get("draft_run_id", ""),
                        data.get("reason_code", data.get("feedback_reason", "")),
                        data.get("abstention_correct", "not_applicable"),
                        data.get("agent_action", "pending"),
                        final_sent_text,
                        float(data.get("edit_distance_ratio", edit_metrics["edit_distance_ratio"]) or 0),
                        int(data.get("edit_distance_tokens", edit_metrics["edit_distance_tokens"]) or 0),
                        data.get("citations_kept", data.get("citations_used", "[]")),
                    ),
                )
                if hasattr(cur, "fetchone"):
                    row = cur.fetchone()
                    feedback_id = str(row[0]) if row else ""
        return feedback_id
    except Exception as e:
        logger.error(f"Feedback write failed: {e}")
        return ""


def record_analytics_event(data: dict) -> str:
    event_id = data.get("id") or f"evt_{uuid.uuid4().hex}"
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO analytics_event (
                        id, event_type, trace_id, draft_run_id, user_id, team_id,
                        session_id, product, issue_category, source_id, chunk_id, metadata
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        event_id,
                        data.get("event_type", ""),
                        data.get("trace_id", ""),
                        data.get("draft_run_id", ""),
                        data.get("user_id", ""),
                        data.get("team_id", ""),
                        data.get("session_id", ""),
                        data.get("product", ""),
                        data.get("issue_category", ""),
                        data.get("source_id", ""),
                        data.get("chunk_id", ""),
                        _json_text(data.get("metadata"), {}),
                    ),
                )
    except Exception as e:
        logger.error(f"Analytics event write failed: {e}")
    return event_id


def save_draft_run(data: dict) -> str:
    draft_run_id = data.get("id") or f"draft_{uuid.uuid4().hex}"
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO draft_run (
                        id, trace_id, user_id, ticket_hash, ticket_preview_redacted,
                        final_draft, confidence_band, confidence_score, validation_status,
                        citations_used_json, source_ids_json, config_hash, schema_version, retention_until
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        NOW() + (%s * INTERVAL '1 day')
                    )
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        draft_run_id,
                        data.get("trace_id", ""),
                        data.get("user_id", ""),
                        data.get("ticket_text_hash") or data.get("ticket_hash", ""),
                        (data.get("ticket_preview_redacted", "") or "")[:360],
                        data.get("final_draft", ""),
                        data.get("confidence_band", ""),
                        float(data.get("confidence_score", 0) or 0),
                        data.get("validation_status", ""),
                        _json_text(data.get("citations_used"), []),
                        _json_text(data.get("source_ids"), []),
                        data.get("config_hash", ""),
                        data.get("schema_version", "v1"),
                        int(data.get("retention_days", project_config.workflow_settings().get("trace_retention_days", 30)) or 30),
                    ),
                )
    except Exception as e:
        logger.error(f"Draft run write failed: {e}")
    return draft_run_id


def create_knowledge_issue(data: dict) -> str:
    issue_id = data.get("id") or f"ki_{uuid.uuid4().hex}"
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO knowledge_issue (
                        id, created_from_feedback_id, draft_run_id, trace_id, issue_type,
                        status, severity, source_id, document_id, chunk_id, title,
                        description, suggested_action, created_by, assigned_to
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        issue_id,
                        data.get("created_from_feedback_id", ""),
                        data.get("draft_run_id", ""),
                        data.get("trace_id", ""),
                        data.get("issue_type", ""),
                        data.get("status", "open"),
                        data.get("severity", "medium"),
                        data.get("source_id", ""),
                        data.get("document_id", ""),
                        data.get("chunk_id", ""),
                        data.get("title", ""),
                        data.get("description", ""),
                        data.get("suggested_action", ""),
                        data.get("created_by", ""),
                        data.get("assigned_to", ""),
                    ),
                )
    except Exception as e:
        logger.error(f"Knowledge issue write failed: {e}")
    return issue_id


def create_feedback_label(data: dict) -> str:
    label_id = data.get("id") or f"fl_{uuid.uuid4().hex}"
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO feedback_label (
                        id, feedback_id, draft_run_id, trace_id, reviewer_user_id,
                        failure_type, severity, root_cause, recommended_action, reviewer_notes
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        label_id,
                        data.get("feedback_id", ""),
                        data.get("draft_run_id", ""),
                        data.get("trace_id", ""),
                        data.get("reviewer_user_id", ""),
                        data.get("failure_type", ""),
                        data.get("severity", "medium"),
                        data.get("root_cause", "unknown"),
                        data.get("recommended_action", ""),
                        data.get("reviewer_notes", ""),
                    ),
                )
    except Exception as e:
        logger.error(f"Feedback label write failed: {e}")
    return label_id


def create_knowledge_patch(data: dict) -> str:
    patch_id = data.get("id") or f"kp_{uuid.uuid4().hex}"
    review_status = data.get("review_status") or "proposed"
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO knowledge_patch (
                        id, knowledge_issue_id, patch_type, target_source_id,
                        target_document_id, target_chunk_id, before_text, after_text,
                        review_status, reviewed_by, review_notes, expires_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        patch_id,
                        data.get("knowledge_issue_id", ""),
                        data.get("patch_type", ""),
                        data.get("target_source_id", ""),
                        data.get("target_document_id", ""),
                        data.get("target_chunk_id", ""),
                        data.get("before_text", ""),
                        data.get("after_text", ""),
                        review_status,
                        data.get("reviewed_by", ""),
                        data.get("review_notes", ""),
                        data.get("expires_at"),
                    ),
                )
    except Exception as e:
        logger.error(f"Knowledge patch write failed: {e}")
    return patch_id


def create_experiment(data: dict) -> str:
    experiment_id = data.get("id") or f"exp_{uuid.uuid4().hex}"
    status = data.get("status") or "disabled"
    mode = data.get("mode") or "offline_replay"
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO experiment (
                        id, name, description, status, mode, owner, start_at,
                        end_at, success_metric, guardrail_metrics_json
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        experiment_id,
                        data.get("name", ""),
                        data.get("description", ""),
                        status,
                        mode,
                        data.get("owner", ""),
                        data.get("start_at"),
                        data.get("end_at"),
                        data.get("success_metric", ""),
                        _json_text(data.get("guardrail_metrics"), {}),
                    ),
                )
    except Exception as e:
        logger.error(f"Experiment write failed: {e}")
    return experiment_id


def create_experiment_arm(data: dict) -> str:
    arm_id = data.get("id") or f"arm_{uuid.uuid4().hex}"
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO experiment_arm (
                        id, experiment_id, name, description, config_overrides_json,
                        pipeline_variant, traffic_percentage
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        arm_id,
                        data.get("experiment_id", ""),
                        data.get("name", ""),
                        data.get("description", ""),
                        _json_text(data.get("config_overrides"), {}),
                        data.get("pipeline_variant", ""),
                        float(data.get("traffic_percentage", 0) or 0),
                    ),
                )
    except Exception as e:
        logger.error(f"Experiment arm write failed: {e}")
    return arm_id


def record_experiment_result(data: dict) -> str:
    result_id = data.get("id") or f"er_{uuid.uuid4().hex}"
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO experiment_result (
                        id, experiment_id, experiment_arm_id, draft_run_id, trace_id,
                        eval_case_id, status, confidence_band, validation_status,
                        citation_precision, faithfulness_score, coverage_result,
                        latency_ms, estimated_cost, feedback_agent_action,
                        edit_distance_ratio, reviewer_label
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        result_id,
                        data.get("experiment_id", ""),
                        data.get("experiment_arm_id", ""),
                        data.get("draft_run_id", ""),
                        data.get("trace_id", ""),
                        data.get("eval_case_id", ""),
                        data.get("status", ""),
                        data.get("confidence_band", ""),
                        data.get("validation_status", ""),
                        float(data.get("citation_precision", 0) or 0),
                        float(data.get("faithfulness_score", 0) or 0),
                        data.get("coverage_result", ""),
                        int(data.get("latency_ms", 0) or 0),
                        float(data.get("estimated_cost", 0) or 0),
                        data.get("feedback_agent_action", ""),
                        float(data.get("edit_distance_ratio", 0) or 0),
                        data.get("reviewer_label", ""),
                    ),
                )
    except Exception as e:
        logger.error(f"Experiment result write failed: {e}")
    return result_id


def save_run_trace(trace: dict) -> None:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                retention_days = int(project_config.workflow_settings().get("trace_retention_days", 30) or 30)
                cur.execute(
                    "DELETE FROM run_trace WHERE created_at < NOW() - (%s * INTERVAL '1 day')",
                    (retention_days,),
                )
                cur.execute(
                    """
                    INSERT INTO run_trace (
                        trace_id, timestamp, ticket_text_hash, redacted_ticket_preview,
                        config_hash, model_provider, workflow_mode, product, platform, role, trace
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (trace_id) DO UPDATE
                    SET trace = EXCLUDED.trace
                    """,
                    (
                        trace["trace_id"],
                        trace["timestamp"],
                        trace["ticket_text_hash"],
                        trace.get("redacted_ticket_preview", ""),
                        trace.get("config_hash", ""),
                        trace.get("model_provider", ""),
                        trace.get("workflow_mode", ""),
                        trace.get("product", ""),
                        trace.get("platform", ""),
                        trace.get("role", ""),
                        json.dumps(trace),
                    ),
                )
    except Exception as e:
        logger.error(f"Run trace write failed: {e}")


def get_run_trace(trace_id: str) -> dict | None:
    if not trace_id:
        return None
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT trace FROM run_trace WHERE trace_id = %s", (trace_id,))
                row = cur.fetchone()
        if not row:
            return None
        return row[0] if isinstance(row[0], dict) else json.loads(row[0])
    except Exception as e:
        logger.error(f"Run trace read failed: {e}")
        return None


def create_review_queue_item(data: dict) -> None:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO human_review_queue (
                        trace_id, cache_key, ticket_preview, full_ticket, confidence,
                        confidence_band, severity, sla_marker, gatekeeper_reason, source_issue_type,
                        auditor_flags, needs_escalation, escalation_reason, route,
                        assigned_reviewer, status, reviewed, reviewer_notes
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        data.get("trace_id", ""),
                        data.get("cache_key", ""),
                        (data.get("ticket_preview", "") or "")[:240],
                        data.get("full_ticket", ""),
                        data.get("confidence", ""),
                        data.get("confidence_band", ""),
                        data.get("severity", "medium"),
                        data.get("sla_marker", ""),
                        data.get("gatekeeper_reason", ""),
                        data.get("source_issue_type", ""),
                        json.dumps(data.get("auditor_flags", {})),
                        bool(data.get("needs_escalation", False)),
                        data.get("escalation_reason", ""),
                        data.get("route", ""),
                        data.get("assigned_reviewer", ""),
                        data.get("status", "open"),
                        bool(data.get("reviewed", False)),
                        data.get("reviewer_notes", ""),
                    ),
                )
    except Exception as e:
        logger.error(f"Human review queue write failed: {e}")
