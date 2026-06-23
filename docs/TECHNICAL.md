# ResolveKit Technical Guide

ResolveKit is a local-first, source-grounded, suggest-only support-AI reference workflow. It is a frozen learning project, not a production-ready support automation system.

This guide is the fast technical orientation: what runs, how a request moves through the system, where safety is enforced, and how to verify the local demo/reference path.

## System Shape

```text
Viewer / Admin UI
  -> FastAPI API
  -> planner and query construction
  -> retrieval and reranking
  -> context packing
  -> responder
  -> validation and confidence
  -> trace, feedback, exports, evals
```

Main surfaces:

- `backend/api/app.py`: FastAPI API, auth, stable contracts, and admin actions.
- `backend/core/analytics.py`: support-intelligence report builder for usage, retrieval, evaluation, knowledge gaps, escalation, and cost sections.
- `pipeline/`: retrieval, reranking, confidence, validation, and response shaping.
- `pipeline/responder.py`: suggest-only draft construction and response formatting.
- `backend/core/run_trace.py`: redacted trace construction and storage helpers.
- `knowledge_loader/`: source connectors and reference `SourceRecord` contract.
- `frontend/ticket/`: Viewer workflow.
- `frontend/ticket/index.html`: support ticket workspace.
- `frontend/configurator/`: Admin/configurator workflow.
- `eval/`, `scripts/`, and `experiments/`: golden evals, A/B, replay, reports, audits, and release utilities.

## Request Flow

1. The Viewer submits a support question to `/resolve` with `mode: "suggest"`.
2. The API authenticates `x-api-key`, rejects unknown request fields, and normalizes the request.
3. The pipeline builds route/query intent and retrieves only active, approved, customer-facing evidence.
4. Retrieval results are reranked, packed into bounded context, and passed to the responder.
5. The responder drafts a suggestion, never an auto-send or account mutation.
6. Validation checks citations, support, source safety, confidence band, and abstention rules.
7. The API returns the draft, caveats, citations, confidence, validation outcome, and trace summary.
8. Admin-only flows can inspect full trace JSON, replay by trace ID, export support bundles, and run eval/A/B jobs.

## Core Reference Routes

Reference docs foreground the route set needed for setup, drafting, source preview, and trace review:

- `POST /resolve`
- `GET /health`
- `POST /configurator/source-preview`
- `GET /traces/{trace_id}`
- `GET /configurator`
- `POST /feedback`

Admin analytics remains available for local review, but it is secondary to the core demo path.

## Stable API Contract

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Ticket workspace |
| `GET` | `/configurator` | Local configurator UI |
| `GET` | `/health` | Health check |
| `POST` | `/resolve` | Generate grounded suggestion |
| `POST` | `/feedback` | Store reviewer feedback |
| `POST` | `/analytics/events` | Store lightweight usage events such as source clicks |
| `GET` | `/draft-runs` | Read-only draft history |
| `POST` | `/knowledge-issues` | Create reviewer-owned knowledge issue |
| `GET` | `/traces/{trace_id}` | Fetch redacted RunTrace |
| `GET` | `/metrics` | Fetch seven-day LLM and quality metrics |
| `GET` | `/metrics/daily` | Fetch daily metrics snapshots |
| `GET` | `/admin/analytics/report` | Fetch the full admin support-intelligence report |
| `GET` | `/admin/analytics/{section}` | Fetch one admin analytics category |
| `POST` | `/configurator/source-preview` | Preview source parsing before ingestion |

Request model rejects unknown fields. `mode: "suggest"` is the only supported response mode.

Forbidden mode example:

```json
{
  "detail": {
    "error_type": "validation-blocked",
    "message": "Unsupported mode. This v3.x demo is suggest-only."
  }
}
```

Provider matrix:

Supported hosted providers are `openai` and `gemini`.

| Provider | Required Variables | Defaults | Smoke Command |
| --- | --- | --- | --- |
| OpenAI | `ACTIVE_PROVIDER=openai`, `OPENAI_API_KEY`, `API_KEY`, `CONFIGURATOR_API_KEY`, `DATABASE_URL` | `gpt-4o-mini` | `make doctor` |
| Gemini | `ACTIVE_PROVIDER=gemini`, `GEMINI_API_KEY`, `API_KEY`, `CONFIGURATOR_API_KEY`, `DATABASE_URL` | `gemini-2.0-flash` | `make doctor` |

Required local config names include `OPENAI_API_KEY` or `GEMINI_API_KEY`, `API_KEY`, `CONFIGURATOR_API_KEY`, `VIEWER_TOKEN`, `CONFIGURATOR_ADMIN_TOKEN`, `CONFIGURATOR_PREFILL_API_KEY=false`, `CORS_ALLOW_ORIGINS`, `KNOWLEDGE_SCHEMA`, and `OPS_SCHEMA`; start from `.env.docker.example`.

Operational tokens:

- `API_KEY`: viewer/API token for ticket workspace requests.
- `CONFIGURATOR_API_KEY`: admin/configurator token; must differ from `API_KEY`.
- `VIEWER_TOKEN`: trace viewer token; must be non-placeholder.
- `CONFIGURATOR_ADMIN_TOKEN`: elevated admin token; must differ from viewer keys.

Use distinct random values of at least 12 characters. Do not leave placeholders such as `change-me` or `change-me-configurator`.

## Source Contract

The local demo supports CSV vector ingest into Postgres/pgvector. XLSX is supported for source-contract validation and configurator preview; vector ingest remains CSV-first. Born-digital PDF fixtures remain preview work only.

Canonical shape lives in `knowledge_loader/source_contract.py` as `SourceRecord`.

Required fields:

`source_id, source_title, source_type, source_authority, is_approved, is_active, is_customer_facing_allowed, approved_at, reviewed_by, needs_review_at, doc_type, product_area, issue_class, version_scope, escalation_risk, body`

Start from `demo_data/onboarding/source_manifest_template.csv`. A valid demo CSV must include those fields. Rows are retrievable only when `is_approved=true`, `is_active=true`, `is_customer_facing_allowed=true`, and `body` is non-empty. Approved rows also require `approved_at` and `reviewed_by`.

Validate before loading:

```bash
.venv/bin/python scripts/validate_sources.py demo_data/csv/minimal_valid_kb.csv
```

| Column | Tier | Purpose | Accepted Values / Example |
| --- | --- | --- | --- |
| `source_id` | Required | Stable source identifier | `kb_001` |
| `source_title` | Required | Human-readable citation title | `Password Reset Guide` |
| `source_type` | Required | Reference file/source family | `csv` |
| `is_approved` | Required | Admits source into governed retrieval | `true` or `false` |
| `is_active` | Required | Keeps disabled docs out of retrieval | `true` or `false` |
| `is_customer_facing_allowed` | Required | Permits customer-facing citation | `true` or `false` |
| `body` | Required | Content to chunk and retrieve | Article text |
| `product_area` | Recommended | Improves routing and retrieval | `billing` |
| `issue_class` | Recommended | Improves routing and retrieval | `login_issue` |
| `version_scope` | Recommended | Product version applicability | `v2` |
| `source_authority` | Recommended | Ranking/trust signal | `canonical`, `approved`, `conditional` |
| `approved_at` | Recommended | Approval timestamp | ISO date |
| `needs_review_at` | Recommended | Freshness signal | ISO date |
| `reviewed_by` | Governance | Audit metadata | `support_ops` |
| `doc_type` | Governance | Finer content category | `faq`, `policy`, `known_issue` |
| `escalation_risk` | Governance | Safety escalation signal | `low`, `medium`, `high` |

Source eligibility truth table:

| `is_approved` | `is_active` | `is_customer_facing_allowed` | Retrievable? | Citable customer-facing? | User sees |
| --- | --- | --- | --- | --- | --- |
| true | true | true | Yes | Yes | Eligible citation |
| true | true | false | No | No | Excluded as internal-only |
| true | false | true | No | No | Excluded as inactive |
| true | false | false | No | No | Excluded as inactive/internal |
| false | true | true | No | No | Excluded as unapproved |
| false | true | false | No | No | Excluded as unapproved/internal |
| false | false | true | No | No | Excluded as inactive/unapproved |
| false | false | false | No | No | Excluded as unsafe |

Re-ingestion semantics: re-running ingest on a changed file computes a new document hash, creates a new document version, and tombstones old active chunks through `plan_document_reingestion` and `tombstone_existing_document_chunks`. Cached chunks missing safety metadata are hydrated from the DB before use; Phase 7 cache tests keep superseded chunks from returning.

Demo files:

- `demo_data/csv/minimal_valid_kb.csv`
- `demo_data/csv/invalid_examples/`
- `demo_data/csv/resolvekit_demo_kb.csv`
- `demo_data/xlsx/resolvekit_demo_kb.xlsx`
- `demo_data/pdf/pdf_manifest.csv`

Regenerate demo assets:

```bash
.venv/bin/python scripts/generate_resolvekit_demo_data.py
```

## Permissions

Viewer token:

- Create draft
- View draft
- View citations
- View trace summary
- Submit feedback

Admin token:

- All Viewer permissions
- Full trace JSON
- Replay
- Source ingest/edit/disable
- Support bundle/export
- Eval and A/B runs
- Config changes
- Audit log

## Safety Rules

- Suggestions only, never auto-send.
- `mode: "suggest"` is required.
- Customer-facing citations must come from approved, active, customer-facing sources.
- Raw tickets, chats, calls, and emails cannot be cited as proof.
- Missing source metadata fails closed.
- Disabled or inactive chunks must not be retrieved.
- Validation blocks unsupported or unsafe claims.
- Red-confidence customer-facing drafts must abstain.
- Logs, traces, exports, and API responses must not expose secrets or private raw ticket data.

## Config Reload Semantics

Doctor and startup logs report the resolved absolute path for each runtime config file. A source label of `local` means `config/*.yaml` exists and overrides the tracked example. `example` means ResolveKit is using `config/*.example.yaml`. `default` means only built-in defaults are active.

Config map:

| File / Surface | Purpose | User Should Edit? | Takes Effect |
| --- | --- | --- | --- |
| `.env.docker` | Provider keys, DB, runtime secrets for Docker | Yes | Restart containers |
| `.env` | Provider keys, DB, runtime secrets for local Python | Advanced | Restart |
| `config/products.yaml` | Product identity, aliases, platforms, roles | Yes | Restart/reload |
| `config/sources.yaml` | Source registry, paths, policy | Yes | Re-ingest for source changes |
| `config/output.yaml` | Draft tone and visible sections | Yes | Live/next resolve |
| `config/retrieval_policy.yaml` | Retrieval weights and chunking rules | Advanced | Restart or re-ingest by field |
| `config/workflow.yaml` | Suggest-only workflow behavior | Rarely | Live/next resolve |

| Runtime file | Applies | Reload behavior |
| --- | --- | --- |
| `.env` / `.env.docker` | Provider keys, DB URL, auth secrets, CORS, warmup flags | Restart |
| `config/products.yaml` | Product identity, aliases, platforms, roles | Restart |
| `config/sources.yaml` | Source paths, column mappings, enabled sources | Re-ingest |
| `config/output.yaml` | Output mode, audience, visible draft sections | Live |
| `config/retrieval_policy.yaml` | Route weights, source authority, chunk/context rules | Live for weights; re-ingest for chunk/context fields |
| `config/workflow.yaml` | Evaluator, retry, and suggest-only workflow controls | Live |

Source-policy, source-path, chunking, and contextual-retrieval edits silently require re-ingestion because stored chunks keep the metadata created at ingest time. Output and workflow edits apply on the next `/resolve` call unless they touch runtime secrets or provider selection.

## Eval And A/B

Golden set: `eval/golden_set/v3_1_starter.jsonl`.

Runner:

```bash
bash scripts/ci_golden_eval.sh
```

Fresh live result capture, when the local API is running:

```bash
.venv/bin/python scripts/generate_golden_results.py
bash scripts/ci_golden_eval.sh
```

Metrics:

- Recall@3 / Recall@5
- MRR
- Source precision
- Citation precision
- Fallback rate
- Validation outcome counts
- Reviewer-ready proxy
- p50/p95 latency
- Cost
- Hard failures

Evaluation outcome: stored results showed the workflow could demonstrate retrieval, cited drafting, validation, and trace review, but source precision, required-point coverage, and confidence calibration were not strong enough for production use. These weak metrics are kept visible because they explain why the project is frozen as a reference implementation.

## Admin Analytics

ResolveKit stores admin analytics from traces, feedback, review queue rows, knowledge issues, API call costs, and explicit `analytics_event` rows. The admin page keeps Analytics, Usage, Retrieval, Evaluation, Costs, Knowledge Gaps, Sources, Replay, Audit, and Config in one shell so admins do not need a separate configurator tab for routine inspection.

Report categories:

- Usage: total queries, active users, active teams, top products, top roles, event counts.
- Retrieval: no-answer rate, low-confidence rate, average top score, most retrieved sources.
- Evaluation: helpful rate, negative feedback, review-required count, feedback reasons, agent actions.
- Knowledge gaps: open issues, missing-source feedback, wrong-source feedback, stale-source feedback.
- Escalations: review queue volume, escalation count, source issue types.
- Costs: trace-level cost, API-call cost, average cost per query, API call count, p95 latency.

Multi-user tracking is intentionally lightweight for the local reference implementation. `/resolve` and `/feedback` accept `user_id`, `team_id`, and `session_id` fields or the equivalent `x-resolvekit-user`, `x-resolvekit-team`, and `x-resolvekit-session` headers. If no user is supplied, the API falls back to a short hash of the API token. This supports demo and internal team reporting without introducing full session auth.

A/B rules: offline replay only, same golden cases for control and treatment, one changed lever per variant, and historical negative results retained when reports exist locally.

## Where To Start Changing Code

| Goal | Files |
| --- | --- |
| Change UI | `frontend/ticket/index.html`, `frontend/configurator/index.html`, `frontend/admin/index.html`, `frontend/onboarding/index.html` |
| Change API route behavior | `backend/api/app.py` |
| Change orchestration | `backend/core/orchestrator.py` |
| Change retrieval/drafting/validation | `pipeline/retriever.py`, `pipeline/reranker.py`, `pipeline/responder.py`, `pipeline/validation.py` |
| Change ingest | `knowledge_loader/kb_loader.py`, `knowledge_loader/source_contract.py` |
| Change demo/production readiness checks | `scripts/run_golden_eval.py`, `scripts/ci_golden_eval.sh`, `scripts/demo_doctor.sh` |

## Demo And Production Readiness

Final status: local Docker demo/reference implementation only. Production readiness is not approved.

Current stored eval status lives in the root README and `docs/README.md`. Treat those metrics as final reference-project measurements, not an active quality target or release gate.

## Quickstart

This quickstart is for local demo/review only. It is not a production deployment guide.

```bash
git clone <repo-url>
cd <repo-directory>
./get_started.sh
```

`get_started.sh` is Docker-first. It detects the OS, verifies Docker Desktop/Compose, starts Postgres plus the onboarding wizard in containers, and opens the browser. The wizard prompts for the user's own OpenAI or Gemini key and writes it only to local `.env.docker`. API routes use `x-api-key`. `/api/me` returns the active role and permissions.

Useful onboarding commands:

```bash
docker compose exec onboarding python scripts/onboarding_doctor.py
docker compose exec onboarding python scripts/init_project.py --demo
docker compose exec onboarding python scripts/init_project.py --product-name "Your Product" --source-folder "demo_data/onboarding"
```

Common failures:

| Failure | Expected behavior |
| --- | --- |
| Missing provider key | Names the exact missing env var |
| Docker not running | Says Docker is required for quick start |
| Port 8000/8765 in use | Shows the conflict and fix |
| Bad CSV row | Names row, column, and expected value |
| No approved sources | Says rows were excluded by source flags |
| Unsupported ingest file | Names supported preview formats and points to the template |

## Security And Exports

- Tokens are local environment secrets.
- Viewer/Admin permissions are enforced server-side.
- Admin actions are written to `experiments/audit_log.jsonl`.
- Support bundles report redaction status and exclude environment secrets.
- Source safety requires approved, active, customer-facing sources.
- Red-confidence drafts must abstain.

Admin-only exports:

- Support bundle by trace ID
- Trace JSON
- Stage logs JSONL
- Retrieved chunks summary
- Final answer
- Validation report
- Config snapshot
- Local eval and A/B reports, when present
- Local audit log JSONL, when present

Export events are audited.
