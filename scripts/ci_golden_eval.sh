#!/usr/bin/env bash
set -euo pipefail

if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
else
  PYTHON_BIN="${PYTHON:-python3}"
fi

RESULTS_FILE="${GOLDEN_RESULTS:-eval/golden_set/last_results.jsonl}"
RESULTS_ARGS=()
if [ -f "$RESULTS_FILE" ]; then
  RESULTS_ARGS=(--results "$RESULTS_FILE")
fi

"$PYTHON_BIN" scripts/run_golden_eval.py \
  "${RESULTS_ARGS[@]}" \
  --release-gate \
  --max-avg-latency-ms "${MAX_AVG_LATENCY_MS:-15000}" \
  --max-total-cost-usd "${MAX_TOTAL_COST_USD:-1.00}" \
  --output eval/golden_set/last_report.json \
  --markdown-output eval/golden_set/last_report.md

"$PYTHON_BIN" scripts/build_eval_report.py \
  --golden-report eval/golden_set/last_report.json \
  --json-output eval/reports/latest.json \
  --markdown-output eval/reports/latest.md \
  --readme README.md
