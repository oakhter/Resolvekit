# QA Reference

This folder contains two kinds of QA:

- **Deterministic tests** that run locally without provider calls.
- **Server-backed API smoke tests** that call a running FastAPI server and may use the configured provider.

## Recommended Checks

Run these before pushing feature work:

```bash
python -m pytest tests/test_resolvekit.py -q
python scripts/qa_retrieval.py
```

When local PostgreSQL is available and reachable:

```bash
python scripts/qa_retrieval.py --with-db
```

When the API server is running:

```bash
python tests/run_qa.py
```

## Deterministic Coverage

| File | What it checks |
| --- | --- |
| `test_resolvekit.py` | Runtime config behavior, route policy scoring, source preview, evidence safety, trace redaction, public docs, API contract, metrics, eval gates, and server-backed API smoke tests. |
| `scripts/qa_retrieval.py` | Deterministic source preview, context-aware chunk text, evaluator-skipped validation, retrieval diagnostics shape, and optional vector DB schema connectivity. |

## API Smoke Coverage

`tests/run_qa.py` runs the API smoke subset in `tests/test_resolvekit.py` against a live server. These checks cover:

| Area | What it checks |
| --- | --- |
| Health | `/health` returns `status: ok` and service metadata. |
| Auth | Missing or invalid API keys return `401`. |
| Resolve | `/resolve` returns a success envelope, required resolution fields, confidence, cache key, usage summary, diagnosis, draft email subject, and retrieval signals. |
| Cache | Repeated requests return cached responses; different products do not share cache keys. |
| Feedback | `/feedback` accepts thumbs-up/down ratings and retrieval signal metadata, and rejects unauthenticated requests. |
| End-to-end | Resolve a ticket, then submit feedback using the returned cache key and retrieval signals. |

## Notes

- `tests/run_qa.py` expects the app to already be running at `http://127.0.0.1:8000` unless `--url` is supplied.
- Provider-backed API tests can incur LLM cost on fresh cache misses.
- The old adversarial/stress script was removed from active QA because it was product-specific and provider-expensive. Add future safety checks as deterministic tests first, then introduce provider-backed scenarios only when they are generic and clearly scoped.
