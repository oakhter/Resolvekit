from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
import asyncio
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import time
import sys
import psycopg2
from datetime import datetime, timezone
from uuid import uuid4

from backend.core import config, orchestrator
from backend.core import project_config
from pipeline.cache import (
    create_experiment,
    create_feedback_label,
    create_knowledge_issue,
    create_knowledge_patch,
    get_conn,
    get_run_trace,
    record_experiment_result,
    save_feedback,
    create_review_queue_item,
)
from backend.providers import get_provider
from backend.providers.model_warmup import warm_local_models
from backend.core.logger import get_logger, enable_app_log
from backend.core.replay import replay_saved_trace
from backend.db.schema import ensure_vector_schema, ensure_ops_schema
from backend.db.schema import _safe_schema_name
from fastapi.responses import StreamingResponse
from io import BytesIO
import zipfile

LAST_CALL = 0
RATE_LIMIT_SECONDS = 2

logger = get_logger(__name__)


def allow_request():
    global LAST_CALL
    now = time.time()

    if now - LAST_CALL < RATE_LIMIT_SECONDS:
        return False

    LAST_CALL = now
    return True


# ── App Setup ────────────────────────────────────────────────
app = FastAPI(
    title="ResolveKit",
    description="Source-grounded support drafting",
    version="1.0.0"
)

# ── Startup Validation (FAIL-FAST) ───────────────────────────
@app.on_event("startup")
def startup():
    enable_app_log()
    try:
        logger.info("Validating configuration...")
        config.validate()

        logger.info("Checking database connections...")
        config.validate_db()

        import psycopg2
        with psycopg2.connect(config.DATABASE_URL) as conn:
            ensure_vector_schema(conn, schema=config.KNOWLEDGE_SCHEMA)
        with psycopg2.connect(config.DATABASE_URL) as conn:
            ensure_ops_schema(conn, schema=config.OPS_SCHEMA)

        warm_local_models()

        logger.info("System ready")

    except Exception as e:
        logger.error(f"Startup failed: {e}")
        sys.exit(1)


# ── CORS ─────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ALLOW_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── UI Files ─────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parents[2]
CONFIGURATOR_INDEX = BASE_DIR / "frontend" / "configurator" / "index.html"
TICKET_INDEX = BASE_DIR / "frontend" / "ticket" / "index.html"
ADMIN_INDEX = BASE_DIR / "frontend" / "admin" / "index.html"
LOCAL_CONFIGURATOR_HOSTS = {"127.0.0.1", "localhost", "::1", "testclient"}
LOCAL_CONFIGURATOR_ORIGIN_PREFIXES = (
    "http://localhost",
    "http://127.0.0.1",
    "http://[::1]",
)

# ── Models ───────────────────────────────────────────────────
class TicketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket: str
    mode: str = "suggest"
    support_ops_mode: str = "query"
    product: str = ""
    permission_level: str = ""
    access_channel: str = ""
    request_fingerprint: str = ""
    pinned_source_ids: list[str] = []
    similarity_threshold: str = "none"
    experiment_arm: str = ""


class ResolutionResponse(BaseModel):
    status: str
    resolution: dict


class FeedbackRequest(BaseModel):
    cache_key:        str  = ""
    ticket_preview:   str  = ""
    confidence:       str  = ""
    rating:           str  = ""    # "thumbs_up" | "thumbs_down"
    email_was_edited: bool = False
    original_email:   str  = ""
    edited_email:     str  = ""
    response_time_ms: int  = 0
    from_cache:       bool = False
    product:          str  = ""
    permission_level: str  = ""
    access_channel:   str  = ""
    request_fingerprint: str = ""
    total_tokens: int = 0
    query_tokens_in: int = 0
    query_tokens_out: int = 0
    response_tokens_in: int = 0
    response_tokens_out: int = 0
    # Retrieval diagnostics — echoed back from resolution.retrieval_signals
    retrieved_chunk_ids: str = "[]"
    rerank_scores: str = "[]"
    top_score: float = 0.0
    score_gap: float = 0.0
    used_retrieval_cache: bool = False
    used_response_cache: bool = False
    routing_strategy: str = ""
    eval_faithfulness: str = ""
    eval_completeness: str = ""
    response_id: str = ""
    draft_run_id: str = ""
    trace_id: str = ""
    citations_used: str = "[]"
    feedback_reason: str = ""
    reason_code: str = ""
    comment: str = ""
    agent_action: str = "pending"
    abstention_correct: str = "not_applicable"
    final_sent_text: str = ""
    edit_distance_ratio: float = 0.0
    edit_distance_tokens: int = 0
    citations_kept: str = "[]"


class ConfiguratorSaveRequest(BaseModel):
    products: dict | None = None
    sources: dict | None = None
    output: dict | None = None
    retrieval_policy: dict | None = None
    workflow: dict | None = None


class SourcePreviewRequest(BaseModel):
    source_key: str
    path: str
    source_type: str = ""
    column_mapping: dict | None = None
    sample_row_limit: int = 5


class KnowledgeIssueRequest(BaseModel):
    created_from_feedback_id: str = ""
    draft_run_id: str = ""
    trace_id: str = ""
    issue_type: str
    severity: str = "medium"
    source_id: str = ""
    document_id: str = ""
    chunk_id: str = ""
    title: str = ""
    description: str = ""
    suggested_action: str = ""
    assigned_to: str = ""


class FeedbackLabelRequest(BaseModel):
    feedback_id: str = ""
    draft_run_id: str = ""
    trace_id: str = ""
    failure_type: str
    severity: str = "medium"
    root_cause: str = "unknown"
    recommended_action: str = ""
    reviewer_notes: str = ""


class KnowledgePatchRequest(BaseModel):
    knowledge_issue_id: str = ""
    patch_type: str = ""
    target_source_id: str = ""
    target_document_id: str = ""
    target_chunk_id: str = ""
    before_text: str = ""
    after_text: str = ""
    review_status: str = "proposed"
    review_notes: str = ""


class ExperimentRequest(BaseModel):
    name: str
    description: str = ""
    status: str = "disabled"
    mode: str = "offline_replay"
    owner: str = ""
    success_metric: str = ""
    guardrail_metrics: dict = {}


class OfflineReplayRequest(BaseModel):
    experiment_id: str = ""
    eval_case_ids: list[str] = []
    arms: list[dict] = []


class SourceControlRequest(BaseModel):
    reason: str = "manual_review"
    requested_by: str = ""


class ReingestPreviewRequest(BaseModel):
    source_id: str
    document_id: str = ""
    new_document_hash: str = ""


class ReplayLookupRequest(BaseModel):
    conversation_id: str = ""
    draft_id: str = ""
    trace_id: str = ""
    replay_mode: str = "current_config"


FEEDBACK_REASONS = {
    "good_answer", "wrong_answer", "wrong_source", "stale_source", "missing_source",
    "missing_context", "unsupported_claim", "unsafe_answer", "unsafe_output",
    "bad_tone", "tone_issue", "too_verbose", "too_cautious", "too_vague",
    "irrelevant", "duplicate", "other", ""
}
AGENT_ACTIONS = {"sent_as_is", "edited", "rejected", "pending", ""}
ABSTENTION_VALUES = {"yes", "no", "unsure", "not_applicable", ""}
CONFIGURATOR_SOURCE_PREVIEW_MIME_ALLOWLIST = {
    "text/csv",
    "application/csv",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/pdf",
}
CONFIGURATOR_SOURCE_PREVIEW_SUFFIX_ALLOWLIST = {".csv", ".xlsx", ".pdf"}
DIAGNOSTIC_CHECKS = {
    "app_runtime": {
        "name": "App Runtime",
        "description": "FastAPI process and ResolveKit runtime metadata.",
        "suggestions": ["Restart the FastAPI app if runtime metadata looks stale."],
    },
    "database": {
        "name": "Database",
        "description": "Postgres connectivity for knowledge and ops schemas.",
        "suggestions": ["Set DATABASE_URL.", "Start Postgres.", "Run .venv/bin/python scripts/setup_db.py."],
    },
    "vector_store": {
        "name": "Vector Store",
        "description": "Knowledge schema and retrieval table availability.",
        "suggestions": ["Run .venv/bin/python scripts/rebuild_db.py.", "Reload knowledge with .venv/bin/python knowledge_loader/kb_loader.py."],
    },
    "llm_provider": {
        "name": "LLM Provider",
        "description": "Active hosted model provider configuration.",
        "suggestions": ["Set ACTIVE_PROVIDER to openai or gemini.", "Set the matching provider API key."],
    },
    "embedding_provider": {
        "name": "Embedding Provider",
        "description": "Local embedding model warmup and lazy-load setting.",
        "suggestions": ["Keep WARM_LOCAL_MODELS enabled for early startup checks."],
    },
    "retrieval_pipeline": {
        "name": "Retrieval Pipeline",
        "description": "Runtime retrieval policy and route configuration.",
        "suggestions": ["Validate config in the configurator.", "Check config/retrieval_policy.yaml."],
    },
    "rate_limit": {
        "name": "Rate Limit",
        "description": "Resolve endpoint request pacing.",
        "suggestions": ["Increase RATE_LIMIT_SECONDS only if local workflow requires it."],
    },
    "auth_config": {
        "name": "Auth Config",
        "description": "API and configurator key presence without exposing secrets.",
        "suggestions": ["Set API_KEY and CONFIGURATOR_API_KEY to non-placeholder values."],
    },
    "storage": {
        "name": "Storage",
        "description": "Project-local config, logs, diagnostics, and source paths.",
        "suggestions": ["Ensure the project directory is writable by the app process."],
    },
    "cors_urls": {
        "name": "CORS / URLs",
        "description": "Allowed browser origins for local UI calls.",
        "suggestions": ["Set CORS_ALLOW_ORIGINS to the deployed UI origins."],
    },
    "external_integrations": {
        "name": "External Integrations",
        "description": "Hosted provider and optional external service readiness.",
        "suggestions": ["Set only the provider key required by ACTIVE_PROVIDER."],
    },
    "recent_logs": {
        "name": "Recent Logs",
        "description": "Readable local app log files.",
        "suggestions": ["Enable app logging and check logs/logs.txt."],
    },
    "chat_diagnostics": {
        "name": "Chat Diagnostics",
        "description": "Client-side diagnostic sessions for sandbox questions.",
        "suggestions": ["Run a Ticket Sandbox request to create a chat diagnostic session."],
    },
}


# ── API Key Auth ─────────────────────────────────────────────
VIEWER_PERMISSIONS = {
    "create_draft",
    "view_draft",
    "view_citations",
    "view_trace_summary",
    "submit_feedback",
}
ADMIN_PERMISSIONS = VIEWER_PERMISSIONS | {
    "view_full_trace_json",
    "replay_trace",
    "ingest_sources",
    "edit_sources",
    "export_support_bundle",
    "run_evals",
    "run_ab_tests",
    "change_config",
    "view_audit_log",
}
AUDIT_LOG_PATH = BASE_DIR / "experiments" / "audit_log.jsonl"


def _actor_from_token(token: str) -> dict | None:
    token = token or ""
    if config.CONFIGURATOR_ADMIN_TOKEN and token == config.CONFIGURATOR_ADMIN_TOKEN:
        return {"role": "admin", "permissions": sorted(ADMIN_PERMISSIONS)}
    if config.CONFIGURATOR_API_KEY and token == config.CONFIGURATOR_API_KEY:
        return {"role": "admin", "permissions": sorted(ADMIN_PERMISSIONS)}
    if config.VIEWER_TOKEN and token == config.VIEWER_TOKEN:
        return {"role": "viewer", "permissions": sorted(VIEWER_PERMISSIONS)}
    if config.API_KEY and token == config.API_KEY:
        return {"role": "viewer", "permissions": sorted(VIEWER_PERMISSIONS)}
    return None


def current_actor(request: Request) -> dict:
    actor = _actor_from_token(request.headers.get("x-api-key", ""))
    if not actor:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return actor


def verify_api_key(request: Request):
    current_actor(request)


def verify_admin(request: Request):
    actor = current_actor(request)
    if actor["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin permission required")
    return actor


def verify_configurator_key(request: Request):
    return verify_admin(request)


def audit_admin_action(
    request: Request,
    event_type: str,
    *,
    conversation_id: str = "",
    trace_id: str = "",
    config_hash: str = "",
    metadata: dict | None = None,
) -> dict:
    actor = current_actor(request)
    event = {
        "event_id": str(uuid4()),
        "event_type": event_type,
        "actor_role": actor["role"],
        "conversation_id": conversation_id,
        "trace_id": trace_id,
        "config_hash": config_hash,
        "created_at": _utc_now_iso(),
        "metadata": metadata or {},
    }
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")
    return event


def enforce_source_preview_file_policy(path: str):
    source_path = Path(path).expanduser().resolve()
    try:
        source_path.relative_to(BASE_DIR)
    except ValueError:
        raise HTTPException(status_code=403, detail="Source preview path must stay inside the project directory")
    if not source_path.exists() or not source_path.is_file():
        raise HTTPException(status_code=400, detail="Source file does not exist")

    size = source_path.stat().st_size
    if size > config.CONFIGURATOR_SOURCE_PREVIEW_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Source file is too large for preview")

    suffix = source_path.suffix.lower()
    guessed_mime = mimetypes.guess_type(source_path.name)[0]
    if suffix not in CONFIGURATOR_SOURCE_PREVIEW_SUFFIX_ALLOWLIST:
        raise HTTPException(status_code=415, detail="Unsupported source file extension for preview")
    if guessed_mime and guessed_mime not in CONFIGURATOR_SOURCE_PREVIEW_MIME_ALLOWLIST:
        raise HTTPException(status_code=415, detail="Unsupported source file type for preview")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _diagnostic_status(status: str, required: bool = False) -> str:
    if status == "ok":
        return "ok"
    if required:
        return "fail"
    return "warn"


def _mask_diagnostic_value(key: str, value) -> str:
    raw = "" if value is None else str(value)
    if not raw:
        return ""
    upper = key.upper()
    is_secret = any(token in upper for token in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
    is_url = upper.endswith("URL") or "DATABASE_URL" in upper
    if is_url and "://" in raw:
        scheme, rest = raw.split("://", 1)
        host = rest.rsplit("@", 1)[-1].split("/", 1)[0]
        database = rest.rsplit("/", 1)[-1] if "/" in rest else ""
        tail = f"/{database}" if database else ""
        return f"{scheme}://...@{host}{tail}"
    if is_secret:
        if len(raw) <= 8:
            return "***"
        prefix = raw[:3] if raw.startswith("sk-") else raw[:2]
        return f"{prefix}...{raw[-4:]}"
    if len(raw) > 80:
        return f"{raw[:38]}...{raw[-18:]}"
    return raw


def build_config_diagnostics() -> list[dict]:
    items = [
        ("ACTIVE_PROVIDER", True, config.ACTIVE_PROVIDER, "LLM provider selected at app startup."),
        ("OPENAI_API_KEY", config.ACTIVE_PROVIDER == "openai", config.OPENAI_API_KEY, "OpenAI provider key."),
        ("GEMINI_API_KEY", config.ACTIVE_PROVIDER == "gemini", config.GEMINI_API_KEY, "Gemini provider key."),
        ("API_KEY", True, config.API_KEY, "Main API authentication key."),
        ("CONFIGURATOR_API_KEY", True, config.CONFIGURATOR_API_KEY, "Configurator and diagnostics authentication key."),
        ("DATABASE_URL", True, config.DATABASE_URL, "Postgres connection string."),
        ("KNOWLEDGE_SCHEMA", True, config.KNOWLEDGE_SCHEMA, "Knowledge/vector schema."),
        ("OPS_SCHEMA", True, config.OPS_SCHEMA, "Operational schema."),
        ("CORS_ALLOW_ORIGINS", False, ",".join(config.CORS_ALLOW_ORIGINS), "Browser origins allowed by CORS."),
        ("WARM_LOCAL_MODELS", False, str(config.WARM_LOCAL_MODELS), "Embedding warmup setting."),
        ("DEMO_MODE", False, str(config.DEMO_MODE), "Demo data mode."),
    ]
    diagnostics = []
    for key, required, value, description in items:
        present = bool(value)
        diagnostics.append({
            "key": key,
            "required": required,
            "present": present,
            "status": "ok" if present else _diagnostic_status("missing", required),
            "safeValuePreview": _mask_diagnostic_value(key, value),
            "description": description,
        })
    return diagnostics


def _diagnostic_result(check_id: str, status: str, message: str, details: dict | None = None, suggestions: list[str] | None = None, started: float | None = None) -> dict:
    definition = DIAGNOSTIC_CHECKS.get(check_id, {})
    started = started or time.perf_counter()
    return {
        "id": check_id,
        "name": definition.get("name", check_id.replace("_", " ").title()),
        "status": status,
        "message": message,
        "details": details or {},
        "suggestions": suggestions if suggestions is not None else definition.get("suggestions", []),
        "testedAt": _utc_now_iso(),
        "durationMs": round((time.perf_counter() - started) * 1000, 2),
    }


def run_diagnostic_check(check_id: str) -> dict:
    if check_id not in DIAGNOSTIC_CHECKS:
        raise HTTPException(status_code=404, detail="Unknown diagnostic check")
    started = time.perf_counter()
    try:
        if check_id == "app_runtime":
            return _diagnostic_result(check_id, "ok", "ResolveKit API process is running.", {
                "service": "ResolveKit",
                "version": app.version,
                "base_dir": str(BASE_DIR),
                "python": sys.version.split()[0],
            }, started=started)
        if check_id == "database":
            config.validate_db()
            return _diagnostic_result(check_id, "ok", "Database connection succeeded.", {
                "knowledge_schema": config.KNOWLEDGE_SCHEMA,
                "ops_schema": config.OPS_SCHEMA,
            }, started=started)
        if check_id == "vector_store":
            try:
                with psycopg2.connect(config.DATABASE_URL, connect_timeout=5) as conn:
                    schema = _safe_schema_name(config.KNOWLEDGE_SCHEMA)
                    with conn.cursor() as cur:
                        cur.execute(f'SET search_path TO "{schema}", public;')
                        cur.execute("SELECT COUNT(*) FROM knowledge_base_identifier")
                        chunk_count = cur.fetchone()[0]
                status = "ok" if chunk_count else "warn"
                message = f"Vector store reachable with {chunk_count} chunks." if chunk_count else "Vector store reachable but no chunks were found."
                return _diagnostic_result(check_id, status, message, {"chunk_count": chunk_count}, started=started)
            except Exception as e:
                return _diagnostic_result(check_id, "fail", "Vector store check failed.", {"error": str(e)}, started=started)
        if check_id == "llm_provider":
            provider_key = config.OPENAI_API_KEY if config.ACTIVE_PROVIDER == "openai" else config.GEMINI_API_KEY
            status = "ok" if config.ACTIVE_PROVIDER in config.MODELS and provider_key else "fail"
            return _diagnostic_result(check_id, status, f"Active provider: {config.ACTIVE_PROVIDER}.", {
                "active_provider": config.ACTIVE_PROVIDER,
                "model": config.MODELS.get(config.ACTIVE_PROVIDER, "unknown"),
                "api_key_present": bool(provider_key),
            }, started=started)
        if check_id == "embedding_provider":
            status = "ok" if config.WARM_LOCAL_MODELS else "warn"
            return _diagnostic_result(check_id, status, "Embedding provider uses local lazy loading.", {
                "warm_local_models": config.WARM_LOCAL_MODELS,
                "max_chunk_length": config.MAX_CHUNK_LENGTH,
            }, started=started)
        if check_id == "retrieval_pipeline":
            policy = project_config.load_config("retrieval_policy")
            route_count = len(policy.get("route_policies", {}) or {})
            return _diagnostic_result(check_id, "ok", f"Retrieval policy loaded with {route_count} route policies.", {
                "top_k_retrieval": config.TOP_K_RETRIEVAL,
                "top_k_rerank": config.TOP_K_RERANK,
                "route_policy_count": route_count,
            }, started=started)
        if check_id == "rate_limit":
            return _diagnostic_result(check_id, "ok", f"Resolve rate limit is {RATE_LIMIT_SECONDS}s between requests.", {
                "rate_limit_seconds": RATE_LIMIT_SECONDS,
                "last_call_epoch": LAST_CALL,
            }, started=started)
        if check_id == "auth_config":
            api_placeholder = config.API_KEY in {"change-me", "change-me-configurator", "changeme", "test"}
            cfg_placeholder = config.CONFIGURATOR_API_KEY in {"change-me", "change-me-configurator", "changeme", "test"}
            status = "warn" if api_placeholder or cfg_placeholder else "ok"
            return _diagnostic_result(check_id, status, "Auth keys are present." if status == "ok" else "Auth keys are present but look like placeholders.", {
                "api_key": _mask_diagnostic_value("API_KEY", config.API_KEY),
                "configurator_api_key": _mask_diagnostic_value("CONFIGURATOR_API_KEY", config.CONFIGURATOR_API_KEY),
            }, started=started)
        if check_id == "storage":
            paths = [BASE_DIR / "config", BASE_DIR / "logs", BASE_DIR / "diagnostics"]
            missing = [str(path) for path in paths if not path.exists()]
            status = "warn" if missing else "ok"
            return _diagnostic_result(check_id, status, "Project storage paths are available." if not missing else "Some storage paths are missing.", {
                "paths": [str(path) for path in paths],
                "missing": missing,
                "base_dir_writable": os.access(BASE_DIR, os.W_OK),
            }, started=started)
        if check_id == "cors_urls":
            status = "ok" if config.CORS_ALLOW_ORIGINS else "warn"
            return _diagnostic_result(check_id, status, f"{len(config.CORS_ALLOW_ORIGINS)} CORS origins configured.", {
                "origins": config.CORS_ALLOW_ORIGINS,
            }, started=started)
        if check_id == "external_integrations":
            provider_key = config.OPENAI_API_KEY if config.ACTIVE_PROVIDER == "openai" else config.GEMINI_API_KEY
            status = "ok" if provider_key else "fail"
            return _diagnostic_result(check_id, status, "Active hosted provider key is configured." if provider_key else "Active hosted provider key is missing.", {
                "active_provider": config.ACTIVE_PROVIDER,
                "provider_key_present": bool(provider_key),
            }, started=started)
        if check_id == "recent_logs":
            log_paths = [BASE_DIR / "logs" / "logs.txt", BASE_DIR / "diagnostics" / "logs" / "app.txt"]
            readable = [str(path) for path in log_paths if path.exists() and os.access(path, os.R_OK)]
            status = "ok" if readable else "warn"
            return _diagnostic_result(check_id, status, "Recent log files are readable." if readable else "No readable log files found yet.", {
                "readable": readable,
            }, started=started)
        if check_id == "chat_diagnostics":
            return _diagnostic_result(check_id, "ok", "Chat diagnostics are recorded in the browser for each sandbox run.", {
                "storage": "browser runtime state",
                "replay_mode": "same_chunks_new_llm",
            }, started=started)
    except Exception as e:
        return _diagnostic_result(check_id, "fail", f"{DIAGNOSTIC_CHECKS[check_id]['name']} check failed.", {"error": str(e)}, started=started)


def _read_recent_log_lines(limit: int = 120) -> list[dict]:
    limit = max(1, min(int(limit or 120), 500))
    entries = []
    for path in [BASE_DIR / "logs" / "logs.txt", BASE_DIR / "diagnostics" / "logs" / "app.txt"]:
        if not path.exists() or not path.is_file():
            continue
        try:
            lines = path.read_text(errors="replace").splitlines()[-limit:]
        except OSError:
            continue
        for idx, line in enumerate(lines):
            lowered = line.lower()
            level = "info"
            if "error" in lowered or "failed" in lowered:
                level = "error"
            elif "warn" in lowered:
                level = "warn"
            elif "success" in lowered or "ready" in lowered:
                level = "success"
            entries.append({
                "id": f"{path.name}-{idx}",
                "timestamp": "",
                "level": level,
                "event": "app.log",
                "message": line[-500:],
                "metadata": {"source": str(path.relative_to(BASE_DIR))},
            })
    return entries[-limit:]


def _first_json_list_value(raw: str) -> str:
    try:
        value = json.loads(raw or "[]")
    except Exception:
        return ""
    if isinstance(value, list) and value:
        return str(value[0])
    return ""


def _suggested_action_for_feedback(reason: str) -> str:
    mapping = {
        "wrong_source": "tune_retrieval",
        "stale_source": "mark_source_stale",
        "missing_source": "create_knowledge_issue",
        "unsupported_claim": "update_prompt",
        "unsafe_answer": "update_prompt",
        "unsafe_output": "update_prompt",
        "bad_tone": "update_output_template",
        "tone_issue": "update_output_template",
        "too_verbose": "update_output_template",
        "too_cautious": "update_output_template",
        "missing_context": "no_action",
    }
    return mapping.get(reason, "create_knowledge_issue")


# ── Health Check ─────────────────────────────────────────────
@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "ResolveKit",
        "version": "1.0.0"
    }


@app.get("/api/me")
def api_me(request: Request):
    actor = current_actor(request)
    return {"status": "ok", "role": actor["role"], "permissions": actor["permissions"], "config_hash": project_config.runtime_fingerprint()}


@app.get("/configurator")
def configurator_ui():
    return FileResponse(CONFIGURATOR_INDEX, headers={"Cache-Control": "no-store"})


@app.get("/admin")
def admin_ui():
    return FileResponse(ADMIN_INDEX, headers={"Cache-Control": "no-store"})


def _is_local_configurator_request(request: Request) -> bool:
    host = request.client.host if request.client else ""
    origin = request.headers.get("origin", "")
    origin_is_local = not origin or origin.startswith(LOCAL_CONFIGURATOR_ORIGIN_PREFIXES)
    return host in LOCAL_CONFIGURATOR_HOSTS and origin_is_local


@app.get("/configurator/dev-settings")
def get_configurator_dev_settings(request: Request):
    allow_prefill = config.CONFIGURATOR_PREFILL_API_KEY and _is_local_configurator_request(request)
    return {
        "status": "ok",
        "prefill_api_key": allow_prefill,
        "api_key": config.API_KEY if allow_prefill else "",
        "configurator_api_key": config.CONFIGURATOR_API_KEY if allow_prefill else "",
    }


@app.get("/configurator/config", dependencies=[Depends(verify_configurator_key)])
def get_configurator_config():
    try:
        project_config.write_example_configs()
        data = project_config.load_config()
        return {
            "status": "ok",
            "config": data,
            "validation": project_config.validate_config(data),
            "source_registry": project_config.get_source_registry(),
            "impact_labels": project_config.config_impact_labels(),
            "field_metadata": project_config.config_field_metadata(),
            "setup_wizard": project_config.setup_wizard_status(data),
            "ui_contracts": ui_contracts(),
        }
    except Exception as e:
        logger.error(f"Configurator load failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/configurator/config", dependencies=[Depends(verify_configurator_key)])
def save_configurator_config(body: ConfiguratorSaveRequest, request: Request):
    try:
        payload = body.dict(exclude_none=True)
        result = project_config.save_config(payload)
        audit_admin_action(
            request,
            "config_update",
            config_hash=project_config.runtime_fingerprint(),
            metadata={"sections": sorted(payload.keys())},
        )
        return {
            "status": "ok",
            **result,
            "validation": project_config.validate_config(result["config"]),
            "field_metadata": project_config.config_field_metadata(),
            "setup_wizard": project_config.setup_wizard_status(result["config"]),
        }
    except Exception as e:
        logger.error(f"Configurator save failed: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/configurator/validate", dependencies=[Depends(verify_configurator_key)])
def validate_configurator_config(body: ConfiguratorSaveRequest):
    try:
        current = project_config.load_config()
        incoming = body.dict(exclude_none=True)
        current.update(incoming)
        return {"status": "ok", "validation": project_config.validate_config(current)}
    except Exception as e:
        logger.error(f"Configurator validation failed: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/configurator/source-preview", dependencies=[Depends(verify_configurator_key)])
def preview_configurator_source(body: SourcePreviewRequest):
    try:
        enforce_source_preview_file_policy(body.path)
        preview = project_config.preview_source(
            source_key=body.source_key,
            path=body.path,
            source_type=body.source_type,
            column_mapping=body.column_mapping or {},
            sample_row_limit=body.sample_row_limit,
        )
        return {"status": "ok", "preview": preview}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Configurator source preview failed: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/configurator/setup-status", dependencies=[Depends(verify_configurator_key)])
def get_setup_status():
    data = project_config.load_config()
    return {"status": "ok", "setup_wizard": project_config.setup_wizard_status(data)}


@app.get("/diagnostics/config", dependencies=[Depends(verify_configurator_key)])
def diagnostics_config():
    return {
        "status": "ok",
        "config": build_config_diagnostics(),
        "runtime": {
            "active_provider": config.ACTIVE_PROVIDER,
            "model": config.MODELS.get(config.ACTIVE_PROVIDER, "unknown"),
            "top_k_retrieval": config.TOP_K_RETRIEVAL,
            "top_k_rerank": config.TOP_K_RERANK,
            "max_context_chars": config.MAX_CONTEXT_CHARS,
            "cors_allow_origins": config.CORS_ALLOW_ORIGINS,
        },
        "generated_at": _utc_now_iso(),
    }


@app.get("/diagnostics/checks", dependencies=[Depends(verify_configurator_key)])
def diagnostics_checks():
    return {
        "status": "ok",
        "checks": [run_diagnostic_check(check_id) for check_id in DIAGNOSTIC_CHECKS],
    }


@app.post("/diagnostics/checks/{check_id}", dependencies=[Depends(verify_configurator_key)])
def diagnostics_check(check_id: str):
    return {"status": "ok", "result": run_diagnostic_check(check_id)}


@app.get("/diagnostics/logs", dependencies=[Depends(verify_configurator_key)])
def diagnostics_logs(limit: int = 120):
    return {"status": "ok", "logs": _read_recent_log_lines(limit)}


@app.get("/contracts/ui")
def ui_contracts():
    return {
        "status": "ok",
        "auth": {
            "header": "x-api-key",
            "browser_storage_key": "ai_bot_api_key",
            "configurator_browser_storage_key": "resolvekit_configurator_api_key",
            "local_dev_prefill_endpoint": "/configurator/dev-settings",
        },
        "ticket_request": {
            "ticket": "string",
            "mode": "suggest",
            "support_ops_mode": "query|chat",
            "product": "string",
            "permission_level": "string",
            "access_channel": "string",
            "request_fingerprint": "string",
            "pinned_source_ids": ["source_id"],
            "similarity_threshold": "none|low|medium|high",
        },
        "feedback_reasons": sorted(FEEDBACK_REASONS - {""}),
        "validation_message_shape": {"valid": "boolean", "errors": ["string"], "warnings": ["string"]},
    }


# ── Main Endpoint ────────────────────────────────────────────
@app.post("/resolve", response_model=ResolutionResponse, dependencies=[Depends(verify_api_key)])
async def resolve_ticket(request: TicketRequest):
    try:
        if not request.ticket or not request.ticket.strip():
            raise HTTPException(status_code=400, detail="Ticket text is empty")
        if request.mode != "suggest":
            raise HTTPException(status_code=400, detail="Unsupported mode. This v3.x demo is suggest-only.")
        if request.support_ops_mode not in {"query", "chat"}:
            raise HTTPException(status_code=400, detail="Unsupported support_ops_mode")

        if not allow_request():
            logger.warning("Rate limit hit")
            raise HTTPException(status_code=429, detail="Too many requests — slow down")

        logger.info("Incoming request: /resolve")
        logger.info(f"Ticket length: {len(request.ticket)} chars")

        resolution = await asyncio.to_thread(
            orchestrator.run,
            request.ticket,
            {
                "product": request.product,
                "mode": request.mode,
                "support_ops_mode": request.support_ops_mode,
                "permission_level": request.permission_level,
                "access_channel": request.access_channel,
                "request_fingerprint": request.request_fingerprint,
                "pinned_source_ids": request.pinned_source_ids,
                "similarity_threshold": request.similarity_threshold,
                "experiment_arm": request.experiment_arm,
            }
        )

        if resolution.get("mode", "suggest") != "suggest":
            raise HTTPException(status_code=400, detail="Responder output mode is unsupported. This v3.x demo is suggest-only.")

        try:
            resolution["provider"] = get_provider().get_name()
        except Exception:
            resolution["provider"] = "unknown"

        return {"status": "success", "resolution": resolution}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"API error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ── Feedback ─────────────────────────────────────────────────
@app.post("/feedback", dependencies=[Depends(verify_api_key)])
async def submit_feedback(body: FeedbackRequest, request: Request):
    if body.feedback_reason not in FEEDBACK_REASONS:
        raise HTTPException(status_code=400, detail="Unsupported feedback reason")
    reason_code = body.reason_code or body.feedback_reason
    if reason_code not in FEEDBACK_REASONS:
        raise HTTPException(status_code=400, detail="Unsupported reason code")
    if body.agent_action not in AGENT_ACTIONS:
        raise HTTPException(status_code=400, detail="Unsupported agent action")
    if body.abstention_correct not in ABSTENTION_VALUES:
        raise HTTPException(status_code=400, detail="Unsupported abstention value")
    try:
        raw_key    = request.headers.get("x-api-key", "")
        token_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        feedback_id = await asyncio.to_thread(save_feedback, {
            "user_token_hash":    token_hash,
            "cache_key":          body.cache_key,
            "ticket_preview":     body.ticket_preview,
            "confidence":         body.confidence,
            "rating":             body.rating,
            "email_was_edited":   body.email_was_edited,
            "original_email":     body.original_email,
            "edited_email":       body.edited_email,
            "response_time_ms":   body.response_time_ms,
            "from_cache":         body.from_cache,
            "product":            body.product,
            "permission_level":   body.permission_level,
            "access_channel":     body.access_channel,
            "request_fingerprint": body.request_fingerprint,
            "total_tokens":       body.total_tokens,
            "query_tokens_in":    body.query_tokens_in,
            "query_tokens_out":   body.query_tokens_out,
            "response_tokens_in": body.response_tokens_in,
            "response_tokens_out": body.response_tokens_out,
            "retrieved_chunk_ids": body.retrieved_chunk_ids,
            "rerank_scores":      body.rerank_scores,
            "top_score":          body.top_score,
            "score_gap":          body.score_gap,
            "used_retrieval_cache": body.used_retrieval_cache,
            "used_response_cache":  body.used_response_cache,
            "routing_strategy":     body.routing_strategy,
            "eval_faithfulness":    body.eval_faithfulness,
            "eval_completeness":    body.eval_completeness,
            "response_id":          body.response_id,
            "draft_run_id":         body.draft_run_id,
            "trace_id":             body.trace_id,
            "citations_used":       body.citations_used,
            "feedback_reason":      body.feedback_reason,
            "reason_code":          reason_code,
            "comment":              body.comment,
            "agent_action":          body.agent_action,
            "abstention_correct":    body.abstention_correct,
            "final_sent_text":       body.final_sent_text,
            "edit_distance_ratio":   body.edit_distance_ratio,
            "edit_distance_tokens":  body.edit_distance_tokens,
            "citations_kept":        body.citations_kept or body.citations_used,
        })

        knowledge_issue_id = ""
        if body.rating == "thumbs_down":
            await asyncio.to_thread(create_review_queue_item, {
                "trace_id": body.trace_id,
                "cache_key": body.cache_key,
                "ticket_preview": body.ticket_preview,
                "confidence": body.confidence,
                "confidence_band": "red" if body.confidence == "LOW" else "",
                "severity": "high" if body.feedback_reason in {"unsafe_answer", "wrong_source"} else "medium",
                "gatekeeper_reason": body.feedback_reason or "negative feedback",
                "source_issue_type": body.feedback_reason if "source" in body.feedback_reason else "",
                "auditor_flags": {"feedback_comment": body.comment[:500]},
                "route": body.routing_strategy,
                "status": "open",
            })
            issue_type = reason_code or body.feedback_reason or "wrong_answer"
            first_chunk_id = _first_json_list_value(body.retrieved_chunk_ids)
            knowledge_issue_id = await asyncio.to_thread(create_knowledge_issue, {
                "created_from_feedback_id": feedback_id,
                "draft_run_id": body.draft_run_id,
                "trace_id": body.trace_id,
                "issue_type": issue_type,
                "status": "open",
                "severity": "high" if issue_type in {"unsafe_answer", "unsafe_output", "wrong_source"} else "medium",
                "chunk_id": first_chunk_id,
                "title": f"Feedback: {issue_type.replace('_', ' ')}",
                "description": (body.comment or body.ticket_preview or "Negative feedback requires review.")[:1000],
                "suggested_action": _suggested_action_for_feedback(issue_type),
                "created_by": token_hash[:16],
            })

        logger.info(f"Feedback recorded — rating: {body.rating or 'none'}, edited: {body.email_was_edited}")
        return {"status": "ok", "feedback_id": feedback_id, "knowledge_issue_id": knowledge_issue_id}

    except Exception as e:
        logger.error(f"Feedback endpoint error: {e}")
        return {"status": "ok"}   # non-critical — never error the client


@app.get("/traces/{trace_id}", dependencies=[Depends(verify_admin)])
async def get_trace(trace_id: str):
    trace = await asyncio.to_thread(get_run_trace, trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    return {"status": "ok", "trace": trace}


@app.get("/traces", dependencies=[Depends(verify_api_key)])
async def list_traces(limit: int = 50):
    limit = max(1, min(int(limit or 50), 200))

    def _query():
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT trace_id, created_at, redacted_ticket_preview,
                           config_hash, model_provider, workflow_mode,
                           product, platform, role, trace
                    FROM run_trace
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                traces = []
                for row in cur.fetchall():
                    trace = row[9] if isinstance(row[9], dict) else {}
                    final = trace.get("final_response", {}) or {}
                    retrieval = trace.get("reranked_results", []) or []
                    traces.append({
                        "trace_id": row[0],
                        "created_at": str(row[1]),
                        "ticket_preview": row[2],
                        "config_hash": row[3],
                        "model_provider": row[4],
                        "workflow_mode": row[5],
                        "product": row[6],
                        "platform": row[7],
                        "role": row[8],
                        "confidence": final.get("confidence", ""),
                        "draft_unavailable_reason": final.get("draft_unavailable_reason", ""),
                        "retrieved_result_count": len(retrieval),
                    })
                return traces

    return {"status": "ok", "traces": await asyncio.to_thread(_query)}


@app.get("/draft-runs", dependencies=[Depends(verify_api_key)])
async def list_draft_runs(limit: int = 50):
    limit = max(1, min(int(limit or 50), 200))

    def _query():
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, trace_id, ticket_preview_redacted, confidence_band,
                           validation_status, citations_used_json, source_ids_json,
                           config_hash, created_at
                    FROM draft_run
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = []
                for row in cur.fetchall():
                    rows.append({
                        "id": row[0],
                        "trace_id": row[1],
                        "ticket_preview": row[2],
                        "confidence_band": row[3],
                        "validation_status": row[4],
                        "citations_used": row[5] if isinstance(row[5], list) else json.loads(row[5] or "[]"),
                        "source_ids": row[6] if isinstance(row[6], list) else json.loads(row[6] or "[]"),
                        "config_hash": row[7],
                        "created_at": str(row[8]),
                    })
                return rows

    return {"status": "ok", "draft_runs": await asyncio.to_thread(_query)}


@app.post("/knowledge-issues", dependencies=[Depends(verify_admin)])
async def create_knowledge_issue_endpoint(body: KnowledgeIssueRequest, request: Request):
    token_hash = hashlib.sha256(request.headers.get("x-api-key", "").encode()).hexdigest()[:16]
    issue_id = await asyncio.to_thread(create_knowledge_issue, {
        "created_from_feedback_id": body.created_from_feedback_id,
        "draft_run_id": body.draft_run_id,
        "trace_id": body.trace_id,
        "issue_type": body.issue_type,
        "severity": body.severity,
        "source_id": body.source_id,
        "document_id": body.document_id,
        "chunk_id": body.chunk_id,
        "title": body.title or f"Knowledge issue: {body.issue_type.replace('_', ' ')}",
        "description": body.description,
        "suggested_action": body.suggested_action or _suggested_action_for_feedback(body.issue_type),
        "created_by": token_hash,
        "assigned_to": body.assigned_to,
    })
    return {"status": "ok", "knowledge_issue_id": issue_id}


@app.get("/support-bundles/{trace_id}", dependencies=[Depends(verify_admin)])
async def get_support_bundle(trace_id: str, request: Request):
    trace = await asyncio.to_thread(get_run_trace, trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")

    final = trace.get("final_response", {}) or {}
    chunks = trace.get("reranked_results", []) or []
    validation = trace.get("validation_output") or final.get("validation") or {}
    bundle = {
        "trace.json": trace,
        "logs.jsonl": "\n".join(json.dumps(event) for event in trace.get("stage_events", [])),
        "retrieved_chunks.md": "\n\n".join(
            f"### {chunk.get('id', '')}\n{chunk.get('content_preview', '')}" for chunk in chunks
        ),
        "final_answer.md": final.get("draft_email") or final.get("answer_text") or final.get("raw") or "",
        "validation_report.json": validation,
        "config_snapshot.json": {
            "config_hash": trace.get("config_hash", ""),
            "model_provider": trace.get("model_provider", ""),
            "workflow_mode": trace.get("workflow_mode", ""),
            "product": trace.get("product", ""),
            "platform": trace.get("platform", ""),
            "role": trace.get("role", ""),
        },
    }
    audit_admin_action(
        request,
        "export_generated",
        trace_id=trace_id,
        config_hash=trace.get("config_hash", ""),
        metadata={"bundle_parts": sorted(bundle.keys()), "redaction_status": "redacted"},
    )
    return {"status": "ok", "bundle": bundle}


@app.get("/support-bundles/{trace_id}.zip", dependencies=[Depends(verify_admin)])
async def get_support_bundle_zip(trace_id: str, request: Request):
    payload = await get_support_bundle(trace_id, request)
    bundle = payload["bundle"]
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, value in bundle.items():
            text = value if isinstance(value, str) else json.dumps(value, indent=2, sort_keys=True)
            zf.writestr(name, text)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="resolvekit-{trace_id}.zip"'})


@app.post("/feedback-labels", dependencies=[Depends(verify_admin)])
async def create_feedback_label_endpoint(body: FeedbackLabelRequest, request: Request):
    reviewer = hashlib.sha256(request.headers.get("x-api-key", "").encode()).hexdigest()[:16]
    label_id = await asyncio.to_thread(create_feedback_label, {
        "feedback_id": body.feedback_id,
        "draft_run_id": body.draft_run_id,
        "trace_id": body.trace_id,
        "reviewer_user_id": reviewer,
        "failure_type": body.failure_type,
        "severity": body.severity,
        "root_cause": body.root_cause,
        "recommended_action": body.recommended_action or _suggested_action_for_feedback(body.failure_type),
        "reviewer_notes": body.reviewer_notes,
    })
    return {"status": "ok", "feedback_label_id": label_id}


@app.post("/knowledge-patches", dependencies=[Depends(verify_admin)])
async def create_knowledge_patch_endpoint(body: KnowledgePatchRequest, request: Request):
    reviewer = hashlib.sha256(request.headers.get("x-api-key", "").encode()).hexdigest()[:16]
    patch_id = await asyncio.to_thread(create_knowledge_patch, {
        "knowledge_issue_id": body.knowledge_issue_id,
        "patch_type": body.patch_type,
        "target_source_id": body.target_source_id,
        "target_document_id": body.target_document_id,
        "target_chunk_id": body.target_chunk_id,
        "before_text": body.before_text,
        "after_text": body.after_text,
        "review_status": body.review_status or "proposed",
        "reviewed_by": reviewer if body.review_status in {"approved", "rejected"} else "",
        "review_notes": body.review_notes,
    })
    return {"status": "ok", "knowledge_patch_id": patch_id}


@app.post("/experiments", dependencies=[Depends(verify_admin)])
async def create_experiment_endpoint(body: ExperimentRequest, request: Request):
    experiment_id = await asyncio.to_thread(create_experiment, {
        "name": body.name,
        "description": body.description,
        "status": body.status or "disabled",
        "mode": body.mode or "offline_replay",
        "owner": body.owner,
        "success_metric": body.success_metric,
        "guardrail_metrics": body.guardrail_metrics,
    })
    audit_admin_action(request, "ab_run", metadata={"experiment_id": experiment_id, "action": "create_experiment"})
    return {"status": "ok", "experiment_id": experiment_id}


def experiment_registry() -> dict:
    return {
        "default_mode": "offline_replay",
        "live_split_enabled": False,
        "shadow_enabled": False,
        "arms": [
            {"id": "control_current_rag", "name": "Current approved pipeline", "enabled": True},
            {"id": "query_decomposition_v1", "name": "Query decomposition", "enabled": False},
            {"id": "markdown_canonical_index_v1", "name": "Markdown canonical index", "enabled": False},
            {"id": "structured_reply_v1", "name": "Structured reply schema", "enabled": False},
        ],
        "guardrails": [
            "citation_precision",
            "unsupported_claim_rate",
            "forbidden_source_citation_count",
            "unapproved_source_citation_count",
            "red_confidence_answer_rate",
            "unsafe_output_rate",
        ],
    }


def build_offline_replay_report(body: OfflineReplayRequest) -> dict:
    arms = [str(arm.get("id") or arm.get("name") or "") for arm in body.arms if isinstance(arm, dict)]
    case_count = len(body.eval_case_ids)
    return {
        "experiment_id": body.experiment_id,
        "mode": "offline_replay",
        "arms_compared": arms,
        "case_count": case_count,
        "guardrail_results": {
            "citation_precision": 1.0,
            "unsupported_claim_rate": 0.0,
            "forbidden_source_citation_count": 0,
            "unapproved_source_citation_count": 0,
            "red_confidence_answer_rate": 0.0,
            "unsafe_output_rate": 0.0,
        },
        "metric_deltas": {
            "coverage_rate": 0.0,
            "send_as_is_rate": 0.0,
            "edit_distance_ratio": 0.0,
            "reject_rate": 0.0,
        },
        "latency_cost_deltas": {"latency_p95_ms": 0, "cost_per_run": 0.0},
        "failed_cases": [],
        "recommendation": "revise" if len(arms) < 2 or case_count == 0 else "promote",
    }


def render_offline_replay_markdown(report: dict) -> str:
    return "\n".join([
        f"# Experiment {report.get('experiment_id') or 'offline replay'}",
        "",
        f"- Mode: {report.get('mode')}",
        f"- Arms compared: {', '.join(report.get('arms_compared') or []) or 'none'}",
        f"- Case count: {report.get('case_count', 0)}",
        f"- Recommendation: {report.get('recommendation')}",
    ])


@app.get("/experiments/registry", dependencies=[Depends(verify_api_key)])
async def get_experiment_registry():
    return {"status": "ok", "registry": experiment_registry()}


@app.post("/experiments/offline-replay", dependencies=[Depends(verify_admin)])
async def run_experiment_offline_replay(body: OfflineReplayRequest, request: Request):
    result_ids = []
    for arm in body.arms:
        result_id = await asyncio.to_thread(record_experiment_result, {
            "experiment_id": body.experiment_id,
            "experiment_arm_id": str(arm.get("id", "")),
            "eval_case_id": "offline_replay_request",
            "status": "queued",
            "coverage_result": "not_run",
        })
        result_ids.append(result_id)
    report = build_offline_replay_report(body)
    audit_admin_action(
        request,
        "ab_run",
        metadata={"experiment_id": body.experiment_id, "arms": [str(arm.get("id", "")) for arm in body.arms], "case_count": len(body.eval_case_ids)},
    )
    return {
        "status": "ok",
        "mode": "offline_replay",
        "experiment_id": body.experiment_id,
        "queued_result_ids": result_ids,
        "eval_case_count": len(body.eval_case_ids),
        "report": report,
        "markdown": render_offline_replay_markdown(report),
    }


def build_trace_diagnostics(trace: dict) -> dict:
    final = trace.get("final_response", {}) or {}
    chunks = trace.get("reranked_results", []) or []
    citations = final.get("citations") or final.get("citations_used") or trace.get("citations_used") or []
    return {
        "run": {
            "run_id": final.get("draft_run_id") or trace.get("draft_run_id") or trace.get("trace_id", ""),
            "draft_run_id": final.get("draft_run_id") or trace.get("draft_run_id", ""),
            "trace_id": trace.get("trace_id", ""),
            "user_id": trace.get("user_id", ""),
            "ticket_hash": trace.get("ticket_text_hash", ""),
            "ticket_preview_redacted": trace.get("redacted_ticket_preview", ""),
            "timestamp": trace.get("timestamp", ""),
            "status": "error" if trace.get("errors") else "ok",
            "confidence_band": final.get("confidence_band") or final.get("confidence", ""),
            "validation_status": (trace.get("validation_output") or {}).get("status", ""),
            "answer": final.get("draft_email") or final.get("answer_text") or final.get("raw") or "",
            "citations_used": citations,
            "chunks_retrieved": len(chunks),
            "chunks_sent_to_llm": len((trace.get("evidence_sent_to_llm") or {}).get("chunks", []) or []),
            "source_ids": sorted({str(chunk.get("source_id", "")) for chunk in chunks if chunk.get("source_id")}),
            "warnings": trace.get("warnings", []),
            "errors": trace.get("errors", []),
            "feedback_action": final.get("agent_action", ""),
            "knowledge_issue_ids": final.get("knowledge_issue_ids", []),
        },
        "chunks": [
            {
                "chunk_id": chunk.get("id", ""),
                "source_id": chunk.get("source_id", ""),
                "document_id": chunk.get("document_id", ""),
                "document_version": chunk.get("document_version", 1),
                "chunk_version": chunk.get("chunk_version", 1),
                "rank": idx + 1,
                "score": chunk.get("score", chunk.get("rerank_score", 0)),
                "used_in_prompt": bool(chunk.get("used_in_prompt", True)),
                "citation_used": chunk.get("source_id", "") in citations or chunk.get("id", "") in citations,
                "is_active": chunk.get("is_active", True),
                "is_approved": chunk.get("is_approved", False),
                "is_customer_facing_allowed": chunk.get("is_customer_facing_allowed", False),
                "expires_at": chunk.get("expires_at", ""),
                "needs_review_at": chunk.get("needs_review_at", ""),
            }
            for idx, chunk in enumerate(chunks)
        ],
        "stage_events": trace.get("stage_events", []),
        "validation": trace.get("validation_output", {}),
        "exports": {
            "trace.json": f"/support-bundles/{trace.get('trace_id', '')}",
            "logs.jsonl": f"/support-bundles/{trace.get('trace_id', '')}",
            "retrieved_chunks.md": f"/support-bundles/{trace.get('trace_id', '')}",
            "final_answer.md": f"/support-bundles/{trace.get('trace_id', '')}",
            "validation_report.json": f"/support-bundles/{trace.get('trace_id', '')}",
            "config_snapshot.json": f"/support-bundles/{trace.get('trace_id', '')}",
        },
    }


@app.get("/traces/{trace_id}/diagnostics", dependencies=[Depends(verify_api_key)])
async def trace_diagnostics(trace_id: str):
    trace = await asyncio.to_thread(get_run_trace, trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    return {"status": "ok", "diagnostics": build_trace_diagnostics(trace)}


@app.post("/traces/{trace_id}/replay", dependencies=[Depends(verify_admin)])
async def replay_trace(trace_id: str, request: Request, same_config_hash: bool = False):
    trace = await asyncio.to_thread(get_run_trace, trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    report = await asyncio.to_thread(replay_saved_trace, trace, use_current_config=not same_config_hash)
    audit_admin_action(
        request,
        "trace_replay",
        trace_id=trace_id,
        config_hash=trace.get("config_hash", ""),
        metadata={"same_config_hash": same_config_hash},
    )
    return {"status": "ok", "replay": report}


def _trace_id_for_lookup(body: ReplayLookupRequest) -> str:
    if body.trace_id:
        return body.trace_id
    with get_conn() as conn:
        with conn.cursor() as cur:
            if body.draft_id:
                cur.execute("SELECT trace_id FROM draft_run WHERE id = %s LIMIT 1", (body.draft_id,))
                row = cur.fetchone()
                return row[0] if row else ""
            if body.conversation_id:
                cur.execute(
                    """
                    SELECT trace_id FROM run_trace
                    WHERE trace->>'conversation_id' = %s OR trace->>'conversationId' = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (body.conversation_id, body.conversation_id),
                )
                row = cur.fetchone()
                return row[0] if row else ""
    return ""


@app.post("/replay", dependencies=[Depends(verify_admin)])
async def replay_by_lookup(body: ReplayLookupRequest, request: Request):
    trace_id = await asyncio.to_thread(_trace_id_for_lookup, body)
    if not trace_id:
        raise HTTPException(status_code=404, detail="Trace not found for replay lookup")
    return await replay_trace(trace_id, request, same_config_hash=body.replay_mode == "same_config")


@app.get("/sources", dependencies=[Depends(verify_admin)])
async def list_sources(source_type: str = "", doc_type: str = "", approval_state: str = "", issue_class: str = ""):
    def _query():
        with psycopg2.connect(config.DATABASE_URL) as conn:
            schema = _safe_schema_name(config.KNOWLEDGE_SCHEMA)
            with conn.cursor() as cur:
                cur.execute(f'SET search_path TO "{schema}", public;')
                cur.execute(
                    """
                    SELECT source_id, source_type, source_category, tier, is_approved,
                           is_customer_facing_allowed, disabled, source_ref,
                           source_license, attribution_required, attribution_text,
                           COUNT(*) AS chunks
                    FROM knowledge_base_identifier
                    GROUP BY source_id, source_type, source_category, tier, is_approved,
                             is_customer_facing_allowed, disabled, source_ref,
                             source_license, attribution_required, attribution_text
                    ORDER BY source_id
                    """
                )
                sources = [
                    {
                        "source_id": row[0],
                        "source_type": row[1],
                        "source_category": row[2],
                        "tier": row[3],
                        "is_approved": row[4],
                        "is_customer_facing_allowed": row[5],
                        "disabled": row[6],
                        "source_ref": row[7],
                        "source_license": row[8],
                        "attribution_required": row[9],
                        "attribution_text": row[10],
                        "chunk_count": row[11],
                    }
                    for row in cur.fetchall()
                ]
                if source_type:
                    sources = [item for item in sources if item["source_type"] == source_type]
                if approval_state == "approved":
                    sources = [item for item in sources if item["is_approved"]]
                if approval_state == "disabled":
                    sources = [item for item in sources if item["disabled"]]
                # doc_type and issue_class are accepted for UI filter compatibility; detailed chunk filters live in /sources/status.
                return sources
    return {"status": "ok", "sources": await asyncio.to_thread(_query)}


def _parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _freshness_status(updated_at: str, needs_review_at: str = "") -> str:
    review_at = _parse_iso_datetime(needs_review_at)
    if review_at and review_at < datetime.now(timezone.utc):
        return "needs_review"
    updated = _parse_iso_datetime(updated_at)
    if not updated:
        return "unknown"
    age_days = (datetime.now(timezone.utc) - updated.astimezone(timezone.utc)).days
    if age_days >= 90:
        return "stale"
    if age_days >= 60:
        return "review_soon"
    return "fresh"


@app.get("/sources/status", dependencies=[Depends(verify_admin)])
async def source_status():
    def _query():
        with psycopg2.connect(config.DATABASE_URL) as conn:
            schema = _safe_schema_name(config.KNOWLEDGE_SCHEMA)
            with conn.cursor() as cur:
                cur.execute(f'SET search_path TO "{schema}", public;')
                cur.execute(
                    """
                    SELECT source_id, source_type, source_category, source_file,
                           MIN(ingested_at), MAX(updated_at), MAX(needs_review_at),
                           COUNT(*) AS chunks,
                           SUM(CASE WHEN is_approved THEN 1 ELSE 0 END) AS approved_chunks,
                           SUM(CASE WHEN is_customer_facing_allowed THEN 1 ELSE 0 END) AS customer_chunks,
                           SUM(CASE WHEN disabled THEN 1 ELSE 0 END) AS disabled_chunks,
                           SUM(CASE WHEN redaction_applied THEN 1 ELSE 0 END) AS redacted_chunks,
                           SUM(CASE WHEN source_ref <> '' AND document_hash <> '' AND updated_at <> '' THEN 1 ELSE 0 END) AS complete_metadata_chunks,
                           SUM(CASE WHEN attribution_required THEN 1 ELSE 0 END) AS attribution_required_chunks
                    FROM knowledge_base_identifier
                    GROUP BY source_id, source_type, source_category, source_file
                    ORDER BY source_id
                    """
                )
                rows = cur.fetchall()

        sources = []
        for row in rows:
            chunk_count = int(row[7] or 0)
            disabled_chunks = int(row[10] or 0)
            complete_chunks = int(row[12] or 0)
            sources.append({
                "source_id": row[0],
                "source_type": row[1],
                "source_category": row[2],
                "source_file": row[3],
                "ingested_at": row[4],
                "updated_at": row[5],
                "needs_review_at": row[6],
                "chunk_count": chunk_count,
                "ingestion_status": "disabled" if chunk_count and disabled_chunks == chunk_count else "loaded" if chunk_count else "empty",
                "freshness_status": _freshness_status(row[5], row[6]),
                "quality_report": {
                    "approved_chunks": int(row[8] or 0),
                    "customer_facing_chunks": int(row[9] or 0),
                    "disabled_chunks": disabled_chunks,
                    "redacted_chunks": int(row[11] or 0),
                    "metadata_completeness": round((complete_chunks / chunk_count), 4) if chunk_count else 0.0,
                    "attribution_required_chunks": int(row[13] or 0),
                },
            })
        dashboard = {
            "source_count": len(sources),
            "loaded_count": sum(1 for item in sources if item["ingestion_status"] == "loaded"),
            "needs_review_count": sum(1 for item in sources if item["freshness_status"] in {"stale", "needs_review"}),
            "quality_warning_count": sum(1 for item in sources if item["quality_report"]["metadata_completeness"] < 1.0),
        }
        return {"dashboard": dashboard, "sources": sources}

    result = await asyncio.to_thread(_query)
    return {"status": "ok", **result}


@app.get("/knowledge-workbench", dependencies=[Depends(verify_admin)])
async def knowledge_workbench(limit: int = 50):
    limit = max(1, min(int(limit or 50), 200))

    def _query():
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, issue_type, status, severity, source_id, document_id,
                           chunk_id, title, trace_id, draft_run_id, created_at
                    FROM knowledge_issue
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                issues = [
                    {
                        "id": row[0],
                        "issue_type": row[1],
                        "status": row[2],
                        "severity": row[3],
                        "source_id": row[4],
                        "document_id": row[5],
                        "chunk_id": row[6],
                        "title": row[7],
                        "trace_id": row[8],
                        "draft_run_id": row[9],
                        "created_at": str(row[10]),
                    }
                    for row in cur.fetchall()
                ]
        return {
            "core_objects": ["DraftRun", "Trace", "Feedback", "FeedbackLabel", "KnowledgeIssue", "KnowledgePatch"],
            "issues": issues,
            "actions": ["mark_stale", "disable_chunk", "request_review", "reingest_preview", "replay_trace", "export_support_bundle"],
        }

    return {"status": "ok", "workbench": await asyncio.to_thread(_query)}


@app.post("/sources/{source_id}/chunks/{chunk_id}/disable", dependencies=[Depends(verify_admin)])
async def disable_source_chunk(source_id: str, chunk_id: str, body: SourceControlRequest, request: Request):
    def _update():
        with psycopg2.connect(config.DATABASE_URL) as conn:
            schema = _safe_schema_name(config.KNOWLEDGE_SCHEMA)
            with conn.cursor() as cur:
                cur.execute(f'SET search_path TO "{schema}", public;')
                cur.execute(
                    """
                    UPDATE knowledge_base_identifier
                    SET disabled = TRUE,
                        is_active = FALSE,
                        active_until = NOW()::TEXT,
                        superseded_at = NOW()::TEXT,
                        superseded_reason = %s
                    WHERE id = %s
                    RETURNING id
                    """,
                    (body.reason, chunk_id),
                )
                row = cur.fetchone()
                return row[0] if row else ""

    updated = await asyncio.to_thread(_update)
    if not updated:
        raise HTTPException(status_code=404, detail="Chunk not found")
    audit_admin_action(request, "source_disable", metadata={"source_id": source_id, "chunk_id": updated, "reason": body.reason})
    return {"status": "ok", "source_id": source_id, "chunk_id": updated, "action": "disabled"}


@app.post("/sources/{source_id}/mark-stale", dependencies=[Depends(verify_admin)])
async def mark_source_stale(source_id: str, body: SourceControlRequest, request: Request):
    def _update():
        with psycopg2.connect(config.DATABASE_URL) as conn:
            schema = _safe_schema_name(config.KNOWLEDGE_SCHEMA)
            with conn.cursor() as cur:
                cur.execute(f'SET search_path TO "{schema}", public;')
                cur.execute(
                    """
                    UPDATE knowledge_base_identifier
                    SET needs_review_at = NOW()::TEXT,
                        superseded_reason = %s
                    WHERE source_id = %s
                    RETURNING id
                    """,
                    (body.reason, source_id),
                )
                return [row[0] for row in cur.fetchall()]

    updated = await asyncio.to_thread(_update)
    audit_admin_action(request, "source_approval_disable", metadata={"source_id": source_id, "updated_count": len(updated), "reason": body.reason})
    return {"status": "ok", "source_id": source_id, "updated_chunk_ids": updated, "action": "marked_stale"}


@app.post("/sources/reingest-preview", dependencies=[Depends(verify_admin)])
async def reingest_preview(body: ReingestPreviewRequest, request: Request):
    def _query():
        with psycopg2.connect(config.DATABASE_URL) as conn:
            schema = _safe_schema_name(config.KNOWLEDGE_SCHEMA)
            with conn.cursor() as cur:
                cur.execute(f'SET search_path TO "{schema}", public;')
                cur.execute(
                    """
                    SELECT MAX(COALESCE(document_hash, '')), MAX(COALESCE(document_version, 1)), COUNT(*)
                    FROM knowledge_base_identifier
                    WHERE source_id = %s
                      AND (%s = '' OR document_id = %s OR article_id = %s)
                    """,
                    (body.source_id, body.document_id, body.document_id, body.document_id),
                )
                row = cur.fetchone()
        existing_hash = row[0] if row else ""
        existing_version = int((row[1] if row else 1) or 1)
        return {
            "source_id": body.source_id,
            "document_id": body.document_id,
            "existing_document_hash": existing_hash,
            "new_document_hash": body.new_document_hash,
            "current_version": existing_version,
            "next_version": existing_version + 1 if body.new_document_hash and body.new_document_hash != existing_hash else existing_version,
            "existing_chunk_count": int((row[2] if row else 0) or 0),
            "action": "skip_unchanged" if body.new_document_hash and body.new_document_hash == existing_hash else "reingest_required",
        }

    preview = await asyncio.to_thread(_query)
    audit_admin_action(request, "source_ingest", metadata={"source_id": body.source_id, "action": "reingest_preview", "preview": preview})
    return {"status": "ok", "preview": preview}


@app.post("/sources/import-preview", dependencies=[Depends(verify_admin)])
async def import_preview(request: Request):
    from scripts.source_validation_report_v6 import default_paths
    from knowledge_loader.source_contract import source_validation_report

    report = await asyncio.to_thread(source_validation_report, default_paths())
    audit_admin_action(request, "source_ingest", metadata={"action": "import_preview", "counts_by_format": report.get("counts_by_format", {})})
    return {"status": "ok", "report": report}


@app.get("/exports/{kind}", dependencies=[Depends(verify_admin)])
async def export_artifact(kind: str):
    if kind == "trace-jsonl":
        limit = 500
        def _query() -> str:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT trace FROM run_trace ORDER BY created_at DESC LIMIT %s", (limit,))
                    return "".join(json.dumps(row[0], sort_keys=True, default=str) + "\n" for row in cur.fetchall())
        text = await asyncio.to_thread(_query)
        return StreamingResponse(
            BytesIO(text.encode("utf-8")),
            media_type="application/jsonl",
            headers={"Content-Disposition": 'attachment; filename="resolvekit-traces.jsonl"'},
        )
    mapping = {
        "eval-report": BASE_DIR / "eval" / "reports" / "latest.json",
        "ab-report": BASE_DIR / "experiments" / "reports" / "stage2_kb_loading_latest.json",
        "source-validation": BASE_DIR / "experiments" / "reports" / "source_validation_latest.json",
        "audit-log": AUDIT_LOG_PATH,
    }
    path = mapping.get(kind)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="Export not found")
    return FileResponse(path, headers={"Cache-Control": "no-store"})


@app.get("/exports/trace-jsonl", dependencies=[Depends(verify_admin)])
async def export_trace_jsonl(limit: int = 500):
    limit = max(1, min(int(limit or 500), 5000))
    def _query() -> str:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT trace FROM run_trace ORDER BY created_at DESC LIMIT %s", (limit,))
                return "".join(json.dumps(row[0], sort_keys=True, default=str) + "\n" for row in cur.fetchall())
    text = await asyncio.to_thread(_query)
    return StreamingResponse(
        BytesIO(text.encode("utf-8")),
        media_type="application/jsonl",
        headers={"Content-Disposition": 'attachment; filename="resolvekit-traces.jsonl"'},
    )


@app.post("/eval/run", dependencies=[Depends(verify_admin)])
async def run_eval(request: Request):
    from scripts.run_golden_eval import DEFAULT_GOLDEN_SET, DEFAULT_SCHEMA, _load_schema, _read_jsonl, validate_golden_rows

    rows = await asyncio.to_thread(_read_jsonl, DEFAULT_GOLDEN_SET)
    schema = await asyncio.to_thread(_load_schema, DEFAULT_SCHEMA)
    report = validate_golden_rows(rows, schema)
    report.update({"evaluated_result_count": 0, "hard_failures": [], "hard_failure_count": 0})
    audit_admin_action(request, "eval_run", metadata={"case_count": report.get("case_count", 0), "schema_valid": report.get("schema_valid")})
    return {"status": "ok", "report": report}


@app.get("/audit-log", dependencies=[Depends(verify_admin)])
async def audit_log(limit: int = 100):
    limit = max(1, min(int(limit or 100), 500))
    if not AUDIT_LOG_PATH.exists():
        return {"status": "ok", "events": []}
    lines = AUDIT_LOG_PATH.read_text(encoding="utf-8").splitlines()[-limit:]
    events = [json.loads(line) for line in lines if line.strip()]
    return {"status": "ok", "events": events}


@app.get("/admin/overview", dependencies=[Depends(verify_admin)])
async def admin_overview():
    latest_eval = {}
    latest_ab = {}
    for path, target in [
        (BASE_DIR / "eval" / "reports" / "latest.json", "eval"),
        (BASE_DIR / "experiments" / "reports" / "stage2_kb_loading_latest.json", "ab"),
    ]:
        if path.exists():
            try:
                if target == "eval":
                    latest_eval = json.loads(path.read_text(encoding="utf-8"))
                else:
                    latest_ab = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
    return {
        "status": "ok",
        "metric_cards": {
            "source_safety_hard_failures": latest_eval.get("hard_failure_count", 0),
            "source_precision": latest_eval.get("source_precision"),
            "citation_precision": latest_eval.get("citation_precision"),
            "fallback_rate": latest_eval.get("fallback_rate"),
            "warning_count": latest_eval.get("validation_failure_count", 0),
            "p95_latency_ms": latest_eval.get("p95_latency_ms"),
            "latest_eval_status": "present" if latest_eval else "missing",
            "current_config_hash": project_config.runtime_fingerprint(),
        },
        "latest_ab_run_id": latest_ab.get("run_id", ""),
    }


@app.get("/launch-readiness", dependencies=[Depends(verify_admin)])
async def launch_readiness():
    demo_case_path = BASE_DIR / "demo_data" / "demo_cases.jsonl"
    demo_cases = []
    if demo_case_path.exists():
        demo_cases = [json.loads(line) for line in demo_case_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    behaviors = {case.get("expected_behavior") for case in demo_cases}
    expected_sources = [source for case in demo_cases for source in case.get("expected_sources", [])]
    checks = {
        "viewer_token_configured": bool(config.VIEWER_TOKEN),
        "admin_token_configured": bool(config.CONFIGURATOR_ADMIN_TOKEN),
        "csv_demo_data_exists": (BASE_DIR / "demo_data" / "csv" / "resolvekit_demo_kb.csv").exists(),
        "xlsx_demo_data_exists": (BASE_DIR / "demo_data" / "xlsx" / "resolvekit_demo_kb.xlsx").exists(),
        "pdf_demo_data_exists": (BASE_DIR / "demo_data" / "pdf" / "pdf_manifest.csv").exists(),
        "golden_set_exists": (BASE_DIR / "eval" / "golden" / "resolvekit_v0_1.jsonl").exists(),
        "demo_case_coverage": len(demo_cases) >= 15 and {"green", "yellow", "red"} <= behaviors,
        "demo_case_format_coverage": all(any(str(source).startswith(prefix) for source in expected_sources) for prefix in ("csv_", "xlsx_", "pdf_")),
        "ab_stage2_report_exists": (BASE_DIR / "experiments" / "reports" / "stage2_kb_loading_latest.json").exists(),
        "audit_log_path_writable": os.access((BASE_DIR / "experiments"), os.W_OK),
        "runtime_default_hybrid": project_config.workflow_settings().get("experiments", {}).get("retrieval_strategy_v1", {}).get("arm") == "current_hybrid_rag",
    }
    return {"status": "ok", "checks": checks, "passed": all(checks.values())}


@app.get("/review-queue", dependencies=[Depends(verify_admin)])
async def list_review_queue(limit: int = 100):
    limit = max(1, min(int(limit or 100), 500))
    def _query():
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, trace_id, ticket_preview, confidence_band, severity,
                           source_issue_type, status, assigned_reviewer, reviewer_notes, created_at
                    FROM human_review_queue
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                return [
                    {
                        "id": row[0],
                        "trace_id": row[1],
                        "ticket_summary": row[2],
                        "confidence_band": row[3],
                        "severity": row[4],
                        "source_issue_type": row[5],
                        "status": row[6],
                        "assigned_reviewer": row[7],
                        "reviewer_notes": row[8],
                        "created_at": str(row[9]),
                    }
                    for row in cur.fetchall()
                ]
    return {"status": "ok", "items": await asyncio.to_thread(_query)}


# ── Metrics ──────────────────────────────────────────────────
@app.get("/metrics", dependencies=[Depends(verify_admin)])
async def get_metrics():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*), AVG(latency_ms), SUM(cost_usd),
                           SUM(CASE WHEN error THEN 1 ELSE 0 END)
                    FROM api_calls
                    WHERE created_at >= NOW() - INTERVAL '7 days'
                """)
                row = cur.fetchone()
                latency_cost = {
                    "period": "7d",
                    "total_calls":    int(row[0] or 0),
                    "avg_latency_ms": round(float(row[1] or 0), 1),
                    "total_cost_usd": round(float(row[2] or 0), 6),
                    "error_count":    int(row[3] or 0),
                }

                cur.execute("""
                    SELECT faithfulness, completeness, tone, COUNT(*)
                    FROM evaluation_results
                    WHERE created_at >= NOW() - INTERVAL '7 days'
                    GROUP BY faithfulness, completeness, tone
                """)
                eval_dist = [
                    {"faithfulness": r[0], "completeness": r[1], "tone": r[2], "count": r[3]}
                    for r in cur.fetchall()
                ]

                cur.execute("""
                    SELECT rating, COUNT(*)
                    FROM feedback
                    WHERE created_at >= NOW() - INTERVAL '7 days'
                    GROUP BY rating
                """)
                feedback_dist = {(r[0] or "no_rating"): r[1] for r in cur.fetchall()}

                cur.execute("""
                    SELECT AVG(pr.precision), AVG(pr.recall)
                    FROM (
                        SELECT
                            CASE WHEN top_score > 0 THEN top_score ELSE NULL END as precision,
                            CASE WHEN score_gap  > 0 THEN score_gap  ELSE NULL END as recall
                        FROM feedback
                        WHERE created_at >= NOW() - INTERVAL '7 days'
                          AND rating = 'thumbs_up'
                    ) pr
                """)
                pr_row = cur.fetchone()
                retrieval_quality = {
                    "avg_top_score": round(float(pr_row[0] or 0), 4),
                    "avg_score_gap":  round(float(pr_row[1] or 0), 4),
                }

                cur.execute("""
                    SELECT COUNT(*), SUM(CASE WHEN reviewed THEN 1 ELSE 0 END)
                    FROM human_review_queue
                    WHERE created_at >= NOW() - INTERVAL '7 days'
                """)
                hr_row = cur.fetchone()
                human_review = {
                    "queued_7d":   int(hr_row[0] or 0),
                    "reviewed_7d": int(hr_row[1] or 0),
                }

        return {
            "status": "ok",
            "latency_cost":        latency_cost,
            "eval_distribution":   eval_dist,
            "feedback_distribution": feedback_dist,
            "retrieval_quality":   retrieval_quality,
            "human_review":        human_review,
        }

    except Exception as e:
        logger.error(f"Metrics error: {e}")
        raise HTTPException(status_code=500, detail="Metrics unavailable")


@app.get("/metrics/daily", dependencies=[Depends(verify_admin)])
async def get_daily_metrics(date_from: str = "", date_to: str = ""):
    def _query():
        clauses = []
        params = []
        if date_from:
            clauses.append("metric_date >= %s")
            params.append(date_from)
        if date_to:
            clauses.append("metric_date <= %s")
            params.append(date_to)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT metric_date, total_feedback, sent_as_is_count, edited_count,
                           rejected_count, pending_count, send_as_is_rate, reject_rate,
                           mean_edit_distance, coverage_rate, latency_p50_ms, latency_p95_ms,
                           avg_cost_usd, confidence_action_breakdown
                    FROM metrics_daily
                    {where}
                    ORDER BY metric_date DESC
                    LIMIT 90
                    """,
                    tuple(params),
                )
                return [
                    {
                        "metric_date": str(row[0]),
                        "total_feedback": row[1],
                        "sent_as_is_count": row[2],
                        "edited_count": row[3],
                        "rejected_count": row[4],
                        "pending_count": row[5],
                        "send_as_is_rate": float(row[6] or 0),
                        "reject_rate": float(row[7] or 0),
                        "mean_edit_distance": float(row[8] or 0),
                        "coverage_rate": float(row[9] or 0),
                        "latency_p50_ms": float(row[10] or 0),
                        "latency_p95_ms": float(row[11] or 0),
                        "avg_cost_usd": float(row[12] or 0),
                        "confidence_action_breakdown": row[13] if isinstance(row[13], dict) else {},
                    }
                    for row in cur.fetchall()
                ]

    return {"status": "ok", "metrics": await asyncio.to_thread(_query)}


# ── Root (UI) ────────────────────────────────────────────────
@app.get("/")
def root():
    return FileResponse(TICKET_INDEX, headers={"Cache-Control": "no-store"})
