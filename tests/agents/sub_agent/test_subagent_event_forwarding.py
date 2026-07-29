# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Tests for sub-agent event forwarding (progress-streaming path).

Covers the projection helper ``_project_subagent_event`` and the streaming
generator ``run_subagent_streaming``, plus the fact that ``run_subagent``
delegates to it. The Runner is mocked so no real LLM is required — this mirrors
the mocking style in ``test_runner.py``.
"""

from __future__ import annotations

from typing import List
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from trpc_agent_sdk.agents.sub_agent import GENERAL_PURPOSE_AGENT
from trpc_agent_sdk.agents.sub_agent import SubAgentConfig
from trpc_agent_sdk.agents.sub_agent._runner import _project_subagent_event
from trpc_agent_sdk.agents.sub_agent._runner import run_subagent
from trpc_agent_sdk.agents.sub_agent._runner import run_subagent_streaming
from trpc_agent_sdk.exceptions import RunCancelledException
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.models import ModelRegistry
from trpc_agent_sdk.tools import ReadTool
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


class MockLLMModel(LLMModel):
    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"test-dynamic-.*"]

    async def _generate_async_impl(self, request, stream=False, ctx=None):
        yield LlmResponse(content=None)

    def validate_request(self, request):
        pass


@pytest.fixture(scope="module", autouse=True)
def _register_mock_model():
    original = ModelRegistry._registry.copy()
    ModelRegistry.register(MockLLMModel)
    yield
    ModelRegistry._registry = original


def _parent_ctx_with_model(model: str) -> MagicMock:
    parent_ctx = MagicMock()
    parent_agent = MagicMock()
    parent_agent.model = model
    parent_agent.generate_content_config = None
    parent_agent.parallel_tool_calls = False
    parent_ctx.agent = parent_agent
    parent_ctx.agent.tools = [ReadTool()]
    parent_ctx.session.app_name = "test_app"
    parent_ctx.artifact_service = None
    return parent_ctx


# --- _project_subagent_event -------------------------------------------------


def test_project_subagent_event_text_and_metadata() -> None:
    event = MagicMock()
    event.author = "subagent_general_purpose"
    event.partial = True
    event.error_code = None
    event.error_message = None
    event.usage_metadata = None
    event.content = Content(role="model", parts=[Part.from_text(text="thinking...")])

    payload = _project_subagent_event(event)

    assert payload["author"] == "subagent_general_purpose"
    assert payload["partial"] is True
    # content is the framework-native Content dump, not a bespoke shape.
    assert payload["content"]["role"] == "model"
    assert payload["content"]["parts"][0]["text"] == "thinking..."
    assert "error" not in payload
    assert "usage" not in payload


def test_project_subagent_event_captures_tool_calls_and_responses() -> None:
    call_part = Part.from_function_call(name="calculator", args={"expr": "1+1"})
    call_part.function_call.id = "call-1"
    resp_part = Part.from_function_response(name="calculator", response={"result": 2})
    resp_part.function_response.id = "call-1"

    event = MagicMock()
    event.author = "subagent_general_purpose"
    event.partial = False
    event.error_code = None
    event.error_message = None
    event.usage_metadata = None
    event.content = Content(role="model", parts=[call_part, resp_part])

    payload = _project_subagent_event(event)

    parts = payload["content"]["parts"]
    assert parts[0]["function_call"] == {"id": "call-1", "args": {"expr": "1+1"}, "name": "calculator"}
    assert parts[1]["function_response"] == {"id": "call-1", "name": "calculator", "response": {"result": 2}}


def test_project_subagent_event_keeps_thought_in_content() -> None:
    """Thought parts are preserved in content (with thought=True) for the consumer to render or hide."""
    thought = Part(text="internal reasoning", thought=True)
    visible = Part.from_text(text="answer")

    event = MagicMock()
    event.author = "sub"
    event.partial = False
    event.error_code = None
    event.error_message = None
    event.usage_metadata = None
    event.content = Content(role="model", parts=[thought, visible])

    payload = _project_subagent_event(event)
    parts = payload["content"]["parts"]
    assert parts[0] == {"text": "internal reasoning", "thought": True}
    assert parts[1]["text"] == "answer"


def test_project_subagent_event_no_content() -> None:
    event = MagicMock()
    event.author = "sub"
    event.partial = False
    event.error_code = None
    event.error_message = None
    event.usage_metadata = None
    event.content = None

    payload = _project_subagent_event(event)
    assert "content" not in payload
    assert payload["author"] == "sub"


def test_project_subagent_event_surfaces_error() -> None:
    """When the event carries an error, it appears under the ``error`` key."""
    event = MagicMock()
    event.author = "sub"
    event.partial = False
    event.error_code = "MAX_TOKENS"
    event.error_message = "context length exceeded"
    event.usage_metadata = None
    event.content = None

    payload = _project_subagent_event(event)
    assert payload["error"] == {"code": "MAX_TOKENS", "message": "context length exceeded"}


def test_project_subagent_event_no_error_key_when_clean() -> None:
    """A clean event omits the ``error`` key entirely (payload stays lean)."""
    event = MagicMock()
    event.author = "sub"
    event.partial = False
    event.error_code = None
    event.error_message = None
    event.usage_metadata = None
    event.content = Content(role="model", parts=[Part.from_text(text="ok")])

    payload = _project_subagent_event(event)
    assert "error" not in payload


def test_project_subagent_event_captures_usage() -> None:
    """Token counts are lifted out of usage_metadata under the ``usage`` key."""
    usage = MagicMock()
    usage.prompt_token_count = 120
    usage.candidates_token_count = 45
    usage.total_token_count = 165

    event = MagicMock()
    event.author = "sub"
    event.partial = False
    event.error_code = None
    event.error_message = None
    event.usage_metadata = usage
    event.content = Content(role="model", parts=[Part.from_text(text="done")])

    payload = _project_subagent_event(event)
    assert payload["usage"] == {"prompt_tokens": 120, "completion_tokens": 45, "total_tokens": 165}


# --- run_subagent_streaming helpers ------------------------------------------


def _make_model_event(text: str, *, partial: bool = False) -> MagicMock:
    event = MagicMock()
    event.content = Content(role="model", parts=[Part.from_text(text=text)])
    event.author = "subagent_general-purpose"
    event.partial = partial
    event.error_code = None
    event.error_message = None
    event.usage_metadata = None
    event.is_error = MagicMock(return_value=False)
    return event


def _mock_streaming_runner(event_stream: list) -> MagicMock:
    async def _fake_run_async(*args, **kwargs):
        for event in event_stream:
            yield event

    mock_runner_instance = MagicMock()
    mock_runner_instance.run_async = _fake_run_async
    mock_runner_instance.session_service = MagicMock()
    mock_runner_instance.session_service.create_session = AsyncMock()
    mock_runner_instance.session_service.append_event = AsyncMock()
    mock_runner_instance.artifact_service = None
    mock_runner_instance.close = AsyncMock()
    return mock_runner_instance


# --- run_subagent_streaming --------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_yields_projections_then_result() -> None:
    """Each sub-agent event yields a projection dict; the last value is the result."""
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    events = [_make_model_event("partial", partial=True), _make_model_event("Final answer.")]
    mock_runner_instance = _mock_streaming_runner(events)

    with patch("trpc_agent_sdk.runners.Runner", MagicMock(return_value=mock_runner_instance)):
        yielded = [v async for v in run_subagent_streaming(
            parent_ctx=parent_ctx,
            archetype=GENERAL_PURPOSE_AGENT,
            prompt="Do something.",
        )]

    # 2 projection dicts (one per event) + 1 final string result.
    assert len(yielded) == 3
    assert all(isinstance(v, dict) and "content" in v for v in yielded[:2])
    assert yielded[-1] == "Final answer."
    mock_runner_instance.close.assert_called_once()


@pytest.mark.asyncio
async def test_streaming_build_error_yields_error_dict_only() -> None:
    """A build failure yields a single error dict as the final value (no projections)."""
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    parent_ctx.agent.model = None  # force resolve_model to raise
    parent_ctx.agent.tools = []

    yielded = [v async for v in run_subagent_streaming(
        parent_ctx=parent_ctx,
        archetype=GENERAL_PURPOSE_AGENT,
        prompt="Do something.",
    )]

    assert len(yielded) == 1
    assert yielded[0]["status"] == "error"


@pytest.mark.asyncio
async def test_streaming_cancelled_final_value() -> None:
    """Cancellation surfaces the marker as the final yielded value, not raised."""
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    mock_runner_instance = _mock_streaming_runner([])
    mock_runner_instance.run_async = MagicMock(side_effect=RunCancelledException())

    with patch("trpc_agent_sdk.runners.Runner", MagicMock(return_value=mock_runner_instance)):
        yielded = [v async for v in run_subagent_streaming(
            parent_ctx=parent_ctx,
            archetype=GENERAL_PURPOSE_AGENT,
            prompt="Do something.",
        )]

    assert yielded[-1] == "[sub-agent cancelled]"
    mock_runner_instance.close.assert_called_once()


@pytest.mark.asyncio
async def test_streaming_max_turns_note_in_final() -> None:
    """max_turns stops streaming and folds the note into the final value."""
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    events = [_make_model_event("Iteration 1."), _make_model_event("Iteration 2.")]
    mock_runner_instance = _mock_streaming_runner(events)

    with patch("trpc_agent_sdk.runners.Runner", MagicMock(return_value=mock_runner_instance)):
        yielded = [v async for v in run_subagent_streaming(
            parent_ctx=parent_ctx,
            archetype=GENERAL_PURPOSE_AGENT,
            prompt="Do something.",
            agent_config=SubAgentConfig(max_turns=1),
        )]

    assert "[sub-agent stopped: max turns reached]" in yielded[-1]
    mock_runner_instance.close.assert_called_once()


@pytest.mark.asyncio
async def test_run_subagent_delegates_to_streaming() -> None:
    """run_subagent returns the same final value the streaming generator ends on."""
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    events = [_make_model_event("partial", partial=True), _make_model_event("Final answer.")]
    mock_runner_instance = _mock_streaming_runner(events)

    with patch("trpc_agent_sdk.runners.Runner", MagicMock(return_value=mock_runner_instance)):
        result = await run_subagent(
            parent_ctx=parent_ctx,
            archetype=GENERAL_PURPOSE_AGENT,
            prompt="Do something.",
        )

    assert result == "Final answer."
