from sentence_transformers import SentenceTransformer

_model = None


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

    embedding = get_model().encode(text)

    return embedding.tolist()
