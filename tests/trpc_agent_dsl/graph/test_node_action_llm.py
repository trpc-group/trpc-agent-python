# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for LLMNodeAction behavior and execution paths."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from google.genai.types import Content
from google.genai.types import FunctionCall
from google.genai.types import FunctionResponse
from google.genai.types import Part
from trpc_agent_sdk.dsl.graph._constants import ROLE_USER
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_LAST_RESPONSE
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_LAST_RESPONSE_ID
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_LAST_TOOL_RESPONSE
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_MESSAGES
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_NODE_RESPONSES
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_ONE_SHOT_MESSAGES
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_USER_INPUT
from trpc_agent_sdk.dsl.graph._constants import STREAM_KEY_ACK
from trpc_agent_sdk.dsl.graph._constants import STREAM_KEY_EVENT
from trpc_agent_sdk.dsl.graph._event_writer import AsyncEventWriter
from trpc_agent_sdk.dsl.graph._event_writer import EventWriter
from trpc_agent_sdk.dsl.graph._node_action._llm import LLMNodeAction
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import GenerateContentConfig


class _AckingWriter:
    """Captures writer payloads and resolves async ack futures."""

    def __init__(self):
        self.payloads: list[dict] = []

    def __call__(self, payload: dict) -> None:
        self.payloads.append(payload)
        ack = payload.get(STREAM_KEY_ACK)
        if ack is not None and not ack.done():
            ack.set_result(True)


class _StreamingModel:
    """Model stub that replays scripted streaming responses."""

    def __init__(self, responses: list[LlmResponse], error: Exception | None = None):
        self.name = "mock-model"
        self._responses = responses
        self._error = error
        self.requests: list[tuple] = []

    async def generate_async(self, request, *, stream: bool, ctx):
        self.requests.append((request, stream, ctx))
        if self._error is not None:
            raise self._error
        for response in self._responses:
            yield response


class _ToolLoopModel:
    """Model stub that can emit tool calls and final text responses."""

    def __init__(self, mode: str):
        self.name = "mock-model"
        self.mode = mode
        self.requests: list[tuple[Any, bool, Any]] = []

    async def generate_async(self, request: Any, *, stream: bool, ctx: Any):
        self.requests.append((request, stream, ctx))
        has_tool_response = self._has_tool_response(request.contents)

        if self.mode == "single_call":
            if not has_tool_response:
                yield LlmResponse(
                    content=Content(
                        role="model",
                        parts=[Part(function_call=FunctionCall(id="call-1", name="adder", args={
                            "a": 1,
                            "b": 2
                        }))],
                    ),
                    partial=False,
                    response_id="resp-1",
                )
                return
            yield LlmResponse(
                content=Content(role="model", parts=[Part.from_text(text="done")]),
                partial=False,
                response_id="resp-2",
            )
            return

        if self.mode == "multi_call":
            if not has_tool_response:
                yield LlmResponse(
                    content=Content(
                        role="model",
                        parts=[
                            Part(function_call=FunctionCall(id="call-1", name="adder", args={
                                "a": 1,
                                "b": 2
                            })),
                            Part(function_call=FunctionCall(id="call-2", name="adder", args={
                                "a": 3,
                                "b": 4
                            })),
                        ],
                    ),
                    partial=False,
                    response_id="resp-1",
                )
                return
            yield LlmResponse(
                content=Content(role="model", parts=[Part.from_text(text="complete")]),
                partial=False,
                response_id="resp-2",
            )
            return

        if self.mode == "always_call":
            call_id = f"call-{len(self.requests)}"
            yield LlmResponse(
                content=Content(
                    role="model",
                    parts=[Part(function_call=FunctionCall(id=call_id, name="adder", args={
                        "a": 1,
                        "b": 2
                    }))],
                ),
                partial=False,
                response_id=f"resp-{len(self.requests)}",
            )
            return

        raise ValueError(f"Unsupported mode: {self.mode}")

    @staticmethod
    def _has_tool_response(contents: list[Content]) -> bool:
        for content in contents:
            if not content.parts:
                continue
            for part in content.parts:
                if part.function_response is not None:
                    return True
        return False


_DEFAULT_TOOL_CTX = object()


def _create_tool_context() -> Any:
    """Build a minimal tool_context object compatible with BaseTool.run_async."""
    return SimpleNamespace(
        agent_context=None,
        agent=SimpleNamespace(
            before_tool_callback=None,
            after_tool_callback=None,
            parallel_tool_calls=False,
        ),
    )


def _build_execute_action(model: _StreamingModel, generation_config: GenerateContentConfig | None = None, tools=None):
    """Create LLMNodeAction with concrete event writers for integration tests."""
    sink = _AckingWriter()
    writer = EventWriter(
        writer=sink,
        invocation_id="inv-1",
        author="llm-node",
        branch="root.llm-node",
    )
    async_writer = AsyncEventWriter(
        writer=sink,
        invocation_id="inv-1",
        author="llm-node",
        branch="root.llm-node",
    )
    action = LLMNodeAction(
        name="llm-node",
        model=model,
        instruction="be helpful",
        tools=tools or {},
        generation_config=generation_config,
        writer=writer,
        async_writer=async_writer,
        ctx="ctx",
    )
    return action, sink


def _build_tool_loop_action(
    model: _ToolLoopModel,
    *,
    tool_parallel: bool = False,
    max_tool_iterations: int = 8,
    ctx: Any = _DEFAULT_TOOL_CTX,
) -> tuple[LLMNodeAction, _AckingWriter, list[tuple[Any, int, int]]]:
    """Create LLMNodeAction for integrated model->tool->model loop tests."""
    sink = _AckingWriter()
    writer = EventWriter(
        writer=sink,
        invocation_id="inv-1",
        author="llm-node",
        branch="root.llm-node",
    )
    async_writer = AsyncEventWriter(
        writer=sink,
        invocation_id="inv-1",
        author="llm-node",
        branch="root.llm-node",
    )
    calls: list[tuple[Any, int, int]] = []

    async def adder(a: int, b: int, tool_context: Any) -> dict[str, int]:
        calls.append((tool_context, a, b))
        return {"sum": a + b}

    action_ctx = _create_tool_context() if ctx is _DEFAULT_TOOL_CTX else ctx

    action = LLMNodeAction(
        name="llm-node",
        model=model,
        instruction="be helpful",
        tools={"adder": FunctionTool(adder)},
        tool_parallel=tool_parallel,
        max_tool_iterations=max_tool_iterations,
        generation_config=None,
        writer=writer,
        async_writer=async_writer,
        ctx=action_ctx,
    )
    return action, sink, calls


class TestLLMNodeActionExecute:
    """Tests for full execute flow."""

    async def test_execute_converts_foreign_tool_messages_in_request_history(self):
        """execute() should convert unknown tool history to text before model call."""
        model = _StreamingModel([
            LlmResponse(content=Content(role="model", parts=[Part.from_text(text="ok")]), partial=False),
        ])
        action, _ = _build_execute_action(model, tools={"known_tool": object()})

        foreign_call = Content(
            role="model",
            parts=[Part(function_call=FunctionCall(id="fc-1", name="legacy_tool", args={"q": "abc"}))],
        )
        foreign_response = Content(
            role="user",
            parts=[Part(function_response=FunctionResponse(id="fc-1", name="legacy_tool", response={"ok": True}))],
        )
        known_call = Content(
            role="model",
            parts=[Part(function_call=FunctionCall(id="fc-2", name="known_tool", args={"x": 1}))],
        )
        messages = [foreign_call, foreign_response, known_call]

        with patch("trpc_agent_sdk.dsl.graph._node_action._llm.LlmRequest.append_tools"):
            await action.execute({STATE_KEY_MESSAGES: messages})
        request = model.requests[0][0]

        assert request.contents[0].parts[0].function_call is None
        assert request.contents[0].parts[0].text.startswith("[Tool Call: legacy_tool(")
        assert request.contents[1].parts[0].function_response is None
        assert request.contents[1].parts[0].text.startswith("[Tool Response (legacy_tool):")
        assert request.contents[2].parts[0].function_call is not None
        assert request.contents[2].parts[0].function_call.name == "known_tool"
        # Original messages remain unchanged.
        assert messages[0].parts[0].function_call is not None

    async def test_execute_keeps_tool_parts_when_tools_are_available(self):
        """Known tool calls should remain structured in the request history."""
        model = _StreamingModel([
            LlmResponse(content=Content(role="model", parts=[Part.from_text(text="ok")]), partial=False),
        ])
        action, _ = _build_execute_action(model, tools={"known_tool": object()})
        messages = [
            Content(
                role="model",
                parts=[Part(function_call=FunctionCall(id="fc-1", name="known_tool", args={"x": 1}))],
            )
        ]

        with patch("trpc_agent_sdk.dsl.graph._node_action._llm.LlmRequest.append_tools"):
            await action.execute({STATE_KEY_MESSAGES: messages})
        request = model.requests[0][0]

        assert request.contents[0].parts[0].function_call is not None
        assert request.contents[0].parts[0].function_call.name == "known_tool"

    async def test_execute_user_input_stage_streams_and_updates_state(self):
        """User-input stage should append user message, stream partial text, and clear user_input."""
        model = _StreamingModel([
            LlmResponse(
                content=Content(role="model", parts=[Part.from_text(text="hel")]),
                partial=True,
            ),
            LlmResponse(
                content=Content(role="model", parts=[Part.from_text(text="hello")]),
                partial=False,
                response_id="resp-1",
            ),
        ])
        action, sink = _build_execute_action(model)
        state = {
            STATE_KEY_MESSAGES: [Content(role="model", parts=[Part.from_text(text="history")])],
            STATE_KEY_USER_INPUT: "question",
        }

        result = await action.execute(state)

        assert result[STATE_KEY_LAST_RESPONSE] == "hello"
        assert result[STATE_KEY_LAST_RESPONSE_ID] == "resp-1"
        assert result[STATE_KEY_NODE_RESPONSES]["llm-node"] == "hello"
        assert result[STATE_KEY_USER_INPUT] == ""
        assert len(result[STATE_KEY_MESSAGES]) == 2
        assert result[STATE_KEY_MESSAGES][0].role == ROLE_USER

        request, stream, ctx = model.requests[0]
        assert stream is True
        assert ctx == "ctx"
        assert request.contents[-1].role == ROLE_USER
        assert request.config.system_instruction == "be helpful"

        streamed_texts = [
            payload[STREAM_KEY_EVENT].get_text() for payload in sink.payloads
            if STREAM_KEY_EVENT in payload and payload[STREAM_KEY_EVENT].partial
        ]
        assert "hel" in streamed_texts

    async def test_execute_skips_duplicate_user_input_when_history_already_has_latest_user_message(self):
        """Duplicate user_input text should not be re-added to model request history."""
        model = _StreamingModel(
            [LlmResponse(content=Content(role="model", parts=[Part.from_text(text="answer")]), partial=False)])
        action, _ = _build_execute_action(model)
        user_message = Content(role=ROLE_USER, parts=[Part.from_text(text="same")])
        state = {
            STATE_KEY_MESSAGES: [user_message],
            STATE_KEY_USER_INPUT: "same",
        }

        result = await action.execute(state)

        assert len(result[STATE_KEY_MESSAGES]) == 1
        request = model.requests[0][0]
        assert request.contents == [user_message]

    async def test_execute_consumes_one_shot_messages_from_node_and_global_state(self):
        """One-shot stages should clear consumed keys from returned state delta."""
        response = LlmResponse(content=Content(role="model", parts=[Part.from_text(text="done")]), partial=False)

        node_model = _StreamingModel([response])
        node_action, _ = _build_execute_action(node_model)
        node_message = Content(role=ROLE_USER, parts=[Part.from_text(text="node once")])
        node_state = {
            STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE: {
                "llm-node": [node_message]
            },
        }
        node_result = await node_action.execute(node_state)

        assert STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE in node_result
        assert node_result[STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE] == {}

        global_model = _StreamingModel([response])
        global_action, _ = _build_execute_action(global_model)
        global_message = Content(role=ROLE_USER, parts=[Part.from_text(text="global once")])
        global_state = {
            STATE_KEY_ONE_SHOT_MESSAGES: [global_message],
        }
        global_result = await global_action.execute(global_state)

        assert global_result[STATE_KEY_ONE_SHOT_MESSAGES] == []

    async def test_execute_keeps_function_call_parts_in_final_model_message(self):
        """Final responses containing function_call parts should preserve structured parts."""
        function_call = FunctionCall(id="fc-1", name="tool", args={"a": 1})
        model = _StreamingModel([
            LlmResponse(
                content=Content(
                    role="model",
                    parts=[
                        Part.from_text(text="result"),
                        Part(function_call=function_call),
                    ],
                ),
                partial=False,
            )
        ])
        action, _ = _build_execute_action(model)

        result = await action.execute({STATE_KEY_MESSAGES: []})

        final_parts = result[STATE_KEY_MESSAGES][0].parts
        assert any(part.function_call for part in final_parts)
        assert any(part.text == "result" for part in final_parts)

    async def test_execute_copies_generation_config_and_appends_tools(self):
        """Configured tools should be appended and generation config should remain unmutated."""
        model = _StreamingModel(
            [LlmResponse(content=Content(role="model", parts=[Part.from_text(text="ok")]), partial=False)])
        generation_config = GenerateContentConfig()
        tools = {"tool-a": object()}
        action, _ = _build_execute_action(model, generation_config=generation_config, tools=tools)

        with patch("trpc_agent_sdk.dsl.graph._node_action._llm.LlmRequest.append_tools") as append_tools:
            await action.execute({STATE_KEY_MESSAGES: []})

        append_tools.assert_called_once()
        # system instruction should be applied to copied config, not the original object.
        assert generation_config.system_instruction is None

    async def test_execute_emits_model_error_and_raises_runtime_error(self):
        """Model errors should be transformed to RuntimeError and emit model_complete(error)."""
        model = _StreamingModel([], error=ValueError("model boom"))
        action, sink = _build_execute_action(model)

        with pytest.raises(RuntimeError, match="LLM node 'llm-node' failed: model boom"):
            await action.execute({STATE_KEY_MESSAGES: []})

        texts = [payload[STREAM_KEY_EVENT].get_text() for payload in sink.payloads if STREAM_KEY_EVENT in payload]
        assert any("failed" in text for text in texts)


class TestLLMNodeActionToolLoop:
    """Tests for integrated llm_node tool execution flow."""

    async def test_execute_runs_tool_loop_and_updates_state(self):
        """llm_node should call tool, append function_response, then produce final answer."""
        model = _ToolLoopModel("single_call")
        tool_ctx = _create_tool_context()
        action, _, calls = _build_tool_loop_action(model, ctx=tool_ctx)

        result = await action.execute({STATE_KEY_MESSAGES: []})

        assert len(model.requests) == 2
        assert len(calls) == 1
        assert calls[0] == (tool_ctx, 1, 2)

        assert result[STATE_KEY_LAST_RESPONSE] == "done"
        assert result[STATE_KEY_NODE_RESPONSES]["llm-node"] == "done"
        assert result[STATE_KEY_LAST_TOOL_RESPONSE] == '{"sum": 3}'

        messages = result[STATE_KEY_MESSAGES]
        assert messages[0].parts[0].function_call is not None
        assert messages[1].parts[0].function_response is not None
        assert messages[2].parts[0].text == "done"

    async def test_execute_emits_function_call_and_function_response_events(self):
        """Tool loop should emit visible function call/response events for observers."""
        model = _ToolLoopModel("single_call")
        action, sink, _ = _build_tool_loop_action(model)

        await action.execute({STATE_KEY_MESSAGES: []})

        emitted_calls: list[FunctionCall] = []
        emitted_responses: list[FunctionResponse] = []
        for payload in sink.payloads:
            event = payload.get(STREAM_KEY_EVENT)
            if event is None or event.content is None or not event.content.parts:
                continue
            for part in event.content.parts:
                if part.function_call is not None:
                    emitted_calls.append(part.function_call)
                if part.function_response is not None:
                    emitted_responses.append(part.function_response)

        assert len(emitted_calls) == 1
        assert len(emitted_responses) == 1
        assert emitted_calls[0].id == "call-1"
        assert emitted_responses[0].id == "call-1"
        assert emitted_calls[0].name == "adder"
        assert emitted_responses[0].name == "adder"

    async def test_execute_supports_parallel_tool_calls(self):
        """tool_parallel should execute multiple function calls in one round."""
        model = _ToolLoopModel("multi_call")
        action, _, calls = _build_tool_loop_action(model, tool_parallel=True)

        result = await action.execute({STATE_KEY_MESSAGES: []})

        assert len(model.requests) == 2
        assert len(calls) == 2
        responses = []
        for message in result[STATE_KEY_MESSAGES]:
            if message.parts and message.parts[0].function_response is not None:
                responses.append(message.parts[0].function_response.response)

        assert {"sum": 3} in responses
        assert {"sum": 7} in responses
        assert result[STATE_KEY_LAST_TOOL_RESPONSE] == '{"sum": 7}'

    async def test_execute_honors_max_tool_iterations(self):
        """Tool loop should stop when max_tool_iterations is reached."""
        model = _ToolLoopModel("always_call")
        action, _, calls = _build_tool_loop_action(model, max_tool_iterations=1)

        result = await action.execute({STATE_KEY_MESSAGES: []})

        assert len(model.requests) == 2
        assert len(calls) == 1
        assert result[STATE_KEY_LAST_TOOL_RESPONSE] == '{"sum": 3}'
        assert result[STATE_KEY_LAST_RESPONSE] == ""

    async def test_execute_requires_invocation_context_when_running_tools(self):
        """Tool execution should fail when InvocationContext is missing."""
        model = _ToolLoopModel("single_call")
        action, _, _ = _build_tool_loop_action(model, ctx=None)

        with pytest.raises(RuntimeError, match="requires InvocationContext"):
            await action.execute({STATE_KEY_MESSAGES: []})
