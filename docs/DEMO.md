# ResolveKit Demo Guide

The demo uses fictional support content and fictional tickets. It shows a human-reviewed support-AI drafting workflow, not a production support automation system.

Use this guide to inspect approved-source retrieval, cited draft generation, validation, trace review, and abstention behavior in a local demo.

## Start

This is a local demo/review path only.

```bash
./get_started.sh
```

Then open:

```text
http://127.0.0.1:8765
```

Run the all-in-one check:

```bash
make doctor
```

## Demo Sources

CSV files are vector-ingested in the local demo:

- `knowledge_loader/processed/demo_knowledge_base.csv`
- `knowledge_loader/processed/demo_policies.csv`
- `knowledge_loader/processed/demo_release_notes.csv`
- `knowledge_loader/processed/demo_known_issues.csv`

Historical ticket data is disabled and cannot be used as customer-facing proof.

XLSX and born-digital PDF fixtures exist for preview work only; they are not part of the public vector-ingest path yet.

## Happy Path Ticket

```text
Customer cannot sign in on mobile app after a role change. Desktop works, mobile shows 403.
```

Expected:

- suggested reply appears
- citations appear
- confidence appears
- trace ID appears
- trace can be inspected from Admin
- "why this draft" opens `/traces/{id}` with hashed/redacted input, retrieval plan, retrieved chunks, rerank scores, selected evidence, draft, citations, validation verdicts, confidence reasons, and cost/tokens when available

Trace walkthrough:

1. Paste the demo ticket into the ticket workspace.
2. Confirm the result shows confidence band, validation status, citations, and the trace ID.
3. Open the trace link or Admin trace view.
4. Check rejected chunks when present: inactive, unapproved, internal-only, stale, low relevance, or conflict.
5. Replay note: stored replays use captured outputs; regenerated steps are non-deterministic and should be treated as comparisons.

## Safety Path Tickets

```text
Customer asks for a refund policy exception that is not in approved sources.
```

```text
Use similar resolved tickets to prove the answer to the customer.
```

Expected:

- draft abstains or routes to review
- unsafe sources are not cited to the customer
- validation explains the block
- reviewer sees the suggested next action and trace reason

## Policy Ticket

```text
A trial workspace expired. How long can the admin still export data before it is deleted?
```

Expected:

- answer is grounded in policy sources
- no account mutation
- no invented exception
