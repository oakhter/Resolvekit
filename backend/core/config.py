import os
from pathlib import Path
from dotenv import load_dotenv
import psycopg2

# ── Load environment variables ───────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

# ── Active Provider ──────────────────────────────────────────
ACTIVE_PROVIDER = os.getenv("ACTIVE_PROVIDER", "gemini")
DEMO_MODE = os.getenv("DEMO_MODE", "true").strip().lower() in {"1", "true", "yes", "on"}
WARM_LOCAL_MODELS = os.getenv("WARM_LOCAL_MODELS", "true").strip().lower() not in {"0", "false", "no", "off"}
CONFIGURATOR_PREFILL_API_KEY = os.getenv("CONFIGURATOR_PREFILL_API_KEY", "false").strip().lower() in {"1", "true", "yes", "on"}
CONFIGURATOR_SOURCE_PREVIEW_MAX_BYTES = int(os.getenv("CONFIGURATOR_SOURCE_PREVIEW_MAX_BYTES", str(25 * 1024 * 1024)))
CORS_ALLOW_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ALLOW_ORIGINS",
        "http://127.0.0.1:8000,http://localhost:8000",
    ).split(",")
    if origin.strip()
]

# ── API Keys ─────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
API_KEY = os.getenv("API_KEY")
CONFIGURATOR_API_KEY = os.getenv("CONFIGURATOR_API_KEY") or API_KEY
VIEWER_TOKEN = os.getenv("VIEWER_TOKEN") or API_KEY
CONFIGURATOR_ADMIN_TOKEN = os.getenv("CONFIGURATOR_ADMIN_TOKEN") or CONFIGURATOR_API_KEY

_PLACEHOLDER_SECRETS = {
    "",
    "change-me",
    "change-me-configurator",
    "changeme",
    "test",
    "replace-with-random-viewer-token",
    "replace-with-random-admin-token",
}

# ── Databases ─────────────────────────────────────────────────
# v3.x layout uses one Postgres database with two schemas:
#   knowledge: retrieval/vector tables
#   ops: operational tables
DATABASE_URL = os.getenv("DATABASE_URL")
KNOWLEDGE_SCHEMA = os.getenv("KNOWLEDGE_SCHEMA", "knowledge")
OPS_SCHEMA = os.getenv("OPS_SCHEMA", "ops")

# ── Model Names ──────────────────────────────────────────────
MODELS = {
    "gemini": "gemini-2.0-flash",
    "openai": "gpt-4o-mini",
    "mock": "resolvekit-mock-preview",
}

# ── Retrieval Settings ───────────────────────────────────────
TOP_K_RETRIEVAL = 20
TOP_K_RERANK = 5
MAX_RERANK_INPUT = 100
MAX_CHUNK_LENGTH = 1000
MAX_CONTEXT_CHARS = 6000


# ── Validation (NO side effects) ─────────────────────────────
def _clean_secret(value: str | None) -> str:
    return str(value or "").strip()


def validate_operational_secrets(values: dict[str, str | None] | None = None) -> None:
    secrets = values or {
        "API_KEY": API_KEY,
        "CONFIGURATOR_API_KEY": CONFIGURATOR_API_KEY,
        "VIEWER_TOKEN": VIEWER_TOKEN,
        "CONFIGURATOR_ADMIN_TOKEN": CONFIGURATOR_ADMIN_TOKEN,
    }
    cleaned = {key: _clean_secret(value) for key, value in secrets.items()}
    for key, value in cleaned.items():
        if value.lower() in _PLACEHOLDER_SECRETS:
            raise ValueError(f"{key} is missing or uses a placeholder value")

    shared_pairs = (
        ("API_KEY", "CONFIGURATOR_API_KEY"),
        ("API_KEY", "CONFIGURATOR_ADMIN_TOKEN"),
        ("CONFIGURATOR_API_KEY", "CONFIGURATOR_ADMIN_TOKEN"),
    )
    for left, right in shared_pairs:
        if cleaned.get(left) and cleaned.get(left) == cleaned.get(right):
            raise ValueError(f"{left} and {right} must not share the same secret")


def validate():
    if ACTIVE_PROVIDER not in MODELS:
        raise ValueError(f"Unsupported ACTIVE_PROVIDER: {ACTIVE_PROVIDER}")
    if ACTIVE_PROVIDER == "gemini" and not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is missing")
    if ACTIVE_PROVIDER == "openai" and not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is missing")
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL is missing")
    validate_operational_secrets()


def validate_db():
    targets = [
        (DATABASE_URL, f"retrieval schema '{KNOWLEDGE_SCHEMA}'"),
        (DATABASE_URL, f"operational schema '{OPS_SCHEMA}'"),
    ]
    for url, label in targets:
        try:
            conn = psycopg2.connect(url, connect_timeout=5)
            conn.close()
        except Exception as e:
            raise RuntimeError(f"Database connection failed ({label}): {e}")
