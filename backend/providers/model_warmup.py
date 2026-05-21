import time

from backend.core import config
from backend.core.logger import get_logger

logger = get_logger(__name__)


def warm_local_models(enabled: bool | None = None) -> bool:
    if enabled is None:
        enabled = config.WARM_LOCAL_MODELS
    if not enabled:
        logger.info("Local model warmup disabled")
        return False

    started = time.perf_counter()
    try:
        from backend.providers.embedding_model import warmup as warm_embedding
        from pipeline.reranker import warmup as warm_reranker

        warm_embedding()
        warm_reranker()
        elapsed = time.perf_counter() - started
        logger.info(f"Local model warmup complete — {elapsed:.2f}s")
        return True
    except Exception as e:
        logger.warning(f"Local model warmup skipped; lazy loading remains available: {e}")
        return False
