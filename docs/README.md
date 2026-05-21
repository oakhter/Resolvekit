# ResolveKit Docs

ResolveKit public docs are intentionally compact. Internal planning docs can exist locally, but git tracks only this index, the demo guide, and the technical guide.

## Maintained Docs

- [Docs Index](README.md) — this file.
- [Demo Guide](DEMO.md) — fictional demo sources, tickets, and expected behavior.
- [Technical Guide](TECHNICAL.md) — architecture, request flow, source contract, safety rules, evals, and Docker-first onboarding.

## Current Alpha Metrics

Golden reports are generated locally by `bash scripts/ci_golden_eval.sh`; generated report outputs are intentionally not committed.

| Metric | Result |
| --- | ---: |
| Golden cases | 52 |
| Evaluated results | 52 |
| Source-safety hard failures | 0 |
| Retrieval Recall@3 | 0.7021 |
| Retrieval Recall@5 | 0.766 |
| Mean reciprocal rank | 0.5798 |
| Route accuracy | 1.0 |
| Confidence band accuracy | 1.0 |
| Abstention accuracy | 1.0 |
| Validation/review warnings | 14 |
| Stored golden-eval cost | 0.023967 USD |

Latest local verification summary:

- Focused Phase 3.3-7 tests: `10 passed`.
- Adjacent strategy/API regression slice: `14 passed`.
- Broad non-live pytest slice: `202 passed, 23 deselected`.
- Golden gate: passed with `0` source-safety hard failures.

## Decision Summary

- Runtime baseline: `current_hybrid_rag`; query decomposition is experiment-controlled; graph-style retrieval remains outside alpha defaults.
- Source formats: CSV, XLSX, and born-digital PDF with manifest metadata. DOCX, OCR, web, raw helpdesk, promoted HTML loaders, and schema inference are deferred.
- Permissions: Viewer and Admin token split. Admin owns full trace JSON, replay, source controls, exports, eval/A/B runs, config changes, and audit logs.
- Validation outcomes: `clean`, `clean_with_caveats`, `corrected`, `abstained`, and `hard_failure`.
- UI layout: Viewer-first support drafting flow with a separate Admin/configurator workflow.

## Documentation Rule

New public documentation should land in one of the tracked docs above. Short-lived notes should stay near code, tests, release artifacts, or private planning files.
