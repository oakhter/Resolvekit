# Changelog

All notable changes to this project are documented here.

## Unreleased

- Public demo onboarding now binds correctly in Docker, uses non-interactive KB loading, routes uploaded custom CSV knowledge into the vector DB even in demo mode, and makes onboarding vector ingest CSV-only instead of silently accepting XLSX/PDF uploads that are preview-only.
- Golden eval release gates now support explicit `public_alpha` and `production` profiles. The alpha profile allows the current zero-hard-failure warning state, while the production profile blocks on validation/review count, source precision, Recall@3/5, and required-point coverage targets.
- Source-safety gate recovery now separates customer-facing citations from retrieved evidence context, adds triage reporting, tightens route-critical evidence selection, normalizes stale abstention drafts out of customer-facing evals, and refreshes the golden run: release gate passes with 0 hard failures, 12 validation/review warnings, Recall@3/5 at 0.6596, source precision at 0.4716, citation precision at 1.0, with the focused eval/source-safety suite passing.
- Phase 8 advanced reasoning experiment is implemented with typed planner output, query-decomposed retrieval, evidence tables, structured reply rendering, stricter validation, and trace/retrieval diagnostics.
- Phase 9 GraphRAG is registered only as a disabled/fail-closed retrieval experiment arm; current RAG plus query decomposition is the active experiment path.
- Public demo docs were simplified into a short README, compact docs index, demo guide, technical guide, code-map JSON, and one root simplification checklist. The older local planning docs and release checklist were removed from the public workspace.
- Added `scripts/demo_doctor.sh` and `make doctor` as the one-command demo readiness check, with terminal status plus JSON/Markdown reports under `diagnostics/demo_doctor/`.
- Ticket workspace no longer blanks after rendering a resolved ticket, and advanced retrieval controls were removed from the main ticket form.
- Ticket and configurator HTML routes now disable browser caching, and the ticket workspace has a render error boundary instead of a blank root.
- Demo 403/login tickets now retrieve the approved mobile-login article again instead of abstaining from stale cache, over-redacted query text, or role-filtered evidence.
- Draft-run persistence now stores plain citation/source strings as valid JSON arrays for JSONB columns.
- Live retrieval A/B evaluation now supports request-scoped `experiment_arm` routing, isolates ticket/response caches by arm, writes per-arm reports under `eval/ab/`, and records the latest baseline-vs-query-decomposition comparison in README.
- README and docs now show refreshed demo-readiness metrics from the latest stored golden eval.
- Golden eval source matching now supports source aliases and rank-aware retrieval metrics from real retrieved source IDs.
- `/resolve` usage summaries now include response-level cost for future golden result generation.

## v0.1.0-public-alpha - 2026-05-15

### Added

- Configurator API routes now use a separate `CONFIGURATOR_API_KEY`, and source preview enforces a CSV allowlist plus a 25 MB default file-size cap.
- README now states the "What ResolveKit Is NOT" boundary before setup details, documents MIT plus LLM-output posture, and adds the v3.6 framework vocabulary audit.
- `/resolve` now rejects any non-`suggest` responder output mode at the API boundary.
- Configurator impact labels now render roadmap badge wording: `Live ✓`, `Reload required`, and `Restart required`.
- Validator now blocks invalid KB-shaped citation syntax and accepts only `[KB-N]` customer-facing citations.
- v3.1 redaction hardening now covers configured customer names, account IDs, addresses, payment identifiers, secrets/API keys, emails, and phone numbers across ingestion, retrieval-to-LLM context, final output, trace storage, and previews.
- v3.1 feedback capture now includes trace/response IDs, product/platform/role context, citation IDs, structured feedback reasons, comments, and negative-feedback review queue creation.
- v3.1 review queue entries now carry age/SLA fields and are created for validator failures, low/red confidence, source conflicts, sensitive routes, escalation paths, and safe abstentions.
- v3.2 conflict detection now includes version-specific behavior conflicts, and RunTrace stores source-type merge details.
- v3.2 configurator source previews now support commit/cancel/download actions, and source authority editors hide raw/forbidden source weights while preserving runtime clamps.
- v3.3 saved trace replay now has a redacted offline command and API endpoint with retrieval, citation, confidence, validation, response, latency, and cost diffs.
- v3.3 paired config replay now compares two stored golden-result files offline and reports retrieval, precision, confidence, abstention, validation, latency, cost, and hard-failure deltas.
- Prompt versions now live in a prompt registry, are stored in RunTrace with model/provider context, and have a prompt changelog plus rollback path.
- Golden eval reporting now includes RAGAS-style faithfulness, RAG triad fields, human-readable Markdown output, and baseline comparison.
- Source freshness reporting now lists stale/near-review sources, warns on high-impact stale sources, and can queue stale-source re-review items.
- Golden eval was run after v3.3 prompt/replay/reporting changes and stored JSON/Markdown reports under `eval/golden_set/`.
- v3.4 support-ops UX adds a first-run setup wizard, field-level config metadata, dirty/pending-change indicators, setup completion tracking, shared UI contracts, query/chat support modes, source pinning, similarity threshold controls, and screenshot refresh automation.
- v3.5 stabilization docs now record benchmark-based roadmap triggers, dependency guardrails, restart-reduction planning, market positioning, and the deliberate v4 direction choice.
- v4 Option B headless/BYOA API groundwork now includes stable `/sources`, `/eval/run`, and `/review-queue` endpoints, API productization docs, webhook and SDK examples, operational playbooks, future-risk gates, and API compatibility checks.
- Source license and attribution metadata now flows through source contracts, source previews, ingestion, database schema, retrieval selects, source listing, and attribution warnings.
- Release operations now include a golden-eval CI wrapper, redacted admin audit export script, and performance smoke script.
- Documentation is consolidated into compact top-level guides under `docs/`.
- `docs/llm/AGENTS.md` now includes future version checklists and per-version definition-of-done gates for v3.6 through v4.0.
- Docker baseline files now support a repeatable app + pgvector Postgres setup.
- `DEMO_MODE` now controls whether the loader uses committed sandbox demo CSV files or expects custom sources.
- `updated_at`, `redaction_status`, and `redaction_applied` evidence metadata now travel through source records, DB schema/migrations, ingestion, retrieval, validation, and RunTrace.
- Workflow config now includes `trace_retention_days`, and trace writes prune expired RunTrace rows.
- Technical contributor context now lives in `docs/TECHNICAL.md`, covering setup, architecture, `/resolve`, ingestion, permissions, safety gates, tracing, testing, and the editable Draw.io diagram.
- Main ticket workspace now shows response-cache and retrieval-cache status in the output metadata.
- Configurator sidebar now includes a back link to the main workspace.
- Draft-unavailable messaging now explains safe abstention/no-approved-source cases instead of leaving the draft panel blank.
- Public release checklist now has a top-level "Remaining Work Before Public Repo" section that collects the still-open release actions in one place.
- Product config helpers now expose canonical ingestion names and retrieval alias sets so the loader and retriever share the same product identity rules.
- Demo knowledge now includes an approved mobile-login 403 article so the API smoke ticket has direct customer-facing evidence.
- README now presents a shorter public-facing setup path, GitHub-compatible Mermaid workflow, baseline demo metrics, source-safety boundary, and the simplified public folder layout.
- Source connector groundwork now defines `SourceDocument`/`SourceSection`, connector dispatch, CSV/PDF/DOCX/XLSX/HTML preview parsing, fail-closed connector errors, and tiny connector fixtures.
- Operator visibility now includes a trace summary list endpoint, per-source ingestion/freshness/quality status, and clearer configurator sandbox draft-unavailable reasons.
- Golden eval gating now includes 50+ cases with connector-format coverage, baseline regression blocking, latency/cost budgets, and release blockers for source-safety hard failures.
- The compact technical guide documents the stable headless/BYOA surface, and `/resolve` rejects unknown fields that could try to bypass validation.
- Baseline metrics capture now records agent action, final sent text, token edit distance, kept citations, daily metrics snapshots, and a `/metrics/daily` API.
- Ingestor now redacts incoming ticket text before downstream retrieval/planning fields and keeps only a hash of the raw ticket text.
- Docs were consolidated into fewer reader-job guides: strategy, technical, implementation sequence, A/B plan, release, demo, and LLM context.
- Public alpha docs now include truth-status labels and a clean-machine Docker smoke script.

### Changed

- Project branding now uses `ResolveKit` across README, API metadata, scripts, Docker defaults, docs, tests, and frontend UI.
- Frontend light and dark themes now use a neutral slate, blue, and teal palette for the ticket workspace and configurator.
- Main ticket workspace footer no longer says `Internal Use Only`.
- Main ticket workspace output metadata now resolves configured product display names instead of falling back to slug-style labels.
- Ticket-level cache hits refresh request context and mark response-cache usage so UI and feedback metadata are accurate.
- README and public LLM review context now describe the current scope as ResolveKit public technical alpha hardening.
- `.env.example` and `.env.docker.example` now use `resolvekit` database names instead of legacy placeholders.
- `docs/llm/AGENTS.md` now aligns with the public-alpha implementation strategy and demotes unverified items.
- Server-backed API smoke tests now accept safe no-draft/no-retrieval abstention responses instead of requiring a customer draft for every successful `/resolve`.
- `knowledge_loader/kb_loader.py` now canonicalizes product values through product config before writing chunks, keeping future product-specific DB rows aligned with setup choices.
- Retrieval now matches configured product slugs, display names, and aliases instead of requiring one exact stored product string.
- Output preferences now let explicit `include` toggles override mode defaults, so `email_draft_only` can still return configured diagnostic and confidence fields.
- Retrieval cache hits now hydrate older cached chunks from the knowledge DB when strict evidence metadata is missing.
- Project folders are now grouped into `backend/`, `pipeline/`, and `frontend/` instead of separate top-level `api/`, `core/`, `db/`, `providers/`, `pipeline_steps/`, and `ui/` folders.

### Fixed

- Existing approved customer-facing knowledge rows now backfill missing `updated_at` metadata during schema setup instead of relaxing evidence validation.
- Strict evidence validation now accepts subtype source labels through their approved base source type while still requiring complete customer-facing metadata.
- Configurator source preview now rejects paths outside the project directory.
- v3.5 QA hardened release-operation scripts so `performance_smoke.py` and `export_admin_audit.py` run from the documented `python scripts/...` form, and the golden-eval CI wrapper uses the project venv when available.
- Reset local demo product display name back to `Example Product`.
- Removed the ticket workspace shortcut that labeled a repeated same-ticket submission as "Served from cache" without making a server request.
- Fixed deterministic retrieval QA fixture metadata so it satisfies the stricter typed evidence validation contract.
- Offline golden eval reports now include route, confidence, retrieval, precision, latency, cost, and hard-failure keys even when no stored result file is supplied.

## v3.0 - 2026-05-01

### Added

- Fictional demo source files for a reusable support app example: knowledge base, policies, release notes, known issues, and offline-only historical tickets.
- Public demo documentation: demo product guide, troubleshooting guide, public release checklist, and illustrative UI screenshots.
- MIT license.
- Public demo checks that verify required assets and product-agnostic examples.

### Changed

- Example product and source defaults now point to neutral demo data.
- Browser UI files now live under `frontend/`: `frontend/ticket/index.html` and `frontend/configurator/index.html`.
- Startup is local-only; tunnel startup support was removed.
- README, AGENTS, ROADMAP, QA docs, and folder map now describe the reusable framework without project-local machine paths.

### Removed

- Removed stale processed source exports and scraper caches from the public demo data set.
- Removed the old generated project overview page.
