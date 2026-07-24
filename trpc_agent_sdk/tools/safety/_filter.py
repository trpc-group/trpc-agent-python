# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool filter integration for reusable safety review."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from typing import Iterable
from typing import Mapping
from typing import Sequence
from typing_extensions import override

from trpc_agent_sdk._tool_safety import SafetyReview
from trpc_agent_sdk._tool_safety import SafetyReviewer
from trpc_agent_sdk._tool_safety_policy import ToolSafetyPolicy
from trpc_agent_sdk._tool_safety_telemetry import trace_tool_safety_review
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import FilterResult
from trpc_agent_sdk.filter import FilterType
from trpc_agent_sdk.filter import register_tool_filter
from trpc_agent_sdk.tools._context_var import get_tool_var

_DEFAULT_BLOCK_DECISIONS = ("deny", "needs_human_review")


@register_tool_filter("tool_safety")
class ToolSafetyFilter(BaseFilter):
    """Block unsafe tool invocations before the tool implementation runs.

    The filter reviews the serialized tool arguments with :class:`SafetyReviewer`.
    Reviews whose decision is in ``block_decisions`` return a structured tool error
    response and stop the filter chain without raising an exception.
    """

    def __init__(
        self,
        *,
        reviewer: SafetyReviewer | None = None,
        allowed_domains: Iterable[str] | None = None,
        policy: ToolSafetyPolicy | None = None,
        policy_path: str | None = None,
        block_decisions: Sequence[str] = _DEFAULT_BLOCK_DECISIONS,
        action_type: str | None = None,
    ) -> None:
        super().__init__()
        self._type = FilterType.TOOL
        self._name = "tool_safety"
        if reviewer is not None and (allowed_domains is not None or policy is not None or policy_path is not None):
            raise ValueError("reviewer cannot be combined with allowed_domains, policy, or policy_path")
        self._reviewer = reviewer or SafetyReviewer(
            allowed_domains=allowed_domains,
            policy=policy,
            policy_path=policy_path,
        )
        self._block_decisions = frozenset(block_decisions)
        self._action_type = action_type

    @override
    async def _before(self, ctx: AgentContext, req: Any, rsp: FilterResult) -> None:
        """Review the tool invocation before executing the tool."""
        del ctx
        tool = get_tool_var()
        tool_name = getattr(tool, "name", "") if tool is not None else ""
        action_type = self._action_type or _infer_action_type(tool_name, req)
        review = self._reviewer.review(
            _serialize_tool_request(req),
            action_type=action_type,
            tool_name=tool_name,
        )
        trace_tool_safety_review(review)
        if review.decision not in self._block_decisions:
            return

        rsp.rsp = _blocked_tool_response(review)
        rsp.error = None
        rsp.is_continue = False


def _infer_action_type(tool_name: str, req: Any) -> str:
    normalized_name = tool_name.lower()
    if normalized_name in {"bash", "shell"}:
        return "bash"
    if isinstance(req, Mapping) and isinstance(req.get("command"), str):
        return "bash"
    return "tool"


def _serialize_tool_request(req: Any) -> str:
    if isinstance(req, str):
        return req
    try:
        return json.dumps(req, ensure_ascii=False, sort_keys=True, default=_json_default)
    except (TypeError, ValueError):
        return str(req)


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _blocked_tool_response(review: SafetyReview) -> dict[str, Any]:
    return {
        "success": False,
        "error": f"TOOL_SAFETY_BLOCKED: {review.finding}",
        "safety": review.report,
        "safety_audit": review.audit,
    }
