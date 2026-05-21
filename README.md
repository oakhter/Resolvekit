# ResolveKit

ResolveKit is a self-hosted, source-backed support-resolution framework.

It drafts suggested support replies using approved sources, citations, confidence scoring, validation, and traces.

It is suggest-only, not an autonomous support agent. It does not auto-send, auto-resolve, mutate customer accounts, or generate KB articles from raw tickets.

## Why Not Just Attach Context To GPT?

Attaching files or pasting context into a general LLM works for one-off support questions. It breaks down when the same workflow needs to run every day across many agents, products, permission levels, and source updates.

ResolveKit makes the workflow repeatable:

- retrieves only the relevant approved chunks instead of sending every possible document
- controls token use and provider cost per request
- applies the same source rules, citation rules, and safety checks every time
- records traces, token usage, latency, cost, validation results, and feedback
- separates approved knowledge from raw tickets, chats, calls, and emails
- gives support teams an API and eval loop instead of a manual prompt habit

The value is not that GPT cannot use context. The value is turning context use into a governed, measurable, lower-waste support system that can scale beyond individual ad hoc chats.

## Who It Is For

Support-ops engineers and developers embedded in support teams.

Good fit:

- teams comfortable with Docker, API keys, source configuration, and evals
- teams that need cited, reviewable draft replies
- teams that care about approved-source boundaries and traceability

Not target yet:

- no-code users expecting hosted SaaS
- helpdesk replacement buyers
- autonomous support-agent use cases

## Public Alpha Status

| Area | Status | Notes |
| --- | --- | --- |
| Suggest-only `/resolve` | Done and tested | `mode: "suggest"` enforced. Unknown request fields rejected. |
| Approved-source citations | Done and tested | Validation fails closed for unsafe citation paths. |
| Trace fetch/replay | Done and tested | Redacted, bounded RunTrace records. |
| Feedback capture | Done and tested | Agent action, edit distance, kept citations, review queue signals. |
| Daily metrics snapshots | Done but not externally validated | API and aggregation exist; real-user metrics need deployment data. |
| Docker public smoke | Done but not externally validated | `scripts/public_smoke.sh` added; outside clean-machine run still required. |
| Source preview | Done and tested | Auth, size cap, MIME/suffix allowlist, repo-root path boundary. |
| Multi-format alpha loaders | Done locally | CSV, XLSX, and born-digital PDF fixtures normalize to `SourceRecord`; DOCX/HTML/OCR remain outside alpha. |
| 100-case golden set | Partial | Current set is 50+ cases; 100-case target remains open. |
| Trace viewer UI | Partial | API visibility exists; richer support-ops UI still planned. |
| Advanced reasoning experiments | Done, default-on as bounded experiment | Typed planner output, query decomposition, evidence table, structured reply rendering, validation upgrades, and trace fields exist behind workflow experiment config. |
| No-code setup | Planned | Future onboarding layer, not current product identity. |

## Safety Guarantees

ResolveKit is designed around these invariants:

- suggestions only, never auto-send
- approved evidence only for customer-facing citations
- raw tickets/chats/calls/emails cannot be cited as proof
- validation blocks unsupported or unsafe claims
- traces are redacted and bounded
- config/source-preview/admin-style routes require auth
- source preview cannot read arbitrary local paths

Details: [Technical Guide](docs/TECHNICAL.md).

## Run It

Fast local path:

```bash
git clone <repo-url>
cd <repo-directory>
./get_started.sh
```

`get_started.sh` is Docker-first. It detects macOS, Linux, or WSL, verifies Docker Desktop/Compose, starts Postgres plus the onboarding wizard in containers, and opens the wizard. The wizard asks for your own OpenAI or Gemini API key and writes it only to local `.env.docker`. No hosted LLM token is committed or shared with demo data.

Shortcut:

```bash
make get-started
```

Developer local setup remains available:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/init_project.py --demo
.venv/bin/python scripts/generate_resolvekit_demo_data.py
.venv/bin/python scripts/setup_db.py
.venv/bin/python knowledge_loader/kb_loader.py
.venv/bin/python start.py
```

Required `.env` basics:

```env
ACTIVE_PROVIDER=openai
OPENAI_API_KEY=
DATABASE_URL=postgresql://resolvekit:resolvekit@localhost:5432/resolvekit
KNOWLEDGE_SCHEMA=knowledge
OPS_SCHEMA=ops
API_KEY=change-me
CONFIGURATOR_API_KEY=change-me-configurator
VIEWER_TOKEN=replace-with-random-viewer-token
CONFIGURATOR_ADMIN_TOKEN=replace-with-random-admin-token
CONFIGURATOR_PREFILL_API_KEY=false
CORS_ALLOW_ORIGINS=http://127.0.0.1:8000,http://localhost:8000
```

Supported hosted providers are `openai` and `gemini`. Set only the provider key needed by `ACTIVE_PROVIDER`.

Open:

```text
Ticket workspace: http://127.0.0.1:8000/
Configurator:     http://127.0.0.1:8000/configurator
API docs:         http://127.0.0.1:8000/docs
```

Role check:

```bash
curl -sS -H "x-api-key: $VIEWER_TOKEN" http://127.0.0.1:8000/api/me
curl -sS -H "x-api-key: $CONFIGURATOR_ADMIN_TOKEN" http://127.0.0.1:8000/api/me
```

Docker path:

```bash
cp .env.docker.example .env.docker
# Set API_KEY, CONFIGURATOR_API_KEY, and one provider key.
bash scripts/public_smoke.sh
```

The public smoke script validates Docker startup, database health, demo ingestion, `/health`, `/resolve`, trace fetch, daily metrics, and source preview.

Public alpha deployments should keep `CONFIGURATOR_PREFILL_API_KEY=false`, set non-placeholder API keys, and restrict `CORS_ALLOW_ORIGINS` to the hosts that should call the API.

## What You Should See

Happy path:

- known support ticket produces a suggested reply
- confidence and citations appear
- trace ID appears in the response
- trace fetch shows retrieval, validation, and final response shape
- feedback can record whether the agent sent, edited, or rejected the draft

Safety path:

- unsupported or low-context ticket abstains or asks for missing context
- validation/review queue records why
- unsafe sources are not customer-facing citations


## Workflow

```mermaid
flowchart TD
    A[Approved sources] --> B[Preview + metadata validation]
    B --> C[Knowledge store]
    D[Support ticket] --> E[Redact + normalize]
    E --> F[Retrieve approved evidence]
    C --> F
    F --> G[Draft suggested reply]
    G --> H[Score + validate citations]
    H --> I{Safe enough?}
    I -->|Yes| J[Show suggestion]
    I -->|No| K[Abstain or review]
    J --> L[Trace + feedback + metrics]
    K --> L
```

## LLM Metrics

Every completed hosted LLM call records an `api_calls` row with provider, model, workflow step, input tokens, output tokens, latency, estimated cost, error status, and error message.

`/resolve` responses include `usage_summary` and `llm_workflow` so operators can see token totals and which LLM stages ran. RunTrace records stage-level token usage and pipeline latency for replay and audit.

Metrics surfaces:

- `GET /metrics` returns the last seven days of LLM call count, average latency, estimated cost, and error count.
- `GET /metrics/daily` returns daily snapshots after `.venv/bin/python scripts/aggregate_metrics_daily.py`.
- Feedback records reviewer action, edit distance, kept citations, confidence, and review-queue signals.

## Evaluation Metrics

ResolveKit is evaluated as a support-resolution system, not just as a chatbot. The core question is whether it retrieves approved evidence, drafts a usable answer, cites safely, stays within cost and latency budgets, and abstains when context is missing.

Current stored golden-eval report:

<!-- eval-report:start -->
| Metric | Current Alpha Result | What It Proves |
| --- | ---: | --- |
| Golden cases | 52 | Size of the manually reviewed support-style eval set. |
| Evaluated results | 52 | Release gate used stored outputs, not schema-only placeholders. |
| Source-safety hard failures | 0 | No forbidden, raw-ticket, unapproved, or unsupported customer-facing citations. |
| Recall@1 | 0.4894 | Whether expected evidence was ranked first. |
| Recall@3 | 0.7021 | Whether expected evidence appeared in the first three sources. |
| Recall@5 | 0.766 | Whether expected evidence appeared in the first five sources. |
| MRR | 0.5798 | Whether the first correct source was ranked near the top. |
| Source precision | 0.2688 | Whether retrieved sources matched expected/allowed sources. |
| Citation recall | 0.766 | Whether expected evidence was cited in the final answer. |
| Citation precision | 0.2688 | Whether final citations were expected/allowed. |
| Required point coverage | n/a | Deterministic check for expected answer content. |
| Route accuracy | 1 | Whether tickets were classified into the expected support route. |
| Confidence accuracy | 1 | Whether green/yellow/red confidence matched expected behavior. |
| Abstention accuracy | 1 | Whether missing-context/review cases abstained correctly. |
| P50 latency | 8597 ms | Median response-time signal for alpha runs. |
| P95 latency | 11152.25 ms | Tail response-time signal for alpha runs. |
| Avg total tokens | n/a | Average prompt+completion tokens per stored result. |
| Avg cost/query | 0.0005 USD | Cost copied from `/resolve` usage fields. |
| Total reported LLM cost | 0.024 USD | Total reported cost for the stored golden run. |
| Release gate passed | True | Current stored-result release gate status. |
<!-- eval-report:end -->

Rank-aware retrieval metrics are also computed by the golden-eval runner:

| Metric | Why It Matters |
| --- | --- |
| Recall@1 | Shows whether expected evidence is ranked first. |
| Recall@3 | Shows whether expected evidence appears in the first three retrieved sources. |
| Recall@5 | Shows whether expected evidence appears in the first five retrieved sources. |
| Mean reciprocal rank | Rewards systems that rank the first correct source near the top. |
| Citation recall and precision | Shows whether the final answer cites expected and approved supporting sources. |
| Required point coverage | Shows whether the generated answer includes deterministic expected points when stored answers are available. |
| Token usage and cost/query | Shows whether retrieval keeps per-query context economical. |
| Validation/review warning count | Shows how often outputs need human review despite avoiding hard safety failures. |

These numbers are intentionally visible during alpha. The current gate passes because source-safety hard failures are zero and evaluated results are present, but source precision, answer validation, and broader golden-set coverage are still active improvement areas.

Live retrieval-arm comparison was run on May 19, 2026 against the local `/resolve` API with 52 golden cases per arm. Current result: keep `current_hybrid_rag` as the baseline/default path. Query decomposition did not improve Recall@3/Recall@5, reduced Recall@1/MRR slightly, and added about 400 ms to median latency in this run.

| Retrieval Arm | Repo Status |
| --- | --- |
| Current hybrid RAG | Baseline path. |
| Current RAG + query decomposition | Implemented through `retrieval_strategy_v1`; live A/B tested, not better than baseline on latest run. |
| Graph-style retrieval-style layer | Disabled/fail-closed experiment arm. |
| Markdown canonical source + current retrieval | Experiment arm definition only. |

Latest live A/B run summary:

- Command: `.venv/bin/python scripts/run_live_ab_eval.py --delay-seconds 2.2`
- Output: generated locally under `eval/ab/` and intentionally not committed.
- Note: live-generated result rows currently trigger stricter eval hard-failure accounting than the stored release-gate report. Both arms had the same hard-failure count, so the comparison is still useful for arm ranking, but the release gate remains the stored golden report above until the live-result export contract is tightened.

| Metric | current_hybrid_rag | current_rag_query_decomposition | Delta vs baseline |
| --- | ---: | ---: | ---: |
| Evaluated cases | 52 | 52 | 0 |
| Hard failures | 97 | 97 | 0 |
| Recall@1 | 0.4468 | 0.4255 | -0.0213 |
| Recall@3 | 0.7021 | 0.7021 | 0 |
| Recall@5 | 0.7872 | 0.7872 | 0 |
| MRR | 0.5674 | 0.5532 | -0.0142 |
| Source precision | 0.2227 | 0.2223 | -0.0004 |
| Citation recall | 0.7872 | 0.7872 | 0 |
| Citation precision | 0.2227 | 0.2223 | -0.0004 |
| Confidence accuracy | 0.8269 | 0.8269 | 0 |
| Abstention accuracy | 0.8269 | 0.8269 | 0 |
| Fallback rate | 0.6538 | 0.6538 | 0 |
| P50 latency ms | 8645.5 | 9046 | 400.5 |
| P95 latency ms | 13179.75 | 13117.05 | -62.7 |
| Avg cost USD | 0.0005 | 0.0005 | 0 |
| Total cost USD | 0.026165 | 0.026294 | 0.000129 |

## Project Layout

```text
backend/              API, safety modules, DB schema
pipeline/             resolve pipeline stages
frontend/             ticket workspace and configurator
knowledge_loader/     source connectors and KB loading
config/               local YAML config and examples
scripts/              setup, smoke, eval, diagnostics
tests/                unit, integration, API, eval, smoke tests
docs/                 compact strategy, technical, implementation, A/B, release, demo, and LLM context docs
eval/                 golden-set evaluation assets
```

## Common Commands

```bash
./get_started.sh
docker compose exec onboarding python scripts/onboarding_doctor.py
docker compose exec onboarding python scripts/setup_db.py
docker compose exec onboarding python knowledge_loader/kb_loader.py
docker compose exec onboarding python scripts/check_db.py --recent
docker compose exec onboarding python scripts/qa_retrieval.py --with-db
docker compose exec onboarding python scripts/aggregate_metrics_daily.py
docker compose exec onboarding python tests/run_qa.py
bash scripts/ci_golden_eval.sh
bash scripts/public_smoke.sh
```

Clean Docker rebuild:

```bash
docker compose exec onboarding python scripts/rebuild_db.py
docker compose exec onboarding python knowledge_loader/kb_loader.py
```

`rebuild_db.py` drops local schemas. Use only for a fresh local database.

## API

Core endpoints:

| Endpoint | Purpose |
| --- | --- |
| `GET /` | Ticket workspace |
| `GET /configurator` | Local configurator UI |
| `GET /health` | Health check |
| `POST /resolve` | Generate grounded suggestion |
| `POST /feedback` | Store reviewer feedback |
| `GET /draft-runs` | Read-only draft history |
| `POST /knowledge-issues` | Create reviewer-owned knowledge issue |
| `GET /traces/{trace_id}` | Fetch redacted RunTrace |
| `GET /metrics` | Fetch seven-day LLM and quality metrics |
| `GET /metrics/daily` | Fetch daily metrics snapshots |
| `POST /configurator/source-preview` | Preview source parsing before ingestion |

Runtime endpoints require `x-api-key: $API_KEY`. Configurator routes require `CONFIGURATOR_API_KEY`.

Contract summary: [Technical Guide](docs/TECHNICAL.md).

## Docs

- [Docs Index](docs/README.md)
- [Technical Guide](docs/TECHNICAL.md)
- [Demo Guide](docs/DEMO.md)

## Experimental

- no-code onboarding
- hosted SaaS
- multi-tenant deployment
- broad BYOA launch
- OCR
- web crawling
- raw-ticket case learning
- autonomous actions

These require separate product and safety review.

## License

MIT. See [LICENSE](LICENSE).

LLM-generated drafts are suggestions for human review. Deployers are responsible for reviewing, editing, approving, and sending final customer replies under their own policies and provider terms. ResolveKit does not claim ownership over deployer prompts, private source content, tickets, or final customer replies.
