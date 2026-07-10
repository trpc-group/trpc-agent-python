from __future__ import annotations

import json


def fake_rubric_score(response: str) -> float:
    """A deterministic structural rubric used only in fake mode."""
    try:
        payload = json.loads(response)
    except json.JSONDecodeError:
        return 0.0
    if not all(key in payload for key in ("route", "tool", "arguments", "answer")):
        return 0.25
    return 1.0
