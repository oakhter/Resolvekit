from __future__ import annotations

import base64
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
UPLOAD_DIR = ROOT / "demo_data" / "onboarding" / "uploads"
ENV_PATH = ROOT / os.getenv("ONBOARDING_ENV_FILE", ".env")
CONTAINER_MODE = os.getenv("ONBOARDING_CONTAINER_MODE", "").strip().lower() in {"1", "true", "yes", "on"}
APP_PROCESS: subprocess.Popen | None = None

sys.path.insert(0, str(ROOT))

from backend.core import project_config
from scripts.init_project import ensure_env, read_env
from scripts.os_detect import command_exists, command_ok, detect_os

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


def _python() -> str:
    return str(VENV_PYTHON if VENV_PYTHON.exists() else sys.executable)


def _command_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(read_env(ENV_PATH))
    return env


def run_command(args: list[str], timeout: int = 300) -> dict:
    started = time.time()
    proc = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, timeout=timeout, env=_command_env())
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
        "duration_ms": int((time.time() - started) * 1000),
        "command": " ".join(args),
    }


def system_status() -> dict:
    env = read_env(ENV_PATH)
    provider = env.get("ACTIVE_PROVIDER", "openai")
    key_name = "OPENAI_API_KEY" if provider == "openai" else ("GEMINI_API_KEY" if provider == "gemini" else "")
    viewer_token = env.get("API_KEY", "")
    admin_token = env.get("CONFIGURATOR_API_KEY", "")
    provider_key = env.get(key_name, "") if key_name else "mock-preview"
    database_url = env.get("DATABASE_URL", "")
    docker_ready = True if CONTAINER_MODE else command_exists("docker") and command_ok(["docker", "info"], timeout=5)
    compose_ready = True if CONTAINER_MODE else command_exists("docker") and command_ok(["docker", "compose", "version"], timeout=5)
    try:
        env_display = str(ENV_PATH.relative_to(ROOT))
    except ValueError:
        env_display = ENV_PATH.name
    return {
        "os": detect_os().to_dict(),
        "python": sys.version.split()[0],
        "venv_python": str(VENV_PYTHON.relative_to(ROOT)) if VENV_PYTHON.exists() else "",
        "env_file": env_display,
        "env_exists": ENV_PATH.exists(),
        "provider": provider,
        "provider_key_present": bool(provider_key),
        "provider_key_placeholder": provider_key in {"", "replace-with-provider-key", "change-me"},
        "provider_key_name": key_name or "MOCK_PROVIDER_NO_KEY",
        "viewer_token_present": bool(viewer_token),
        "viewer_token_placeholder": viewer_token in {"", "change-me", "replace-with-random-viewer-token"},
        "admin_token_present": bool(admin_token),
        "admin_token_placeholder": admin_token in {"", "change-me-configurator", "replace-with-random-admin-token"},
        "docker_cli": True if CONTAINER_MODE else command_exists("docker"),
        "docker_ready": docker_ready,
        "docker_compose": compose_ready,
        "container_mode": CONTAINER_MODE,
        "database_url_present": bool(database_url),
        "default_database_credentials": "resolvekit:resolvekit" in database_url,
        "app_port_free": port_free(8000),
    }


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def configure_env(provider: str, provider_key: str) -> dict:
    env = ensure_env(provider=provider, provider_key=provider_key, demo=True, path=ENV_PATH, interactive=False)
    return {
        "ok": True,
        "provider": env["ACTIVE_PROVIDER"],
        "viewer_token": env["API_KEY"],
        "admin_token": env["CONFIGURATOR_API_KEY"],
    }


def install_dependencies() -> dict:
    if CONTAINER_MODE:
        return {"ok": True, "stdout": "Dependencies are already installed in the Docker image."}
    return run_command([_python(), "-m", "pip", "install", "-r", "requirements.txt"], timeout=900)


def start_database() -> dict:
    if CONTAINER_MODE:
        try:
            import psycopg2

            env = read_env(ENV_PATH)
            conn = psycopg2.connect(env.get("DATABASE_URL", os.getenv("DATABASE_URL", "")), connect_timeout=5)
            conn.close()
            return {"ok": True, "stdout": "Docker database is reachable."}
        except Exception as exc:
            return {"ok": False, "stderr": str(exc).splitlines()[0]}
    if not command_exists("docker"):
        return {"ok": False, "stderr": "Docker CLI not found.", "hint": detect_os().docker_install_hint}
    if not command_ok(["docker", "compose", "version"], timeout=5):
        return {"ok": False, "stderr": "Docker Compose plugin not found.", "hint": detect_os().docker_install_hint}
    return run_command(["docker", "compose", "up", "-d", "db"], timeout=300)


def generate_demo_data() -> dict:
    return run_command([_python(), "scripts/generate_resolvekit_demo_data.py"], timeout=180)


def setup_database() -> dict:
    return run_command([_python(), "scripts/setup_db.py"], timeout=180)


def load_knowledge() -> dict:
    return run_command([_python(), "knowledge_loader/kb_loader.py", "--all"], timeout=300)


def run_doctor() -> dict:
    return run_command([_python(), "scripts/onboarding_doctor.py"], timeout=60)


def start_app() -> dict:
    global APP_PROCESS
    if APP_PROCESS and APP_PROCESS.poll() is None:
        return {"ok": True, "stdout": "App already running.", "url": "http://127.0.0.1:8000"}
    APP_PROCESS = subprocess.Popen(
        [_python(), "-m", "uvicorn", "backend.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_command_env(),
    )
    time.sleep(3)
    if APP_PROCESS.poll() is not None:
        stderr = APP_PROCESS.stderr.read() if APP_PROCESS.stderr else "App exited."
        return {"ok": False, "stderr": stderr[-4000:]}
    return {"ok": True, "stdout": "App started.", "url": "http://127.0.0.1:8000"}


def first_draft_smoke(ticket: str = "Customer cannot sign in on mobile app after a role change. Desktop works, mobile shows 403.") -> dict:
    env = read_env(ENV_PATH)
    token = env.get("API_KEY", "")
    if not token:
        return {"ok": False, "stderr": "API_KEY missing."}
    payload = json.dumps({
        "ticket": ticket,
        "support_ops_mode": "query",
        "product": "example_product",
        "permission_level": "agent",
        "access_channel": "mobile_app",
        "similarity_threshold": "none",
        "pinned_source_ids": [],
    }).encode("utf-8")
    req = Request(
        "http://127.0.0.1:8000/resolve",
        data=payload,
        headers={"Content-Type": "application/json", "x-api-key": token},
        method="POST",
    )
    try:
        with urlopen(req, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
            return {"ok": response.status == 200, "status": response.status, "resolution": data.get("resolution", {})}
    except URLError as exc:
        return {"ok": False, "stderr": str(exc.reason)}
    except Exception as exc:
        return {"ok": False, "stderr": str(exc)}


def save_uploaded_source(filename: str, content_base64: str) -> dict:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name
    if Path(safe_name).suffix.lower() != ".csv":
        return {"ok": False, "error": "Vector ingest currently supports CSV knowledge files."}
    target = UPLOAD_DIR / safe_name
    target.write_bytes(base64.b64decode(content_base64))
    rel = str(target.relative_to(ROOT))
    preview = project_config.preview_source(
        source_key="knowledge_base",
        path=rel,
        source_type="official_help_article",
        column_mapping={"title": "title", "content": "content", "url": "url", "url_name": "url_name"},
        sample_row_limit=5,
    )
    return {"ok": True, "path": rel, "preview": preview}


def _source_key(path: str) -> str:
    stem = Path(path).stem.lower()
    stem = re.sub(r"[^a-z0-9]+", "_", stem).strip("_") or "source"
    return f"onboarding_{stem}"


def ingest_uploaded_sources(paths: list[str]) -> dict:
    if not paths:
        return {"ok": False, "stderr": "No uploaded source paths supplied."}
    unsupported = [path for path in paths if Path(path).suffix.lower() != ".csv"]
    if unsupported:
        names = ", ".join(Path(path).name for path in unsupported)
        return {"ok": False, "stderr": f"Vector ingest currently supports CSV knowledge files only: {names}"}
    sources_path = ROOT / "config" / "sources.yaml"
    data = {"sources": {}}
    if sources_path.exists():
        text = sources_path.read_text(encoding="utf-8")
        if yaml:
            data = yaml.safe_load(text) or data
        else:
            data = json.loads(text)
    data.setdefault("sources", {})
    added = []
    for path in paths:
        rel = str(Path(path))
        suffix = Path(rel).suffix.lower()
        if suffix not in {".csv", ".xlsx", ".pdf"}:
            continue
        key = _source_key(rel)
        data["sources"][key] = {
            "enabled": True,
            "source_type": "official_help_article",
            "path": rel,
            "audience": "customer_facing",
            "required_columns": ["title", "content"] if suffix in {".csv", ".xlsx"} else [],
            "column_mapping": {"title": "title", "content": "content", "url": "url", "url_name": "url_name"},
            "default_authority": 0.9,
        }
        added.append(key)
    if yaml:
        sources_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    else:
        sources_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    load_result = load_knowledge()
    return {"ok": bool(added) and load_result.get("ok", False), "added_sources": added, "load_result": load_result}


def reset_demo_state() -> dict:
    removed: list[str] = []
    for path in [
        UPLOAD_DIR,
        ROOT / "config" / "sources.yaml",
        ROOT / "config" / "products.yaml",
        ROOT / "config" / "output.yaml",
        ROOT / "config" / "retrieval_policy.yaml",
        ROOT / "config" / "workflow.yaml",
    ]:
        if path.is_dir():
            shutil.rmtree(path)
            removed.append(str(path.relative_to(ROOT)))
        elif path.exists():
            path.unlink()
            removed.append(str(path.relative_to(ROOT)))
    if CONTAINER_MODE:
        return {
            "ok": True,
            "removed": removed,
            "stdout": "Local generated demo files removed.",
            "hint": "To stop containers or remove the database volume, run on the host: docker compose down or docker compose down -v",
        }
    down_result = run_command(["docker", "compose", "down"], timeout=120)
    return {
        "ok": down_result.get("ok", False),
        "removed": removed,
        "stdout": down_result.get("stdout", ""),
        "stderr": down_result.get("stderr", ""),
        "hint": "Use docker compose down -v only when you intentionally want to delete the local database volume.",
    }


TASKS = {
    "install_dependencies": install_dependencies,
    "start_database": start_database,
    "generate_demo_data": generate_demo_data,
    "setup_database": setup_database,
    "load_knowledge": load_knowledge,
    "run_doctor": run_doctor,
    "start_app": start_app,
    "first_draft_smoke": first_draft_smoke,
    "reset_demo_state": reset_demo_state,
}


def run_task(name: str) -> dict:
    if name not in TASKS:
        return {"ok": False, "stderr": f"Unknown task: {name}"}
    return TASKS[name]()
