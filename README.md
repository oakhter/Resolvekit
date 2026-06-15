# ResolveKit

ResolveKit is a **local-first, suggest-only support-drafting kit** and support-facing RAG starter kit. It drafts replies from approved sources only, cites every claim, abstains when unsure, and never sends anything without human review.

It is **suggest-only** and **not an autonomous support agent**. It does not auto-send, auto-resolve, mutate customer accounts, or turn raw tickets into customer-facing knowledge. Calls to `/resolve` are intended to run with `mode: "suggest"` and human review.

> Do not load private customer data into a public or shared instance. ResolveKit is local-first and demo-oriented; you are responsible for what you ingest and where you run it. Ingested content and submitted tickets are sent to your configured LLM provider and stored in the local database and trace store.

Privacy boundary: ticket text and retrieved KB snippets go to the configured provider, OpenAI or Gemini. KB chunks, traces, and doctor reports stay in the local Postgres volume and trace store. Hashed tickets, not raw tickets, enter traces. local-first doesn't mean offline. Exposing beyond localhost exposes traces and admin analytics.

## Current Status

Public developer preview. Demo readiness is passing on the stored evaluation set; production readiness is not approved.

<!-- eval-report:start -->
| Metric | Current value |
| --- | ---: |
| Demo readiness | passed |
| Golden cases | 52 |
| Source-safety hard failures | 0 |
| Validation/review warnings | 12 |
| Recall@3/5 | 0.6596 |
| Source precision | 0.4716 |
| Citation precision | 1 |
| Required-point coverage | 0.0577 |
| Total eval cost | 0.0232 USD |
| Production readiness | not approved |
<!-- eval-report:end -->

Treat every draft as a reviewer aid. Retrieval quality still needs work before production use: warnings are too high, source precision is below target, and required-point coverage is low.

## Why This Exists

Teams experimenting with support RAG should not have to rebuild the same plumbing every time: Docker setup, CSV KB ingest, pgvector retrieval, citations, validation, traces, metrics, and evals. ResolveKit keeps that workflow in one small project so teams can test the shape of a governed support-drafting system before investing in a full platform.

## Quick Start

Docker is the canonical public-preview path. Local Python mode is for development and needs Postgres with pgvector; use Docker if unsure.

```bash
git clone <repo-url>
cd <repo-directory>
cp .env.docker.example .env.docker   # add your provider key and local secrets
./get_started.sh
make doctor
```

`get_started.sh` starts Docker Postgres plus the onboarding wizard at:

```text
Onboarding wizard: http://127.0.0.1:8765
Ticket workspace:  http://127.0.0.1:8000/
```

The wizard asks for an OpenAI/Gemini key, or `ACTIVE_PROVIDER=mock` for no-key preview, and stores secrets only in local `.env.docker`.

You're set up when:
- `make doctor` prints `Demo readiness: READY`
- The ticket workspace opens at `http://127.0.0.1:8000/`
- The demo ticket returns a draft with citations
- Confidence and validation status are visible
- The trace link opens a redacted run trace

Shortcut:

```bash
make get-started
```

Entry points:

| Command | Use When |
| --- | --- |
| `./get_started.sh` | First-time Docker onboarding |
| `make doctor` | Checking demo readiness |
| `bash scripts/public_smoke.sh` | Running the public smoke test |
| `python start.py` | Local development path, advanced |
| `scripts/demo_start.sh` | Maintainer/demo helper |

## Demo Doctor

Run one command to check local readiness:

```bash
make doctor
```

This runs Docker checks, config checks, secret/local-path hygiene, focused tests, stored evaluation, Docker smoke, and onboarding endpoint checks. It writes:

```text
diagnostics/demo_doctor/latest.json
diagnostics/demo_doctor/latest.md
```

The terminal summary uses plain language:

```text
Demo readiness: READY
Production readiness: NOT READY
```

## What It Does

ResolveKit takes a support ticket, retrieves approved knowledge, drafts a suggested reply, validates citations, records traces, and exposes metrics.

```text
Ticket -> Retrieval Plan -> Approved Sources -> Rerank -> Evidence Bundle -> Draft -> Validate -> Confidence -> Trace/Review
```

**Public preview ingest supports CSV and XLSX preview/validation; vector load remains CSV-first.** Bring your own docs by starting from `demo_data/onboarding/source_manifest_template.csv`, previewing row-level issues, ingesting valid approved rows, then running a demo ticket. Envelope: single KB namespace per deployment, English-only prompts/eval, OpenAI/Gemini or mock preview, pgvector, loopback URLs.

## What This Is

- Local/self-hosted developer preview
- CSV-first support knowledge ingest
- Cited draft suggestions for support reviewers
- Confidence, abstention, validation, and redacted traces
- Human review required before any customer response

## What This Is Not

- Not production-approved
- Not an autonomous agent
- Not a customer chatbot
- Not a helpdesk replacement
- Not auto-send, auto-resolve, or account action
- Not a source-of-truth editor
- Not multi-tenant SaaS
- Not for end customers
- Raw tickets are never cited as evidence

Suggest-only contract: use `mode: "suggest"`. Other modes are rejected server-side as a safety feature.

Happy path:

- upload or use a CSV knowledge base
- ingest it into Postgres/pgvector
- ask for a suggested reply
- inspect citations, confidence, and trace data
- record feedback on whether the draft was useful

Sample walkthrough: paste "Customer cannot sign in on mobile app after a role change"; expect a cited draft, confidence band, validation status, and trace link. See `docs/DEMO.md`.

Safety path:

- missing context should lead to abstention or review
- raw historical tickets are not customer-facing evidence
- unsupported claims are blocked or flagged

## Configuration Files

Config map:

| File / Surface | Purpose | User Should Edit? | Takes Effect |
| --- | --- | --- | --- |
| `.env` | Provider keys, DB, runtime secrets for local Python | Yes | Restart |
| `.env.docker` | Provider keys, DB, runtime secrets for Docker | Yes | Restart containers |
| `config/products.yaml` | Product identity, aliases, platforms, roles | Yes | Restart/reload |
| `config/sources.yaml` | Source registry, paths, policy | Yes | Re-ingest for source changes |
| `config/output.yaml` | Draft tone and visible sections | Yes | Live/next resolve |
| `config/retrieval_policy.yaml` | Retrieval weights and chunking rules | Advanced | Restart or re-ingest by field |
| `config/workflow.yaml` | Suggest-only workflow behavior | Rarely | Live/next resolve |
| `configs/baseline.yaml` | Baseline experiment config | No for first demo | Advanced only |
| `configs/ab/` | Offline experiment variants | No for first demo | Advanced only |

Ignore `configs/ab/` unless experimenting.

Required basics:

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

Supported hosted providers are `openai` and `gemini`. Set only the provider key needed by `ACTIVE_PROVIDER`; `model_warmup` checks the selected provider during doctor runs.
No-key preview mode is available with `ACTIVE_PROVIDER=mock`; it returns canned drafts labeled `MOCK PREVIEW` so you can inspect ingest, UI, traces, and review flow without paid API calls.

## Common Failures

| Failure | Good Error Behavior |
| --- | --- |
| Missing provider key | Names the exact missing env var |
| Docker not running | Says Docker is required for quickstart |
| Port 8000/8765 in use | Shows the conflict and fix |
| DB not ready | Says what to wait for or run |
| Bad CSV row | Names row, column, expected value |
| No approved sources | Says rows were excluded by source flags |
| Non-CSV ingest | Says CSV-only and points to the template |
| Provider call fails | Shows provider and safe remediation |

## Code Map

Core work lives in `frontend/`, `backend/api/`, `backend/core/`, `backend/providers/`, `pipeline/`, `knowledge_loader/`, and `scripts/`. See `docs/TECHNICAL.md` and `docs/CODE_MAP.json`.

## AI Transparency And Ethics

This codebase and its documentation were substantially generated with AI assistance and then reviewed through tests and local smoke runs.

The project is meant as a learning and experimentation base for support RAG systems. It is not a drop-in autonomous agent. LLM-generated drafts are suggestions for human review. Teams should follow their own policy for disclosing AI assistance in support workflows.

Do not use raw tickets, chats, calls, or emails as customer-facing evidence. Keep approved knowledge sources separate from historical support data, and validate citations before showing a draft to a customer.

ResolveKit does not claim ownership over deployer prompts, private source content, tickets, or final customer replies.

## Developer Commands

```bash
bash scripts/public_smoke.sh
.venv/bin/python -m pytest tests/test_resolvekit.py -k "onboarding or public_smoke or launch_readiness or diagnostics_masks_secret_values" tests/test_mock_provider.py tests/test_post_launch_hardening.py tests/test_source_contract_properties.py tests/test_ui_snapshots.py
bash scripts/ci_golden_eval.sh
make reset-demo
make reload-kb
```

Logs live under `diagnostics/logs/` and app stdout; attach `diagnostics/demo_doctor/latest.md` after checking it contains no private source content. Open gaps from `llm_review.md`: golden-case audit, warning reduction, GIFs, fresh-machine launch gate, final doctor report, release tag, router/orchestrator cleanup, extra ingest format, true local LLM. No SaaS, billing, or enterprise promises.

## API Shape

Primary endpoint:

```text
POST /resolve
```

Request model rejects unknown fields. Use suggest mode:

```json
{
  "mode": "suggest",
  "ticket": "Customer cannot sign in on mobile app after a role change.",
  "product": "example_product",
  "permission_level": "agent",
  "access_channel": "mobile_app"
}
```

See [docs/TECHNICAL.md](docs/TECHNICAL.md) for API routes, trace fields, metrics, and safety details.

## License

MIT. See [LICENSE](LICENSE).
