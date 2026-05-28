# ResolveKit Technical Guide

ResolveKit is a local-first, source-grounded, suggest-only support drafting framework. This guide is the fast technical orientation: what runs, how a request moves through the system, where safety is enforced, and how to verify the alpha path.

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
- `knowledge_loader/`: source connectors and alpha `SourceRecord` contract.
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

## Source Contract

Alpha supports CSV, XLSX, and born-digital PDF with manifest metadata.

Canonical shape lives in `knowledge_loader/source_contract.py` as `SourceRecord`.

Required fields:

`source_id, source_title, source_type, source_authority, is_approved, is_active, is_customer_facing_allowed, approved_at, reviewed_by, needs_review_at, doc_type, product_area, issue_class, version_scope, escalation_risk, body`

Demo files:

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

## Eval And A/B

Golden set: `eval/golden/resolvekit_v0_1.jsonl`.

Runner:

```bash
.venv/bin/python -m eval.run --config configs/baseline.yaml --golden eval/golden/resolvekit_v0_1.jsonl --output runs/baseline.jsonl
```

A/B configs live in `configs/ab/`.

```bash
.venv/bin/python scripts/materialize_ab_configs.py
.venv/bin/python scripts/run_ab_stage2_eval.py
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

## Admin Analytics

ResolveKit stores admin analytics from traces, feedback, review queue rows, knowledge issues, API call costs, and explicit `analytics_event` rows. The admin page keeps Analytics, Usage, Retrieval, Evaluation, Costs, Knowledge Gaps, Sources, Replay, Audit, and Config in one shell so admins do not need a separate configurator tab for routine inspection.

Report categories:

- Usage: total queries, active users, active teams, top products, top roles, event counts.
- Retrieval: no-answer rate, low-confidence rate, average top score, most retrieved sources.
- Evaluation: helpful rate, negative feedback, review-required count, feedback reasons, agent actions.
- Knowledge gaps: open issues, missing-source feedback, wrong-source feedback, stale-source feedback.
- Escalations: review queue volume, escalation count, source issue types.
- Costs: trace-level cost, API-call cost, average cost per query, API call count, p95 latency.

Multi-user tracking is intentionally lightweight for alpha. `/resolve` and `/feedback` accept `user_id`, `team_id`, and `session_id` fields or the equivalent `x-resolvekit-user`, `x-resolvekit-team`, and `x-resolvekit-session` headers. If no user is supplied, the API falls back to a short hash of the API token. This supports demo and internal team reporting without introducing full session auth.

A/B rules: offline replay only for alpha, same golden cases for control and treatment, one changed lever per variant, negative results retained, and ship/no-ship decisions recorded under `experiments/decisions/`.

## Release Gates

- [ ] Docker quickstart works cleanly.
- [x] Viewer token works.
- [x] Admin token works.
- [x] CSV demo data loads.
- [x] XLSX demo data loads.
- [x] PDF demo data loads.
- [ ] At least one green, yellow, and red case work.
- [x] Trace viewer works.
- [x] Replay works by trace ID.
- [x] Support bundle export works.
- [x] Eval runner works.
- [x] One A/B stage with five variants runs.
- [x] Secret scan passes.
- [x] Source-safety hard failures equal zero.
- [x] Red-confidence drafts abstain.

## Quickstart

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
- Eval and A/B reports from `eval/reports/` and `experiments/reports/`
- Audit log JSONL from `experiments/audit_log.jsonl`

Export events are audited.
