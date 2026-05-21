import logging
import time
from contextlib import contextmanager
from pathlib import Path
from logging.handlers import RotatingFileHandler

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR      = PROJECT_ROOT / "diagnostics" / "logs"
DEBUG_LOG    = PROJECT_ROOT / "DEBUG.log"
LOGS_DIR     = PROJECT_ROOT / "logs"
APP_LOG      = LOGS_DIR / "logs.txt"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

_configured        = False
_debug_log_enabled = False
_app_log_enabled   = False


def _configure_root() -> None:
    global _configured
    if _configured:
        return

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")

    # Console: INFO only — keeps terminal readable in production
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    root.addHandler(console)

    # File: DEBUG — full detail for post-hoc diagnosis
    file_handler = RotatingFileHandler(
        LOG_DIR / "app.txt",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    for noisy in ("httpcore", "httpx", "openai", "sentence_transformers", "torch"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True


def enable_debug_file_log() -> None:
    """
    Adds a DEBUG.log file handler at the project root.
    Called by start.py when DEBUG=True.
    Idempotent — safe to call multiple times.
    """
    global _debug_log_enabled
    if _debug_log_enabled:
        return
    _configure_root()
    root = logging.getLogger()
    fmt  = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    fh   = RotatingFileHandler(DEBUG_LOG, maxBytes=10 * 1024 * 1024, backupCount=2, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    _debug_log_enabled = True


def enable_app_log() -> None:
    """
    Enables logs/logs.txt at the project root — full DEBUG detail for pipeline
    actions and API calls. Called by FastAPI startup so every run is captured.
    Idempotent.
    """
    global _app_log_enabled
    if _app_log_enabled:
        return
    _configure_root()
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    fmt  = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    fh   = RotatingFileHandler(APP_LOG, maxBytes=20 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    _app_log_enabled = True


def get_logger(name: str) -> logging.Logger:
    _configure_root()
    return logging.getLogger(name)


@contextmanager
def log_timing(name: str, logger: logging.Logger):
    """Context manager that logs elapsed ms for a named block."""
    start = time.perf_counter()
    try:
        yield
    finally:
        ms = (time.perf_counter() - start) * 1000
        logger.info(f"{name} — {ms:.0f}ms")
