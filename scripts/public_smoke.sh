#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="${ENV_FILE:-.env.docker}"
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required" >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "docker compose is required" >&2
  exit 1
fi

wait_for_http() {
  local url="$1"
  local output="$2"
  local attempts="${3:-60}"
  local delay_seconds="${4:-2}"
  local attempt=1
  while [ "$attempt" -le "$attempts" ]; do
    if curl -fsS "$url" >"$output" 2>/tmp/resolvekit_public_smoke_curl_error.txt; then
      return 0
    fi
    sleep "$delay_seconds"
    attempt=$((attempt + 1))
  done
  cat /tmp/resolvekit_public_smoke_curl_error.txt >&2
  echo "Timed out waiting for $url" >&2
  return 1
}

if [ ! -f "$ENV_FILE" ]; then
  cp .env.docker.example "$ENV_FILE"
  echo "created $ENV_FILE from .env.docker.example"
fi

env_value() {
  local key="$1"
  awk -F= -v key="$key" '$1 == key {print substr($0, index($0, "=") + 1)}' "$ENV_FILE" | tail -1
}

API_KEY_VALUE="${API_KEY_VALUE:-$(env_value API_KEY)}"
CONFIGURATOR_API_KEY_VALUE="${CONFIGURATOR_API_KEY_VALUE:-$(env_value CONFIGURATOR_API_KEY)}"

if [ -z "$API_KEY_VALUE" ] || [ "$API_KEY_VALUE" = "change-me" ]; then
  echo "API_KEY in $ENV_FILE must be set before public smoke" >&2
  exit 1
fi

if [ -z "$CONFIGURATOR_API_KEY_VALUE" ] || [ "$CONFIGURATOR_API_KEY_VALUE" = "change-me-configurator" ]; then
  echo "CONFIGURATOR_API_KEY in $ENV_FILE must be set before public smoke" >&2
  exit 1
fi

docker compose up -d --build db app
docker compose exec -T db pg_isready -U resolvekit -d resolvekit
docker compose exec -T app python scripts/setup_db.py
printf "all\n" | docker compose exec -T app python knowledge_loader/kb_loader.py

wait_for_http "$BASE_URL/health" /tmp/resolvekit_health.json

resolve_payload='{
  "ticket": "Customer cannot sign in on mobile app after a role change. Desktop works, mobile shows 403.",
  "mode": "suggest",
  "product": "example_product",
  "access_channel": "mobile_app",
  "permission_level": "agent"
}'

curl -fsS \
  -H "content-type: application/json" \
  -H "x-api-key: $API_KEY_VALUE" \
  -d "$resolve_payload" \
  "$BASE_URL/resolve" >/tmp/resolvekit_resolve.json

TRACE_ID="$(
  python3 - <<'PY'
import json
from pathlib import Path

data = json.loads(Path("/tmp/resolvekit_resolve.json").read_text())
trace_id = data.get("resolution", {}).get("trace_id", "")
if not trace_id:
    raise SystemExit("resolve response did not include trace_id")
print(trace_id)
PY
)"

curl -fsS -H "x-api-key: $CONFIGURATOR_API_KEY_VALUE" "$BASE_URL/traces/$TRACE_ID" >/tmp/resolvekit_trace.json
curl -fsS -H "x-api-key: $CONFIGURATOR_API_KEY_VALUE" "$BASE_URL/metrics/daily" >/tmp/resolvekit_metrics_daily.json

preview_payload='{
  "source_key": "knowledge_base",
  "path": "/app/knowledge_loader/processed/demo_knowledge_base.csv",
  "source_type": "knowledge_base",
  "sample_row_limit": 2
}'

curl -fsS \
  -H "content-type: application/json" \
  -H "x-api-key: $CONFIGURATOR_API_KEY_VALUE" \
  -d "$preview_payload" \
  "$BASE_URL/configurator/source-preview" >/tmp/resolvekit_source_preview.json

echo "public smoke passed"
echo "trace_id=$TRACE_ID"
