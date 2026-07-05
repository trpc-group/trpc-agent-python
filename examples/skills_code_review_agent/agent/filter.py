# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""ReviewGuardFilter — the framework enforcement site for the review policy (issue #92, req 7).

A TOOL-scoped filter (``register_tool_filter``): it inspects a tool call's args before the tool
runs and refuses high-risk sandbox actions, so ``deny`` / ``needs_human_review`` never reach
execution. Attach it on the tool (``FunctionTool(fn, filters_name=["review_guard"])``), NOT on the
agent — the agent resolves ``filters_name`` in the AGENT namespace and would raise.

It shares its decision logic with the deterministic sandbox gate via ``pipeline.policy.ReviewPolicy``.
"""
from __future__ import annotations

from typing import Any

from trpc_agent_sdk.filter import BaseFilter, FilterResult, register_tool_filter

from pipeline.policy import ReviewPolicy

_GUARDED_ARG_KEYS = ("command", "script", "cmd")


@register_tool_filter("review_guard")
class ReviewGuardFilter(BaseFilter):
    """Blocks a guarded tool call whose command the policy denies or flags for human review."""

    policy = ReviewPolicy()

    def _command(self, req: Any) -> str:
        if isinstance(req, dict):
            for key in _GUARDED_ARG_KEYS:
                if req.get(key):
                    return str(req[key])
        return ""

    async def _before(self, ctx: Any, req: Any, rsp: FilterResult) -> None:
        command = self._command(req)
        if not command:
            return  # nothing risky to gate (e.g. review_code(diff_text=...))
        decision = self.policy.evaluate(command=command)
        if not decision.allowed:
            rsp.is_continue = False
            rsp.error = PermissionError(f"review_guard blocked ({decision.category}): {decision.reason}")
