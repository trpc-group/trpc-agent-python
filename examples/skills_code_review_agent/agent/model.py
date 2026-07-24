# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""FakeReviewModel — deterministic, no-API-key model for the dry-run agent path (criterion 6/8).

It does not call any LLM. On the first turn it emits a single tool call to ``review_code`` with the
user's diff; on the second turn (after the tool result comes back) it emits a short text summary.
This lets the Skills + tool + telemetry agent path run in CI with no secrets, while the actual
findings come from the deterministic scanner pipeline behind the tool.
"""
from __future__ import annotations

from typing import AsyncGenerator, List, Optional

from trpc_agent_sdk.models import LlmRequest, LlmResponse, LLMModel
from trpc_agent_sdk.types import Content, FunctionCall, Part

_TOOL_NAME = "review_code"


def _iter_parts(request: LlmRequest):
    for content in request.contents or []:
        for part in content.parts or []:
            yield content, part


def _has_tool_result(request: LlmRequest) -> Optional[dict]:
    """Return the tool's response dict if this request already carries one (the second turn)."""
    for _content, part in _iter_parts(request):
        if part is not None and part.function_response is not None:
            return part.function_response.response or {}
    return None


def _last_user_diff(request: LlmRequest) -> str:
    """The diff to review is the most recent user text part."""
    latest = ""
    for content, part in _iter_parts(request):
        if part is not None and part.text and (content.role or "user") == "user":
            latest = part.text
    return latest


class FakeReviewModel(LLMModel):

    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"fake-review.*"]

    def validate_request(self, request: LlmRequest) -> None:  # no external validation needed
        return None

    async def _generate_async_impl(self,
                                   request: LlmRequest,
                                   stream: bool = False,
                                   ctx: object = None) -> AsyncGenerator[LlmResponse, None]:
        tool_result = _has_tool_result(request)
        if tool_result is not None:
            summary = tool_result.get("summary", {})
            sev = tool_result.get("severity", {})
            text = (f"Review complete (task {tool_result.get('task_id', '?')}). "
                    f"{summary.get('total', 0)} active finding(s) "
                    f"[critical={sev.get('critical', 0)}, high={sev.get('high', 0)}, "
                    f"medium={sev.get('medium', 0)}, low={sev.get('low', 0)}], "
                    f"{summary.get('needs_human_review', 0)} for human review. "
                    f"See review_report.json for details.")
            yield LlmResponse(content=Content(role="model", parts=[Part(text=text)]))
            return

        diff = _last_user_diff(request)
        call = FunctionCall(id="cr-call-1", name=_TOOL_NAME, args={"diff_text": diff})
        yield LlmResponse(content=Content(role="model", parts=[Part(function_call=call)]))
