import re


def _safe_schema_name(schema: str) -> str:
    schema = (schema or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise ValueError(f"Unsafe schema name: {schema!r}")
    return schema


def _set_search_path(cursor, schema: str) -> None:
    schema = _safe_schema_name(schema)
    cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}";')
    cursor.execute(f'SET search_path TO "{schema}", public;')


# ── Vector DB Schema (DATABASE_URL, knowledge schema) ─────────
CREATE_EXTENSION = """
CREATE EXTENSION IF NOT EXISTS vector;
"""

CREATE_KNOWLEDGE_BASE = """
CREATE TABLE IF NOT EXISTS knowledge_base (
    id          TEXT PRIMARY KEY,
    embedding   vector(384),
    product     TEXT NOT NULL DEFAULT '',
    platform    TEXT NOT NULL DEFAULT '',
    doc_type    TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMP DEFAULT NOW()
);
"""

CREATE_KNOWLEDGE_BASE_INDEX = """
CREATE INDEX IF NOT EXISTS knowledge_base_embedding_idx
ON knowledge_base
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);
"""

ANALYZE_KNOWLEDGE_BASE = """
ANALYZE knowledge_base;
"""

# Migrations — safe to re-run on existing installs
ALTER_KB_ADD_PRODUCT  = "ALTER TABLE knowledge_base ADD COLUMN IF NOT EXISTS product  TEXT NOT NULL DEFAULT '';"
ALTER_KB_ADD_PLATFORM = "ALTER TABLE knowledge_base ADD COLUMN IF NOT EXISTS platform TEXT NOT NULL DEFAULT '';"
ALTER_KB_ADD_DOC_TYPE = "ALTER TABLE knowledge_base ADD COLUMN IF NOT EXISTS doc_type TEXT NOT NULL DEFAULT '';"

# Retrieval metadata lives in DATABASE_URL because retriever queries JOIN it.
CREATE_KB_IDENTIFIER = """
CREATE TABLE IF NOT EXISTS knowledge_base_identifier (
    id           TEXT PRIMARY KEY,
    title        TEXT,
    url_name     TEXT,
    url          TEXT,
    content      TEXT,
    embedding_text TEXT DEFAULT '',
    display_text TEXT DEFAULT '',
    article_id   TEXT DEFAULT '',
    chunk_index  INTEGER,
    total_chunks INTEGER,
    source_file  TEXT,
    summary      TEXT DEFAULT '',
    keywords     TEXT DEFAULT '',
    questions    TEXT DEFAULT '',
    chunk_type   TEXT DEFAULT 'concept',
    parent_section_id TEXT DEFAULT '',
    heading_path TEXT DEFAULT '',
    source_type  TEXT DEFAULT 'knowledge_base',
    source_id    TEXT DEFAULT '',
    document_id  TEXT DEFAULT '',
    document_version INTEGER DEFAULT 1,
    chunk_version INTEGER DEFAULT 1,
    is_approved  BOOLEAN DEFAULT FALSE,
    tier         TEXT DEFAULT '',
    source_ref   TEXT DEFAULT '',
    lineage_ref  TEXT DEFAULT '',
    reviewed_by  TEXT DEFAULT '',
    approved_at  TEXT DEFAULT '',
    expires_at   TEXT DEFAULT '',
    needs_review_at TEXT DEFAULT '',
    audience_allowed TEXT DEFAULT '[]',
    source_category TEXT DEFAULT '',
    is_customer_facing_allowed BOOLEAN DEFAULT FALSE,
    is_internal_only BOOLEAN DEFAULT FALSE,
    is_future_only BOOLEAN DEFAULT FALSE,
    source_url   TEXT DEFAULT '',
    document_hash TEXT DEFAULT '',
    chunk_hash   TEXT DEFAULT '',
    is_active BOOLEAN DEFAULT TRUE,
    superseded_by_chunk_id TEXT DEFAULT '',
    superseded_at TEXT DEFAULT '',
    superseded_reason TEXT DEFAULT '',
    active_from TEXT DEFAULT '',
    active_until TEXT DEFAULT '',
    updated_at   TEXT DEFAULT '',
    redaction_status TEXT DEFAULT '',
    redaction_applied BOOLEAN DEFAULT FALSE,
    ingested_at  TEXT DEFAULT '',
    loader_version TEXT DEFAULT '',
    config_hash TEXT DEFAULT '',
    disabled BOOLEAN DEFAULT FALSE,
    source_authority NUMERIC DEFAULT 1.0,
    condition_flags TEXT DEFAULT '[]',
    source_license TEXT DEFAULT '',
    attribution_required BOOLEAN DEFAULT FALSE,
    attribution_text TEXT DEFAULT '',
    created_at   TIMESTAMP DEFAULT NOW()
);
"""

CREATE_KB_IDENTIFIER_INDEX = """
CREATE INDEX IF NOT EXISTS kb_identifier_id_idx
ON knowledge_base_identifier (id);
"""

CREATE_ARTICLE_SECTION = """
CREATE TABLE IF NOT EXISTS article_section (
    id           TEXT PRIMARY KEY,
    article_id   TEXT NOT NULL,
    title        TEXT,
    heading_path TEXT,
    section_text TEXT NOT NULL,
    product      TEXT,
    platform     TEXT,
    doc_type     TEXT,
    source_type  TEXT,
    url          TEXT,
    source_file  TEXT,
    updated_at   TIMESTAMP DEFAULT NOW()
);
"""

# ── Operational Schema (DATABASE_URL, ops schema) ─────────────

CREATE_RESPONSE_CACHE = """
CREATE TABLE IF NOT EXISTS response_cache (
    key           TEXT PRIMARY KEY,
    response      JSONB,
    provider      TEXT        DEFAULT '',
    created_at    TIMESTAMP   DEFAULT NOW()
);
"""

CREATE_RETRIEVAL_CACHE = """
CREATE TABLE IF NOT EXISTS retrieval_cache (
    key           TEXT PRIMARY KEY,
    chunks        JSONB,
    query_text    TEXT        DEFAULT '',
    chunk_count   INTEGER     DEFAULT 0,
    created_at    TIMESTAMP   DEFAULT NOW()
);
"""

CREATE_FEEDBACK_TABLE = """
CREATE TABLE IF NOT EXISTS feedback (
    id                   SERIAL      PRIMARY KEY,
    draft_run_id         TEXT        DEFAULT '',
    reason_code          TEXT        DEFAULT '',
    abstention_correct   TEXT        DEFAULT 'not_applicable',
    user_token_hash      TEXT        NOT NULL,
    user_id              TEXT        DEFAULT '',
    team_id              TEXT        DEFAULT '',
    session_id           TEXT        DEFAULT '',
    cache_key            TEXT,
    ticket_preview       TEXT,
    confidence           TEXT,
    rating               TEXT,
    email_was_edited     BOOLEAN     DEFAULT FALSE,
    original_email       TEXT,
    edited_email         TEXT,
    response_time_ms     INTEGER     DEFAULT 0,
    from_cache           BOOLEAN     DEFAULT FALSE,
    product              TEXT,
    permission_level     TEXT,
    access_channel       TEXT,
    request_fingerprint  TEXT,
    total_tokens         INTEGER     DEFAULT 0,
    query_tokens_in      INTEGER     DEFAULT 0,
    query_tokens_out     INTEGER     DEFAULT 0,
    response_tokens_in   INTEGER     DEFAULT 0,
    response_tokens_out  INTEGER     DEFAULT 0,
    retrieved_chunk_ids  TEXT        DEFAULT '[]',
    rerank_scores        TEXT        DEFAULT '[]',
    top_score            FLOAT       DEFAULT 0,
    score_gap            FLOAT       DEFAULT 0,
    used_retrieval_cache BOOLEAN     DEFAULT FALSE,
    used_response_cache  BOOLEAN     DEFAULT FALSE,
    routing_strategy     TEXT,
    eval_faithfulness    TEXT,
    eval_completeness    TEXT,
    response_id          TEXT        DEFAULT '',
    trace_id             TEXT        DEFAULT '',
    citations_used       TEXT        DEFAULT '[]',
    feedback_reason      TEXT        DEFAULT '',
    comment              TEXT        DEFAULT '',
    agent_action         TEXT        DEFAULT 'pending',
    final_sent_text      TEXT        DEFAULT '',
    edit_distance_ratio  FLOAT       DEFAULT 0,
    edit_distance_tokens INTEGER     DEFAULT 0,
    citations_kept       TEXT        DEFAULT '[]',
    created_at           TIMESTAMP   DEFAULT NOW()
);
"""

CREATE_DRAFT_RUN_TABLE = """
CREATE TABLE IF NOT EXISTS draft_run (
    id                      TEXT      PRIMARY KEY,
    trace_id                TEXT      NOT NULL,
    user_id                 TEXT      DEFAULT '',
    ticket_hash             TEXT      DEFAULT '',
    ticket_preview_redacted TEXT      DEFAULT '',
    final_draft             TEXT      DEFAULT '',
    confidence_band         TEXT      DEFAULT '',
    confidence_score        REAL      DEFAULT 0,
    validation_status       TEXT      DEFAULT '',
    citations_used_json     JSONB     DEFAULT '[]'::jsonb,
    source_ids_json         JSONB     DEFAULT '[]'::jsonb,
    config_hash             TEXT      DEFAULT '',
    experiment_id           TEXT      DEFAULT '',
    experiment_arm          TEXT      DEFAULT '',
    experiment_mode         TEXT      DEFAULT '',
    variant_config_hash     TEXT      DEFAULT '',
    source_version_set      JSONB     DEFAULT '{}'::jsonb,
    assigned_at             TIMESTAMP,
    assignment_reason       TEXT      DEFAULT '',
    schema_version          TEXT      DEFAULT 'v1',
    created_at              TIMESTAMP DEFAULT NOW(),
    retention_until         TIMESTAMP
);
"""

CREATE_FEEDBACK_LABEL_TABLE = """
CREATE TABLE IF NOT EXISTS feedback_label (
    id                 TEXT      PRIMARY KEY,
    feedback_id        TEXT      DEFAULT '',
    draft_run_id       TEXT      DEFAULT '',
    trace_id           TEXT      DEFAULT '',
    reviewer_user_id   TEXT      DEFAULT '',
    failure_type       TEXT      DEFAULT '',
    severity           TEXT      DEFAULT 'medium',
    root_cause         TEXT      DEFAULT 'unknown',
    recommended_action TEXT      DEFAULT '',
    reviewer_notes     TEXT      DEFAULT '',
    created_at         TIMESTAMP DEFAULT NOW()
);
"""

CREATE_KNOWLEDGE_ISSUE_TABLE = """
CREATE TABLE IF NOT EXISTS knowledge_issue (
    id                       TEXT      PRIMARY KEY,
    created_from_feedback_id TEXT      DEFAULT '',
    draft_run_id             TEXT      DEFAULT '',
    trace_id                 TEXT      DEFAULT '',
    issue_type               TEXT      DEFAULT '',
    status                   TEXT      DEFAULT 'open',
    severity                 TEXT      DEFAULT 'medium',
    source_id                TEXT      DEFAULT '',
    document_id              TEXT      DEFAULT '',
    chunk_id                 TEXT      DEFAULT '',
    title                    TEXT      DEFAULT '',
    description              TEXT      DEFAULT '',
    suggested_action         TEXT      DEFAULT '',
    created_by               TEXT      DEFAULT '',
    assigned_to              TEXT      DEFAULT '',
    created_at               TIMESTAMP DEFAULT NOW(),
    updated_at               TIMESTAMP DEFAULT NOW(),
    resolved_at              TIMESTAMP
);
"""

CREATE_KNOWLEDGE_PATCH_TABLE = """
CREATE TABLE IF NOT EXISTS knowledge_patch (
    id                 TEXT      PRIMARY KEY,
    knowledge_issue_id TEXT      DEFAULT '',
    patch_type         TEXT      DEFAULT '',
    target_source_id   TEXT      DEFAULT '',
    target_document_id TEXT      DEFAULT '',
    target_chunk_id    TEXT      DEFAULT '',
    before_text        TEXT      DEFAULT '',
    after_text         TEXT      DEFAULT '',
    review_status      TEXT      DEFAULT 'proposed',
    reviewed_by        TEXT      DEFAULT '',
    review_notes       TEXT      DEFAULT '',
    expires_at         TIMESTAMP,
    applied_at         TIMESTAMP,
    created_at         TIMESTAMP DEFAULT NOW()
);
"""

CREATE_EXPERIMENT_TABLE = """
CREATE TABLE IF NOT EXISTS experiment (
    id                     TEXT      PRIMARY KEY,
    name                   TEXT      NOT NULL,
    description            TEXT      DEFAULT '',
    status                 TEXT      DEFAULT 'disabled',
    mode                   TEXT      DEFAULT 'offline_replay',
    owner                  TEXT      DEFAULT '',
    start_at               TIMESTAMP,
    end_at                 TIMESTAMP,
    success_metric         TEXT      DEFAULT '',
    guardrail_metrics_json JSONB     DEFAULT '{}'::jsonb,
    created_at             TIMESTAMP DEFAULT NOW()
);
"""

CREATE_EXPERIMENT_ARM_TABLE = """
CREATE TABLE IF NOT EXISTS experiment_arm (
    id                    TEXT      PRIMARY KEY,
    experiment_id          TEXT      NOT NULL,
    name                  TEXT      NOT NULL,
    description           TEXT      DEFAULT '',
    config_overrides_json JSONB     DEFAULT '{}'::jsonb,
    pipeline_variant      TEXT      DEFAULT '',
    traffic_percentage    REAL      DEFAULT 0,
    created_at            TIMESTAMP DEFAULT NOW()
);
"""

CREATE_EXPERIMENT_RESULT_TABLE = """
CREATE TABLE IF NOT EXISTS experiment_result (
    id                    TEXT      PRIMARY KEY,
    experiment_id          TEXT      DEFAULT '',
    experiment_arm_id      TEXT      DEFAULT '',
    draft_run_id           TEXT      DEFAULT '',
    trace_id               TEXT      DEFAULT '',
    eval_case_id           TEXT      DEFAULT '',
    status                 TEXT      DEFAULT '',
    confidence_band        TEXT      DEFAULT '',
    validation_status      TEXT      DEFAULT '',
    citation_precision     REAL      DEFAULT 0,
    faithfulness_score     REAL      DEFAULT 0,
    coverage_result        TEXT      DEFAULT '',
    latency_ms             INTEGER   DEFAULT 0,
    estimated_cost         NUMERIC(14, 10) DEFAULT 0,
    feedback_agent_action  TEXT      DEFAULT '',
    edit_distance_ratio    REAL      DEFAULT 0,
    reviewer_label         TEXT      DEFAULT '',
    created_at             TIMESTAMP DEFAULT NOW()
);
"""

CREATE_METRICS_DAILY = """
CREATE TABLE IF NOT EXISTS metrics_daily (
    metric_date             DATE        PRIMARY KEY,
    total_feedback          INTEGER     DEFAULT 0,
    sent_as_is_count        INTEGER     DEFAULT 0,
    edited_count            INTEGER     DEFAULT 0,
    rejected_count          INTEGER     DEFAULT 0,
    pending_count           INTEGER     DEFAULT 0,
    send_as_is_rate         FLOAT       DEFAULT 0,
    reject_rate             FLOAT       DEFAULT 0,
    mean_edit_distance      FLOAT       DEFAULT 0,
    coverage_rate           FLOAT       DEFAULT 0,
    latency_p50_ms          FLOAT       DEFAULT 0,
    latency_p95_ms          FLOAT       DEFAULT 0,
    avg_cost_usd            NUMERIC(14, 10) DEFAULT 0,
    confidence_action_breakdown JSONB   DEFAULT '{}'::jsonb,
    created_at              TIMESTAMP   DEFAULT NOW(),
    updated_at              TIMESTAMP   DEFAULT NOW()
);
"""

CREATE_API_CALLS_TABLE = """
CREATE TABLE IF NOT EXISTS api_calls (
    id            SERIAL          PRIMARY KEY,
    model         TEXT            NOT NULL,
    endpoint      TEXT            NOT NULL,
    provider      TEXT            DEFAULT '',
    step          TEXT            DEFAULT '',
    trace_id      TEXT            DEFAULT '',
    draft_run_id  TEXT            DEFAULT '',
    user_id       TEXT            DEFAULT '',
    team_id       TEXT            DEFAULT '',
    session_id    TEXT            DEFAULT '',
    tokens_in     INTEGER         DEFAULT 0,
    tokens_out    INTEGER         DEFAULT 0,
    latency_ms    INTEGER         DEFAULT 0,
    cost_usd      NUMERIC(14, 10) DEFAULT 0,
    error         BOOLEAN         DEFAULT FALSE,
    error_message TEXT            DEFAULT '',
    created_at    TIMESTAMP       DEFAULT NOW()
);
"""

CREATE_ANALYTICS_EVENT_TABLE = """
CREATE TABLE IF NOT EXISTS analytics_event (
    id            TEXT      PRIMARY KEY,
    event_type    TEXT      NOT NULL,
    trace_id      TEXT      DEFAULT '',
    draft_run_id  TEXT      DEFAULT '',
    user_id       TEXT      DEFAULT '',
    team_id       TEXT      DEFAULT '',
    session_id    TEXT      DEFAULT '',
    product       TEXT      DEFAULT '',
    issue_category TEXT     DEFAULT '',
    source_id     TEXT      DEFAULT '',
    chunk_id      TEXT      DEFAULT '',
    metadata      JSONB     DEFAULT '{}'::jsonb,
    created_at    TIMESTAMP DEFAULT NOW()
);
"""

CREATE_EVALUATION_RESULTS = """
CREATE TABLE IF NOT EXISTS evaluation_results (
    id               SERIAL    PRIMARY KEY,
    cache_key        TEXT      UNIQUE NOT NULL,
    faithfulness     TEXT,
    completeness     TEXT,
    tone             TEXT,
    flags            TEXT,
    summary          TEXT,
    eval_tokens_in   INTEGER   DEFAULT 0,
    eval_tokens_out  INTEGER   DEFAULT 0,
    retry_triggered  BOOLEAN   DEFAULT FALSE,
    product          TEXT      DEFAULT '',
    access_channel   TEXT      DEFAULT '',
    created_at       TIMESTAMP DEFAULT NOW()
);
"""

CREATE_HUMAN_REVIEW_QUEUE = """
CREATE TABLE IF NOT EXISTS human_review_queue (
    id                SERIAL    PRIMARY KEY,
    trace_id          TEXT      DEFAULT '',
    cache_key         TEXT,
    ticket_preview    TEXT,
    full_ticket       TEXT,
    confidence        TEXT,
    confidence_band   TEXT      DEFAULT '',
    severity          TEXT      DEFAULT 'medium',
    age_started_at    TIMESTAMP DEFAULT NOW(),
    sla_marker        TEXT      DEFAULT '',
    gatekeeper_reason TEXT,
    source_issue_type TEXT      DEFAULT '',
    auditor_flags     JSONB,
    needs_escalation  BOOLEAN   DEFAULT FALSE,
    escalation_reason TEXT,
    route             TEXT,
    assigned_reviewer TEXT      DEFAULT '',
    status            TEXT      DEFAULT 'open',
    reviewed          BOOLEAN   DEFAULT FALSE,
    reviewer_notes    TEXT      DEFAULT '',
    created_at        TIMESTAMP DEFAULT NOW()
);
"""

CREATE_RUN_TRACE_TABLE = """
CREATE TABLE IF NOT EXISTS run_trace (
    trace_id                 TEXT      PRIMARY KEY,
    timestamp                TIMESTAMP DEFAULT NOW(),
    ticket_text_hash         TEXT      NOT NULL,
    redacted_ticket_preview  TEXT,
    config_hash              TEXT      DEFAULT '',
    model_provider           TEXT      DEFAULT '',
    workflow_mode            TEXT      DEFAULT '',
    product                  TEXT      DEFAULT '',
    platform                 TEXT      DEFAULT '',
    role                     TEXT      DEFAULT '',
    trace                    JSONB     NOT NULL,
    created_at               TIMESTAMP DEFAULT NOW()
);
"""

# ── Migrations: add columns to existing tables ────────────────
# These are safe to run repeatedly (IF NOT EXISTS).

ALTER_FEEDBACK_ADD_PRODUCT = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS product TEXT;"
ALTER_FEEDBACK_ADD_USER_ID = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS user_id TEXT DEFAULT '';"
ALTER_FEEDBACK_ADD_TEAM_ID = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS team_id TEXT DEFAULT '';"
ALTER_FEEDBACK_ADD_SESSION_ID = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS session_id TEXT DEFAULT '';"
ALTER_FEEDBACK_ADD_DRAFT_RUN_ID = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS draft_run_id TEXT DEFAULT '';"
ALTER_FEEDBACK_ADD_REASON_CODE = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS reason_code TEXT DEFAULT '';"
ALTER_FEEDBACK_ADD_ABSTENTION_CORRECT = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS abstention_correct TEXT DEFAULT 'not_applicable';"
ALTER_DRAFT_RUN_ADD_EXPERIMENT_ID = "ALTER TABLE draft_run ADD COLUMN IF NOT EXISTS experiment_id TEXT DEFAULT '';"
ALTER_DRAFT_RUN_ADD_EXPERIMENT_ARM = "ALTER TABLE draft_run ADD COLUMN IF NOT EXISTS experiment_arm TEXT DEFAULT '';"
ALTER_DRAFT_RUN_ADD_EXPERIMENT_MODE = "ALTER TABLE draft_run ADD COLUMN IF NOT EXISTS experiment_mode TEXT DEFAULT '';"
ALTER_DRAFT_RUN_ADD_VARIANT_CONFIG_HASH = "ALTER TABLE draft_run ADD COLUMN IF NOT EXISTS variant_config_hash TEXT DEFAULT '';"
ALTER_DRAFT_RUN_ADD_SOURCE_VERSION_SET = "ALTER TABLE draft_run ADD COLUMN IF NOT EXISTS source_version_set JSONB DEFAULT '{}'::jsonb;"
ALTER_DRAFT_RUN_ADD_ASSIGNED_AT = "ALTER TABLE draft_run ADD COLUMN IF NOT EXISTS assigned_at TIMESTAMP;"
ALTER_DRAFT_RUN_ADD_ASSIGNMENT_REASON = "ALTER TABLE draft_run ADD COLUMN IF NOT EXISTS assignment_reason TEXT DEFAULT '';"
ALTER_FEEDBACK_ADD_PERMISSION_LEVEL = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS permission_level TEXT;"
ALTER_FEEDBACK_ADD_ACCESS_CHANNEL = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS access_channel TEXT;"
ALTER_FEEDBACK_ADD_REQUEST_FINGERPRINT = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS request_fingerprint TEXT;"
ALTER_FEEDBACK_ADD_TOTAL_TOKENS = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS total_tokens INTEGER DEFAULT 0;"
ALTER_FEEDBACK_ADD_QUERY_TOKENS_IN = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS query_tokens_in INTEGER DEFAULT 0;"
ALTER_FEEDBACK_ADD_QUERY_TOKENS_OUT = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS query_tokens_out INTEGER DEFAULT 0;"
ALTER_FEEDBACK_ADD_RESPONSE_TOKENS_IN = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS response_tokens_in INTEGER DEFAULT 0;"
ALTER_FEEDBACK_ADD_RESPONSE_TOKENS_OUT = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS response_tokens_out INTEGER DEFAULT 0;"
ALTER_FEEDBACK_ADD_RETRIEVED_CHUNK_IDS = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS retrieved_chunk_ids TEXT DEFAULT '[]';"
ALTER_FEEDBACK_ADD_RERANK_SCORES = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS rerank_scores TEXT DEFAULT '[]';"
ALTER_FEEDBACK_ADD_TOP_SCORE = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS top_score FLOAT DEFAULT 0;"
ALTER_FEEDBACK_ADD_SCORE_GAP = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS score_gap FLOAT DEFAULT 0;"
ALTER_FEEDBACK_ADD_USED_RETRIEVAL_CACHE = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS used_retrieval_cache BOOLEAN DEFAULT FALSE;"
ALTER_FEEDBACK_ADD_USED_RESPONSE_CACHE = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS used_response_cache BOOLEAN DEFAULT FALSE;"
ALTER_FEEDBACK_ADD_ROUTING_STRATEGY = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS routing_strategy TEXT;"
ALTER_FEEDBACK_ADD_EVAL_FAITHFULNESS = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS eval_faithfulness TEXT;"
ALTER_FEEDBACK_ADD_EVAL_COMPLETENESS = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS eval_completeness TEXT;"
ALTER_FEEDBACK_ADD_RESPONSE_ID = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS response_id TEXT DEFAULT '';"
ALTER_FEEDBACK_ADD_TRACE_ID = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS trace_id TEXT DEFAULT '';"
ALTER_FEEDBACK_ADD_CITATIONS_USED = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS citations_used TEXT DEFAULT '[]';"
ALTER_FEEDBACK_ADD_FEEDBACK_REASON = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS feedback_reason TEXT DEFAULT '';"
ALTER_FEEDBACK_ADD_COMMENT = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS comment TEXT DEFAULT '';"
ALTER_FEEDBACK_ADD_AGENT_ACTION = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS agent_action TEXT DEFAULT 'pending';"
ALTER_FEEDBACK_ADD_FINAL_SENT_TEXT = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS final_sent_text TEXT DEFAULT '';"
ALTER_FEEDBACK_ADD_EDIT_DISTANCE_RATIO = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS edit_distance_ratio FLOAT DEFAULT 0;"
ALTER_FEEDBACK_ADD_EDIT_DISTANCE_TOKENS = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS edit_distance_tokens INTEGER DEFAULT 0;"
ALTER_FEEDBACK_ADD_CITATIONS_KEPT = "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS citations_kept TEXT DEFAULT '[]';"

ALTER_API_CALLS_ADD_PROVIDER = "ALTER TABLE api_calls ADD COLUMN IF NOT EXISTS provider TEXT DEFAULT '';"
ALTER_API_CALLS_ADD_STEP = "ALTER TABLE api_calls ADD COLUMN IF NOT EXISTS step TEXT DEFAULT '';"
ALTER_API_CALLS_ADD_ERROR_MESSAGE = "ALTER TABLE api_calls ADD COLUMN IF NOT EXISTS error_message TEXT DEFAULT '';"
ALTER_API_CALLS_ADD_TRACE_ID = "ALTER TABLE api_calls ADD COLUMN IF NOT EXISTS trace_id TEXT DEFAULT '';"
ALTER_API_CALLS_ADD_DRAFT_RUN_ID = "ALTER TABLE api_calls ADD COLUMN IF NOT EXISTS draft_run_id TEXT DEFAULT '';"
ALTER_API_CALLS_ADD_USER_ID = "ALTER TABLE api_calls ADD COLUMN IF NOT EXISTS user_id TEXT DEFAULT '';"
ALTER_API_CALLS_ADD_TEAM_ID = "ALTER TABLE api_calls ADD COLUMN IF NOT EXISTS team_id TEXT DEFAULT '';"
ALTER_API_CALLS_ADD_SESSION_ID = "ALTER TABLE api_calls ADD COLUMN IF NOT EXISTS session_id TEXT DEFAULT '';"

ALTER_EVAL_ADD_RETRY_TRIGGERED = "ALTER TABLE evaluation_results ADD COLUMN IF NOT EXISTS retry_triggered BOOLEAN DEFAULT FALSE;"
ALTER_EVAL_ADD_PRODUCT = "ALTER TABLE evaluation_results ADD COLUMN IF NOT EXISTS product TEXT DEFAULT '';"
ALTER_EVAL_ADD_ACCESS_CHANNEL = "ALTER TABLE evaluation_results ADD COLUMN IF NOT EXISTS access_channel TEXT DEFAULT '';"

ALTER_HRQ_ADD_FULL_TICKET = "ALTER TABLE human_review_queue ADD COLUMN IF NOT EXISTS full_ticket TEXT;"
ALTER_HRQ_ADD_REVIEWER_NOTES = "ALTER TABLE human_review_queue ADD COLUMN IF NOT EXISTS reviewer_notes TEXT DEFAULT '';"
ALTER_HRQ_ADD_TRACE_ID = "ALTER TABLE human_review_queue ADD COLUMN IF NOT EXISTS trace_id TEXT DEFAULT '';"
ALTER_HRQ_ADD_CONFIDENCE_BAND = "ALTER TABLE human_review_queue ADD COLUMN IF NOT EXISTS confidence_band TEXT DEFAULT '';"
ALTER_HRQ_ADD_SEVERITY = "ALTER TABLE human_review_queue ADD COLUMN IF NOT EXISTS severity TEXT DEFAULT 'medium';"
ALTER_HRQ_ADD_AGE_STARTED_AT = "ALTER TABLE human_review_queue ADD COLUMN IF NOT EXISTS age_started_at TIMESTAMP DEFAULT NOW();"
ALTER_HRQ_ADD_SLA_MARKER = "ALTER TABLE human_review_queue ADD COLUMN IF NOT EXISTS sla_marker TEXT DEFAULT '';"
ALTER_HRQ_ADD_SOURCE_ISSUE_TYPE = "ALTER TABLE human_review_queue ADD COLUMN IF NOT EXISTS source_issue_type TEXT DEFAULT '';"
ALTER_HRQ_ADD_ASSIGNED_REVIEWER = "ALTER TABLE human_review_queue ADD COLUMN IF NOT EXISTS assigned_reviewer TEXT DEFAULT '';"
ALTER_HRQ_ADD_STATUS = "ALTER TABLE human_review_queue ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'open';"

ALTER_KBI_ADD_SUMMARY   = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS summary   TEXT DEFAULT '';"
ALTER_KBI_ADD_KEYWORDS  = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS keywords  TEXT DEFAULT '';"
ALTER_KBI_ADD_QUESTIONS = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS questions TEXT DEFAULT '';"
ALTER_KBI_ADD_EMBEDDING_TEXT = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS embedding_text TEXT DEFAULT '';"
ALTER_KBI_ADD_DISPLAY_TEXT = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS display_text TEXT DEFAULT '';"
ALTER_KBI_ADD_ARTICLE_ID = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS article_id TEXT DEFAULT '';"
ALTER_KBI_ADD_CHUNK_TYPE = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS chunk_type TEXT DEFAULT 'concept';"
ALTER_KBI_ADD_PARENT_SECTION_ID = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS parent_section_id TEXT DEFAULT '';"
ALTER_KBI_ADD_HEADING_PATH = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS heading_path TEXT DEFAULT '';"
ALTER_KBI_ADD_SOURCE_TYPE = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS source_type TEXT DEFAULT 'knowledge_base';"
ALTER_KBI_ADD_SOURCE_ID = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS source_id TEXT DEFAULT '';"
ALTER_KBI_ADD_DOCUMENT_ID = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS document_id TEXT DEFAULT '';"
ALTER_KBI_ADD_DOCUMENT_VERSION = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS document_version INTEGER DEFAULT 1;"
ALTER_KBI_ADD_CHUNK_VERSION = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS chunk_version INTEGER DEFAULT 1;"
ALTER_KBI_ADD_IS_APPROVED = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS is_approved BOOLEAN DEFAULT FALSE;"
ALTER_KBI_ADD_TIER = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS tier TEXT DEFAULT '';"
ALTER_KBI_ADD_SOURCE_REF = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS source_ref TEXT DEFAULT '';"
ALTER_KBI_ADD_LINEAGE_REF = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS lineage_ref TEXT DEFAULT '';"
ALTER_KBI_ADD_REVIEWED_BY = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS reviewed_by TEXT DEFAULT '';"
ALTER_KBI_ADD_APPROVED_AT = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS approved_at TEXT DEFAULT '';"
ALTER_KBI_ADD_EXPIRES_AT = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS expires_at TEXT DEFAULT '';"
ALTER_KBI_ADD_NEEDS_REVIEW_AT = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS needs_review_at TEXT DEFAULT '';"
ALTER_KBI_ADD_AUDIENCE_ALLOWED = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS audience_allowed TEXT DEFAULT '[]';"
ALTER_KBI_ADD_SOURCE_CATEGORY = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS source_category TEXT DEFAULT '';"
ALTER_KBI_ADD_IS_CUSTOMER_FACING_ALLOWED = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS is_customer_facing_allowed BOOLEAN DEFAULT FALSE;"
ALTER_KBI_ADD_IS_INTERNAL_ONLY = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS is_internal_only BOOLEAN DEFAULT FALSE;"
ALTER_KBI_ADD_IS_FUTURE_ONLY = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS is_future_only BOOLEAN DEFAULT FALSE;"
ALTER_KBI_ADD_SOURCE_URL = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS source_url TEXT DEFAULT '';"
ALTER_KBI_ADD_DOCUMENT_HASH = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS document_hash TEXT DEFAULT '';"
ALTER_KBI_ADD_CHUNK_HASH = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS chunk_hash TEXT DEFAULT '';"
ALTER_KBI_ADD_IS_ACTIVE = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;"
ALTER_KBI_ADD_SUPERSEDED_BY_CHUNK_ID = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS superseded_by_chunk_id TEXT DEFAULT '';"
ALTER_KBI_ADD_SUPERSEDED_AT = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS superseded_at TEXT DEFAULT '';"
ALTER_KBI_ADD_SUPERSEDED_REASON = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS superseded_reason TEXT DEFAULT '';"
ALTER_KBI_ADD_ACTIVE_FROM = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS active_from TEXT DEFAULT '';"
ALTER_KBI_ADD_ACTIVE_UNTIL = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS active_until TEXT DEFAULT '';"
ALTER_KBI_ADD_UPDATED_AT = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS updated_at TEXT DEFAULT '';"
BACKFILL_KBI_UPDATED_AT = """
UPDATE knowledge_base_identifier
SET updated_at = COALESCE(NULLIF(ingested_at, ''), NULLIF(approved_at, ''), NOW()::TEXT)
WHERE COALESCE(updated_at, '') = ''
  AND (
    COALESCE(ingested_at, '') <> ''
    OR COALESCE(approved_at, '') <> ''
    OR COALESCE(is_approved, FALSE) = TRUE
  );
"""
ALTER_KBI_ADD_REDACTION_STATUS = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS redaction_status TEXT DEFAULT '';"
ALTER_KBI_ADD_REDACTION_APPLIED = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS redaction_applied BOOLEAN DEFAULT FALSE;"
ALTER_KBI_ADD_INGESTED_AT = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS ingested_at TEXT DEFAULT '';"
ALTER_KBI_ADD_LOADER_VERSION = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS loader_version TEXT DEFAULT '';"
ALTER_KBI_ADD_CONFIG_HASH = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS config_hash TEXT DEFAULT '';"
ALTER_KBI_ADD_DISABLED = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS disabled BOOLEAN DEFAULT FALSE;"
ALTER_KBI_ADD_SOURCE_AUTHORITY = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS source_authority NUMERIC DEFAULT 1.0;"
ALTER_KBI_ADD_CONDITION_FLAGS = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS condition_flags TEXT DEFAULT '[]';"
ALTER_KBI_ADD_SOURCE_LICENSE = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS source_license TEXT DEFAULT '';"
ALTER_KBI_ADD_ATTRIBUTION_REQUIRED = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS attribution_required BOOLEAN DEFAULT FALSE;"
ALTER_KBI_ADD_ATTRIBUTION_TEXT = "ALTER TABLE knowledge_base_identifier ADD COLUMN IF NOT EXISTS attribution_text TEXT DEFAULT '';"

ALTER_RESPONSE_CACHE_ADD_PROVIDER = "ALTER TABLE response_cache ADD COLUMN IF NOT EXISTS provider TEXT DEFAULT '';"

ALTER_RETRIEVAL_CACHE_ADD_QUERY_TEXT = "ALTER TABLE retrieval_cache ADD COLUMN IF NOT EXISTS query_text TEXT DEFAULT '';"
ALTER_RETRIEVAL_CACHE_ADD_CHUNK_COUNT = "ALTER TABLE retrieval_cache ADD COLUMN IF NOT EXISTS chunk_count INTEGER DEFAULT 0;"

# ── Queries: vector DB ────────────────────────────────────────
INSERT_CHUNK = """
INSERT INTO knowledge_base (id, embedding, product, platform, doc_type)
VALUES (%s, %s::vector, %s, %s, %s)
ON CONFLICT (id) DO UPDATE
SET embedding = EXCLUDED.embedding,
    product   = EXCLUDED.product,
    platform  = EXCLUDED.platform,
    doc_type  = EXCLUDED.doc_type;
"""

INSERT_KB_IDENTIFIER = """
INSERT INTO knowledge_base_identifier (
    id, title, url_name, url, content, embedding_text, display_text, article_id,
    chunk_index, total_chunks, source_file, summary, keywords, questions, chunk_type, parent_section_id, heading_path,
    source_type, source_id, document_id, document_version, chunk_version, is_approved, tier, source_ref, lineage_ref, reviewed_by, approved_at, expires_at,
    needs_review_at, audience_allowed, source_category, is_customer_facing_allowed, is_internal_only, is_future_only,
    source_url, document_hash, chunk_hash, is_active, superseded_by_chunk_id, superseded_at, superseded_reason, active_from, active_until,
    updated_at, redaction_status, redaction_applied,
    ingested_at, loader_version, config_hash, disabled, source_authority, condition_flags,
    source_license, attribution_required, attribution_text
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (id) DO UPDATE
SET title        = EXCLUDED.title,
    url_name     = EXCLUDED.url_name,
    url          = EXCLUDED.url,
    content      = EXCLUDED.content,
    embedding_text = EXCLUDED.embedding_text,
    display_text = EXCLUDED.display_text,
    article_id   = EXCLUDED.article_id,
    chunk_index  = EXCLUDED.chunk_index,
    total_chunks = EXCLUDED.total_chunks,
    source_file  = EXCLUDED.source_file,
    summary      = EXCLUDED.summary,
    keywords     = EXCLUDED.keywords,
    questions    = EXCLUDED.questions,
    chunk_type   = EXCLUDED.chunk_type,
    parent_section_id = EXCLUDED.parent_section_id,
    heading_path = EXCLUDED.heading_path,
    source_type  = EXCLUDED.source_type,
    source_id    = EXCLUDED.source_id,
    document_id  = EXCLUDED.document_id,
    document_version = EXCLUDED.document_version,
    chunk_version = EXCLUDED.chunk_version,
    is_approved  = EXCLUDED.is_approved,
    tier         = EXCLUDED.tier,
    source_ref   = EXCLUDED.source_ref,
    lineage_ref  = EXCLUDED.lineage_ref,
    reviewed_by  = EXCLUDED.reviewed_by,
    approved_at  = EXCLUDED.approved_at,
    expires_at   = EXCLUDED.expires_at,
    needs_review_at = EXCLUDED.needs_review_at,
    audience_allowed = EXCLUDED.audience_allowed,
    source_category = EXCLUDED.source_category,
    is_customer_facing_allowed = EXCLUDED.is_customer_facing_allowed,
    is_internal_only = EXCLUDED.is_internal_only,
    is_future_only = EXCLUDED.is_future_only,
    source_url = EXCLUDED.source_url,
    document_hash = EXCLUDED.document_hash,
    chunk_hash = EXCLUDED.chunk_hash,
    is_active = EXCLUDED.is_active,
    superseded_by_chunk_id = EXCLUDED.superseded_by_chunk_id,
    superseded_at = EXCLUDED.superseded_at,
    superseded_reason = EXCLUDED.superseded_reason,
    active_from = EXCLUDED.active_from,
    active_until = EXCLUDED.active_until,
    updated_at = EXCLUDED.updated_at,
    redaction_status = EXCLUDED.redaction_status,
    redaction_applied = EXCLUDED.redaction_applied,
    ingested_at = EXCLUDED.ingested_at,
    loader_version = EXCLUDED.loader_version,
    config_hash = EXCLUDED.config_hash,
    disabled = EXCLUDED.disabled,
    source_authority = EXCLUDED.source_authority,
    condition_flags = EXCLUDED.condition_flags,
    source_license = EXCLUDED.source_license,
    attribution_required = EXCLUDED.attribution_required,
    attribution_text = EXCLUDED.attribution_text;
"""

INSERT_ARTICLE_SECTION = """
INSERT INTO article_section (
    id, article_id, title, heading_path, section_text, product, platform,
    doc_type, source_type, url, source_file
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (id) DO UPDATE
SET title = EXCLUDED.title,
    heading_path = EXCLUDED.heading_path,
    section_text = EXCLUDED.section_text,
    product = EXCLUDED.product,
    platform = EXCLUDED.platform,
    doc_type = EXCLUDED.doc_type,
    source_type = EXCLUDED.source_type,
    url = EXCLUDED.url,
    source_file = EXCLUDED.source_file,
    updated_at = NOW();
"""

SEMANTIC_SEARCH = """
SELECT
    kb.id,
    ki.content,
    COALESCE(NULLIF(ki.display_text, ''), ki.content) AS display_text,
    COALESCE(NULLIF(ki.embedding_text, ''), ki.content) AS embedding_text,
    COALESCE(ki.article_id, '') AS article_id,
    ki.title,
    ki.url_name,
    ki.url,
    ki.source_file,
    ki.chunk_index,
    ki.total_chunks,
    COALESCE(ki.chunk_type, 'concept') AS chunk_type,
    COALESCE(ki.parent_section_id, '') AS parent_section_id,
    COALESCE(ki.heading_path, '') AS heading_path,
    COALESCE(ki.source_type, kb.doc_type, 'knowledge_base') AS source_type,
    COALESCE(ki.source_id, '') AS source_id,
    COALESCE(ki.is_approved, FALSE) AS is_approved,
    COALESCE(ki.tier, '') AS tier,
    COALESCE(ki.source_ref, '') AS source_ref,
    COALESCE(ki.lineage_ref, '') AS lineage_ref,
    COALESCE(ki.reviewed_by, '') AS reviewed_by,
    COALESCE(ki.approved_at, '') AS approved_at,
    COALESCE(ki.expires_at, '') AS expires_at,
    COALESCE(ki.needs_review_at, '') AS needs_review_at,
    COALESCE(ki.audience_allowed, '[]') AS audience_allowed,
    COALESCE(ki.source_category, '') AS source_category,
    COALESCE(ki.is_customer_facing_allowed, FALSE) AS is_customer_facing_allowed,
    COALESCE(ki.is_internal_only, FALSE) AS is_internal_only,
    COALESCE(ki.is_future_only, FALSE) AS is_future_only,
    COALESCE(ki.source_url, '') AS source_url,
    COALESCE(ki.document_hash, '') AS document_hash,
    COALESCE(ki.chunk_hash, '') AS chunk_hash,
    COALESCE(ki.updated_at, '') AS updated_at,
    COALESCE(ki.redaction_status, '') AS redaction_status,
    COALESCE(ki.redaction_applied, FALSE) AS redaction_applied,
    COALESCE(ki.ingested_at, '') AS ingested_at,
    COALESCE(ki.loader_version, '') AS loader_version,
    COALESCE(ki.config_hash, '') AS config_hash,
    COALESCE(ki.disabled, FALSE) AS disabled,
    COALESCE(ki.source_authority, 1.0) AS source_authority,
    COALESCE(ki.condition_flags, '[]') AS condition_flags,
    COALESCE(ki.source_license, '') AS source_license,
    COALESCE(ki.attribution_required, FALSE) AS attribution_required,
    COALESCE(ki.attribution_text, '') AS attribution_text,
    kb.product,
    kb.platform,
    kb.doc_type,
    1 - (kb.embedding <=> %s::vector) AS score
FROM knowledge_base kb
JOIN knowledge_base_identifier ki ON kb.id = ki.id
WHERE LOWER(kb.product)  = ANY(%s)
  AND LOWER(kb.platform) = %s
  AND COALESCE(ki.disabled, FALSE) = FALSE
  AND COALESCE(ki.is_active, TRUE) = TRUE
ORDER BY kb.embedding <=> %s::vector
LIMIT %s;
"""

FETCH_ALL_FOR_BM25 = """
SELECT
    kb.id,
    ki.content,
    COALESCE(NULLIF(ki.display_text, ''), ki.content) AS display_text,
    COALESCE(NULLIF(ki.embedding_text, ''), ki.content) AS embedding_text,
    COALESCE(ki.article_id, '') AS article_id,
    ki.title,
    ki.url_name,
    ki.url,
    ki.source_file,
    ki.chunk_index,
    ki.total_chunks,
    COALESCE(ki.chunk_type, 'concept') AS chunk_type,
    COALESCE(ki.parent_section_id, '') AS parent_section_id,
    COALESCE(ki.heading_path, '') AS heading_path,
    COALESCE(ki.source_type, kb.doc_type, 'knowledge_base') AS source_type,
    COALESCE(ki.source_id, '') AS source_id,
    COALESCE(ki.is_approved, FALSE) AS is_approved,
    COALESCE(ki.tier, '') AS tier,
    COALESCE(ki.source_ref, '') AS source_ref,
    COALESCE(ki.lineage_ref, '') AS lineage_ref,
    COALESCE(ki.reviewed_by, '') AS reviewed_by,
    COALESCE(ki.approved_at, '') AS approved_at,
    COALESCE(ki.expires_at, '') AS expires_at,
    COALESCE(ki.needs_review_at, '') AS needs_review_at,
    COALESCE(ki.audience_allowed, '[]') AS audience_allowed,
    COALESCE(ki.source_category, '') AS source_category,
    COALESCE(ki.is_customer_facing_allowed, FALSE) AS is_customer_facing_allowed,
    COALESCE(ki.is_internal_only, FALSE) AS is_internal_only,
    COALESCE(ki.is_future_only, FALSE) AS is_future_only,
    COALESCE(ki.source_url, '') AS source_url,
    COALESCE(ki.document_hash, '') AS document_hash,
    COALESCE(ki.chunk_hash, '') AS chunk_hash,
    COALESCE(ki.updated_at, '') AS updated_at,
    COALESCE(ki.redaction_status, '') AS redaction_status,
    COALESCE(ki.redaction_applied, FALSE) AS redaction_applied,
    COALESCE(ki.ingested_at, '') AS ingested_at,
    COALESCE(ki.loader_version, '') AS loader_version,
    COALESCE(ki.config_hash, '') AS config_hash,
    COALESCE(ki.disabled, FALSE) AS disabled,
    COALESCE(ki.source_authority, 1.0) AS source_authority,
    COALESCE(ki.condition_flags, '[]') AS condition_flags,
    COALESCE(ki.source_license, '') AS source_license,
    COALESCE(ki.attribution_required, FALSE) AS attribution_required,
    COALESCE(ki.attribution_text, '') AS attribution_text,
    kb.product,
    kb.platform,
    kb.doc_type
FROM knowledge_base kb
JOIN knowledge_base_identifier ki ON kb.id = ki.id
WHERE LOWER(kb.product)  = ANY(%s)
  AND LOWER(kb.platform) = %s
  AND COALESCE(ki.disabled, FALSE) = FALSE
  AND COALESCE(ki.is_active, TRUE) = TRUE;
"""

DELETE_CHUNK = "DELETE FROM knowledge_base WHERE id = %s;"

GET_CHUNK_BY_ID = """
SELECT
    kb.id,
    ki.content,
    COALESCE(NULLIF(ki.display_text, ''), ki.content) AS display_text,
    COALESCE(NULLIF(ki.embedding_text, ''), ki.content) AS embedding_text,
    COALESCE(ki.article_id, '') AS article_id,
    ki.title,
    ki.url_name,
    ki.url,
    ki.source_file,
    ki.chunk_index,
    ki.total_chunks,
    COALESCE(ki.chunk_type, 'concept') AS chunk_type,
    COALESCE(ki.parent_section_id, '') AS parent_section_id,
    COALESCE(ki.heading_path, '') AS heading_path,
    COALESCE(ki.source_type, kb.doc_type, 'knowledge_base') AS source_type,
    COALESCE(ki.source_id, '') AS source_id,
    COALESCE(ki.is_approved, FALSE) AS is_approved,
    COALESCE(ki.tier, '') AS tier,
    COALESCE(ki.source_ref, '') AS source_ref,
    COALESCE(ki.lineage_ref, '') AS lineage_ref,
    COALESCE(ki.reviewed_by, '') AS reviewed_by,
    COALESCE(ki.approved_at, '') AS approved_at,
    COALESCE(ki.expires_at, '') AS expires_at,
    COALESCE(ki.needs_review_at, '') AS needs_review_at,
    COALESCE(ki.audience_allowed, '[]') AS audience_allowed,
    COALESCE(ki.source_category, '') AS source_category,
    COALESCE(ki.is_customer_facing_allowed, FALSE) AS is_customer_facing_allowed,
    COALESCE(ki.is_internal_only, FALSE) AS is_internal_only,
    COALESCE(ki.is_future_only, FALSE) AS is_future_only,
    COALESCE(ki.source_url, '') AS source_url,
    COALESCE(ki.document_hash, '') AS document_hash,
    COALESCE(ki.chunk_hash, '') AS chunk_hash,
    COALESCE(ki.updated_at, '') AS updated_at,
    COALESCE(ki.redaction_status, '') AS redaction_status,
    COALESCE(ki.redaction_applied, FALSE) AS redaction_applied,
    COALESCE(ki.ingested_at, '') AS ingested_at,
    COALESCE(ki.loader_version, '') AS loader_version,
    COALESCE(ki.config_hash, '') AS config_hash,
    COALESCE(ki.disabled, FALSE) AS disabled,
    COALESCE(ki.source_authority, 1.0) AS source_authority,
    COALESCE(ki.condition_flags, '[]') AS condition_flags,
    COALESCE(ki.source_license, '') AS source_license,
    COALESCE(ki.attribution_required, FALSE) AS attribution_required,
    COALESCE(ki.attribution_text, '') AS attribution_text,
    kb.product,
    kb.platform,
    kb.doc_type
FROM knowledge_base kb
JOIN knowledge_base_identifier ki ON kb.id = ki.id
WHERE kb.id = %s;
"""

ROW_COUNT          = "SELECT COUNT(*) FROM knowledge_base;"
GET_ALL_KB_IDS     = "SELECT id FROM knowledge_base;"

FETCH_PARENT_SECTIONS = """
SELECT id, article_id, title, heading_path, section_text, product, platform,
       doc_type, source_type, url, source_file
FROM article_section
WHERE id = ANY(%s);
"""

FETCH_NEIGHBOR_CHUNKS = """
SELECT
    kb.id,
    ki.content,
    COALESCE(NULLIF(ki.display_text, ''), ki.content) AS display_text,
    COALESCE(NULLIF(ki.embedding_text, ''), ki.content) AS embedding_text,
    COALESCE(ki.article_id, '') AS article_id,
    ki.title,
    ki.url_name,
    ki.url,
    ki.source_file,
    COALESCE(ki.chunk_type, 'concept') AS chunk_type,
    COALESCE(ki.parent_section_id, '') AS parent_section_id,
    COALESCE(ki.heading_path, '') AS heading_path,
    COALESCE(ki.source_type, kb.doc_type, 'knowledge_base') AS source_type,
    COALESCE(ki.source_id, '') AS source_id,
    COALESCE(ki.is_approved, FALSE) AS is_approved,
    COALESCE(ki.tier, '') AS tier,
    COALESCE(ki.source_ref, '') AS source_ref,
    COALESCE(ki.lineage_ref, '') AS lineage_ref,
    COALESCE(ki.reviewed_by, '') AS reviewed_by,
    COALESCE(ki.approved_at, '') AS approved_at,
    COALESCE(ki.expires_at, '') AS expires_at,
    COALESCE(ki.needs_review_at, '') AS needs_review_at,
    COALESCE(ki.audience_allowed, '[]') AS audience_allowed,
    COALESCE(ki.source_category, '') AS source_category,
    COALESCE(ki.is_customer_facing_allowed, FALSE) AS is_customer_facing_allowed,
    COALESCE(ki.is_internal_only, FALSE) AS is_internal_only,
    COALESCE(ki.is_future_only, FALSE) AS is_future_only,
    COALESCE(ki.source_url, '') AS source_url,
    COALESCE(ki.document_hash, '') AS document_hash,
    COALESCE(ki.chunk_hash, '') AS chunk_hash,
    COALESCE(ki.updated_at, '') AS updated_at,
    COALESCE(ki.redaction_status, '') AS redaction_status,
    COALESCE(ki.redaction_applied, FALSE) AS redaction_applied,
    COALESCE(ki.ingested_at, '') AS ingested_at,
    COALESCE(ki.loader_version, '') AS loader_version,
    COALESCE(ki.config_hash, '') AS config_hash,
    COALESCE(ki.disabled, FALSE) AS disabled,
    COALESCE(ki.source_authority, 1.0) AS source_authority,
    COALESCE(ki.condition_flags, '[]') AS condition_flags,
    COALESCE(ki.source_license, '') AS source_license,
    COALESCE(ki.attribution_required, FALSE) AS attribution_required,
    COALESCE(ki.attribution_text, '') AS attribution_text,
    kb.product,
    kb.platform,
    kb.doc_type,
    ki.chunk_index,
    ki.total_chunks
FROM knowledge_base kb
JOIN knowledge_base_identifier ki ON kb.id = ki.id
WHERE COALESCE(ki.article_id, '') = %s
  AND ki.chunk_index BETWEEN %s AND %s
ORDER BY ki.chunk_index;
"""

# ── Setup query groups ────────────────────────────────────────
VECTOR_SETUP_QUERIES = [
    CREATE_EXTENSION,
    CREATE_KNOWLEDGE_BASE,
    CREATE_KNOWLEDGE_BASE_INDEX,
    ANALYZE_KNOWLEDGE_BASE,
    ALTER_KB_ADD_PRODUCT,
    ALTER_KB_ADD_PLATFORM,
    ALTER_KB_ADD_DOC_TYPE,
    CREATE_KB_IDENTIFIER,
    CREATE_KB_IDENTIFIER_INDEX,
    CREATE_ARTICLE_SECTION,
    ALTER_KBI_ADD_SUMMARY,
    ALTER_KBI_ADD_KEYWORDS,
    ALTER_KBI_ADD_QUESTIONS,
    ALTER_KBI_ADD_EMBEDDING_TEXT,
    ALTER_KBI_ADD_DISPLAY_TEXT,
    ALTER_KBI_ADD_ARTICLE_ID,
    ALTER_KBI_ADD_CHUNK_TYPE,
    ALTER_KBI_ADD_PARENT_SECTION_ID,
    ALTER_KBI_ADD_HEADING_PATH,
    ALTER_KBI_ADD_SOURCE_TYPE,
    ALTER_KBI_ADD_SOURCE_ID,
    ALTER_KBI_ADD_DOCUMENT_ID,
    ALTER_KBI_ADD_DOCUMENT_VERSION,
    ALTER_KBI_ADD_CHUNK_VERSION,
    ALTER_KBI_ADD_IS_APPROVED,
    ALTER_KBI_ADD_TIER,
    ALTER_KBI_ADD_SOURCE_REF,
    ALTER_KBI_ADD_LINEAGE_REF,
    ALTER_KBI_ADD_REVIEWED_BY,
    ALTER_KBI_ADD_APPROVED_AT,
    ALTER_KBI_ADD_EXPIRES_AT,
    ALTER_KBI_ADD_NEEDS_REVIEW_AT,
    ALTER_KBI_ADD_AUDIENCE_ALLOWED,
    ALTER_KBI_ADD_SOURCE_CATEGORY,
    ALTER_KBI_ADD_IS_CUSTOMER_FACING_ALLOWED,
    ALTER_KBI_ADD_IS_INTERNAL_ONLY,
    ALTER_KBI_ADD_IS_FUTURE_ONLY,
    ALTER_KBI_ADD_SOURCE_URL,
    ALTER_KBI_ADD_DOCUMENT_HASH,
    ALTER_KBI_ADD_CHUNK_HASH,
    ALTER_KBI_ADD_IS_ACTIVE,
    ALTER_KBI_ADD_SUPERSEDED_BY_CHUNK_ID,
    ALTER_KBI_ADD_SUPERSEDED_AT,
    ALTER_KBI_ADD_SUPERSEDED_REASON,
    ALTER_KBI_ADD_ACTIVE_FROM,
    ALTER_KBI_ADD_ACTIVE_UNTIL,
    ALTER_KBI_ADD_UPDATED_AT,
    BACKFILL_KBI_UPDATED_AT,
    ALTER_KBI_ADD_REDACTION_STATUS,
    ALTER_KBI_ADD_REDACTION_APPLIED,
    ALTER_KBI_ADD_INGESTED_AT,
    ALTER_KBI_ADD_LOADER_VERSION,
    ALTER_KBI_ADD_CONFIG_HASH,
    ALTER_KBI_ADD_DISABLED,
    ALTER_KBI_ADD_SOURCE_AUTHORITY,
    ALTER_KBI_ADD_CONDITION_FLAGS,
    ALTER_KBI_ADD_SOURCE_LICENSE,
    ALTER_KBI_ADD_ATTRIBUTION_REQUIRED,
    ALTER_KBI_ADD_ATTRIBUTION_TEXT,
]

OPS_SETUP_QUERIES = [
    CREATE_RESPONSE_CACHE,
    CREATE_RETRIEVAL_CACHE,
    CREATE_FEEDBACK_TABLE,
    CREATE_DRAFT_RUN_TABLE,
    CREATE_FEEDBACK_LABEL_TABLE,
    CREATE_KNOWLEDGE_ISSUE_TABLE,
    CREATE_KNOWLEDGE_PATCH_TABLE,
    CREATE_EXPERIMENT_TABLE,
    CREATE_EXPERIMENT_ARM_TABLE,
    CREATE_EXPERIMENT_RESULT_TABLE,
    CREATE_API_CALLS_TABLE,
    CREATE_ANALYTICS_EVENT_TABLE,
    CREATE_EVALUATION_RESULTS,
    CREATE_HUMAN_REVIEW_QUEUE,
    CREATE_RUN_TRACE_TABLE,
    CREATE_METRICS_DAILY,
    # Migrations for existing installs
    ALTER_FEEDBACK_ADD_DRAFT_RUN_ID,
    ALTER_FEEDBACK_ADD_REASON_CODE,
    ALTER_FEEDBACK_ADD_ABSTENTION_CORRECT,
    ALTER_DRAFT_RUN_ADD_EXPERIMENT_ID,
    ALTER_DRAFT_RUN_ADD_EXPERIMENT_ARM,
    ALTER_DRAFT_RUN_ADD_EXPERIMENT_MODE,
    ALTER_DRAFT_RUN_ADD_VARIANT_CONFIG_HASH,
    ALTER_DRAFT_RUN_ADD_SOURCE_VERSION_SET,
    ALTER_DRAFT_RUN_ADD_ASSIGNED_AT,
    ALTER_DRAFT_RUN_ADD_ASSIGNMENT_REASON,
    ALTER_FEEDBACK_ADD_PRODUCT,
    ALTER_FEEDBACK_ADD_USER_ID,
    ALTER_FEEDBACK_ADD_TEAM_ID,
    ALTER_FEEDBACK_ADD_SESSION_ID,
    ALTER_FEEDBACK_ADD_PERMISSION_LEVEL,
    ALTER_FEEDBACK_ADD_ACCESS_CHANNEL,
    ALTER_FEEDBACK_ADD_REQUEST_FINGERPRINT,
    ALTER_FEEDBACK_ADD_TOTAL_TOKENS,
    ALTER_FEEDBACK_ADD_QUERY_TOKENS_IN,
    ALTER_FEEDBACK_ADD_QUERY_TOKENS_OUT,
    ALTER_FEEDBACK_ADD_RESPONSE_TOKENS_IN,
    ALTER_FEEDBACK_ADD_RESPONSE_TOKENS_OUT,
    ALTER_FEEDBACK_ADD_RETRIEVED_CHUNK_IDS,
    ALTER_FEEDBACK_ADD_RERANK_SCORES,
    ALTER_FEEDBACK_ADD_TOP_SCORE,
    ALTER_FEEDBACK_ADD_SCORE_GAP,
    ALTER_FEEDBACK_ADD_USED_RETRIEVAL_CACHE,
    ALTER_FEEDBACK_ADD_USED_RESPONSE_CACHE,
    ALTER_FEEDBACK_ADD_ROUTING_STRATEGY,
    ALTER_FEEDBACK_ADD_EVAL_FAITHFULNESS,
    ALTER_FEEDBACK_ADD_EVAL_COMPLETENESS,
    ALTER_FEEDBACK_ADD_RESPONSE_ID,
    ALTER_FEEDBACK_ADD_TRACE_ID,
    ALTER_FEEDBACK_ADD_CITATIONS_USED,
    ALTER_FEEDBACK_ADD_FEEDBACK_REASON,
    ALTER_FEEDBACK_ADD_COMMENT,
    ALTER_FEEDBACK_ADD_AGENT_ACTION,
    ALTER_FEEDBACK_ADD_FINAL_SENT_TEXT,
    ALTER_FEEDBACK_ADD_EDIT_DISTANCE_RATIO,
    ALTER_FEEDBACK_ADD_EDIT_DISTANCE_TOKENS,
    ALTER_FEEDBACK_ADD_CITATIONS_KEPT,
    ALTER_API_CALLS_ADD_PROVIDER,
    ALTER_API_CALLS_ADD_STEP,
    ALTER_API_CALLS_ADD_ERROR_MESSAGE,
    ALTER_API_CALLS_ADD_TRACE_ID,
    ALTER_API_CALLS_ADD_DRAFT_RUN_ID,
    ALTER_API_CALLS_ADD_USER_ID,
    ALTER_API_CALLS_ADD_TEAM_ID,
    ALTER_API_CALLS_ADD_SESSION_ID,
    ALTER_EVAL_ADD_RETRY_TRIGGERED,
    ALTER_EVAL_ADD_PRODUCT,
    ALTER_EVAL_ADD_ACCESS_CHANNEL,
    ALTER_HRQ_ADD_FULL_TICKET,
    ALTER_HRQ_ADD_REVIEWER_NOTES,
    ALTER_HRQ_ADD_TRACE_ID,
    ALTER_HRQ_ADD_CONFIDENCE_BAND,
    ALTER_HRQ_ADD_SEVERITY,
    ALTER_HRQ_ADD_AGE_STARTED_AT,
    ALTER_HRQ_ADD_SLA_MARKER,
    ALTER_HRQ_ADD_SOURCE_ISSUE_TYPE,
    ALTER_HRQ_ADD_ASSIGNED_REVIEWER,
    ALTER_HRQ_ADD_STATUS,
    ALTER_RESPONSE_CACHE_ADD_PROVIDER,
    ALTER_RETRIEVAL_CACHE_ADD_QUERY_TEXT,
    ALTER_RETRIEVAL_CACHE_ADD_CHUNK_COUNT,
]


def ensure_vector_schema(conn, schema: str = "knowledge") -> None:
    with conn.cursor() as cursor:
        _set_search_path(cursor, schema)
        for query in VECTOR_SETUP_QUERIES:
            cursor.execute(query)
    conn.commit()


def ensure_ops_schema(conn, schema: str = "ops") -> None:
    with conn.cursor() as cursor:
        _set_search_path(cursor, schema)
        for query in OPS_SETUP_QUERIES:
            cursor.execute(query)
    conn.commit()


def ensure_runtime_schema(conn, schema: str = "ops") -> None:
    ensure_ops_schema(conn, schema=schema)


if __name__ == "__main__":
    import psycopg2
    from backend.core import config
    from backend.core.logger import get_logger

    logger = get_logger(__name__)

    try:
        print("Setting up knowledge schema...")
        with psycopg2.connect(config.DATABASE_URL) as conn:
            ensure_vector_schema(conn, schema=config.KNOWLEDGE_SCHEMA)
        print("knowledge schema ready")

        print("Setting up ops schema...")
        with psycopg2.connect(config.DATABASE_URL) as conn:
            ensure_ops_schema(conn, schema=config.OPS_SCHEMA)
        print("ops schema ready")

    except Exception as e:
        logger.error(f"Schema setup failed: {e}")
        print(f"Schema setup failed: {e}")
