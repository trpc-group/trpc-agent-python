# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""The review tool the agent calls — a thin wrapper over the deterministic pipeline."""
from __future__ import annotations

from typing import Any

from trpc_agent_sdk.tools import FunctionTool

from pipeline.engine import run_review

from . import filter as _guard  # noqa: F401 - importing registers the "review_guard" tool filter


def review_code(diff_text: str) -> dict[str, Any]:
    """Run the code-review pipeline on a unified diff and return a findings summary.

    Args:
        diff_text: the unified diff to review.
    """
    result = run_review(diff_text=diff_text)
    return {
        "task_id": result.task_id,
        "summary": result.report.findings_summary,
        "severity": result.report.severity_stats,
    }


def build_review_tool() -> FunctionTool:
    # The guard is TOOL-scoped: attach on the tool, not on the agent.
    return FunctionTool(review_code, filters_name=["review_guard"])
