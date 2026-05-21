#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

echo "ResolveKit Get Started"
OS="$(uname -s 2>/dev/null || echo unknown)"
ARCH="$(uname -m 2>/dev/null || echo unknown)"
echo "Detected: $OS $ARCH"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker not found."
  echo "macOS: install Docker Desktop, then rerun ./get_started.sh"
  echo "Linux: install Docker Engine plus Docker Compose plugin, then rerun ./get_started.sh"
  echo "Windows: use WSL2 with Docker Desktop integration."
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin missing. Install Docker Desktop or Compose plugin, then rerun."
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon not reachable."
  echo "Open Docker Desktop, wait until it says Docker is running, then rerun ./get_started.sh"
  echo "If this terminal still fails, quit/reopen terminal and retry: docker info"
  exit 1
fi

if [ ! -f ".env.docker" ]; then
  cp .env.docker.example .env.docker
  echo "Created .env.docker from .env.docker.example"
fi

echo "Building and starting Docker services..."
docker compose up -d --build db onboarding

echo "Onboarding wizard: http://127.0.0.1:8765"
if command -v open >/dev/null 2>&1; then
  (sleep 2 && open "http://127.0.0.1:8765") >/dev/null 2>&1 &
elif command -v xdg-open >/dev/null 2>&1; then
  (sleep 2 && xdg-open "http://127.0.0.1:8765") >/dev/null 2>&1 &
fi

echo "Logs: docker compose logs -f onboarding"
docker compose logs -f onboarding
