# Public Demo Checklist

Status date: 2026-06-12

Scope: public-facing alpha demo for reviewers. Goal is clone, Docker start, enter own provider key, import a CSV KB into Postgres/pgvector, generate cited support drafts through the UI with the reviewer's LLM provider, inspect traces, and understand safety gates. This is not production launch.

Decision: repo is ready for public repo alpha after staging/committing the current working tree. Key rotation and live-provider smoke remain deferred by owner decision.

## Current State

- [x] Public alpha golden gate passes with stored results.
- [x] Source-safety hard failures are `0`.
- [x] Citation precision is `1.0`.
- [x] Docker-first quickstart exists: `./get_started.sh`.
- [x] Docker smoke exists: `bash scripts/public_smoke.sh`.
- [x] Onboarding wizard exists: `http://127.0.0.1:8765`.
- [x] Viewer/Admin auth split exists.
- [x] CSV demo/vector ingest path exists.
- [x] XLSX and born-digital PDF preview/demo fixtures exist.
- [x] Trace, metrics, source preview, feedback, and admin analytics routes exist.
- [x] Current checkout Docker public smoke has been re-verified after latest gate/docs changes.
  - Result: `bash scripts/public_smoke.sh` passed on 2026-06-12.
- [x] Fresh-folder Docker public smoke has been re-verified after latest gate/docs changes.
  - Result: `bash scripts/public_smoke.sh` passed from a separate temp folder on 2026-06-12.
- [x] Public tracked-tree secret scan has no provider/private-key pattern hits.
- [x] `/launch-readiness` checks active `eval/golden_set/v3_1_starter.jsonl`.
- [x] Docker onboarding binds to `0.0.0.0` inside container.
- [x] Docker onboarding service starts with module entrypoint.
- [x] Onboarding KB loader uses non-interactive `--all`.
- [x] Uploaded custom CSV sources are loaded into the vector DB even when demo mode is on.
- [x] Onboarding vector ingest is honest: CSV only; XLSX/PDF are not silently accepted for vector load.
- [x] README/docs clarify CSV vector ingest; XLSX/PDF remain preview/fixture coverage.
- [x] Production launch gate fails by design.

## Verified On 2026-06-12

- [x] `bash scripts/ci_golden_eval.sh`
  - Result: pass.
  - Profile: `public_alpha`.
  - Cases: `52`.
  - Hard failures: `0`.
  - Warnings: `12`.
  - Recall@3/5: `0.6596`.
  - Source precision: `0.4716`.
  - Citation precision: `1.0`.
- [x] `.venv/bin/python scripts/run_golden_eval.py --results eval/golden_set/last_results.jsonl --release-gate --release-profile production --max-avg-latency-ms 15000 --max-total-cost-usd 1.00`
  - Result: fails as intended.
  - Blockers: warning count, Recall@3, Recall@5, source precision, required-point coverage.
- [x] `.venv/bin/python -m pytest tests/test_resolvekit.py -k "release_gate or public_smoke or onboarding or launch_readiness or diagnostics_masks_secret_values"`
  - Result: `20 passed, 270 deselected`.
- [x] Tracked-file secret pattern scan.
  - Result: no hosted provider key pattern found.
  - Expected hits only: demo/default Postgres URLs.
- [x] `.venv/bin/python -m pytest tests/test_resolvekit.py -k "demo_mode_includes_configured_custom_csv_sources or onboarding_server_binds_all_interfaces_in_container or onboarding_load_knowledge_uses_noninteractive_all or kb_loader_all_flag_skips_interactive_selection or launch_readiness_uses_active_golden_set_path or onboarding_vector_ingest_accepts_csv_only or onboarding_upload_ui_describes_csv_vector_ingest"`
  - Result: readiness fixes passed.
- [x] `git status --short --ignored .env .env.docker config/sources.yaml demo_data/onboarding/uploads eval/golden_set/last_report.json eval/golden_set/last_report.md eval/golden_set/last_results.jsonl eval/reports/latest.json eval/reports/latest.md logs diagnostics/logs .understand-anything`
  - Result: private/local artifacts are ignored.
- [x] Tracked and publishable-untracked local-path/provider-secret scan.
  - Result: no tracked local-path or provider/private-key hits.
- [x] `.venv/bin/python -m pytest tests/test_resolvekit.py -k "home_page or configurator_source_preview_ui_exists or diagnostics_ui or ticket_workspace_has_render_error_boundary or ticket_workspace_keeps_advanced_retrieval_controls_out_of_main_form"`
  - Result: `10 passed, 279 deselected`.
- [x] TestClient route check for `/`, `/configurator`, and `/admin`.
  - Result: all returned HTTP `200` with `Cache-Control: no-store`.
- [x] `docker compose version`
  - Result: Docker Compose `v5.1.4` available.
- [x] `docker info`
  - Result: Docker daemon reachable after starting Docker Desktop.
- [x] `bash scripts/public_smoke.sh`
  - Result: `public smoke passed`.
  - Evidence: DB schema applied, demo KB loaded, vector index rebuilt, API smoke returned trace `trace_537b65adc23a4f80a2411df1b93cec19`.
- [x] Docker onboarding wizard endpoint check.
  - Command: `docker compose up -d --build onboarding`, then `curl -fsS http://127.0.0.1:8765/` and `curl -fsS http://127.0.0.1:8765/api/status`.
  - Result: UI returned HTTP `200`; status API returned `status: ok`, `container_mode: true`, and `docker_ready: true`.
- [x] Fresh-folder Docker public smoke.
  - Command: `bash scripts/public_smoke.sh` from a separate temp folder with an isolated Compose project name.
  - Result: `public smoke passed`.
  - Evidence: DB schema applied, demo KB loaded, vector index rebuilt, API smoke returned trace `trace_c4928aa753644f96bc1bbeede6f7b261`.
- [x] Fresh-folder Docker onboarding wizard endpoint check.
  - Command: `docker compose up -d --build onboarding` from a separate temp folder with an isolated Compose project name, then `curl -fsS http://127.0.0.1:8765/` and `curl -fsS http://127.0.0.1:8765/api/status`.
  - Result: UI returned HTTP `200`; status API returned `status: ok`, `container_mode: true`, and `docker_ready: true`.

## Remaining Non-Key Checks Before Public Demo

- [x] Fix `/launch-readiness` active golden path.
  - File: `backend/api/app.py`.
  - Changed `eval/golden/resolvekit_v0_1.jsonl` check to active `eval/golden_set/v3_1_starter.jsonl`.
  - Added focused readiness path coverage.
  - Done when: readiness endpoint passes with current golden set.
- [x] Decide whether `docs/RELEASE_CHECKLIST.md` should be tracked.
  - Current state: ignored by `.gitignore` via `docs/*`.
  - Decision: keep ignored; root checklist is tracked/public-facing.
- [x] Run final tracked-tree secret scan.
  - Command: scan tracked and publishable-untracked files for local paths, hosted provider key prefixes, and private-key headers.
  - Done: no output.
- [x] Review dirty worktree before publishing.
  - Command: `git status --short`.
  - Done: 24 modified files and 2 untracked files reviewed as public-alpha/source-safety/onboarding/checklist work.
  - Pending owner action: stage/commit or explicitly drop any file before publishing.
- [x] Verify `.env`, `.env.docker`, uploaded files, generated eval reports, logs, DB volumes, and local planning docs are not tracked.
  - Command: `git status --short --ignored`.
  - Done: private/local artifacts are ignored or absent.
- [x] Run Docker public smoke on current checkout.
  - Command: `bash scripts/public_smoke.sh`.
  - Done: output included `public smoke passed`; DB schema applied, demo KB loaded, vector index rebuilt, and API smoke returned trace `trace_537b65adc23a4f80a2411df1b93cec19`.
- [x] Run clean Docker public smoke from a separate fresh folder.
  - Command: `bash scripts/public_smoke.sh`.
  - Done: separate temp folder output included `public smoke passed` and trace `trace_c4928aa753644f96bc1bbeede6f7b261`.
- [x] Run onboarding wizard service on current checkout.
  - Command: `docker compose up -d --build onboarding`.
  - Done: root UI returned HTTP `200`; `/api/status` returned `status: ok`.
- [x] Fix Docker onboarding entrypoint.
  - File: `docker-compose.yml`.
  - Root cause: `python scripts/onboarding_server.py` made `/app/scripts` the import root, so `from scripts import onboarding_tasks` failed in the container.
  - Fix: run `python -m scripts.onboarding_server`.
  - Test: `test_onboarding_compose_runs_server_as_module`.
- [x] Run onboarding wizard service from a separate fresh folder.
  - Command: `docker compose up -d --build onboarding` with an isolated Compose project name.
  - Done: root UI returned HTTP `200`; `/api/status` returned `status: ok`.
- [x] Confirm public UI screens.
  - Viewer: `http://127.0.0.1:8000/`.
  - Configurator: `http://127.0.0.1:8000/configurator`.
  - Admin: `http://127.0.0.1:8000/admin`.
  - Done: focused UI tests passed, and TestClient returned `200`/`no-store` for all three routes.

## Deferred By Owner

- [ ] Rotate any OpenAI/Gemini key used during local testing.
  - Done when: old key no longer works, new demo key is reviewer-owned or throwaway, no key exists in tracked files.
- [ ] Run live provider smoke with a real reviewer key.
  - Done when: reviewer-supplied key produces a cited draft from imported CSV KB.
- [ ] Run one live green/yellow/red demo case.
  - Source: `demo_data/demo_cases.jsonl`.
  - Done when: green drafts cite approved KB, yellow shows caveat/review signal, red abstains.
- [ ] Run live support-bundle redaction check against a generated trace.
  - Endpoint: `/support-bundles/{trace_id}.zip`.
  - Done when: bundle contains no provider key/private ticket text.

## Demo Script

- [ ] Start clean demo.
  - `git clone <public-repo-url>`
  - `cd <repo>`
  - `./get_started.sh`
- [ ] Enter provider key in onboarding wizard.
  - Provider: OpenAI or Gemini.
  - Key storage: `.env.docker` only.
- [ ] Import custom CSV KB.
  - Upload CSV knowledge file in Sources step.
  - Ingest uploaded sources.
  - Expected: custom CSV path is added to `config/sources.yaml`, loader runs `knowledge_loader/kb_loader.py --all`, vectors are inserted into Postgres/pgvector.
- [ ] Run setup tasks in wizard.
  - Generate demo data.
  - Setup DB.
  - Load knowledge.
  - Start app.
  - First draft smoke.
- [ ] Show viewer draft.
  - Ticket: mobile app 403 after role change.
  - Expected: suggested reply, confidence, citations, trace ID.
- [ ] Show admin trace.
  - Expected: retrieval, validation, citations, latency, token/cost fields.
- [ ] Show source preview.
  - Expected: CSV preview with metadata and safety fields.
- [ ] Show metrics.
  - Expected: `/metrics`, `/metrics/daily`, admin analytics report.
- [ ] Show safety behavior.
  - Unsupported ticket abstains or routes to review.
  - Raw historical tickets are not customer-facing evidence.
  - Red confidence does not produce sendable draft.

## Public Alpha Gate

- [x] Run alpha gate.
  - Command: `bash scripts/ci_golden_eval.sh`.
  - Result: passed.
- [x] Confirm alpha gate metrics.
  - Hard failures: `0`.
  - Citation precision: `1.0`.
  - Release profile: `public_alpha`.
  - Warning count documented.
- [x] Confirm README eval block updated by generated report.
  - Done: `Release profile | public_alpha` appears in README.

## Production Gate

- [x] Run production gate.
  - Command: `.venv/bin/python scripts/run_golden_eval.py --results eval/golden_set/last_results.jsonl --release-gate --release-profile production --max-avg-latency-ms 15000 --max-total-cost-usd 1.00`
  - Current result: fails as expected.
- [ ] Do not call demo production-ready until all production targets pass.
  - Validation/review failures: `<= 4`.
  - Recall@3: `>= 0.75`.
  - Recall@5: `>= 0.75`.
  - Source precision: `>= 0.60`.
  - Required-point coverage: `>= 0.50`.
  - Source-safety hard failures: `0`.

## Quality Work For Next Public Demo Iteration

- [ ] Add XLSX/PDF vector ingest, or keep them preview-only in all public copy.
  - Current state: CSV vector ingest is ready; XLSX/PDF preview exists but vector ingestion is not wired.

- [ ] Reduce validation/review warnings from `12` to `<= 4`.
  - Start with warnings from current golden report.
  - Do not relax source-safety rules.
- [ ] Improve source precision from `0.4716` to `>= 0.60`.
  - Focus: route-critical evidence selection, source de-dup, metadata boosts, query cleaning.
- [ ] Improve Recall@3/5 from `0.6596` to `>= 0.75`.
  - Focus: expected source aliases, retrieval policy, query construction, chunk coverage.
- [ ] Improve required-point coverage from `0.0577` to `>= 0.50`.
  - Focus: answer templates, required-point extraction, responder instructions, non-stale stored results.
- [ ] Re-run live A/B after retrieval changes.
  - Command: `.venv/bin/python scripts/run_live_ab_eval.py --delay-seconds 2.2`.
  - Done when: current default remains justified by fresh numbers or new default selected.

## Public Repo Packaging

- [x] Confirm license.
  - File: `LICENSE`.
  - Current: MIT.
- [x] Confirm README first screen says alpha/developer preview.
- [x] Confirm README says suggest-only, never autonomous send/resolve.
- [x] Confirm `.env.docker.example` has placeholders only.
- [x] Confirm `CONFIGURATOR_PREFILL_API_KEY=false` for public demo.
- [x] Confirm CORS origins are local-only by default.
- [x] Confirm demo data is fictional and product-neutral.
- [x] Confirm no local-only absolute paths appear in tracked docs/scripts.
- [x] Confirm ignored `.understand-anything/` and local planning docs are not needed by public reviewer.
- [x] Confirm generated artifacts are ignored unless intentionally published.

## Optional Hosted Demo Gate

Use only if public-facing means internet-hosted, not just public repo.

- [ ] Put app behind HTTPS.
- [ ] Use non-default database password and private network DB.
- [ ] Use separate viewer/admin tokens.
- [ ] Disable key prefill.
- [ ] Restrict CORS to hosted origin.
- [ ] Add rate limiting or reverse-proxy throttling.
- [ ] Add provider spend cap.
- [ ] Add demo reset job.
- [ ] Add log retention and redaction review.
- [ ] Do not expose onboarding wizard publicly without extra auth.

## Final Publish Decision

- [x] Public repo alpha approved for CSV KB import demo.
  - Required: remaining non-deferred checks complete.
  - Required: alpha gate passes.
  - Required: clean Docker smoke passes.
  - Required: tracked-tree secret scan clean.
  - Deferred: key rotation and live-provider smoke.
- [ ] Production launch approved.
  - Required: production gate passes.
  - Required: hosted security gate passes if internet-hosted.
  - Current decision: not approved.
