import re
import json
from copy import deepcopy
from backend.providers import get_provider
from backend.core.logger import get_logger
from pipeline.cache import get_cached_response, save_cached_response, hash_key
from backend.core import config, project_config
from backend.core.prompts import RESPONDER_PROMPT_VERSION

logger = get_logger(__name__)


SYSTEM_PROMPT = """
You are a senior technical support engineer handling software, authentication, integrations, account management, and platform issues.

You will receive a support ticket and structured KB context. Work through diagnosis before writing the response.

EVIDENCE RULES — you MUST follow these:
- Base ALL reasoning ONLY on the provided KB chunks
- Every cause you state must cite which [KB-N] chunk supports it
- Every factual or actionable resolution step must cite the supporting [KB-N] chunk
- If the KB does not cover the issue, say so explicitly and set Confidence to LOW
- Do NOT invent steps not implied by the KB

TICKET ANALYSIS:
1. What are the specific symptoms? (error codes, platforms, timing, scope)
2. Which KB chunks are directly relevant? Cite them.
3. What is the most likely root cause? What is an alternative?
4. Are there red flags needing escalation? (data loss, security, system-wide outage, billing)
5. Is urgency language present? Only acknowledge urgency in the email when the ticket explicitly uses urgency language.
6. Is key info missing for diagnosis? (OS, error text, account ID) — add a step to gather it.

OUTPUT RULES:
- Root cause explains the mechanism, not the symptom
- Resolution steps are specific: exact settings, menu paths, commands, versions
- Role already selected in request context → write guidance for that role; do not redirect to admin unless KB explicitly says that role lacks access
- If retrieved sources indicate that resolution depends on a role, permission, setting, platform, plan, policy, feature flag, version, or account configuration, do not state the resolution as guaranteed. Present it as conditional and list what must be checked.
- If multiple hypotheses are plausible from the sources, list the top 2-3 possibilities and say what information would distinguish them.
- If the ticket is short or missing operational details, ask for or recommend checking the missing details instead of asserting a single root cause.
- Confidence must not be HIGH when required facts are missing for a conditional source.
- PRODUCT RULE: Each KB chunk is tagged with a Product field. If REQUEST CONTEXT specifies one product but all retrieved KB chunks are tagged with a different product, you MUST state: "No relevant knowledge base content was found for [product]." Set Confidence to LOW and do NOT use the wrong product's steps. Do not guess or adapt content from a different product.
- ACCESS CHANNEL RULE: If access_channel is "mobile app", all resolution steps must be mobile-specific (app navigation, not browser). If access_channel is "website", all steps must be browser/web-interface specific. Do not mix mobile and web steps.

DRAFT EMAIL RULES:
- Tone: soft, professional, and concise — helpful without sounding scripted, dramatic, or overly apologetic
- Open with a brief, natural line tied to the customer's actual request. Do not use generic empathy formulas such as "I understand how important", "I understand the urgency", or "I know this is frustrating" unless the ticket explicitly says the issue is urgent, frustrating, blocking, or high impact.
- Explain the likely cause in plain language (no jargon, no acronyms without explanation)
- If a resolution step requires information we don't yet have, include ONE specific, polite clarifying question:
  "To help us resolve this as quickly as possible, could you let us know [X]?"
- If the diagnosis has two plausible causes, acknowledge the uncertainty honestly:
  "We have a couple of theories about what may be causing this — it could be X or Y."
  Then explain what the user can try first.
- If urgency language was in the ticket, acknowledge it briefly and specifically in the opening line. If urgency language was not present, do not mention urgency, impact, or frustration.
- Close with "Kind regards" — warmer than "Best"
- Fully written: no [brackets], no placeholders, no "fill in here"
- Must include Subject line, greeting starting with "Hi", full body, sign-off from "Support Team"
- Email is the LAST section

STRICTLY follow this exact format. No preamble, no commentary outside sections.

Issue Classification:
<One specific line — e.g. "Mobile OAuth token expiry — post-update permission reset">

Diagnosis:
Hypothesis 1: <cause> — Likelihood: HIGH/MEDIUM/LOW — Evidence: [KB-1]
Hypothesis 2: <alternative cause> — Likelihood: MEDIUM/LOW — Evidence: [KB-2]
(Include only hypotheses supported by KB evidence. Omit if only one.)

Root Cause:
<2–3 sentences. State the most likely cause from Diagnosis. Cite the supporting chunk(s). If KB coverage is partial, say so.>

Resolution Steps:
<Numbered list. Specific and actionable. Each factual or actionable step must cite [KB-N]. Address top hypothesis first. Add info-gathering steps if key data is missing. Flag escalation explicitly if needed.>

Sources:
<Comma-separated KB source filenames cited, or "General technical knowledge" if KB was insufficient>

Confidence:
HIGH / MEDIUM / LOW

Draft Email:
Subject: <one-line subject>

Hi,

<Body: concise professional opening tied to the request → cause explained plainly → steps or clarifying question → realistic timeframe only if supported by KB>

Kind regards,
Support Team
"""
SYSTEM_PROMPT_VERSION = RESPONDER_PROMPT_VERSION


# ── FORMAT CHUNKS ───────────────────────────────────────────

def _extract_key_points(content: str, max_points: int = 4) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", content.strip())
    points = []
    for s in sentences:
        s = s.strip()
        if 20 <= len(s) <= 300:
            points.append(s)
        if len(points) >= max_points:
            break
    return points or [content[:200].strip()]


def format_chunks(chunks: list) -> str:
    if not chunks:
        return "No relevant knowledge base content found."

    parts = []
    for i, chunk in enumerate(chunks):
        source  = chunk.get("source_file") or chunk.get("title", "unknown")
        product = chunk.get("product", "")
        heading = chunk.get("heading_path", "")
        chunk_type = chunk.get("chunk_type", "concept")
        source_type = chunk.get("source_type", chunk.get("doc_type", "knowledge_base"))
        authority = float(chunk.get("source_authority") or 1.0)
        condition_flags = chunk.get("condition_flags") or []
        if isinstance(condition_flags, str):
            condition_flags = [condition_flags] if condition_flags else []
        retrieval_reason = chunk.get("retrieval_reason", "initial_match")
        content = chunk.get("content", "")[:config.MAX_CHUNK_LENGTH]
        score   = chunk.get("rerank_score", 0.0)

        key_points = _extract_key_points(content)
        points_str = "\n".join(f"  • {p}" for p in key_points)

        header = (
            f"[KB-{i+1}] Source: {source} | Product: {product or 'untagged'} | "
            f"Type: {source_type}/{chunk_type} | Heading: {heading or 'n/a'} | "
            f"Authority: {authority:.2f} | Relevance: {score:.2f} | "
            f"Condition Flags: {', '.join(condition_flags) if condition_flags else 'none'} | "
            f"Selected because: {retrieval_reason}"
        )
        parts.append(f"{header}\nKey Points:\n{points_str}")

    return "\n\n".join(parts)[:config.MAX_CONTEXT_CHARS]


def format_evidence_table(table: dict) -> str:
    if not table:
        return "No structured evidence table available."
    lines = ["SUPPORTED FACTS:"]
    for fact in table.get("supported_facts", [])[:8]:
        citations = ", ".join(fact.get("citations") or [])
        lines.append(f"- {fact.get('claim', '')} ({citations}; {fact.get('confidence', 'medium')})")
    missing = table.get("missing_context") or []
    lines.append("MISSING CONTEXT:")
    lines.extend(f"- {item}" for item in missing[:8]) if missing else lines.append("- none")
    conflicts = table.get("conflicts") or []
    lines.append("CONFLICTS:")
    lines.extend(
        f"- {item.get('topic', 'source conflict')}: {item.get('source_a', '')} vs {item.get('source_b', '')}"
        for item in conflicts[:6]
    ) if conflicts else lines.append("- none")
    return "\n".join(lines)[:config.MAX_CONTEXT_CHARS]


def build_structured_reply(resolution: dict, evidence_table: dict) -> dict:
    missing_context = evidence_table.get("missing_context", []) if evidence_table else []
    conflicts = evidence_table.get("conflicts", []) if evidence_table else []
    caveats = []
    if missing_context:
        caveats.append("Missing context: " + ", ".join(missing_context[:5]))
    if conflicts:
        caveats.append("Source conflicts require review before relying on one answer.")
    if resolution.get("confidence") == "LOW":
        caveats.append("Low confidence; human review recommended.")
    return {
        "acknowledgment": resolution.get("issue_classification", ""),
        "core_answer": resolution.get("resolution_steps") or resolution.get("root_cause") or "",
        "caveats": caveats,
        "next_steps": resolution.get("resolution_steps", ""),
        "citations": sorted(set(re.findall(r"\[KB-\d+\]", "\n".join([
            resolution.get("root_cause", ""),
            resolution.get("resolution_steps", ""),
            resolution.get("draft_email", ""),
        ])))),
    }


def render_structured_reply(reply: dict) -> str:
    parts = []
    if reply.get("acknowledgment"):
        parts.append(str(reply["acknowledgment"]).strip())
    if reply.get("core_answer"):
        parts.append(str(reply["core_answer"]).strip())
    if reply.get("caveats"):
        parts.append("Caveats:\n" + "\n".join(f"- {item}" for item in reply["caveats"]))
    if reply.get("citations"):
        parts.append("Citations: " + ", ".join(reply["citations"]))
    return "\n\n".join(part for part in parts if part)


# ── PARSING ─────────────────────────────────────────────────

_SECTIONS = [
    ("issue_classification", "Issue Classification:"),
    ("diagnosis",            "Diagnosis:"),
    ("root_cause",           "Root Cause:"),
    ("resolution_steps",     "Resolution Steps:"),
    ("sources",              "Sources:"),
    ("confidence",           "Confidence:"),
    ("draft_email",          "Draft Email:"),
]


def parse_response(text: str) -> dict:
    result = {key: "" for key, _ in _SECTIONS}
    result["raw"] = text

    lower = text.lower()
    positions = []

    for key, label in _SECTIONS:
        idx = lower.find(label.lower())
        if idx != -1:
            positions.append((idx, key, label))

    positions.sort(key=lambda x: x[0])

    for i, (pos, key, label) in enumerate(positions):
        content_start = pos + len(label)
        content_end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        result[key] = text[content_start:content_end].strip()

    return result


_PLACEHOLDER_PATTERNS = [
    r"\[customer name\]",
    r"\[your name\]",
    r"\[company name\]",
    r"\[.*?\]",
]


def clean_draft_email(email: str) -> str:
    cleaned = email or ""
    for pattern in _PLACEHOLDER_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.I)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def apply_output_preferences(resolution: dict) -> dict:
    prefs = project_config.output_preferences()
    include = prefs.get("include", {})
    mode = prefs.get("mode", "resolution_full")

    mode_defaults = {
        "resolution_full": {
            "issue_classification": True,
            "diagnosis": True,
            "root_cause": True,
            "resolution_steps": True,
            "sources": True,
            "confidence": True,
            "draft_email": True,
        },
        "email_draft_only": {
            "issue_classification": False,
            "diagnosis": False,
            "root_cause": False,
            "resolution_steps": False,
            "sources": False,
            "confidence": False,
            "draft_email": True,
        },
        "internal_agent_assist": {
            "issue_classification": True,
            "diagnosis": True,
            "root_cause": True,
            "resolution_steps": True,
            "sources": True,
            "confidence": True,
            "draft_email": False,
        },
        "diagnosis_only": {
            "issue_classification": True,
            "diagnosis": True,
            "root_cause": True,
            "resolution_steps": False,
            "sources": True,
            "confidence": True,
            "draft_email": False,
        },
    }
    effective = dict(mode_defaults.get(mode, {}))
    if mode == "custom":
        effective = {}
        for key, enabled in include.items():
            mapped = "resolution_steps" if key == "resolution_steps" else key
            effective[mapped] = bool(enabled)
    else:
        for key, enabled in include.items():
            mapped = "resolution_steps" if key == "resolution_steps" else key
            effective[mapped] = bool(enabled)

    for key in ["issue_classification", "diagnosis", "root_cause", "resolution_steps", "sources", "confidence", "draft_email"]:
        if key in effective and not effective[key]:
            resolution[key] = ""

    max_lines = int(prefs.get("diagnosis", {}).get("max_lines", 3) or 3)
    if resolution.get("diagnosis") and max_lines > 0:
        lines = [line for line in resolution["diagnosis"].splitlines() if line.strip()]
        resolution["diagnosis"] = "\n".join(lines[:max_lines])

    if resolution.get("draft_email"):
        resolution["draft_email"] = clean_draft_email(resolution["draft_email"])

    resolution["output_preferences"] = {
        "mode": mode,
        "audience": prefs.get("audience", "internal_assist"),
        "include": include,
    }
    return resolution


def attach_canonical_resolution(resolution: dict) -> dict:
    canonical = deepcopy({
        key: value
        for key, value in resolution.items()
        if key not in {"canonical_resolution", "output_preferences"}
    })
    resolution["canonical_resolution"] = canonical
    return resolution


# ── MAIN ────────────────────────────────────────────────────

def run(context: dict) -> dict:
    cache_key = ""
    try:
        if not context:
            raise ValueError("Context is missing")

        ticket = context.get("ticket")
        top_chunks = context.get("top_chunks")

        if not ticket or not top_chunks:
            raise ValueError("Missing ticket or chunks")

        cleaned_ticket = ticket.get("cleaned", "")
        if not cleaned_ticket:
            raise ValueError("Cleaned ticket missing")

        request_meta = context.get("request_meta", {})

        # Build support ticket text with request context
        meta_lines = []
        if request_meta.get("product"):
            meta_lines.append(f"Selected Product: {request_meta['product']}")
        if request_meta.get("permission_level"):
            meta_lines.append(f"Selected Permission Level: {request_meta['permission_level']}")
        if request_meta.get("access_channel"):
            channel = project_config.platform_label(
                request_meta["access_channel"],
                request_meta.get("product", ""),
            )
            meta_lines.append(f"Selected Access Channel: {channel}")

        support_ticket = cleaned_ticket
        if meta_lines:
            support_ticket += "\n\nREQUEST CONTEXT:\n" + "\n".join(meta_lines)

        # ── STABLE CACHE KEY ────────────────────────────────
        chunk_ids = sorted([str(c.get("id", "")) for c in top_chunks if c.get("id")])
        normalized_ticket = cleaned_ticket.lower().strip()
        cache_key = hash_key(json.dumps({
            "ticket": normalized_ticket,
            "chunks": chunk_ids,
            "request_meta": {
                "product": request_meta.get("product", ""),
                "permission_level": request_meta.get("permission_level", ""),
                "access_channel": request_meta.get("access_channel", ""),
                "experiment_arm": request_meta.get("experiment_arm", ""),
            },
            "response_config": project_config.response_fingerprint(),
        }, sort_keys=True))

        logger.info(f"RESPONSE CACHE KEY: {cache_key}")

        cached = get_cached_response(cache_key)
        if cached:
            logger.info("⚡ RESPONSE CACHE HIT")
            cached = apply_output_preferences(cached)
            cached["from_cache"] = True
            cached["cache_key"]  = cache_key
            context["resolution"] = cached
            context["response_cache_hit"] = True
            return context

        context["response_cache_hit"] = False

        # ── STRUCTURED KB CONTEXT ───────────────────────────
        kb_context = format_chunks(top_chunks)
        evidence_context = format_evidence_table(context.get("evidence_table", {}))

        user_message = (
            f"SUPPORT TICKET:\n{support_ticket}\n\n"
            f"STRUCTURED EVIDENCE TABLE:\n{evidence_context}\n\n"
            f"KNOWLEDGE BASE CONTEXT:\n{kb_context}"
        )

        provider = get_provider()
        setattr(provider, "current_step", "responder")
        logger.info("Responder calling provider")

        raw = provider.complete(system_prompt=SYSTEM_PROMPT, user_message=user_message)

        if not raw:
            raise ValueError("Empty LLM response")

        logger.info(f"LLM response preview: {raw[:120]}")
        logger.debug(f"RESPONDER FULL RESPONSE:\n{raw}")

        resolution = parse_response(raw)
        resolution["from_cache"] = False
        resolution["cache_key"]  = cache_key
        resolution["usage"] = context.get("usage", {})
        resolution["usage"]["responder"] = getattr(provider, "last_usage", {
            "model": "", "endpoint": "completion",
            "tokens_in": 0, "tokens_out": 0, "latency_ms": 0, "error": False,
        })

        if not resolution["issue_classification"]:
            logger.warning("Parsing failed — using fallback")
            resolution = {
                "issue_classification": "Unstructured Response",
                "diagnosis": "",
                "root_cause": "",
                "resolution_steps": raw,
                "sources": "",
                "confidence": "LOW",
                "draft_email": raw,
                "raw": raw,
                "from_cache": False,
                "cache_key": cache_key,
            }
            resolution["usage"] = context.get("usage", {})

        if project_config.experiment_settings("advanced_reasoning").get("structured_reply", False):
            structured_reply = build_structured_reply(resolution, context.get("evidence_table", {}))
            resolution["structured_reply"] = structured_reply
            resolution["rendered_reply"] = render_structured_reply(structured_reply)

        resolution = attach_canonical_resolution(resolution)
        resolution = apply_output_preferences(resolution)
        save_cached_response(cache_key, resolution, provider=provider.get_name())

        context["resolution"] = resolution
        return context

    except Exception as e:
        logger.error(f"Responder failed: {e}")
        context["resolution"] = {
            "issue_classification": "System Error",
            "diagnosis": "",
            "root_cause": str(e),
            "resolution_steps": "",
            "sources": "",
            "confidence": "LOW",
            "draft_email": "System encountered an error.",
            "raw": "",
            "from_cache": False,
            "cache_key": cache_key,
        }
        context["response_cache_hit"] = False
        return context


# ── TEST ────────────────────────────────────────────────────

if __name__ == "__main__":
    test_context = {
        "ticket": {
            "cleaned": "User cannot log in to the demo mobile app. Getting error code 403 on mobile only."
        },
        "top_chunks": [
            {
                "id": "chunk_001",
                "content": "Error 403 on mobile login usually occurs after a permission change. Navigate to Settings > Authentication and revoke existing OAuth tokens. Users must re-authenticate after tokens are revoked.",
                "metadata": {"source": "troubleshooting.txt"},
                "rerank_score": 8.2,
            }
        ],
    }

    result = run(test_context)
    res = result["resolution"]
    print("\n── Resolution Output ─────────────────────")
    print(f"Classification: {res['issue_classification']}")
    print(f"Diagnosis:      {res['diagnosis'][:80]}...")
    print(f"Root Cause:     {res['root_cause'][:80]}...")
    print(f"Confidence:     {res['confidence']}")
