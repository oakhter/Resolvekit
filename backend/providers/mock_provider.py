from __future__ import annotations

import hashlib
import math

from backend.providers.base import BaseProvider


class MockProvider(BaseProvider):
    """Deterministic no-key preview provider for ingest, UI, trace, and smoke flows."""

    def __init__(self):
        self.model = "resolvekit-mock-preview"
        self.last_usage = {
            "model": self.model,
            "endpoint": "completion",
            "step": "responder",
            "tokens_in": 0,
            "tokens_out": 0,
            "latency_ms": 0,
            "cost_usd": 0.0,
            "error": False,
            "provider": "mock",
        }

    def complete(self, system_prompt: str, user_message: str) -> str:
        if not user_message or not user_message.strip():
            raise ValueError("User message is empty")
        self.last_usage = {
            **self.last_usage,
            "tokens_in": len((system_prompt or "").split()) + len(user_message.split()),
            "tokens_out": 94,
        }
        return (
            "Issue Classification:\n"
            "MOCK PREVIEW - support drafting flow demonstration\n\n"
            "Diagnosis:\n"
            "Hypothesis 1: The answer depends on approved KB evidence provided in the request context — "
            "Likelihood: MEDIUM — Evidence: [KB-1]\n\n"
            "Root Cause:\n"
            "MOCK PREVIEW: This canned response does not call a hosted model. Use it to inspect ingest, UI, "
            "citations, traces, and review flow before adding provider keys. [KB-1]\n\n"
            "Resolution Steps:\n"
            "1. Review the cited approved source before sending any customer reply. [KB-1]\n"
            "2. Replace ACTIVE_PROVIDER=mock with openai or gemini for real drafting. [KB-1]\n\n"
            "Sources:\n"
            "mock_preview_provider [KB-1]\n\n"
            "Confidence:\n"
            "LOW\n\n"
            "Draft Email:\n"
            "Subject: MOCK PREVIEW - review required\n\n"
            "Hi,\n\n"
            "MOCK PREVIEW: This is a canned draft for no-key preview mode. Please review the approved source "
            "and switch to a hosted provider before using ResolveKit for real drafting.\n\n"
            "Kind regards,\n"
            "Support Team"
        )

    def get_embedding(self, text: str, is_query: bool = False) -> list[float]:
        seed = hashlib.sha256((text or "").encode("utf-8")).digest()
        values = []
        for index in range(384):
            byte = seed[index % len(seed)]
            angle = (byte + index + (17 if is_query else 0)) / 17.0
            values.append(round(math.sin(angle), 6))
        return values

    def get_name(self) -> str:
        return "mock"
