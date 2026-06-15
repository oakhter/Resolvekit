# ResolveKit — Public Preview Implementation Plan

**Compiled:** 2026-06-12
**Merged from:** `claude_review.md` and `gpt_review.md`, both reviewing the working tree at `306331b`
**What this is:** every actionable item from both reviews, deduplicated and ordered into execution phases, with paste-ready tables embedded in the phase that uses them. Nothing from either review was dropped; items that appeared in both were merged into a single entry. Phases run in order, each ends with an exit gate, and the gate is the review point before moving on.

---

## Definition of Done

The project ships when all of the following are true:

- [ ] The repo is public with a tagged release and a committed doctor report showing READY
- [ ] A stranger on a fresh machine reaches a cited draft, sees one explained abstention, and opens a trace following only the README
- [ ] Every Phase 1 safety gate runs in CI, so the safety contract can't silently regress
- [ ] Every number in the README metrics table is either healthy or honestly explained

## The Reconciled Verdict

Both reviews reached the same recommendation: **revise before public preview, and don't simplify heavily.** The architecture and safety posture are good, and the heavy cutting already happened. Nearly every fix below is documentation, a guard clause, a startup check, a test, or a one-day audit. Neither review found an architectural safety flaw; the only candidate launch blocker is network defaults, and that's a configuration check, not a redesign.

| Dimension | Score | Reason |
| --- | ---: | --- |
| GitHub usability | 6/10 | Core idea is clear; first-run and config complexity drag it down |
| Configuration readiness | 5–5.5/10 | Good env/YAML separation undermined by invisible live config and the `config`/`configs` collision |
| Technical architecture | 7/10 | Disciplined layers and contracts; concentration and duplication are the debts |
| Safety and trust | 8/10 | Strong server-enforced suggest-only and redaction posture; close network, export, and disclosure gaps |
| Demo credibility | 5.5–6/10 | The demo path runs reliably; the quality numbers aren't publishable unexplained |
| Portfolio strength | 8/10 | Maps directly to support-ops and AI-support roles when framed as governed drafting |
| Execution feasibility | 8/10 | Most fixes are docs, guardrails, doctor checks, tests, and small UI changes |

Where the two reviews disagreed on a score, the table shows the range.

## Phase Map

| Phase | Goal | Rough Effort | Exit Gate |
| --- | --- | --- | --- |
| 0 | Ground-truth audits | 1–2 days | Every claim headed for the README is verified against code behavior |
| 1 | Safety gates | ~1 day | Fresh clone binds loopback-only, refuses empty admin keys, redaction suite passes |
| 2 | One happy path | 1–2 days | A stranger reaches a cited draft using only the README |
| 3 | Config clarity | ~1 day | Doctor lists every loaded config file with its absolute path |
| 4 | Source contract and ingestion UX | 1–2 days | A half-filled CSV produces row-level reasons, and the tier table matches code |
| 5 | Doctor and failure handling | ~1 day | `make doctor` exits 0 only when demo-ready; every failure carries a fix line |
| 6 | Demo and trust UX | 1–2 days | An abstaining demo ticket shows band, reason, and a working trace link |
| 7 | Tests and CI | 2–3 days | A PR that breaks fail-closed retrieval or citation precision fails CI |
| 8 | Release day | Half day | A stranger succeeds with the README only; doctor report published |
| 9 | Post-launch cleanup | Optional | Read "How Far to Go" before committing time here |

Total for Phases 0–8: roughly 10–14 focused days, matching both reviews' "one focused work cycle." Estimates assume the machinery exists as both reviews describe it — most items surface existing behavior rather than build new behavior.

---

## Phase 0 — Ground-Truth Audits

Verify before documenting. Nothing public should rest on a guess, and several later items change shape depending on what the code actually does.

- [ ] **Audit 10 golden cases by hand** to explain required-point coverage 0.0577 and source precision 0.4716. Classify each miss as one of: metric bug, golden-expectation mismatch, retrieval failure, rerank failure, responder gap, or validation too strict. Fix the dominant cause or restate the metric honestly. Done when coverage reaches at least 0.5 on the demo set, or the README explains exactly what the number measures and why it's low at preview (`eval/golden_set/v3_1_starter.jsonl`, `scripts/run_golden_eval.py`, `pipeline/retriever.py`, `pipeline/reranker.py`, `pipeline/responder.py`)
- [x] **Verify network bind defaults:** confirm `start.py`, the Dockerfile, and compose port mappings don't publish 8000/8765 on `0.0.0.0` by default
- [x] **Verify key handling:** confirm admin and configurator keys have no empty-string default that passes `verify_admin`
- [x] **Verify field enforcement** against `_missing_safety_metadata` and `SourceValidationError` so the Phase 4 tier table describes what the code does, not what the design intends; either add safe defaults in code for recommended-tier fields or move them into the required tier in the docs
- [x] **Demo data hygiene sweep:** confirm no realistic-looking secrets or tokens appear in demo content; even fake ones confuse users when they show up unmasked in traces
- [x] **Demo ticket realism check:** confirm `demo_cases.jsonl` tickets read like real tickets rather than keyword probes, and that the set includes one ticket designed to abstain and one designed to trigger a validation warning
- [x] **Confirm a schema-version marker exists** for orderly migrations; note its absence if missing
- [x] **Confirm rerank scores appear in traces;** the reranker is among the least-instrumented modules and source precision is the weak metric
- [x] **Triage the 12 demo warnings** and plan the path to 4 or fewer, the stated production target
- [x] **Inventory duplicate and stale pairs** for later resolution: `config/` vs `configs/`, `eval/golden/` vs `eval/golden_set/`, `pipeline/cache.py` vs `pipeline/orchestrator_cache.py`, the two doctor scripts, `scripts/source_validation_report_v6.py`, and the stale knowledge graph vs `docs/CODE_MAP.json` (both `contains` and `exports` at exactly 815 edges suggests generator artifacts)

**Exit gate:** every claim headed for the README or docs is verified against actual code behavior.

---

## Phase 1 — Safety Gates

Close these before anything goes public. The only candidate preview blocker in either review lives here.

- [x] Enforce loopback-only binding by default in `start.py` and compose mappings
- [x] Refuse to start the admin surface when admin or configurator keys are empty
- [x] Add a doctor check for bind host and key strength that flags any non-loopback exposure
- [x] Add a README security note: exposing the app beyond localhost exposes traces and admin analytics
- [x] Add the README privacy paragraph: ticket text and retrieved KB snippets go to the configured provider (OpenAI or Gemini); KB chunks, traces, and doctor reports stay in the local Postgres volume and trace store; hashed tickets, not raw tickets, enter traces; "local-first" doesn't mean offline
- [x] Add this warning verbatim near the top of the README:

> Do not load private customer data into a public or shared instance. ResolveKit is local-first and demo-oriented; you are responsible for what you ingest and where you run it. Ingested content and submitted tickets are sent to your configured LLM provider and stored in the local database and trace store.

- [x] Extend redaction tests beyond diagnostics to at least one exported support bundle, and to admin or report exports if they exist; keep the masking test inside the doctor as a release gate
- [x] Add named fail-closed tests so the behavior can't regress: a chunk missing `is_approved`, `is_active`, or `is_customer_facing_allowed` blocks retrieval rather than warning and continuing
- [x] Add the raw-ticket ban test: an internal or raw-ticket source can never be cited as evidence
- [x] Mark `knowledge_loader/kb_scraper.py` experimental in its docstring and exclude it from the public ingest path explicitly

**Exit gate:** a fresh clone binds loopback-only, refuses empty admin keys, and the full redaction suite passes.

---

## Phase 2 — One Happy Path

The README's job is to compress the user path, not teach the architecture. Five jobs only: say what ResolveKit is, say what it's not, give one Docker-first quickstart, show one successful demo result, and explain how to bring your own CSV. Link advanced docs for everything else.

### Quickstart and Success

- [x] Rewrite the README around one canonical Docker-first quickstart; the local Python path is secondary and marked advanced:

```bash
git clone <repo>
cd resolvekit
cp .env.example .env   # add your provider key
./get_started.sh
make doctor
```

- [x] Add the success box near the quickstart, paste-ready:

> You're set up when:
> - `make doctor` prints `Demo readiness: READY`
> - The ticket workspace opens at `http://127.0.0.1:8000/`
> - The demo ticket returns a draft with citations
> - Confidence and validation status are visible
> - The trace link opens a redacted run trace

- [x] State the local path's real cost: "Local mode needs Postgres with pgvector; use Docker if unsure"
- [x] Explain that the wizard and the app are two servers: wizard at `http://127.0.0.1:8765`, app at `http://127.0.0.1:8000/`
- [x] Add the entry-point table so the three start scripts stop competing:

| Command | Use When |
| --- | --- |
| `./get_started.sh` | First-time Docker onboarding |
| `make doctor` | Checking demo readiness |
| `bash scripts/public_smoke.sh` | Running the public smoke test |
| `python start.py` | Local development path (advanced) |
| `scripts/demo_start.sh` | Maintainer/demo helper — mark or hide if not for public users |

### Positioning

- [x] Lead with the governed one-liner: "ResolveKit is a local-first, suggest-only support-drafting kit. It drafts replies from approved sources only, cites every claim, abstains when unsure, and never sends anything without human review." Don't lead with "a RAG framework for support resolution"
- [x] Add the What This Is block: local/self-hosted preview, CSV-first ingest, cited draft suggestions, confidence and abstention, redacted traces, human review required
- [x] Add the What This Is Not block: not production-approved, not an autonomous agent, not a customer chatbot, not a helpdesk replacement, not auto-send, not auto-resolve, not a source-of-truth editor, not multi-tenant SaaS, not for end customers — and raw tickets are never cited
- [x] Put the production caveat above the fold: demo-ready, suggest-only, not production-approved
- [x] Show the actual 422 response for a forbidden mode; a real error earns more trust than prose
- [x] Document `suggest` as the only public mode in docs and schema, and present the rejection of other modes as a feature
- [x] Paste one full sample walkthrough: ticket in, draft out, citations, confidence, validation status (the rest links to `docs/DEMO.md`)
- [x] Add the simple architecture one-liner; the deep code map stays in docs:

```text
Ticket → Retrieval Plan → Approved Sources → Rerank → Evidence Bundle → Draft → Validate → Confidence → Trace/Review
```

- [x] State the supported envelope plainly: single KB namespace per deployment, English-only prompts and eval, OpenAI/Gemini providers, pgvector as the only store, loopback URLs
- [x] Add the honest metrics table: all eight metrics, current vs target, one explanatory line for the weak ones; publishing 0.0577 with a 0.50 target and one sentence reads as engineering maturity, while hiding it reads as marketing
- [x] Keep the roadmap to three to five items (improve source precision and coverage, improve CSV validation and preview, one more ingest format, better trace UX, CI and eval gates); no SaaS, billing, or enterprise promises

### Environment Templates

- [x] Comment every variable in `.env.example` and `.env.docker.example` with required/optional labels: provider selection, the key for the selected provider, embedding model, DB connection, admin and configurator keys, bind host, and mock mode if it exists. Pattern:

```env
# Required if LLM_PROVIDER=openai. Used for draft generation and embeddings unless overridden.
OPENAI_API_KEY=
```

- [x] Add startup validation that names the missing variable and exits non-zero; deleting the provider key must produce an error containing the variable name, never a stack trace
- [x] Add a provider matrix to the docs: required variables per provider, defaults, and a smoke command; add a doctor check that calls `model_warmup` for the selected provider

**Exit gate:** a stranger reaches a cited draft following only the README.

---

## Phase 3 — Config Clarity

About 39 percent of tracked files (85 of 220) are configuration. The structure is right — secrets in env, policy in YAML, experiments fenced — but a public user needs a map, and the app needs to say what it loaded. Editing the wrong file and seeing no change is the most likely "it's broken" report.

- [x] Add the config map table to the README (starter below; correct it against code):

| File / Surface | Purpose | User Should Edit? | Takes Effect |
| --- | --- | --- | --- |
| `.env` | Provider keys, DB, runtime secrets | Yes | Restart |
| `config/products.yaml` | Product identity and areas | Yes | Restart/reload |
| `config/sources.yaml` | Source registry and policy | Yes | Re-ingest for source changes |
| `config/output.yaml` | Draft tone and format | Yes | Live/reload |
| `config/retrieval_policy.yaml` | Retrieval weights and rules | Advanced | Restart or re-ingest by field |
| `config/workflow.yaml` | Suggest-only workflow behavior | Rarely | Restart |
| `configs/baseline.yaml` | Baseline experiment config | No for first demo | Advanced only |
| `configs/ab/` | Experiments (75 variants) | No for first demo | Advanced only |

- [x] Print the resolved path of every loaded config file at startup (`backend/core/config.py`, `backend/core/project_config.py`, `start.py`)
- [x] Include resolved config paths in the doctor report, labeled default, generated, or user-edited
- [x] Document reload semantics in `docs/TECHNICAL.md` with one row per runtime file and a reload column limited to three values — live, restart, re-ingest; chunking and source-policy edits silently require re-ingestion today, which is the trap to call out
- [x] Mirror the reload semantics as badges in the configurator UI and its save responses (`save_configurator_config`, `validate_configurator_config` in `backend/api/app.py`, `frontend/configurator/index.html`)
- [x] Fence the experiment tree: a root-README line ("ignore `configs/ab/` unless experimenting"), a header in `configs/baseline.yaml` declaring it the only runtime-relevant file there, and an "advanced experiments only" header in `configs/ab/README.md`
- [x] Add one minimal worked product example in `config/products.example.yaml` comments, mirrored by the demo CSV's `product_area` values; retrieval quality depends on this mapping
- [x] Validate all five runtime YAMLs at startup with errors in the form file → key → problem → expected value, plus one deliberately-broken-key test per file; hand-editing YAML is exactly what this audience does

**Exit gate:** doctor output lists every loaded config file with its absolute path.

---

## Phase 4 — Source Contract and Ingestion UX

The source contract is the product. The loader machinery is more mature than the docs suggest (`SourceValidationError`, `source_validation_report`, typed `SourceRecord`/`EvidenceChunk` models, `preview_import_summary`), so the work here is documentation and surfacing, not building.

### CSV-Only, Enforced

- [x] Add the bold README line: "Public preview ingest supports CSV only"
- [x] Make the loader reject non-CSV paths with a one-line actionable error — "Convert to CSV using `demo_data/onboarding/source_manifest_template.csv`" — covered by a test (`knowledge_loader/kb_loader.py`)
- [x] Add `PREVIEW_ONLY` notes inside `demo_data/pdf/` and `demo_data/xlsx/`; the repo ships ten PDFs, an XLSX, five connectors, and A/B configs named `v2_xlsx_baseline.yaml` and `v3_pdf_baseline.yaml`, which together set the format trap

### Data Dictionary

- [x] Publish the tiered dictionary in `docs/TECHNICAL.md`. Tier assignments below are provisional until the Phase 0 enforcement audit confirms them — the dictionary must describe what the code does:

| Column | Tier | Purpose | Accepted Values / Example |
| --- | --- | --- | --- |
| `source_id` | Required | Stable source identifier | `kb_001` |
| `source_title` | Required | Human-readable citation title | `Password Reset Guide` |
| `source_type` | Required | Content category | Enum: `kb_article`, `policy`, `release_note`, `known_issue`, etc |
| `is_approved` | Required | Admits source into governed retrieval | `true`/`false` |
| `is_active` | Required | Keeps disabled or stale docs out of retrieval | `true`/`false` |
| `is_customer_facing_allowed` | Required | Permits citation in customer-facing drafts | `true`/`false` |
| `body` | Required | Content to chunk and retrieve | Article text |
| `product_area` | Recommended | Improves routing and retrieval | `billing` |
| `issue_class` | Recommended | Improves routing and retrieval | `login_issue` |
| `version_scope` | Recommended | Product version applicability | `v2` |
| `source_authority` | Recommended | Ranking and trust signal | `high` |
| `approved_at` | Recommended | Approval timestamp | ISO date |
| `needs_review_at` | Recommended | Freshness and staleness signal | ISO date |
| `reviewed_by` | Governance | Audit metadata | `support_ops` |
| `doc_type` | Governance | Finer content category | `guide` |
| `escalation_risk` | Governance | Safety escalation signal | Enum: `low`/`medium`/`high` |

### Examples and Validation

- [x] Ship `demo_data/csv/minimal_valid_kb.csv` with 3 rows
- [x] Ship `demo_data/csv/invalid_examples/` covering: missing `is_approved`, inactive source, internal-only source, bad date, empty body, and unknown source type — each failing with the exact error a user would see
- [x] Add `scripts/validate_sources.py <file>` as a dry-run command reusing `source_validation_report`
- [x] Surface preview output in the configurator and doctor; silent exclusions on a fail-closed system look like a broken product:

```text
Rows scanned: 42
Rows ingestible: 31
Rows excluded: 11
- row 7: missing is_approved
- row 12: is_customer_facing_allowed=false
- row 15: body is empty
```

- [x] Include a first-3-chunks preview in that output
- [x] Promote `source_manifest_template.csv` out of `demo_data/onboarding/` obscurity into the README's "bring your own docs" section
- [x] Make preview-before-ingest a named quickstart step: select CSV → preview parsing → see row-level issues → ingest valid approved rows → run a demo ticket

### Eligibility Truth Table

- [x] Add the source eligibility truth table to `docs/TECHNICAL.md`: every `is_approved`/`is_active`/`is_customer_facing_allowed` combination → retrievable? citable customer-facing? what the user sees. Cross-reference `source_contract.py`, `retriever.py`, `validation.py`, and `confidence.py` (`_chunk_is_stale` reads freshness). Per-field starter below; derive the eight-row combination table from code:

| Field | Effect If False, Missing, or Stale | Enforced At |
| --- | --- | --- |
| `is_approved` | Source can't support a customer-facing draft | Ingest, retrieval, validation |
| `is_active` | Chunk isn't retrieved | Retrieval |
| `is_customer_facing_allowed` | Source can't be cited to a customer | Retrieval, validation |
| `needs_review_at` | May lower confidence or mark stale | Confidence, validation |
| `source_authority` | Affects ranking and confidence | Retrieval, rerank, confidence |

- [x] Lock every combination in the truth table with a test
- [x] Document re-ingestion semantics in one paragraph: re-running ingest on a changed file creates a new document version and tombstones old chunks (`plan_document_reingestion`, `tombstone_existing_document_chunks`); state how caches are invalidated — and if that half isn't true yet, the Phase 7 cache test closes it

**Exit gate:** a half-filled CSV produces row-level reasons in the preview response, and the tier table matches code behavior.

---

## Phase 5 — Doctor and Failure Handling

The doctor is the support channel and the official release gate. Status without remediation still loses users.

- [x] Add a fix-it line to every failing doctor check, starting with the top five failure modes. Pattern:

```text
FAIL: OPENAI_API_KEY is missing
Fix: Add OPENAI_API_KEY to .env, or set LLM_PROVIDER=gemini and provide GEMINI_API_KEY.
```

- [x] Complete the doctor check set: Docker running, DB reachable, schema initialized, KB loaded, provider key and config valid (with `model_warmup`), app health OK, `/resolve` works, trace works, source preview works, metrics endpoint works, secrets masked, loopback binding safe, port conflicts, resolved config paths, ingest summary, warnings summarized with one-line meanings, latest report location, and overall preview status
- [x] Consolidate the two doctors or give each a one-line division of labor (`scripts/demo_doctor.sh`, `scripts/onboarding_doctor.py`)
- [x] Make `make doctor` exit non-zero on real blockers, with the secret-masking check kept in as a gate
- [x] Add the README common-failures table:

| Failure | Good Error Behavior |
| --- | --- |
| Missing provider key | Names the exact missing env var |
| Docker not running | Says Docker is required for the quickstart |
| Port 8000/8765 in use | Shows the conflict and the fix |
| DB not ready | Says what to wait for or run |
| Bad CSV row | Names row, column, and expected value |
| No approved sources | Says rows were excluded by source flags |
| Non-CSV ingest | Says public ingest is CSV-only and points to the template |
| Provider call fails | Shows provider name and safe remediation |
| Local Postgres/pgvector missing | Says use Docker if unsure |

- [ ] Drive demo warnings to 4 or fewer, each explained in the doctor output (`pipeline/validation.py`, golden set)
- [x] Give `/resolve` an error taxonomy callers can rely on: the response shape distinguishes retrieval-empty, validation-blocked, abstained, and provider-error, surfaced in both the UI and the API
- [x] Add `make reset-demo` wrapping `scripts/rebuild_db.py` plus re-ingest, documented as idempotent and proven safe to run twice in a row
- [x] Wrap the three persistence verbs in Make targets: initialize DB, wipe demo DB, reload KB (`setup_db.py`, `rebuild_db.py`); document that the Postgres volume and trace store contain the user's KB text and hashed tickets
- [x] Keep `/metrics` simple and honest: requests, abstentions, warnings, citation precision, source-safety failures, approximate cost
- [x] Add one doc line for logs: location, level variable, format, whether they're redacted, and how to attach a doctor report to an issue safely
- [x] Keep `/health` basic but useful: app alive, DB reachable, provider configured, KB present if possible

**Exit gate:** `make doctor` exits 0 only when demo-ready, and every failure in `latest.md` includes a remediation line.

---

## Phase 6 — Demo and Trust UX

A reviewer staring at a draft needs one click to its trace, and an explained abstention is the single most persuasive trust demo this product can give. A red-band abstention with no visible reason looks like a malfunction and trains users to distrust exactly the behavior that should earn trust.

### Result Panel and Citations

- [x] Show confidence band, a one-line abstention or warning reason, and a "why this draft" link to `/traces/{id}` in the ticket UI (`frontend/ticket/index.html`, `backend/core/orchestrator.py`)
- [x] Add a suggested next action for the reviewer on abstention — what evidence is missing or conflicting
- [x] Render citations human-readable: `source_title`, source/doc type, `product_area`, a chunk excerpt, and the why-eligible flags that admitted it, with the source ID as secondary metadata; a citation showing only `chunk_123` is a citation a reviewer won't trust (`backend/core/evidence.py`, citation response schema, ticket UI)

### Trace Viewer

- [x] Confirm the trace shows: hashed/redacted input, retrieval query and plan, retrieved chunks with eligibility metadata, rerank scores, the selected evidence bundle, the draft, citations, per-claim validation verdicts, confidence band with inputs and reasons, and cost/tokens if available
- [x] Show rejected or filtered chunks where useful (inactive, unapproved, internal-only, stale, low relevance, conflict); this is how users learn to fix their CSVs
- [x] Add one sentence in the trace UI on replay: replays use stored outputs, or regenerated steps get labeled non-deterministic; this prevents confused bug reports (`replay.py`, `compare_config_replay.py`)

### Wizard and Walkthrough

- [x] End the wizard with a handoff: open the ticket workspace at `http://127.0.0.1:8000/`, paste this demo ticket, you should see a cited draft, click this trace link (`frontend/onboarding/index.html`)
- [x] Add an onboarding step that shows source rows and eligibility before ingestion
- [x] Include one demo ticket whose best answer exists only in an internal or offline source, so ResolveKit visibly abstains rather than citing it; this proves the safety model in one screen
- [x] Cover the demo scenario set: normal answerable, ambiguous, stale source, internal-only source, known issue, policy, release note, escalation risk
- [x] Build `docs/DEMO.md` with a trace walkthrough and screenshots (`refresh_screenshots.py` already exists, so this is cheap)
- [ ] Record two GIFs — the happy path (paste ticket → draft → citations → click a citation → open trace) and the differentiator (paste an internal-only ask → abstain → reason shown → trace shows the missing or unsafe evidence)
- [x] Keep the review queue as proof of human-in-the-loop design; the first demo must not depend on its polish
- [x] Optional, the only pre-launch item requiring new code (both reviews rate it P1; skip if time-boxed): a no-key preview mode — a mock/smoke provider behind a config flag returning canned drafts clearly labeled as mock, so users can inspect ingest, UI, and traces without paid API calls (`backend/providers/`, `backend/core/config.py`)

**Exit gate:** an abstaining demo ticket shows band, reason, and a working trace link.

---

## Phase 7 — Tests and CI

The suite is real (about 297 tests, a focused 15-test preview subset, a full-path Docker smoke), but coverage is concentrated in one mega-file, and the graph shows no direct test imports for `reranker.py`, `scorer.py`, `source_contract.py`, `kb_scraper.py`, or either provider module. Safety properties deserve named tests, not indirect coverage.

### Keep What Passes

- [x] Keep `scripts/public_smoke.sh` (schema → KB load → `/health` → `/resolve` → trace → daily metrics → source preview) in the release checklist; it's the best single asset in the repo
- [x] Keep the focused `-k` onboarding/launch-readiness subset documented

### Launch-Gating Tests

- [x] `source_contract.py` direct unit tests: each missing safety field raises `SourceValidationError` naming row and column
- [x] Fail-closed retrieval: a chunk lacking safety metadata, or with `is_active=false`, is never returned (locks `_missing_safety_metadata`)
- [x] An `is_customer_facing_allowed=false` source is never cited
- [x] A raw or offline ticket source can't be used as evidence
- [x] A red-confidence customer-facing draft returns the abstain response shape
- [x] API contract: `mode != "suggest"` rejected and unknown request field rejected, asserting both status and message
- [x] Non-CSV ingest path returns the one-line actionable error, not a stack trace
- [x] Bad CSV returns row and column via preview
- [x] Missing provider key error names the exact variable
- [x] Doctor exits non-zero on blockers, with the secret-masking gate included
- [x] Loopback bind and admin-key safety check
- [x] Export/support-bundle redaction
- [x] Demo data contains no realistic secrets
- [x] Cache invalidation: ingest a doc → query and cache → re-ingest the changed doc → assert the tombstoned chunk never returns (covers `pipeline/cache.py`, `pipeline/orchestrator_cache.py`, and `_hydrate_cached_chunks`); a stale cache here serves disabled or superseded evidence, which makes this a safety test, not a performance test
- [x] One deliberately-broken-key startup validation test per runtime YAML

### CI

- [x] One GitHub Actions workflow on PRs: the focused `-k` subset plus `public_smoke.sh` (`.github/workflows/`)
- [x] `scripts/ci_golden_eval.sh` thresholds as a failing gate: citation precision = 1.0, source-safety failures = 0, warnings at or under the agreed cap

### Fast-Follow Tests (After Launch Is Fine)

- [x] Golden retrieval regression with stable expected source IDs
- [x] Minimal-valid and invalid-fixture tests matching the shipped CSV examples
- [x] Citation formatting tests (human-readable fields present)
- [x] UI trace-link test
- [x] Config reload behavior tests and provider config validation tests
- [x] Port-conflict handling test
- [x] No-key/mock-provider mode test, if Phase 6's optional item shipped
- [x] Direct tests for `reranker.py` and `scorer.py`/confidence

**Exit gate:** a PR that breaks fail-closed retrieval or citation precision fails CI.

---

## Phase 8 — Release Day

- [ ] Regenerate the Understand Anything graph and `docs/CODE_MAP.json` at the release commit; pick one generated source of truth going forward and add regeneration to the release checklist — deterministic local scan refreshed; full graph rebuild still requires the Understand graph assembly runner
- [x] Add deprecation or canonical-path headers to any duplicate pairs not yet deleted (two doctors, two golden-set paths, two cache modules, the `_v6` script)
- [x] Foreground only the core routes in the docs: `/resolve`, `/health`, `/configurator/source-preview`, `/traces/{id}`, plus `/configurator` and `/feedback` if useful; admin analytics stays documented but demoted
- [x] Run the launch gate for real: a fresh machine — or better, a friend — following only the README, reaching a cited draft, one explained abstention, and an open trace — local Docker fresh-clone gate passed at the current local release commit with doctor READY, public smoke passed, and trace IDs emitted; external friend/README-only walkthrough remains separate validation
- [x] Publish the final doctor run as "known demo status" (the last recorded run was exit 0, 15 focused tests passed, smoke passed; refresh it at the release commit) — READY report generated from the clean fresh clone for the current local release commit and copied locally to ignored `diagnostics/demo_doctor/release_<commit>.*`; not force-added because ignored report artifacts are intentionally local
- [x] Tag the release, write short release notes, and open issues for Phase 9 items so the repo reads as maintained — local tag `v0.1.0-public-preview` created; GitHub publication intentionally not pushed to avoid GitHub Actions

**Exit gate:** a stranger succeeds with the README only, and the doctor report is committed or published.

---

## Phase 9 — Post-Launch Cleanup (Optional)

Read "How Far to Go" before spending time here. These are real improvements with near-zero first-week user impact.

- [ ] Extract configurator and diagnostics (and admin) `APIRouter` modules out of `backend/api/app.py` in two no-behavior-change PRs; `app.py` carries 74 functions today and is the file most likely to rot — done when it drops under roughly 30 functions with the suite green
- [x] Keep the orchestrator readable by splitting only clear sub-concerns (response assembly, validation outcome handling, trace persistence, review-queue write, abstention formatting); no rewrite
- [ ] Delete or archive the stale half of each duplicate pair inventoried in Phase 0
- [x] Route all new tests into focused files (`test_source_contract.py`, `test_safety.py`, `test_api_contract.py`, `test_retrieval.py`, `test_onboarding.py`) and split the mega-file by domain over time
- [x] Wire one additional ingest format (HTML or XLSX) into public ingest under the same safety contract — only after CSV-path feedback
- [x] Add `performance_smoke.py` to CI with a generous budget, plus cost and latency regression checks
- [x] Add property-based tests for source-record parsing
- [x] Add UI snapshot tests for configurator and admin, and a Windows-native pass (`os_detect.py` shows intent; Docker covers it today)
- [ ] Eval report rendering, admin analytics polish, deployment hardening, and a second screenshot set via `refresh_screenshots.py`
- [ ] Local LLM and additional provider matrix — no-key mock provider implemented; true local LLM provider remains deferred

---

## Not Implemented / Deferred Items

These items are intentionally not implemented in this pass. They are either release-day actions, external validation/media work, optional post-launch cleanup, or larger product-quality work that should not be hidden behind a checked box.

| Source | Item | Status | Reason |
| --- | --- | --- | --- |
| Definition of Done | Repo public, tagged release, committed READY doctor report | Partially implemented | Local tag and local READY doctor exist for the current local release commit; repo publication/push and committed ignored report are intentionally deferred |
| Definition of Done | Stranger fresh-machine walkthrough | Partially implemented | Local Docker fresh-clone gate passed for the current local release commit; external human README-only walk with explicit abstention remains pending |
| Definition of Done | Every README metric healthy or fully explained | Partially implemented | Metrics are published honestly; warning count and required-point coverage still need quality work |
| Phase 0 | Audit 10 golden cases by hand | Not implemented | Needs manual ticket-by-ticket analysis |
| Phase 5 | Drive demo warnings to 4 or fewer | Not implemented | Requires validation/golden-set quality work |
| Phase 6 | Record two GIFs | Not implemented | Requires UI capture/media production |
| Phase 8 | Regenerate Understand Anything graph at release commit | Partially implemented | Previous deterministic scan exists, but metadata still points at `fd36d7b`; full graph assembly runner unavailable in this shell |
| Phase 8 | Fresh-machine launch gate | Implemented locally | `SOURCE_REPO=<repo> GATE_ROOT=/tmp/resolvekit-fresh-machine-gate DB_HOST_PORT=55432 scripts/fresh_machine_launch_gate.sh` exited 0 for the current local release commit |
| Phase 8 | Publish final doctor report | Implemented locally | Clean-clone READY doctor copied to ignored `diagnostics/demo_doctor/release_<commit>.*`; not committed per ignored-artifact policy |
| Phase 8 | Tag release, release notes, Phase 9 issues | Implemented locally | Local release tag maintained; GitHub publication intentionally deferred |
| Phase 9 | Router extraction from `backend/api/app.py` | Deferred | Optional post-launch refactor |
| Phase 9 | Delete/archive stale duplicate paths | Deferred | Optional post-launch cleanup |
| Phase 9 | Eval/admin/deployment polish and second screenshots | Deferred | Optional polish |
| Phase 9 | True local LLM provider | Deferred | No-key mock provider added; full local LLM integration remains future provider work |

---

## Do Not Build (Scope-Creep Watchlist)

This doubles as a guardrail list for AI-assisted coding from here on: every row is something an agent will eagerly offer to build.

| Tempting Thing | Why Defer |
| --- | --- |
| Multi-tenant SaaS or hosted mode | Different threat model, isolation, and billing; destroys local-first simplicity |
| Full RBAC/SSO | The three-key split (user/admin/configurator) is enough for clone-and-run self-host |
| Live connectors (Zendesk, Intercom, Confluence) or productizing `kb_scraper.py` | Each adds auth, sync, and rate-limit failure modes; CSV-first keeps the safety contract auditable |
| Auto-send or auto-resolve, ever | Violates the suggest-only trust boundary that is the entire product stance |
| Automatic KB rewriting or self-healing knowledge | `/knowledge-issues` capturing reviewer-flagged gaps is the ceiling; machine-edited sources break the approval contract |
| Complex workflow builder | One reliable suggest workflow is the product |
| A/B experimentation UI | 75 file-based variants behind a README is the right altitude |
| Knowledge-graph or entity layer | The retrieval problem at hand is precision tuning, not graph reasoning |
| Analytics dashboard polish | Not valuable until users can ingest docs and trust drafts |
| Vector-store abstraction layer | pgvector is fine; an abstraction means testing N backends forever |
| Full PDF/XLSX ingest now | Valuable later; dangerous to imply before the CSV path is reliable |
| Multilingual support and local-LLM matrix | Both real, both later; document the English + OpenAI/Gemini envelope |
| Production deployment guide and enterprise compliance pages | Premature until demo and config are stable |

---

## How Far to Go (Interview Value)

Interview value concentrates early in this plan and decays fast after Phase 8.

- Phases 0–2 carry most of the value: a working five-minute demo and numbers that survive questioning. Demo credibility moves from roughly 5.5 to 7.5 here
- Phases 3–6 produce the differentiator: the explained abstention with a trace link — the one moment that separates this from every generic RAG demo
- Phase 7 is the maturity signal: a green CI badge gating safety properties reads as engineering judgment, cheap at 2–3 days
- Phase 8 is the proof: a stranger succeeding with the README only
- Phase 9 is near-zero interview value for support-ops, AI-support-ops, customer-operations, and product-support roles; it only pays in software engineering interviews, where the `app.py` extraction and test split become a refactoring story worth telling

**Recommended stop line:** ship Phases 0–8 and freeze. After that, spend remaining prep time on ownership, not features.

### The Ownership Drill (the Black-Box Cure)

The phases are sequenced so the shipping work doubles as re-learning: the golden-case audit walks ten tickets through the whole pipeline by hand, and the truth table and config map can only be written correctly by reading the code — not by asking the model that wrote it. Two working rules keep the repo from going dark again: nothing merges that can't be explained in two sentences, and every accepted AI change gets one line in a `DECISIONS.md`.

Interview readiness is binary on these ten questions, answered cold with the repo closed:

1. Walk through what happens when a ticket hits `/resolve`, stage by stage
2. Why fail-closed retrieval instead of warn-and-continue?
3. What exactly does `is_customer_facing_allowed` gate, and where is it enforced?
4. Why is required-point coverage 0.06 while citation precision is 1.0, and what did the audit find?
5. What leaves the machine, and what stays local?
6. Why is suggest-only enforced server-side rather than in the UI?
7. How does re-ingestion avoid serving stale chunks?
8. What's in a run trace, and what's redacted out of it?
9. Why CSV-only ingest when five connectors exist in the code?
10. What would you build next, and why didn't you build X (pick anything from the watchlist)?

Any question that can't be answered points at the next file to read. All ten answered cold is a better readiness metric than any score in the verdict table.

---

## Portfolio Material (Paste-Ready)

Present ResolveKit as operational tooling, not "a chatbot." These bullets work across Support Operations, Product Support, Support Analytics, Customer Operations, AI Support Operations, and Technical/SaaS Support roles, because they show the combination those roles screen for: AI capability, governance instinct, and measurement.

- Built a local-first, suggest-only AI support-drafting kit with fail-closed source governance — 1.0 citation precision and zero source-safety failures across a 52-case golden evaluation
- Designed a 16-field source contract (approval, freshness, customer-facing permission, authority) enforced at ingest, retrieval, and validation, so unapproved or stale knowledge can never reach a customer-facing draft
- Implemented confidence-banded abstention and a human review queue, ensuring low-confidence drafts refuse rather than guess and every suggestion carries an audit trail
- Shipped redacted, replayable run traces with ticket hashing and secret masking, enabling claim-level audit of every AI draft without exposing customer data
- Built one-command diagnostics ("demo doctor"), CSV source preview with row-level validation, and a golden-eval pipeline with per-run cost tracking, turning draft quality into measurable, regression-gated metrics

---

## Target README Outline

1. **One-sentence pitch** — "ResolveKit is a local-first support-drafting assistant that turns approved product docs into cited, human-reviewed reply suggestions"
2. **What this is** — local/self-hosted demo, CSV-first ingest, cited draft suggestions, human review required, trace and debug view included
3. **What this is not** — the full block from Phase 2
4. **Quickstart** — Docker-first, one path, five commands, expected URLs, the success box
5. **Demo** — one pasted sample ticket → draft → citation block, plus one abstention example with its reason; one GIF covering both, with the abstention clip as the differentiator
6. **Configure your own product** — edit `.env`, edit product YAML, provide the CSV (link the tier table and `minimal_valid_kb.csv`), preview the source, reload or rebuild
7. **Safety model** — approved/active/customer-facing only, raw tickets never evidence, suggest-only with the actual 422 shown, redaction, the do-not-load-private-data warning
8. **Diagnostics** — `make doctor`, trace viewer, source preview, smoke tests, the common-failures table
9. **Limitations and honest metrics** — CSV-only, English-only, two providers, demo-grade retrieval; the current-vs-target table for all eight metrics, including the bad ones
10. **Roadmap** — three to five items, no SaaS, billing, or enterprise promises

---

## Traceability Notes

- Every checklist item, table, and threshold above traces to one or both source reviews; nothing was dropped
- Judgment calls made during the merge: the reset target is named `make reset-demo` (one review called it `make reset`); the no-key mock provider is marked optional pre-launch (one review rated it a firm P1, the other "if easy"); tier assignments in the data dictionary are provisional pending the Phase 0 enforcement audit; scores show a range where the reviews differed (configuration 5 vs 5.5, demo credibility 5.5 vs 6)
- Both reviews assessed repo shape at commit `306331b`, not a live run; treat any item as falsified if the code says otherwise, and update this plan rather than the code's reality
