from backend.providers.gemini import GeminiProvider
from backend.providers.openai_provider import OpenAIProvider

from backend.core.logger import get_logger
from backend.core import config

logger = get_logger(__name__)


PROVIDERS = {
    "gemini": GeminiProvider,
    "openai": OpenAIProvider,
}

_provider_cache = {}


def get_provider():
    """
    Returns the active provider based on config.
    """

    provider_name = config.ACTIVE_PROVIDER.strip().lower()
    model_name = config.MODELS.get(provider_name, "")
    api_key = {
        "gemini": config.GEMINI_API_KEY,
        "openai": config.OPENAI_API_KEY,
    }.get(provider_name, "")

    if provider_name not in PROVIDERS:
        logger.error(f"Unknown provider requested: {provider_name}")
        raise ValueError(f"Unknown provider: {provider_name}")

    cache_key = (provider_name, model_name, api_key)
    if cache_key not in _provider_cache:
        logger.info(f"Using provider: {provider_name}")
        _provider_cache[cache_key] = PROVIDERS[provider_name]()

    return _provider_cache[cache_key]


def reset_provider_cache():
    _provider_cache.clear()
