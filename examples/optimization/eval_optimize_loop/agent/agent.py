# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Deterministic fake agent for the eval-optimize-loop example.

The example's default mode must run without an API key, so this module exposes
an async ``call_agent`` that derives behavior from the current prompt text on
disk. The pipeline still uses AgentEvaluator for scoring; this module only
stands in for the model.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


PROMPT_DIR = Path(__file__).parent / "prompts"
ROUTER_PROMPT_PATH = PROMPT_DIR / "router.md"
SYSTEM_PROMPT_PATH = PROMPT_DIR / "system.md"
SKILL_PROMPT_PATH = PROMPT_DIR / "skill.md"

_SPACE_RE = re.compile(r"\s+")


def _compact_json(payload: dict[str, str]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def normalize_json_text(raw: str) -> str:
    """Normalize JSON-like model output for stable exact matching."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return _SPACE_RE.sub(" ", raw.strip())
    return _compact_json(parsed)


def _read_prompt_text() -> str:
    return "\n\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROUTER_PROMPT_PATH, SYSTEM_PROMPT_PATH, SKILL_PROMPT_PATH)
    ).lower()


def _prompt_flags() -> dict[str, bool]:
    prompt = _read_prompt_text()
    return {
        "refund_rule": "treat double charge" in prompt or "vip refund requests" in prompt,
        "keep_outage_p1": "p1 for urgent production outages" in prompt,
        "keep_plan_p3": "p3 for low-risk informational requests" in prompt,
        "overfit_payment_outage": "overfit_payment_outage" in prompt,
    }


def answer_for_query(query: str) -> str:
    """Return the fake model answer for one support ticket."""
    text = query.lower()
    flags = _prompt_flags()

    if "production checkout outage" in text:
        if flags["overfit_payment_outage"]:
            return _compact_json({
                "category": "billing",
                "priority": "p2",
                "action": "refund_review",
            })
        priority = "p1" if flags["keep_outage_p1"] else "p2"
        return _compact_json({
            "category": "technical",
            "priority": priority,
            "action": "escalate",
        })

    if "vip customer" in text and "double charged" in text:
        if flags["refund_rule"]:
            return _compact_json({
                "category": "billing",
                "priority": "p1",
                "action": "refund_review",
            })
        return _compact_json({
            "category": "account",
            "priority": "p2",
            "action": "answer",
        })

    if "double charged" in text or "refund" in text:
        if flags["refund_rule"]:
            return _compact_json({
                "category": "billing",
                "priority": "p2",
                "action": "refund_review",
            })
        return _compact_json({
            "category": "account",
            "priority": "p2",
            "action": "answer",
        })

    if "password reset" in text or "email address" in text:
        return _compact_json({
            "category": "account",
            "priority": "p3",
            "action": "answer",
        })

    if "plan comparison" in text or "pricing tiers" in text:
        priority = "p3" if flags["keep_plan_p3"] else "p2"
        return _compact_json({
            "category": "billing",
            "priority": priority,
            "action": "answer",
        })

    if "policy citation" in text or "pol-77" in text:
        return _compact_json({
            "category": "billing",
            "action": "answer",
        })

    if "guaranteed instant fix" in text or "repeated invoice confusion" in text:
        return _compact_json({
            "category": "billing",
            "priority": "p3",
            "action": "answer",
        })

    if "mobile app crashes" in text:
        return _compact_json({
            "category": "technical",
            "priority": "p2",
            "action": "troubleshooting",
        })

    if "legacy desktop sync" in text:
        return _compact_json({
            "category": "technical",
            "priority": "p2",
            "action": "troubleshooting",
        })

    return _compact_json({
        "category": "technical",
        "priority": "p2",
        "action": "troubleshooting",
    })


async def call_agent(query: str) -> str:
    """AgentEvaluator / AgentOptimizer compatible black-box entry point."""
    return answer_for_query(query)
