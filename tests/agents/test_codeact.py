# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the opt-in CodeAct execution protocol."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, PrivateAttr
from typing_extensions import override

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.code_executors import (
    BaseCodeExecutor,
    CodeBlock,
    CodeExecutionInput,
    CodeExecutionResult,
    create_code_execution_result,
)
from trpc_agent_sdk.codeact import (
    CODEACT_RESULT_PREFIX,
    BaseCodeActRuntime,
    CodeActConfig,
    CodeActExecutionResult,
    CodeActObjectRef,
    CodeActObjectStore,
    CodeActResponseProcessor,
    InProcessCodeActRuntime,
    create_codeact_execution_result,
    parse_codeact_result,
    prepare_codeact_code,
)
from trpc_agent_sdk.context import AgentContext, InvocationContext
from trpc_agent_sdk.models import LLMModel, LlmRequest, LlmResponse
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import Content, GenerateContentResponseUsageMetadata, Part


async def _unused_model_factory(_: dict[str, Any]):
    raise AssertionError("model factory should not be called by these tests")


class Answer(BaseModel):
    value: int


class RecordingRuntime(BaseCodeActRuntime):
    """Runtime test double that records prepared CodeAct cells."""

    _last_cells: list[str] | None = PrivateAttr(default=None)
    _result_value: Any = PrivateAttr(default=None)

    def __init__(self, result_value: Any = None, **data: Any):
        super().__init__(**data)
        self._result_value = result_value

    @property
    def last_cells(self) -> list[str] | None:
        return self._last_cells

    @override
    async def execute(
        self,
        invocation_context: InvocationContext,
        cells: list[str],
    ) -> CodeActExecutionResult:
        self._last_cells = cells
        payload = json.dumps(self._result_value, ensure_ascii=False, separators=(",", ":"))
        return create_codeact_execution_result(stdout=f"{CODEACT_RESULT_PREFIX}{payload}")


class RecordingExecutor(BaseCodeExecutor):
    """Code executor test double used to verify independent routing."""

    _last_input: CodeExecutionInput | None = PrivateAttr(default=None)

    @property
    def last_input(self) -> CodeExecutionInput | None:
        return self._last_input

    @override
    async def execute_code(
        self,
        invocation_context: InvocationContext,
        code_execution_input: CodeExecutionInput,
    ) -> CodeExecutionResult:
        self._last_input = code_execution_input
        return create_code_execution_result(stdout="ordinary code executor")


class ScriptedModel(LLMModel):
    """Model test double that returns scripted complete responses."""

    def __init__(
        self,
        responses: list[str],
        usage_metadata: GenerateContentResponseUsageMetadata | None = None,
    ):
        super().__init__(model_name="codeact-test-model")
        self.responses = responses
        self.call_count = 0
        self.requests: list[LlmRequest] = []
        self.usage_metadata = usage_metadata

    @classmethod
    def supported_models(cls) -> list[str]:
        return [r"codeact-test-model"]

    @property
    def calls(self) -> int:
        return self.call_count

    async def _generate_async_impl(self, request: LlmRequest, stream: bool = False, ctx=None):
        self.requests.append(request)
        text = self.responses[self.call_count]
        self.call_count += 1
        yield LlmResponse(
            content=Content(role="model", parts=[Part(text=text)]),
            usage_metadata=self.usage_metadata,
        )

    def validate_request(self, request: LlmRequest) -> None:
        return None


def _make_agent(runtime: BaseCodeActRuntime, **kwargs: Any) -> LlmAgent:
    return LlmAgent(
        name="codeact_agent",
        model=_unused_model_factory,
        code_act=CodeActConfig(runtime=runtime),
        **kwargs,
    )


def test_prepare_codeact_code_injects_return_result_helper():
    code = prepare_codeact_code("return_result({'value': 7})")

    assert "def return_result(value):" in code
    assert CODEACT_RESULT_PREFIX in code
    assert code.endswith("return_result({'value': 7})")


def test_parse_codeact_result_validates_output_schema():
    output = f"Code execution result:\n{CODEACT_RESULT_PREFIX}" + '{"value":7}\n'

    parsed = parse_codeact_result(output, Answer)

    assert parsed.is_final
    assert parsed.value == {"value": 7}
    assert parsed.serialized == '{"value":7}'


def test_parse_codeact_result_returns_validation_feedback():
    output = f"{CODEACT_RESULT_PREFIX}" + '{"value":"not-an-integer"}'

    parsed = parse_codeact_result(output, Answer)

    assert not parsed.is_final
    assert "validation failed" in (parsed.validation_error or "")


def test_object_store_keeps_large_values_by_reference_and_releases_them():
    store = CodeActObjectStore(inline_threshold=16, preview_chars=32)

    reference = store.wrap(list(range(20)))

    assert isinstance(reference, CodeActObjectRef)
    assert len(reference) == 20
    assert reference[-1] == 19
    assert "builtins.list" in reference.describe()
    assert len(repr(reference)) < 160

    store.clear()
    with pytest.raises(ReferenceError):
        len(reference)


def test_codeact_uses_in_process_runtime_by_default():
    assert isinstance(CodeActConfig().runtime, InProcessCodeActRuntime)


def test_codeact_tools_require_capable_runtime():
    with pytest.raises(ValueError, match="supports_tools=True"):
        LlmAgent(
            name="codeact_agent",
            model=_unused_model_factory,
            code_act=CodeActConfig(runtime=RecordingRuntime()),
            tools=[FunctionTool(lambda: "result")],
        )


@pytest.mark.asyncio
async def test_codeact_processor_emits_validated_result():
    runtime = RecordingRuntime({"value": 7})
    agent = _make_agent(runtime, output_schema=Answer)
    ctx = SimpleNamespace(
        invocation_id="inv-1",
        agent=agent,
        branch=None,
        session=SimpleNamespace(state={}),
    )
    response = LlmResponse(
        content=Content(
            role="model",
            parts=[Part(text="```python\nreturn_result({'value': 7})\n```")],
        ))

    events = [event async for event in CodeActResponseProcessor.run_async(ctx, response)]

    assert [event.object for event in events] == [None, "codeact.result"]
    assert events[-1].get_text() == '{"value":7}'
    assert runtime.last_cells is not None
    assert "def return_result(value):" in runtime.last_cells[0]
    assert events[0].content.parts[0].executable_code.code == "return_result({'value': 7})"


@pytest.mark.asyncio
async def test_codeact_processor_executes_only_first_cell():
    runtime = RecordingRuntime({"value": 7})
    agent = _make_agent(runtime, output_schema=Answer)
    ctx = SimpleNamespace(
        invocation_id="inv-single-cell",
        agent=agent,
        branch=None,
        session=SimpleNamespace(state={}),
    )
    response = LlmResponse(
        content=Content(
            role="model",
            parts=[
                Part(
                    text=(
                        "```python\nfirst = 1\n```\n"
                        "This text incorrectly predicts the observation.\n"
                        "```python\nsecond = 2\n```"
                    ))
            ],
        ))

    events = [event async for event in CodeActResponseProcessor.run_async(ctx, response)]

    assert [event.object for event in events] == [None, "codeact.result"]
    assert runtime.last_cells is not None
    assert len(runtime.last_cells) == 1
    assert "first = 1" in runtime.last_cells[0]
    assert "second = 2" not in runtime.last_cells[0]
    assert response.content is None


@pytest.mark.asyncio
async def test_return_result_stops_current_cell_and_remaining_cells():
    runtime = InProcessCodeActRuntime()
    ctx = SimpleNamespace(
        invocation_id="inv-return-signal",
        agent=SimpleNamespace(tools=[]),
    )

    result = await runtime.execute(
        ctx,
        [
            prepare_codeact_code(
                "value = 1\nreturn_result({'value': value})\nvalue = 2"
            ),
            "value = 3",
        ],
    )
    observation = await runtime.execute(ctx, ["print(value)"])

    assert CODEACT_RESULT_PREFIX in result.output
    assert "Code execution error" not in result.output
    assert observation.output == "Code execution result:\n1\n\n"
    await runtime.release(ctx.invocation_id)


@pytest.mark.asyncio
async def test_llm_agent_codeact_retries_text_only_and_stops_on_result():
    model = ScriptedModel([
        "I should calculate this first.",
        "```python\nreturn_result({'value': 7})\n```",
    ])
    agent = LlmAgent(
        name="codeact_agent",
        model=model,
        code_act=CodeActConfig(runtime=RecordingRuntime({"value": 7})),
        output_schema=Answer,
    )
    session_service = InMemorySessionService()
    await session_service.create_session(app_name="test", user_id="u1", session_id="s1")
    runner = Runner(app_name="test", agent=agent, session_service=session_service)

    events = [
        event async for event in runner.run_async(
            user_id="u1",
            session_id="s1",
            new_message=Content(role="user", parts=[Part(text="Return seven")]),
        )
    ]

    assert model.calls == 2
    assert model.requests[0].config.response_schema is None
    assert "CodeAct execution mode" in str(model.requests[0].config.system_instruction)
    assert not model.requests[0].config.tools
    stored_session = await session_service.get_session(app_name="test", user_id="u1", session_id="s1")
    assert any(event.object == "codeact.feedback" for event in stored_session.events)
    assert all("I should calculate this first." not in event.get_text() for event in events)
    final_event = next(event for event in events if event.object == "codeact.result")
    assert final_event.get_text() == '{"value":7}'


@pytest.mark.asyncio
async def test_llm_agent_codeact_allows_text_when_result_is_not_required():
    model = ScriptedModel(["A plain text answer."])
    agent = LlmAgent(
        name="text_codeact_agent",
        model=model,
        code_act=CodeActConfig(
            runtime=RecordingRuntime(),
            require_result=False,
        ),
        output_key="answer",
    )
    session_service = InMemorySessionService()
    await session_service.create_session(app_name="test", user_id="u1", session_id="text")
    runner = Runner(app_name="test", agent=agent, session_service=session_service)

    events = [
        event async for event in runner.run_async(
            user_id="u1",
            session_id="text",
            new_message=Content(role="user", parts=[Part(text="Answer in text")]),
        )
    ]

    assert any(event.get_text() == "A plain text answer." for event in events)
    assert not any(event.object == "codeact.feedback" for event in events)
    stored_session = await session_service.get_session(app_name="test", user_id="u1", session_id="text")
    assert stored_session.state["answer"] == "A plain text answer."


@pytest.mark.asyncio
async def test_llm_agent_codeact_preserves_usage_metadata():
    usage = GenerateContentResponseUsageMetadata(
        prompt_token_count=10,
        candidates_token_count=5,
        total_token_count=15,
    )
    model = ScriptedModel(
        ["```python\nreturn_result({'value': 7})\n```"],
        usage_metadata=usage,
    )
    agent = LlmAgent(
        name="usage_codeact_agent",
        model=model,
        code_act=CodeActConfig(runtime=RecordingRuntime({"value": 7})),
        output_schema=Answer,
    )
    session_service = InMemorySessionService()
    await session_service.create_session(app_name="test", user_id="u1", session_id="usage")
    runner = Runner(app_name="test", agent=agent, session_service=session_service)

    events = [
        event async for event in runner.run_async(
            user_id="u1",
            session_id="usage",
            new_message=Content(role="user", parts=[Part(text="Return seven")]),
        )
    ]

    final_event = next(event for event in events if event.object == "codeact.result")
    assert final_event.usage_metadata == usage
    assert len([event for event in events if event.usage_metadata]) == 1


@pytest.mark.asyncio
async def test_llm_agent_codeact_accumulates_events_with_override_messages():
    model = ScriptedModel([
        "```python\nvalue = 7\nprint(value)\n```",
        "```python\nreturn_result({'value': value})\n```",
    ])
    agent = LlmAgent(
        name="override_codeact_agent",
        model=model,
        code_act=CodeActConfig(runtime=InProcessCodeActRuntime()),
        output_schema=Answer,
    )
    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name="test",
        user_id="u1",
        session_id="override",
    )
    runner = Runner(app_name="test", agent=agent, session_service=session_service)
    ctx = runner._new_invocation_context(
        session,
        agent_context=AgentContext(),
    )
    ctx.override_messages = [
        Content(role="user", parts=[Part(text="Return seven")]),
    ]

    events = [event async for event in agent._run_async_impl(ctx)]

    assert next(event for event in events if event.object == "codeact.result").get_text() == '{"value":7}'
    second_request_parts = [
        part
        for content in model.requests[1].contents
        for part in content.parts
    ]
    assert any(part.executable_code for part in second_request_parts)
    assert any(part.code_execution_result for part in second_request_parts)


@pytest.mark.asyncio
async def test_llm_agent_codeact_release_error_does_not_mask_result():

    class FailingReleaseRuntime(RecordingRuntime):

        _release_calls: int = PrivateAttr(default=0)

        @property
        def release_calls(self) -> int:
            return self._release_calls

        @override
        async def release(self, execution_id: str) -> None:
            self._release_calls += 1
            raise RuntimeError(f"failed to release {execution_id}")

    model = ScriptedModel([
        "```python\nreturn_result({'value': 7})\n```",
    ])
    runtime = FailingReleaseRuntime({"value": 7})
    agent = LlmAgent(
        name="release_codeact_agent",
        model=model,
        code_act=CodeActConfig(runtime=runtime),
        output_schema=Answer,
    )
    session_service = InMemorySessionService()
    await session_service.create_session(app_name="test", user_id="u1", session_id="release")
    runner = Runner(app_name="test", agent=agent, session_service=session_service)

    events = [
        event async for event in runner.run_async(
            user_id="u1",
            session_id="release",
            new_message=Content(role="user", parts=[Part(text="Return seven")]),
        )
    ]

    assert next(event for event in events if event.object == "codeact.result").get_text() == '{"value":7}'
    assert runtime.release_calls == 1


@pytest.mark.asyncio
async def test_in_process_codeact_persists_object_refs_and_calls_tools():
    def make_values(count: int) -> list[int]:
        """Build a list large enough to remain behind an ObjectRef."""
        return list(range(count))

    model = ScriptedModel([
        (
            "```python\n"
            "print(doc(self))\n"
            "print(doc(self.make_values))\n"
            "values = await self.make_values(count=300)\n"
            "print(values)\n"
            "print(doc(values))\n"
            "```"
        ),
        "```python\nreturn_result({'value': values[-1]})\n```",
    ])
    runtime = InProcessCodeActRuntime(inline_object_threshold=64)
    agent = LlmAgent(
        name="live_codeact_agent",
        model=model,
        tools=[FunctionTool(make_values)],
        code_act=CodeActConfig(runtime=runtime),
        output_schema=Answer,
    )
    session_service = InMemorySessionService()
    await session_service.create_session(app_name="test", user_id="u1", session_id="live")
    runner = Runner(app_name="test", agent=agent, session_service=session_service)

    events = [
        event async for event in runner.run_async(
            user_id="u1",
            session_id="live",
            new_message=Content(role="user", parts=[Part(text="Return the last generated value")]),
        )
    ]

    assert model.calls == 2
    assert not model.requests[0].config.tools
    assert any(
        part.code_execution_result and "ObjectRef" in (part.code_execution_result.output or "")
        for event in events
        if event.content
        for part in event.content.parts
    )
    assert any(
        part.code_execution_result and "make_values" in (part.code_execution_result.output or "")
        for event in events
        if event.content
        for part in event.content.parts
    )
    assert any(
        part.code_execution_result and "count" in (part.code_execution_result.output or "")
        for event in events
        if event.content
        for part in event.content.parts
    )
    final_event = next(event for event in events if event.object == "codeact.result")
    assert final_event.get_text() == '{"value":299}'
    assert not runtime.has_execution(final_event.invocation_id)


@pytest.mark.asyncio
async def test_codeact_and_code_executor_use_independent_paths():
    model = ScriptedModel([
        "```python\nreturn_result({'value': 7})\n```",
    ])
    runtime = RecordingRuntime({"value": 7})
    executor = RecordingExecutor()
    agent = LlmAgent(
        name="split_runtime_agent",
        model=model,
        code_act=CodeActConfig(runtime=runtime),
        code_executor=executor,
        output_schema=Answer,
    )
    session_service = InMemorySessionService()
    await session_service.create_session(app_name="test", user_id="u1", session_id="split")
    runner = Runner(app_name="test", agent=agent, session_service=session_service)

    events = [
        event async for event in runner.run_async(
            user_id="u1",
            session_id="split",
            new_message=Content(role="user", parts=[Part(text="Return seven")]),
        )
    ]

    assert runtime.last_cells is not None
    assert executor.last_input is None
    assert next(event for event in events if event.object == "codeact.result").get_text() == '{"value":7}'


@pytest.mark.asyncio
async def test_codeact_tool_can_delegate_user_code_to_code_executor():
    executor = RecordingExecutor()

    async def execute_user_code(code: str, tool_context: InvocationContext) -> int:
        """Execute user-provided code through the separately configured executor."""
        await executor.execute_code(
            tool_context,
            CodeExecutionInput(code_blocks=[CodeBlock(code=code, language="python")]),
        )
        return 7

    model = ScriptedModel([
        (
            "```python\n"
            "value = await self.execute_user_code(code='print(7)')\n"
            "return_result({'value': value})\n"
            "```"
        ),
    ])
    agent = LlmAgent(
        name="delegating_codeact_agent",
        model=model,
        tools=[FunctionTool(execute_user_code)],
        code_act=CodeActConfig(runtime=InProcessCodeActRuntime()),
        code_executor=executor,
        output_schema=Answer,
    )
    session_service = InMemorySessionService()
    await session_service.create_session(app_name="test", user_id="u1", session_id="delegate")
    runner = Runner(app_name="test", agent=agent, session_service=session_service)

    events = [
        event async for event in runner.run_async(
            user_id="u1",
            session_id="delegate",
            new_message=Content(role="user", parts=[Part(text="Execute user code safely")]),
        )
    ]

    assert executor.last_input is not None
    assert executor.last_input.code_blocks[0].code == "print(7)"
    assert next(event for event in events if event.object == "codeact.result").get_text() == '{"value":7}'
