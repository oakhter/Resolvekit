from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.providers import get_provider


def test_embedding():
    try:
        provider = get_provider()

        print("Running embedding test...\n")

        embedding = provider.get_embedding("login error 403 mobile")

        # ── Basic Validation ────────────────────────────────
        if not embedding:
            raise ValueError("Embedding is empty")

        if not isinstance(embedding, list):
            raise ValueError("Embedding is not a list")

        if not all(isinstance(x, float) for x in embedding[:10]):
            raise ValueError("Embedding values are not floats")

        # ── Output ──────────────────────────────────────────
        print(f"Dimensions: {len(embedding)}")
        print(f"First 5 values: {embedding[:5]}")

        # ── Dimension Check ─────────────────────────────────
        if len(embedding) == 384:
            print("\n✅ Embeddings working — 384 dimensions confirmed")
        else:
            print(f"\n❌ Wrong dimensions — expected 384 got {len(embedding)}")

    except Exception as e:
        print(f"\n❌ Test failed: {e}")


if __name__ == "__main__":
    test_embedding()
