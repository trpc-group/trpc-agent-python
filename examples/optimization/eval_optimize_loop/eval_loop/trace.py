"""Trace helpers for fake-mode runs."""

from __future__ import annotations

from typing import Any


def make_trace(enabled: bool, *, prompt_id: str, case_id: str, model_trace: dict[str, Any],
               judge_trace: dict[str, Any]) -> dict[str, Any]:
    if not enabled:
        return {}
    return {
        "trace_mode": "fake",
        "prompt_id": prompt_id,
        "case_id": case_id,
        "model": model_trace,
        "judge": judge_trace,
    }
