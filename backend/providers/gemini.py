import time
import psycopg2
from google import genai
from backend.providers.base import BaseProvider
from backend.core.logger import get_logger
from backend.core import config
from backend.db.schema import _safe_schema_name

logger = get_logger(__name__)

_PRICING = {
    "gemini-2.0-flash": {"input": 0.10 / 1_000_000, "output": 0.40 / 1_000_000},
    "gemini-1.5-flash": {"input": 0.075 / 1_000_000, "output": 0.30 / 1_000_000},
}


def _cost(model: str, tokens_in: int, tokens_out: int) -> float:
    p = _PRICING.get(model, _PRICING["gemini-2.0-flash"])
    return tokens_in * p["input"] + tokens_out * p["output"]


def _log_api_call(
    model: str,
    tokens_in: int,
    tokens_out: int,
    latency_ms: int,
    error: bool = False,
    error_message: str = "",
    step: str = "",
) -> None:
    try:
        cost = _cost(model, tokens_in, tokens_out)
        with psycopg2.connect(config.DATABASE_URL) as conn:
            with conn.cursor() as cur:
                schema = _safe_schema_name(config.OPS_SCHEMA)
                cur.execute(f'SET search_path TO "{schema}", public;')
                cur.execute(
                    """
                    INSERT INTO api_calls
                        (model, endpoint, provider, step, tokens_in, tokens_out,
                         latency_ms, cost_usd, error, error_message)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (model, "completion", "gemini", step, tokens_in, tokens_out,
                     latency_ms, cost, error, error_message),
                )
    except Exception as e:
        logger.warning(f"API call logging skipped: {e}")


class GeminiProvider(BaseProvider):
    """
    Google Gemini implementation using the google-genai SDK.
    """

    def __init__(self):
        try:
            self.model = "gemini-2.0-flash"
            self.client = genai.Client(api_key=config.GEMINI_API_KEY)
            self.last_usage = {
                "model": self.model,
                "endpoint": "completion",
                "step": "",
                "tokens_in": 0,
                "tokens_out": 0,
                "latency_ms": 0,
                "cost_usd": 0.0,
                "error": False,
            }
            logger.info(f"Gemini provider initialized — model: {self.model}")

        except Exception as e:
            logger.error(f"Gemini provider initialization failed: {e}")
            raise

    def complete(self, system_prompt: str, user_message: str) -> str:
        if not user_message or not user_message.strip():
            raise ValueError("User message is empty")

        t0 = time.perf_counter()
        error = False
        error_msg = ""
        tokens_in = tokens_out = 0
        step = getattr(self, "current_step", "responder")

        try:
            logger.debug(f"GEMINI REQUEST — model: {self.model}\n--- USER MESSAGE ---\n{user_message}\n--- END ---")

            response = self.client.models.generate_content(
                model=self.model,
                contents=f"{system_prompt}\n\n{user_message}",
            )

            usage = getattr(response, "usage_metadata", None)
            if usage:
                tokens_in  = getattr(usage, "prompt_token_count", 0) or 0
                tokens_out = getattr(usage, "candidates_token_count", 0) or 0

            if response.text:
                logger.debug(f"GEMINI RESPONSE — in:{tokens_in} out:{tokens_out}\n--- RESPONSE ---\n{response.text}\n--- END ---")
                return response.text
            else:
                raise ValueError("Empty response from Gemini")

        except Exception as e:
            error = True
            error_msg = str(e)
            logger.error(f"Gemini completion failed: {e}")
            raise ValueError(f"Gemini completion failed: {e}")

        finally:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            cost_usd = _cost(self.model, tokens_in, tokens_out)
            self.last_usage = {
                "model": self.model,
                "endpoint": "completion",
                "step": step,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "latency_ms": latency_ms,
                "cost_usd": cost_usd,
                "error": error,
            }
            _log_api_call(
                self.model, tokens_in, tokens_out, latency_ms,
                error=error, error_message=error_msg, step=step,
            )

    def get_embedding(self, text: str, is_query: bool = False) -> list[float]:
        try:
            if not text or not text.strip():
                raise ValueError("Embedding input text is empty")

            logger.debug("Using local embedding model")

            from backend.providers.embedding_model import get_embedding as local_embedding

            embedding = local_embedding(text)

            if not embedding:
                raise ValueError("Empty embedding returned")

            return embedding

        except Exception as e:
            logger.error(f"Local embedding failed: {e}")
            raise ValueError(f"Local embedding failed: {e}")

    def get_name(self) -> str:
        return "gemini"


# ── Test Block ───────────────────────────────────────────────

if __name__ == "__main__":
    provider = GeminiProvider()

    print(f"\nProvider: {provider.get_name()}")

    try:
        print("\n── Testing complete() ──")
        response = provider.complete(
            system_prompt="You are a helpful assistant.",
            user_message="Say hello in one sentence."
        )
        print(f"Response: {response}")

    except Exception as e:
        print(f"❌ complete() failed: {e}")

    try:
        print("\n── Testing get_embedding() ──")
        embedding = provider.get_embedding("test ticket about login issue")
        print(f"Embedding length: {len(embedding)}")
        print(f"First 5 values: {embedding[:5]}")

    except Exception as e:
        print(f"❌ get_embedding() failed: {e}")

    print("\n✅ Gemini provider test finished")
