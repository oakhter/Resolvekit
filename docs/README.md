# ResolveKit Docs

ResolveKit public docs are intentionally compact. Internal planning docs can exist locally, but git tracks only this index, the demo guide, and the technical guide.

## Release Posture

ResolveKit is ready for public repository review as an alpha/developer preview, not for production launch. The stored release gate passes with `0` source-safety hard failures and citation precision at `1.0`, but source precision, answer coverage, and validation/review warnings remain active quality gates.

Production-readiness targets before a broader launch:

- keep source-safety hard failures at `0`
- reduce validation/review warnings from `12` toward fewer than `5`
- improve source precision from `0.4716` toward at least `0.60`
- improve Recall@3/5 from `0.6596` toward at least `0.75`
- improve required-point coverage on the golden set

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
| Retrieval Recall@3 | 0.6596 |
| Retrieval Recall@5 | 0.6596 |
| Mean reciprocal rank | 0.5709 |
| Route accuracy | 1.0 |
| Confidence band accuracy | 0.75 |
| Abstention accuracy | 0.7692 |
| Validation/review warnings | 12 |
| Stored golden-eval cost | 0.023234 USD |

Latest local verification summary:

- Focused canonical/abstention tests: `4 passed`.
- Focused direct-evidence selector tests: `3 passed`.
- Focused eval/source-safety suite: passing.
- Golden gate: passed with `0` source-safety hard failures.

## Decision Summary

- Runtime baseline: `current_hybrid_rag`; query decomposition is experiment-controlled; graph-style retrieval remains outside alpha defaults.
- Source formats: CSV vector ingest for public alpha onboarding. XLSX and born-digital PDF preview/fixtures exist, but public vector ingest is CSV-only until the connector-to-vector path is wired.
- Permissions: Viewer and Admin token split. Admin owns full trace JSON, replay, source controls, exports, eval/A/B runs, config changes, and audit logs.
- Support intelligence: Admin analytics are grouped into usage, retrieval health, evaluation, knowledge gaps, escalation signals, and costs. Multi-user reporting uses explicit user/team/session fields or headers, with API-token hash fallback.
- Validation outcomes: `clean`, `clean_with_caveats`, `corrected`, `abstained`, and `hard_failure`.
- UI layout: Viewer-first support drafting flow with Admin analytics, config, replay, source, and audit sections in one admin shell.

## Documentation Rule

New public documentation should land in one of the tracked docs above. Short-lived notes should stay near code, tests, release artifacts, or private planning files.
