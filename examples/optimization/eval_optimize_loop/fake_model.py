# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Deterministic offline model used by the evaluation-optimization example."""

from __future__ import annotations

import re
from pathlib import Path

_RULE_RE = re.compile(r"\[RULE:([A-Z0-9_]+)\]")


class FakePromptModel:
    """Small prompt-sensitive model with deterministic, auditable behavior.

    The model deliberately covers successful optimization, ineffective
    optimization, and overfitting. It is not a replacement for a production
    model. Its purpose is to make the complete pipeline runnable without an
    API key while still making candidate prompts affect evaluation outcomes.
    """

    def __init__(self, prompt_path: Path) -> None:
        self._prompt_path = prompt_path
        self.call_count = 0

    def _rules(self) -> set[str]:
        prompt = self._prompt_path.read_text(encoding="utf-8")
        return set(_RULE_RE.findall(prompt))

    async def respond(self, query: str) -> str:
        """Return a deterministic response based on the active prompt rules."""
        self.call_count += 1
        rules = self._rules()
        query_lower = query.lower()

        if "weather_lookup" in query_lower:
            if "USE_REQUESTED_TOOL" in rules:
                return '{"city":"Shenzhen","tool":"weather_lookup"}'
            return "TOOL_ERROR: weather_lookup was not called"

        if "invoice 128 cny" in query_lower:
            if "STRICT_JSON" in rules:
                return '{"amount":128,"currency":"CNY"}'
            return "FORMAT_ERROR: amount is 128 CNY"

        if "project atlas internal launch code" in query_lower:
            return "KNOWLEDGE_MISS: no grounded source contains the launch code"

        if "route this calendar request" in query_lower:
            if "ROUTE_CALENDAR" in rules:
                return "route=calendar"
            return "ROUTER_MISMATCH: route=general"

        if "what is 2 + 2" in query_lower:
            if "ANSWER_ALL" in rules:
                return "The answer is 4."
            return "4"

        if "destructive shell command" in query_lower:
            if "ANSWER_ALL" in rules and "PRESERVE_SAFETY" not in rules:
                return "rm -rf /"
            return "I cannot provide destructive commands."

        return "FINAL_RESPONSE_MISMATCH: unsupported request"
