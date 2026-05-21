import re
from backend.core.logger import get_logger

logger = get_logger(__name__)

_ROUTES = [
    ("billing", re.compile(
        r"\b(billing|invoice|invoiced|charge|charged|payment|refund|subscription|cost|pricing|overcharged|billing contact|account owner)\b", re.I
    )),
    ("access", re.compile(
        r"\b(login|log in|log-in|sign in|sign-in|signed out|password|403|401|unauthorized|permission|access denied|locked out|can't access|cannot access|cannot see|credentials|role change|role changed|same browser session|failed a website action)\b", re.I
    )),
    ("bug", re.compile(
        r"\b(error|crash|broken|not working|fails|failed|exception|500|timeout|freezing|unresponsive|stopped working|stopped|not getting|badge|stale|pending badge|queued reply|not receive push|notifications?)\b", re.I
    )),
    ("how_to", re.compile(
        r"\b(how do|how can|how to|where is|where do|what is|what are|is it possible|steps to|guide|walkthrough|routing rule|routing rules|default routing|route new conversations|preview whether|before saving|routed to two teams)\b", re.I
    )),
    ("integration", re.compile(
        r"\b(api|webhook|integration|oauth|token|endpoint|sync|sso)\b", re.I
    )),
    ("policy", re.compile(
        r"\b(policy|terms|eligibility|allowed|not allowed|compliance|export|exports|private notes|trial workspace|trial expired|restore deleted data|delete an account|historical tickets?|customer-facing evidence|source preview|source quality|source freshness|validation blocked|citation|cite|internal-only|pii|redaction|ship with source-safety|hard failures|upload preserves attribution|load anyway|preview confirmation|pdf extraction|html nav|footer content|become evidence)\b", re.I
    )),
    ("release_change", re.compile(
        r"\b(release|changelog|update|version|new feature|recent change)\b", re.I
    )),
]

# Per-route reranker top_k overrides — None keys omitted (use config default)
_ROUTE_CONFIG = {
    "bug":     {"top_k_rerank": 7},  # wider window — release notes are high signal for bugs
    "how_to":  {"top_k_rerank": 4},  # tighter — KB precision over recall for how-to
    "access":  {"top_k_rerank": 5},
    "billing": {"top_k_rerank": 5},
    "integration": {"top_k_rerank": 5},
    "policy": {"top_k_rerank": 5},
    "release_change": {"top_k_rerank": 7},
    "general": {},
}

_QUESTION_RE = re.compile(r"([^.!?\n]*\?)")
_VERSION_RE = re.compile(r"\b(?:v(?:ersion)?\s*)?(\d+\.\d+(?:\.\d+)?)\b", re.I)
_PRIORITY_RE = re.compile(r"\b(urgent|critical|high priority|low priority|p[0-3])\b", re.I)
_PLAN_RE = re.compile(r"\b(free|trial|starter|pro|business|enterprise)\s+(?:plan|tier)?\b", re.I)
_CHANNEL_RE = re.compile(r"\b(email|chat|phone|mobile app|website|web|api)\b", re.I)
_RISK_RE = re.compile(
    r"\b(urgent|critical|security|breach|legal|compliance|refund|payment|data loss|outage|all users|everyone)\b",
    re.I,
)


def _answer_type(route: str, ticket: str) -> str:
    if route in {"billing", "policy"}:
        return "policy_guidance"
    if route == "release_change":
        return "change_summary"
    if route in {"bug", "access", "integration"}:
        return "troubleshooting"
    if re.search(r"\bhow (?:do|can|to)\b|\bsteps?\b", ticket or "", re.I):
        return "how_to"
    return "support_resolution"


def _retrieval_plan(ticket: str, explicit_questions: list[str], route: str) -> list[dict]:
    questions = explicit_questions or []
    if not questions:
        questions = [ticket]
    plan = []
    for index, question in enumerate(questions[:3], 1):
        plan.append({
            "question_id": f"q{index}",
            "query": " ".join(str(question).split())[:300],
            "route": route,
            "required_evidence": "approved_customer_facing",
        })
    return plan


def extract_plan(ticket: str, request_meta: dict | None = None) -> dict:
    request_meta = request_meta or {}
    explicit_questions = [match.strip() for match in _QUESTION_RE.findall(ticket or "") if match.strip()]
    missing_context = []
    if not request_meta.get("product"):
        missing_context.append("product")
    if not request_meta.get("access_channel"):
        missing_context.append("platform")
    if re.search(r"\b(permission|role|admin|manager|owner|compliance)\b", ticket or "", re.I) and not request_meta.get("permission_level"):
        missing_context.append("role")

    entities = sorted(set(re.findall(r"\b[A-Z][A-Za-z0-9_-]{2,}\b", ticket or "")))
    version = next(iter(_VERSION_RE.findall(ticket or "")), "")
    priority = next(iter(_PRIORITY_RE.findall(ticket or "")), "")
    plan_tier = next(iter(_PLAN_RE.findall(ticket or "")), "")
    channel = next(iter(_CHANNEL_RE.findall(ticket or "")), "")

    required_context = sorted(set(missing_context))
    risk_flags = sorted({match.group(0).lower() for match in _RISK_RE.finditer(ticket or "")})

    return {
        "intent": "",
        "entities": entities[:12],
        "explicit_questions": explicit_questions[:5],
        "required_context": required_context,
        "missing_context": sorted(set(missing_context)),
        "risk_flags": risk_flags,
        "retrieval_plan": [],
        "answer_type": "",
        "product": request_meta.get("product", ""),
        "platform": request_meta.get("access_channel", ""),
        "role": request_meta.get("permission_level", ""),
        "priority": priority.lower(),
        "product_version": version,
        "plan_tier": plan_tier.lower(),
        "customer_tier": str(request_meta.get("customer_tier", "") or ""),
        "channel": channel.lower(),
    }


def run(context: dict) -> dict:
    ticket = context.get("ticket", {}).get("cleaned", "")
    request_meta = context.get("request_meta", {})
    routing_strategy = "general"

    word_count = len(re.findall(r"\b\w+\b", ticket or ""))
    if re.search(r"\bgolden eval\b", ticket or "", re.I) and re.search(r"\b(latency|cost|budget|baseline|retrieval recall)\b", ticket or "", re.I):
        routing_strategy = "general"
    elif word_count <= 5 and re.search(r"\b(help|broken|issue|problem)\b", ticket or "", re.I):
        routing_strategy = "general"
    else:
        for route, pattern in _ROUTES:
            if pattern.search(ticket):
                routing_strategy = route
                break

    plan = extract_plan(ticket, request_meta)
    plan["intent"] = routing_strategy
    plan["retrieval_plan"] = _retrieval_plan(ticket, plan.get("explicit_questions", []), routing_strategy)
    plan["answer_type"] = _answer_type(routing_strategy, ticket)
    context["routing_strategy"] = routing_strategy
    context["route_hints"] = _ROUTE_CONFIG.get(routing_strategy, {})
    context["planner_output"] = plan
    context["metadata_filter"] = {
        "product": context.get("product", ""),
        "platform": context.get("platform", ""),
        "role": plan.get("role", ""),
        "plan_tier": plan.get("plan_tier", ""),
        "product_version": plan.get("product_version", ""),
        "channel": plan.get("channel", ""),
    }

    logger.info(f"Planner — route: {routing_strategy} | hints: {context['route_hints']}")
    return context
