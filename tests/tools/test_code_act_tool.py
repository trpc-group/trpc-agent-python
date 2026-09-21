# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for LLM-implemented ellipsis function tools."""

from __future__ import annotations

from typing import Any

import pytest

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.codeact import (
    CodeActImplementation,
    CodeActImplementationPolicy,
    FileCodeActImplementationStore,
    InMemoryCodeActImplementationStore,
)
from trpc_agent_sdk.models import LLMModel, LlmRequest, LlmResponse
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import CodeActTool, is_code_act_function
from trpc_agent_sdk.types import Content, Part


def add_numbers(left: int, right: int) -> int:
    """Add two integers."""
    ...  # noqa: PIE790  # CodeActTool implementation marker


def implemented_function() -> int:
    """Return a deterministic value."""
    return 1


class ScriptedResponseModel(LLMModel):
    """Return pre-built model responses for nested-Agent testing."""

    def __init__(self, responses: list[LlmResponse]) -> None:
        super().__init__(model_name="code_act-function-test-model")
        self.responses = responses
        self.call_count = 0
        self.requests: list[LlmRequest] = []

    @classmethod
    def supported_models(cls) -> list[str]:
        return [r"code_act-function-test-model"]

    async def _generate_async_impl(
        self,
        request: LlmRequest,
        stream: bool = False,
        ctx: Any = None,
    ):
        del stream, ctx
        self.requests.append(request)
        response = self.responses[self.call_count]
        self.call_count += 1
        yield response

    def validate_request(self, request: LlmRequest) -> None:
        del request


def _text_response(text: str) -> LlmResponse:
    return LlmResponse(
        content=Content(role="model", parts=[Part.from_text(text=text)])
    )


def test_code_act_function_detection_requires_ellipsis_body():
    assert is_code_act_function(add_numbers)
    assert not is_code_act_function(implemented_function)
    with pytest.raises(ValueError, match="body is `...`"):
        CodeActTool(implemented_function)


def test_dynamic_policy_does_not_require_implementation_store():
    tool = CodeActTool(add_numbers)

    assert tool.implementation_policy is CodeActImplementationPolicy.DYNAMIC
    assert tool.implementation_store is None


@pytest.mark.parametrize(
    "policy",
    [
        CodeActImplementationPolicy.PREFER_APPROVED,
        "approved_only",
    ],
)
def test_non_dynamic_policy_defaults_to_in_memory_store(policy):
    tool = CodeActTool(add_numbers, implementation_policy=policy)

    assert isinstance(
        tool.implementation_store,
        InMemoryCodeActImplementationStore,
    )


@pytest.mark.asyncio
async def test_code_act_tool_generates_and_executes_code():
    nested_model = ScriptedResponseModel(
        [
            _text_response(
                "```python\n"
                "arguments = await self.get_function_arguments()\n"
                "value = arguments['left'] + arguments['right']\n"
                "return_result({'result': value})\n"
                "```"
            )
        ]
    )
    tool = CodeActTool(add_numbers, model=nested_model)

    call_part = Part.from_function_call(
        name="add_numbers",
        args={"left": 3, "right": 4},
    )
    call_part.function_call.id = "code_act-call"
    parent_model = ScriptedResponseModel(
        [
            LlmResponse(
                content=Content(role="model", parts=[call_part])
            ),
            _text_response("The generated implementation returned seven."),
        ]
    )
    agent = LlmAgent(
        name="code_act_tool_parent",
        model=parent_model,
        tools=[tool],
    )
    sessions = InMemorySessionService()
    await sessions.create_session(
        app_name="test",
        user_id="u1",
        session_id="code_act",
    )
    runner = Runner(
        app_name="test",
        agent=agent,
        session_service=sessions,
    )

    events = [
        event
        async for event in runner.run_async(
            user_id="u1",
            session_id="code_act",
            new_message=Content(
                role="user",
                parts=[Part.from_text(text="Add three and four")],
            ),
        )
    ]

    function_responses = [
        part.function_response.response
        for event in events
        if event.content
        for part in event.content.parts
        if part.function_response
    ]
    assert function_responses == [{"result": 7}]
    assert nested_model.call_count == 1
    assert "add_numbers(left: int, right: int) -> int" in str(
        nested_model.requests[0].config.system_instruction
    )
    assert any(
        event.get_text() == "The generated implementation returned seven."
        for event in events
    )
    await runner.close()


@pytest.mark.asyncio
async def test_code_act_tool_saves_approves_and_reuses_implementation(
    monkeypatch,
):
    nested_model = ScriptedResponseModel(
        [
            _text_response(
                "```python\n"
                "arguments = await self.get_function_arguments()\n"
                "print('arguments loaded')\n"
                "```"
            ),
            _text_response(
                "```python\n"
                "return_result({'result': "
                "arguments['left'] + arguments['right']})\n"
                "```"
            ),
        ]
    )
    store = InMemoryCodeActImplementationStore()
    tool = CodeActTool(
        add_numbers,
        model=nested_model,
        implementation_store=store,
        implementation_policy="prefer_approved",
    )

    first_call = Part.from_function_call(
        name="add_numbers",
        args={"left": 3, "right": 4},
    )
    first_call.function_call.id = "first-code-act-call"
    second_call = Part.from_function_call(
        name="add_numbers",
        args={"left": 10, "right": 5},
    )
    second_call.function_call.id = "second-code-act-call"
    parent_model = ScriptedResponseModel(
        [
            LlmResponse(content=Content(role="model", parts=[first_call])),
            _text_response("first complete"),
            LlmResponse(content=Content(role="model", parts=[second_call])),
            _text_response("second complete"),
        ]
    )
    agent = LlmAgent(
        name="code_act_tool_parent",
        model=parent_model,
        tools=[tool],
    )
    sessions = InMemorySessionService()
    await sessions.create_session(
        app_name="test",
        user_id="u1",
        session_id="solidified",
    )
    runner = Runner(
        app_name="test",
        agent=agent,
        session_service=sessions,
    )

    first_events = [
        event
        async for event in runner.run_async(
            user_id="u1",
            session_id="solidified",
            new_message=Content(
                role="user",
                parts=[Part.from_text(text="Add three and four")],
            ),
        )
    ]
    assert any(
        part.function_response
        and part.function_response.response == {"result": 7}
        for event in first_events
        if event.content
        for part in event.content.parts
    )
    candidates = await tool.list_implementations()
    assert len(candidates) == 1
    await tool.approve(candidates[0].version, approved_by="reviewer")

    async def reject_nested_agent(**kwargs):
        del kwargs
        raise AssertionError(
            "approved implementation must bypass the nested Agent"
        )

    monkeypatch.setattr(tool, "_run_nested_agent", reject_nested_agent)

    second_events = [
        event
        async for event in runner.run_async(
            user_id="u1",
            session_id="solidified",
            new_message=Content(
                role="user",
                parts=[Part.from_text(text="Add ten and five")],
            ),
        )
    ]
    assert any(
        part.function_response
        and part.function_response.response == {"result": 15}
        for event in second_events
        if event.content
        for part in event.content.parts
    )
    assert nested_model.call_count == 2
    assert len(await tool.list_implementations()) == 1
    await runner.close()


@pytest.mark.asyncio
async def test_file_implementation_store_can_promote_and_rollback(tmp_path):
    store = FileCodeActImplementationStore(tmp_path)
    first = CodeActImplementation.create(
        tool_name="add_numbers",
        contract_hash="contract",
        capability_hash="capabilities",
        code_cells=["return_result({'result': 1})"],
    )
    second = CodeActImplementation.create(
        tool_name="add_numbers",
        contract_hash="contract",
        capability_hash="capabilities",
        code_cells=["return_result({'result': 2})"],
    )
    await store.save_candidate(first)
    await store.save_candidate(second)

    await store.approve(
        "add_numbers",
        "contract",
        second.version,
        approved_by="reviewer",
    )
    assert await store.get_approved("add_numbers", "contract") == second

    await store.approve("add_numbers", "contract", first.version)
    assert await store.get_approved("add_numbers", "contract") == first
    assert {
        implementation.version
        for implementation in await store.list_implementations(
            "add_numbers",
            "contract",
        )
    } == {first.version, second.version}
