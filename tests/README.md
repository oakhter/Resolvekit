# Tests

Active pytest coverage lives in one behavior-based file: `test_resolvekit.py`.

| File | What it covers |
| --- | --- |
| `test_resolvekit.py` | Consolidated unit, integration, public-demo, safety, source-preview, config, API-contract, trace, metrics, eval-gate, and server-backed API smoke coverage. |
| `run_qa.py` | QA runner for server-backed API smoke tests. Requires a running API server and may use the configured provider on cache misses. |
| `qa.md` | Human-readable deterministic and server-backed QA checklist. |
| `conftest.py` | Shared pytest fixtures for API base URL and auth headers. |

## Commands

Collect everything:

```bash
.venv/bin/python -m pytest tests/test_resolvekit.py --collect-only -q
```

Run deterministic/non-server coverage:

```bash
.venv/bin/python -m pytest tests/test_resolvekit.py -q -k "not TestHealth and not TestAuth and not TestResolve and not TestFeedback and not TestEndToEnd"
```

Run server-backed API smoke after starting the app:

```bash
.venv/bin/python tests/run_qa.py
```

Run all coverage, including server-backed API tests:

```bash
.venv/bin/python -m pytest tests/test_resolvekit.py -q
```

## Rules

New pytest coverage belongs in `test_resolvekit.py`.

Do not add release-number test files or topic-specific split files. Use section comments inside `test_resolvekit.py` when grouping helps.

`scripts/qa_retrieval.py` lives outside this folder because it is a developer QA utility rather than a pytest module. It verifies retrieval diagnostics without LLM calls.

Milestone-specific task lists belong outside this folder.
