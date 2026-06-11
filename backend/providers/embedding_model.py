import hashlib
import os
import random

from sentence_transformers import SentenceTransformer

_model = None
EMBEDDING_DIMENSIONS = 384


def smoke_test_embedding(text: str) -> list[float]:
    seed = int.from_bytes(hashlib.sha256(text.strip().lower().encode("utf-8")).digest()[:8], "big")
    rng = random.Random(seed)
    return [rng.uniform(-1.0, 1.0) for _ in range(EMBEDDING_DIMENSIONS)]


def smoke_test_mode_enabled() -> bool:
    return os.getenv("SMOKE_TEST_MODE", "").strip().lower() in {"1", "true", "yes", "on"}


def get_model():
    """Load the embedding model once per process."""
    global _model
    if _model is None:
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def warmup() -> None:
    get_model().encode("warmup")


def get_embedding(text: str) -> list[float]:
    """
    Generate embedding using local SentenceTransformer model.

    Args:
        text (str): Input text

    Returns:
        list[float]: Embedding vector (384 dimensions)
    """
    if not text or not text.strip():
        raise ValueError("Embedding input text is empty")

    if smoke_test_mode_enabled():
        return smoke_test_embedding(text)

    embedding = get_model().encode(text)

    return embedding.tolist()
