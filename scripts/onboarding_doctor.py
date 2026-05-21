from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / os.getenv("ONBOARDING_ENV_FILE", ".env")

sys.path.insert(0, str(ROOT))

from scripts.init_project import provider_key_name, read_env


def result(status: str, name: str, detail: str = "") -> dict[str, str]:
    return {"status": status, "name": name, "detail": detail}


def check_env() -> list[dict[str, str]]:
    env = read_env(ENV_PATH)
    checks = []
    checks.append(result("ok" if ENV_PATH.exists() else "fail", "env file", ".env exists" if ENV_PATH.exists() else "Run scripts/init_project.py --demo"))
    provider = env.get("ACTIVE_PROVIDER", "openai")
    try:
        key_name = provider_key_name(provider)
        checks.append(result("ok", "provider", provider))
        checks.append(result("ok" if env.get(key_name) else "fail", "provider key", f"{key_name} present" if env.get(key_name) else f"{key_name} missing"))
    except ValueError as exc:
        checks.append(result("fail", "provider", str(exc)))
    checks.append(result("ok" if env.get("API_KEY") and env.get("API_KEY") != "change-me" else "fail", "viewer token", "API_KEY configured"))
    checks.append(result("ok" if env.get("CONFIGURATOR_API_KEY") and env.get("CONFIGURATOR_API_KEY") != "change-me-configurator" else "fail", "admin token", "CONFIGURATOR_API_KEY configured"))
    checks.append(result("ok" if env.get("DATABASE_URL") else "fail", "database url", "DATABASE_URL configured"))
    return checks


def check_files() -> list[dict[str, str]]:
    required = [
        "docker-compose.yml",
        "scripts/generate_resolvekit_demo_data.py",
        "scripts/setup_db.py",
        "knowledge_loader/kb_loader.py",
        "frontend/ticket/index.html",
        "docs/DEMO.md",
    ]
    return [
        result("ok" if (ROOT / path).exists() else "fail", path, "found" if (ROOT / path).exists() else "missing")
        for path in required
    ]


def check_db() -> dict[str, str]:
    env = read_env(ENV_PATH)
    database_url = env.get("DATABASE_URL", "")
    if not database_url:
        return result("fail", "database", "DATABASE_URL missing")
    try:
        import psycopg2

        conn = psycopg2.connect(database_url, connect_timeout=5)
        conn.close()
        return result("ok", "database", "reachable")
    except Exception as exc:
        return result("fail", "database", str(exc).splitlines()[0])


def check_port(host: str, port: int) -> dict[str, str]:
    try:
        with socket.create_connection((host, port), timeout=2):
            return result("ok", "app port", f"{host}:{port} reachable")
    except OSError:
        return result("warn", "app port", f"{host}:{port} not listening")


def check_health(base_url: str) -> dict[str, str]:
    try:
        with urlopen(f"{base_url.rstrip('/')}/health", timeout=5) as response:
            return result("ok" if response.status == 200 else "fail", "health", f"HTTP {response.status}")
    except URLError as exc:
        return result("warn", "health", str(exc.reason))
    except Exception as exc:
        return result("warn", "health", str(exc))


def check_role(base_url: str, token: str, label: str) -> dict[str, str]:
    if not token:
        return result("fail", label, "token missing")
    request = Request(f"{base_url.rstrip('/')}/api/me", headers={"x-api-key": token})
    try:
        with urlopen(request, timeout=5) as response:
            return result("ok" if response.status == 200 else "fail", label, f"HTTP {response.status}")
    except Exception as exc:
        return result("warn", label, str(exc).splitlines()[0])


def run_checks(base_url: str = "http://127.0.0.1:8000") -> list[dict[str, str]]:
    env = read_env(ENV_PATH)
    checks = []
    checks.extend(check_env())
    checks.extend(check_files())
    checks.append(check_db())
    checks.append(check_port("127.0.0.1", 8000))
    checks.append(check_health(base_url))
    checks.append(check_role(base_url, env.get("API_KEY", ""), "viewer role"))
    checks.append(check_role(base_url, env.get("CONFIGURATOR_API_KEY", ""), "admin role"))
    return checks


def print_report(checks: list[dict[str, str]]) -> None:
    for item in checks:
        marker = {"ok": "ok", "warn": "warn", "fail": "fail"}[item["status"]]
        detail = f" — {item['detail']}" if item.get("detail") else ""
        print(f"[{marker}] {item['name']}{detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check ResolveKit onboarding readiness.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    checks = run_checks(args.base_url)
    print_report(checks)
    return 1 if any(item["status"] == "fail" for item in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
