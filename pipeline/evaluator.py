from backend.providers import get_provider
from backend.core.logger import get_logger
from pipeline.cache import get_conn
from backend.core.prompts import EVALUATOR_PROMPT_VERSION

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are a QA reviewer for AI-generated support ticket resolutions.

Score the resolution against the KB sources on three dimensions:

FAITHFULNESS — Are the root cause and resolution steps grounded in the KB chunks provided?
  HIGH   = every claim is directly supported by a KB chunk
  MEDIUM = mostly supported, minor unsupported assertions present
  LOW    = significant claims not in the KB, or KB was largely ignored

COMPLETENESS — Does the resolution address the main symptoms in the ticket?
  HIGH   = all symptoms addressed with specific steps
  MEDIUM = main symptom addressed, secondary symptoms missed
  LOW    = key symptoms not addressed

TONE — Does the draft email meet ALL of these?
  PASS = empathetic opening, no [brackets] or placeholders, closes with "Kind regards", plain language
  FAIL = one or more criteria not met

FLAGS — List specific issues found. Use NONE if there are no issues.

Return ONLY this exact format. No preamble, no commentary.

Faithfulness: HIGH|MEDIUM|LOW
Completeness: HIGH|MEDIUM|LOW
Tone: PASS|FAIL
Flags: <comma-separated issues or NONE>
Summary: <one sentence>"""
SYSTEM_PROMPT_VERSION = EVALUATOR_PROMPT_VERSION


def _build_user_message(ticket: str, top_chunks: list, resolution: dict) -> str:
    chunk_lines = []
    for i, c in enumerate(top_chunks):
        source  = c.get("source_file") or c.get("title", "unknown")
        product = c.get("product", "")
        content = c.get("content", "")[:400]
        chunk_lines.append(f"[KB-{i+1}] {source} (Product: {product or 'untagged'}): {content}")

    return (
        f"TICKET:\n{ticket}\n\n"
        f"KB CHUNKS USED:\n" + "\n\n".join(chunk_lines) + "\n\n"
        f"RESOLUTION OUTPUT:\n"
        f"Root Cause: {resolution.get('root_cause', '')}\n"
        f"Resolution Steps: {resolution.get('resolution_steps', '')}\n"
        f"Sources: {resolution.get('sources', '')}\n\n"
        f"DRAFT EMAIL:\n{resolution.get('draft_email', '')}"
    )


def _parse(text: str) -> dict:
    result = {"faithfulness": "", "completeness": "", "tone": "", "flags": [], "summary": ""}
    for line in text.splitlines():
        line = line.strip()
        if line.lower().startswith("faithfulness:"):
            result["faithfulness"] = line.split(":", 1)[1].strip().upper()
        elif line.lower().startswith("completeness:"):
            result["completeness"] = line.split(":", 1)[1].strip().upper()
        elif line.lower().startswith("tone:"):
            result["tone"] = line.split(":", 1)[1].strip().upper()
        elif line.lower().startswith("flags:"):
            raw = line.split(":", 1)[1].strip()
            result["flags"] = [] if raw.upper() == "NONE" else [f.strip() for f in raw.split(",")]
        elif line.lower().startswith("summary:"):
            result["summary"] = line.split(":", 1)[1].strip()
    return result


def _save(cache_key: str, eval_score: dict, usage: dict, extra: dict | None = None) -> None:
    extra = extra or {}
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO evaluation_results
                        (cache_key, faithfulness, completeness, tone, flags, summary,
                         eval_tokens_in, eval_tokens_out,
                         retry_triggered, product, access_channel)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (cache_key) DO NOTHING
                    """,
                    (
                        cache_key,
                        eval_score.get("faithfulness", ""),
                        eval_score.get("completeness", ""),
                        eval_score.get("tone", ""),
                        str(eval_score.get("flags", [])),
                        eval_score.get("summary", ""),
                        int(usage.get("tokens_in", 0) or 0),
                        int(usage.get("tokens_out", 0) or 0),
                        bool(extra.get("retry_triggered", False)),
                        extra.get("product") or "",
                        extra.get("access_channel") or "",
                    ),
                )
    except Exception as e:
        logger.error(f"Eval save failed: {e}")


def run(context: dict) -> dict:
    try:
        resolution = context.get("resolution", {})
        top_chunks = context.get("top_chunks", [])
        ticket = context.get("ticket", {}).get("cleaned", "")
        cache_key = resolution.get("cache_key", "")

        if not ticket or not top_chunks or not resolution.get("root_cause"):
            logger.info("Evaluator skipped — missing ticket, retrieved evidence, or root cause")
            context["eval_score"] = {
                "faithfulness": "SKIPPED",
                "completeness": "SKIPPED",
                "tone": "SKIPPED",
                "flags": [],
                "summary": "Evaluator skipped because ticket, retrieved evidence, or root cause was unavailable.",
                "evaluation_skipped": True,
                "usage": {"tokens_in": 0, "tokens_out": 0},
            }
            return context

        user_message = _build_user_message(ticket, top_chunks, resolution)
        provider = get_provider()
        setattr(provider, "current_step", "evaluator")
        logger.info("Evaluator calling provider")

        raw = provider.complete(system_prompt=SYSTEM_PROMPT, user_message=user_message)
        logger.debug(f"EVALUATOR FULL RESPONSE:\n{raw}")
        eval_score = _parse(raw) if raw else {}

        usage = getattr(provider, "last_usage", {"tokens_in": 0, "tokens_out": 0})
        eval_score["usage"] = {
            "tokens_in": int(usage.get("tokens_in", 0) or 0),
            "tokens_out": int(usage.get("tokens_out", 0) or 0),
            "cost_usd": float(usage.get("cost_usd", 0.0) or 0.0),
        }

        if cache_key:
            request_meta = context.get("request_meta", {})
            _save(cache_key, eval_score, usage, extra={
                "retry_triggered": bool(context.get("_retried", False)),
                "product":         request_meta.get("product", ""),
                "access_channel":  request_meta.get("access_channel", ""),
            })

        logger.info(
            f"Eval — faithfulness: {eval_score.get('faithfulness')} | "
            f"completeness: {eval_score.get('completeness')} | "
            f"tone: {eval_score.get('tone')}"
        )

        context["eval_score"] = eval_score
        return context

    except Exception as e:
        logger.error(f"Evaluator failed: {e}")
        context["eval_score"] = {}
        return context
