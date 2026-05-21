"""
QA fixtures — base URL defaults to localhost:8000.
Override with QA_BASE_URL env var if running against a different host.
"""
import os
import time
import pytest
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")


@pytest.fixture(autouse=True)
def _between_tests():
    yield
    time.sleep(2.5)  # keep requests outside the 2s global rate-limit window


@pytest.fixture(scope="session")
def base_url() -> str:
    url = os.getenv("QA_BASE_URL", "http://127.0.0.1:8000")
    print(f"\n  Target: {url}")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def api_key() -> str:
    key = os.getenv("API_KEY", "")
    if not key:
        pytest.skip("API_KEY not set in .env")
    return key


@pytest.fixture(scope="session")
def auth(api_key) -> dict:
    return {"x-api-key": api_key}
