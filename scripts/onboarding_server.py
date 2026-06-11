from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from scripts import onboarding_tasks

CONTAINER_MODE = os.getenv("ONBOARDING_CONTAINER_MODE", "").strip().lower() in {"1", "true", "yes", "on"}


ROOT = Path(__file__).resolve().parents[1]
UI_PATH = ROOT / "frontend" / "onboarding" / "index.html"

app = FastAPI(title="ResolveKit Onboarding", version="1.0")


class EnvRequest(BaseModel):
    provider: str
    provider_key: str


class TaskRequest(BaseModel):
    task: str


class UploadRequest(BaseModel):
    filename: str
    content_base64: str


class IngestRequest(BaseModel):
    paths: list[str]


@app.get("/", response_class=HTMLResponse)
def index():
    if not UI_PATH.exists():
        raise HTTPException(status_code=500, detail="Onboarding UI missing.")
    return HTMLResponse(UI_PATH.read_text(encoding="utf-8"))


@app.get("/api/status")
def status():
    return {"status": "ok", "setup": onboarding_tasks.system_status()}


@app.post("/api/env")
def configure_env(body: EnvRequest):
    if body.provider not in {"openai", "gemini"}:
        raise HTTPException(status_code=400, detail="Provider must be openai or gemini.")
    if not body.provider_key.strip():
        raise HTTPException(status_code=400, detail="Provider key is required.")
    return onboarding_tasks.configure_env(body.provider, body.provider_key.strip())


@app.post("/api/task")
def run_task(body: TaskRequest):
    result = onboarding_tasks.run_task(body.task)
    if not result.get("ok"):
        return {"status": "error", "result": result}
    return {"status": "ok", "result": result}


@app.post("/api/upload-source")
def upload_source(body: UploadRequest):
    result = onboarding_tasks.save_uploaded_source(body.filename, body.content_base64)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Upload failed."))
    return {"status": "ok", **result}


@app.post("/api/ingest-sources")
def ingest_sources(body: IngestRequest):
    result = onboarding_tasks.ingest_uploaded_sources(body.paths)
    if not result.get("ok"):
        return {"status": "error", "result": result}
    return {"status": "ok", "result": result}


@app.get("/api/export-status")
def export_status():
    return {"status": "ok", "json": json.dumps(onboarding_tasks.system_status(), indent=2)}


def bind_host() -> str:
    return "0.0.0.0" if CONTAINER_MODE else "127.0.0.1"


def main() -> int:
    import uvicorn

    uvicorn.run("scripts.onboarding_server:app", host=bind_host(), port=8765, reload=False, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
