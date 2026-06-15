# ResolveKit Docs

ResolveKit docs are intentionally compact. Start with the root [README](../README.md), then use these files only when you need detail.

## Maintained Docs

- [Demo Guide](DEMO.md): demo script, sample tickets, expected behavior.
- [Technical Guide](TECHNICAL.md): architecture, API contracts, safety rules, metrics, and where to change code.
- [Code Map](CODE_MAP.json): compact machine-readable map for reviewers.

## Current Demo Readiness

| Metric | Current value |
| --- | ---: |
| Golden cases | 52 |
| Source-safety hard failures | 0 |
| Validation/review warnings | 12 |
| Recall@3/5 | 0.6596 |
| Source precision | 0.4716 |
| Citation precision | 1.0 |
| Required-point coverage | 0.0577 |
| Production readiness | not approved |

Run:

```bash
make doctor
```

The doctor command writes `diagnostics/demo_doctor/latest.json` and `diagnostics/demo_doctor/latest.md`.

## Documentation Rule

Public docs should stay short and current. Old planning notes, generated reports, local graphs, logs, and private setup files should remain ignored or be removed from the workspace before publishing.
