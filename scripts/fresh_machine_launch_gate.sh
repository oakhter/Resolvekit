#!/usr/bin/env bash
set -euo pipefail

SOURCE_REPO="${SOURCE_REPO:-$(git config --get remote.origin.url 2>/dev/null || pwd)}"
GATE_ROOT="${GATE_ROOT:-/tmp/resolvekit-fresh-machine-gate}"
WORK_DIR="$GATE_ROOT/ResolveKit"
# Fresh-machine gate forces ACTIVE_PROVIDER=mock so it can run without hosted API keys.
export DB_HOST_PORT="${DB_HOST_PORT:-55432}"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-resolvekit_fresh_gate}"

rm -rf "$GATE_ROOT"
mkdir -p "$GATE_ROOT"

git clone "$SOURCE_REPO" "$WORK_DIR"
cd "$WORK_DIR"
trap 'docker compose down --remove-orphans >/dev/null 2>&1 || true' EXIT

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.docker.example .env.docker
python3 - <<'PY'
from pathlib import Path

path = Path(".env.docker")
text = path.read_text()
updates = {
    "ACTIVE_PROVIDER": "mock",
    "API_KEY": "fresh-viewer-token-123",
    "CONFIGURATOR_API_KEY": "fresh-admin-token-456",
    "VIEWER_TOKEN": "fresh-trace-token-789",
    "CONFIGURATOR_ADMIN_TOKEN": "fresh-config-admin-token-abc",
}
lines = []
seen = set()
for line in text.splitlines():
    if "=" not in line or line.lstrip().startswith("#"):
        lines.append(line)
        continue
    key = line.split("=", 1)[0]
    if key in updates:
        lines.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        lines.append(line)
for key, value in updates.items():
    if key not in seen:
        lines.append(f"{key}={value}")
path.write_text("\n".join(lines).rstrip() + "\n")
PY
cp .env.docker .env

make doctor
bash scripts/public_smoke.sh
