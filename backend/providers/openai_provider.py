import time
from openai import OpenAI
from backend.providers.base import BaseProvider
from backend.core.logger import get_logger
from backend.core import config
from backend.db.schema import _safe_schema_name

logger = get_logger(__name__)

# ── Per-token pricing (USD). Update if model/pricing changes. ──
_PRICING: dict[str, dict] = {
    "gpt-4.1-mini":  {"input": 0.40  / 1_000_000, "output": 1.60  / 1_000_000},
    "gpt-4o-mini":   {"input": 0.15  / 1_000_000, "output": 0.60  / 1_000_000},
    "gpt-4o":        {"input": 2.50  / 1_000_000, "output": 10.00 / 1_000_000},
    "gpt-4.1":       {"input": 2.00  / 1_000_000, "output": 8.00  / 1_000_000},
}


def _cost(model: str, tokens_in: int, tokens_out: int) -> float:
    p = _PRICING.get(model, _PRICING["gpt-4.1-mini"])
    return tokens_in * p["input"] + tokens_out * p["output"]


def _log_api_call(
    model: str,
    endpoint: str,
    tokens_in: int,
    tokens_out: int,
    latency_ms: int,
    error: bool = False,
    error_message: str = "",
    provider: str = "openai",
    step: str = "",
) -> None:
    """Write one row to api_calls. Never raises — cost logging is non-critical."""
    try:
        import psycopg2
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
                    (model, endpoint, provider, step, tokens_in, tokens_out,
                     latency_ms, cost, error, error_message),
                )
    except Exception as e:
        logger.warning(f"API call logging skipped: {e}")


class OpenAIProvider(BaseProvider):

    def __init__(self):
        try:
            self.client = OpenAI(api_key=config.OPENAI_API_KEY)
            self.model  = config.MODELS.get("openai", "gpt-4o-mini")
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
            logger.info(f"OpenAI provider initialized — model: {self.model}")
        except Exception as e:
            logger.error(f"OpenAI provider init failed: {e}")
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
            logger.debug(f"OPENAI REQUEST — model: {self.model}\n--- USER MESSAGE ---\n{user_message}\n--- END ---")
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_message},
                ],
                temperature=0.2,
                max_tokens=1000,
            )

            usage      = response.usage
            tokens_in  = usage.prompt_tokens     if usage else 0
            tokens_out = usage.completion_tokens if usage else 0

            content = response.choices[0].message.content
            if not content:
                raise ValueError("Empty response from OpenAI")

            logger.debug(f"OPENAI RESPONSE — in:{tokens_in} out:{tokens_out}\n--- RESPONSE ---\n{content}\n--- END ---")
            return content

        except Exception as e:
            error = True
            error_msg = str(e)
            logger.error(f"OpenAI completion failed: {e}")
            raise ValueError(f"OpenAI completion failed: {e}")

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
                self.model, "completion", tokens_in, tokens_out, latency_ms,
                error=error, error_message=error_msg, provider="openai", step=step,
            )

    def get_embedding(self, text: str, is_query: bool = False) -> list[float]:
        if not text or not text.strip():
            raise ValueError("Embedding input text is empty")
        try:
            logger.debug("Using local embedding model (no API cost)")
            from backend.providers.embedding_model import get_embedding as local_embedding

            embedding = local_embedding(text)
            if not embedding:
                raise ValueError("Empty embedding returned")
            return embedding
        except Exception as e:
            logger.error(f"Local embedding failed: {e}")
            raise ValueError(f"Local embedding failed: {e}")

    def get_name(self) -> str:
        return "openai"
