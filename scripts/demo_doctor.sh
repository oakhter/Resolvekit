#!/usr/bin/env bash
# Canonical release doctor: run this before publishing a preview release.
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

JSON_REPORT="diagnostics/demo_doctor/latest.json"
MD_REPORT="diagnostics/demo_doctor/latest.md"
REPORT_DIR="$(dirname "$JSON_REPORT")"
ENV_FILE="${ENV_FILE:-.env.docker}"
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
ONBOARDING_URL="${ONBOARDING_URL:-http://127.0.0.1:8765}"
if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
else
  PYTHON_BIN="${PYTHON:-python3}"
fi

mkdir -p "$REPORT_DIR"

RESULT_NAMES=()
RESULT_STATUS=()
RESULT_DETAILS=()
RESULT_FIXES=()
FAILURES=0
WARNINGS=0

say() {
  printf '%s\n' "$*"
}

record() {
  local name="$1"
  local status="$2"
  local detail="${3:-}"
  local fix="${4:-}"
  RESULT_NAMES+=("$name")
  RESULT_STATUS+=("$status")
  RESULT_DETAILS+=("$detail")
  RESULT_FIXES+=("$fix")
  case "$status" in
    FAIL) FAILURES=$((FAILURES + 1)) ;;
    WARN) WARNINGS=$((WARNINGS + 1)) ;;
  esac
  printf '%-34s %s\n' "$name" "$status"
  if [ -n "$detail" ]; then
    printf '  %s\n' "$detail"
  fi
  if [ "$status" = "FAIL" ] && [ -n "$fix" ]; then
    printf '  Fix: %s\n' "$fix"
  fi
}

run_check() {
  local name="$1"
  shift
  local output
  if output="$("$@" 2>&1)"; then
    record "$name" "PASS" "$(printf '%s' "$output" | tail -5)"
    return 0
  fi
    record "$name" "FAIL" "$(printf '%s' "$output" | tail -12)" "Run the command shown above directly, fix the reported error, then rerun make doctor."
  return 1
}

env_value() {
  local key="$1"
  if [ ! -f "$ENV_FILE" ]; then
    return 0
  fi
  awk -F= -v key="$key" '$1 == key {print substr($0, index($0, "=") + 1)}' "$ENV_FILE" | tail -1
}

json_escape() {
  python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'
}

write_reports() {
  {
    printf '{\n'
    printf '  "demo_readiness": "%s",\n' "$([ "$FAILURES" -eq 0 ] && printf READY || printf NOT_READY)"
    printf '  "production_readiness": "NOT_READY",\n'
    printf '  "failures": %s,\n' "$FAILURES"
    printf '  "warnings": %s,\n' "$WARNINGS"
    printf '  "checks": [\n'
    local last_index=$((${#RESULT_NAMES[@]} - 1))
    for i in "${!RESULT_NAMES[@]}"; do
      local comma=","
      [ "$i" -eq "$last_index" ] && comma=""
      printf '    {"name": %s, "status": %s, "detail": %s}%s\n' \
        "$(printf '%s' "${RESULT_NAMES[$i]}" | json_escape)" \
        "$(printf '%s' "${RESULT_STATUS[$i]}" | json_escape)" \
        "$(printf '%s' "${RESULT_DETAILS[$i]}" | json_escape)" \
        "$comma"
    done
    printf '  ]\n'
    printf '}\n'
  } > "$JSON_REPORT"

  {
    printf '# ResolveKit Demo Doctor\n\n'
    printf '%s\n' "- Demo readiness: $([ "$FAILURES" -eq 0 ] && printf READY || printf 'NOT READY')"
    printf '%s\n' "- Production readiness: NOT READY"
    printf '%s\n' "- Failures: $FAILURES"
    printf '%s\n\n' "- Warnings: $WARNINGS"
    printf '| Check | Status | Fix |\n| --- | --- | --- |\n'
    for i in "${!RESULT_NAMES[@]}"; do
      printf '| %s | %s | %s |\n' "${RESULT_NAMES[$i]}" "${RESULT_STATUS[$i]}" "${RESULT_FIXES[$i]:-}"
    done
    printf '\nOpen onboarding: %s\n\n' "$ONBOARDING_URL"
    printf 'Open ticket UI: %s/\n\n' "${BASE_URL%/}"
    printf 'Open admin: %s/admin\n' "${BASE_URL%/}"
  } > "$MD_REPORT"
}

say "ResolveKit Demo Doctor"
say

if command -v docker >/dev/null 2>&1; then
  record "Docker CLI" "PASS" "docker found"
else
  record "Docker CLI" "FAIL" "Install Docker Desktop or Docker Engine." "Install Docker Desktop, reopen the terminal, then rerun make doctor."
fi

if docker info >/dev/null 2>&1; then
  record "Docker daemon" "PASS" "daemon reachable"
else
  record "Docker daemon" "FAIL" "Start Docker, then rerun this command." "Open Docker Desktop or start Docker Engine, wait for docker info to pass, then rerun."
fi

if docker compose version >/dev/null 2>&1; then
  record "Docker Compose" "PASS" "$(docker compose version)"
else
  record "Docker Compose" "FAIL" "Docker Compose plugin is required." "Install Docker Compose v2 plugin."
fi

check_port_available() {
  local name="$1"
  local port="$2"
  local url="$3"
  if curl -fsS "$url" >/dev/null 2>&1; then
    record "$name port" "PASS" "$url already responds"
    return
  fi
  if python3 - "$port" <<'PY' >/dev/null 2>&1
import socket
import sys

port = int(sys.argv[1])
sock = socket.socket()
try:
    sock.bind(("127.0.0.1", port))
finally:
    sock.close()
PY
  then
    record "$name port" "PASS" "127.0.0.1:$port is available"
  else
    record "$name port" "FAIL" "127.0.0.1:$port is already in use and not responding as ResolveKit." "Stop the process using port $port or change the local port mapping, then rerun make doctor."
  fi
}

check_port_available "App" "8000" "${BASE_URL%/}/health"
check_port_available "Onboarding" "8765" "${ONBOARDING_URL%/}/api/status"

if [ ! -f "$ENV_FILE" ] && [ -f ".env.docker.example" ]; then
  cp .env.docker.example "$ENV_FILE"
  record "Docker env file" "WARN" "Created $ENV_FILE from .env.docker.example. Fill in local keys before live provider checks."
elif [ -f "$ENV_FILE" ]; then
  record "Docker env file" "PASS" "$ENV_FILE exists"
else
  record "Docker env file" "FAIL" "$ENV_FILE missing and .env.docker.example not found" "Restore .env.docker.example or create $ENV_FILE from README values."
fi

api_key="$(env_value API_KEY)"
admin_key="$(env_value CONFIGURATOR_API_KEY)"
active_provider="$(env_value ACTIVE_PROVIDER)"
active_provider="${active_provider:-openai}"
provider_upper="$(printf '%s' "$active_provider" | tr '[:lower:]' '[:upper:]')"
provider_key_name="${provider_upper}_API_KEY"
provider_key="$(env_value "$provider_key_name")"

if [ -n "$api_key" ] && [ "$api_key" != "change-me" ]; then
  record "Viewer token" "PASS" "configured"
else
  record "Viewer token" "FAIL" "API_KEY must be set in $ENV_FILE" "Set API_KEY in $ENV_FILE to a non-placeholder random value."
fi

if [ -n "$admin_key" ] && [ "$admin_key" != "change-me-configurator" ]; then
  record "Admin token" "PASS" "configured"
else
  record "Admin token" "FAIL" "CONFIGURATOR_API_KEY must be set in $ENV_FILE" "Set CONFIGURATOR_API_KEY in $ENV_FILE to a non-placeholder random value distinct from API_KEY."
fi

bind_host="$(env_value BIND_HOST)"
app_bind_host="$(env_value APP_BIND_HOST)"
if [ "$bind_host" = "0.0.0.0" ] || [ "$app_bind_host" = "0.0.0.0" ]; then
  record "Loopback exposure" "FAIL" "BIND_HOST/APP_BIND_HOST is set to 0.0.0.0" "Use 127.0.0.1 unless you have reviewed trace and admin exposure risks."
elif grep -q '"127.0.0.1:8000:8000"' docker-compose.yml \
  && grep -q '"127.0.0.1:8765:8765"' docker-compose.yml \
  && grep -q '127.0.0.1' Dockerfile; then
  record "Loopback exposure" "PASS" "Docker app, onboarding, and default bind host are loopback-only."
else
  record "Loopback exposure" "FAIL" "Docker/default bind host is not provably loopback-only." "Bind app and onboarding ports to 127.0.0.1, then rerun make doctor."
fi

if [ "${#api_key}" -ge 12 ] && [ "${#admin_key}" -ge 12 ] && [ "$api_key" != "$admin_key" ]; then
  record "Key strength" "PASS" "viewer and admin tokens are distinct and non-placeholder"
else
  record "Key strength" "FAIL" "Viewer/admin tokens must be distinct random values of at least 12 characters." "Set API_KEY and CONFIGURATOR_API_KEY to distinct random values in $ENV_FILE."
fi

record "Resolved config paths" "PASS" "$("$PYTHON_BIN" - <<'PY'
from backend.core import project_config
for key, item in project_config.resolved_config_files().items():
    print(f"{key}: {item['source']} -> {item['active_path']}")
PY
)" ""

if "$PYTHON_BIN" scripts/validate_sources.py demo_data/csv/minimal_valid_kb.csv >/tmp/resolvekit_demo_doctor_source_preview.txt 2>&1; then
  record "Source preview dry run" "PASS" "$(cat /tmp/resolvekit_demo_doctor_source_preview.txt | tail -8)"
else
  record "Source preview dry run" "FAIL" "$(cat /tmp/resolvekit_demo_doctor_source_preview.txt | tail -12)" "Fix CSV row-level errors shown above, then rerun scripts/validate_sources.py <file>."
fi

if [ -n "$provider_key" ]; then
  record "Provider key" "PASS" "$active_provider key configured"
else
  record "Provider key" "WARN" "$active_provider key is not configured; smoke may use local/mock paths only."
fi

run_check "Ignored local artifacts" git status --short --ignored .env .env.docker config/sources.yaml config/products.yaml config/output.yaml config/retrieval_policy.yaml config/workflow.yaml logs diagnostics/logs demo_data/onboarding/uploads .understand-anything

path_pattern='/(Users|private)/'
key_pattern="sk-"
key_pattern="${key_pattern}proj-|AI"
key_pattern="${key_pattern}za"
private_key_pattern='BEGIN .* PRIVATE'
private_key_pattern="${private_key_pattern} KEY"
scan_pattern="${path_pattern}|${key_pattern}|${private_key_pattern}"
publishable_files="$(git ls-files --cached --others --exclude-standard)"
if [ -n "$publishable_files" ] && printf '%s\n' "$publishable_files" | xargs rg -n "$scan_pattern" >/tmp/resolvekit_demo_doctor_secret_scan.txt 2>&1; then
  record "Secret/local-path scan" "FAIL" "$(tail -10 /tmp/resolvekit_demo_doctor_secret_scan.txt)"
else
  record "Secret/local-path scan" "PASS" "no tracked or publishable-untracked hits"
fi

run_check "Whitespace check" git diff --check
run_check "Focused tests" "$PYTHON_BIN" -m pytest tests/test_resolvekit.py -k "onboarding or public_smoke or launch_readiness or diagnostics_masks_secret_values"
run_check "Demo readiness evaluation" bash scripts/ci_golden_eval.sh
run_check "Docker smoke" bash scripts/public_smoke.sh

docker compose stop app >/dev/null 2>&1 || true
if docker compose up -d --build onboarding >/tmp/resolvekit_demo_doctor_onboarding.txt 2>&1; then
  onboarding_ready=0
  for wait_index in 1 2 3 4 5 6 7 8 9 10; do
    if curl -fsS "$ONBOARDING_URL/" >/tmp/resolvekit_demo_doctor_onboarding_ui.html 2>&1 \
      && curl -fsS "$ONBOARDING_URL/api/status" >/tmp/resolvekit_demo_doctor_onboarding_status.json 2>&1; then
      onboarding_ready=1
      break
    fi
    sleep 2
  done
  if [ "$onboarding_ready" -eq 1 ]; then
    record "Onboarding wizard" "PASS" "$ONBOARDING_URL responded"
  else
    record "Onboarding wizard" "FAIL" "onboarding HTTP check failed after waiting"
  fi
else
  record "Onboarding wizard" "FAIL" "$(tail -12 /tmp/resolvekit_demo_doctor_onboarding.txt)"
fi

docker compose stop onboarding >/dev/null 2>&1 || true
docker compose up -d db app >/dev/null 2>&1 || true

write_reports

say
if [ "$FAILURES" -eq 0 ]; then
  say "Demo readiness: READY"
else
  say "Demo readiness: NOT READY"
fi
say "Production readiness: NOT READY"
say
say "Open onboarding: $ONBOARDING_URL"
say "Open ticket UI: ${BASE_URL%/}/"
say "Open admin: ${BASE_URL%/}/admin"
say
say "Reports:"
say "- $JSON_REPORT"
say "- $MD_REPORT"

if [ "$FAILURES" -eq 0 ]; then
  exit 0
fi
exit 1
