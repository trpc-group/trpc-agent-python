# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Execution-path tests for CodeNodeAction."""

from types import SimpleNamespace
from typing import Any

import pytest
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_LAST_RESPONSE
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_NODE_RESPONSES
from trpc_agent_sdk.dsl.graph._event_writer import AsyncEventWriter
from trpc_agent_sdk.dsl.graph._event_writer import EventWriter
from trpc_agent_sdk.dsl.graph._node_action._code import CodeNodeAction


def _build_action(executor: Any, *, ctx: Any = None) -> CodeNodeAction:
    """Create CodeNodeAction with concrete event writer instances."""
    writer = EventWriter(
        writer=lambda payload: None,
        invocation_id="inv-1",
        author="code-node",
        branch="root.code-node",
    )
    async_writer = AsyncEventWriter(
        writer=lambda payload: None,
        invocation_id="inv-1",
        author="code-node",
        branch="root.code-node",
    )
    return CodeNodeAction(
        name="code-node",
        code_executor=executor,
        code="print(42)",
        language="python",
        writer=writer,
        async_writer=async_writer,
        ctx=ctx,
    )


class _ScriptedExecutor:
    """Executor stub that records inputs and returns configured output."""

    def __init__(self, output: str | None):
        self.output = output
        self.calls: list[tuple[Any, Any]] = []

    async def execute_code(self, ctx: Any, execution_input: Any) -> Any:
        self.calls.append((ctx, execution_input))
        return SimpleNamespace(output=self.output)


class TestCodeNodeActionExecute:
    """Tests for code-node execution and state mapping."""

    async def test_execute_requires_invocation_context(self):
        """Code node should fail fast when invocation context is missing."""
        action = _build_action(_ScriptedExecutor(output="ignored"), ctx=None)

        with pytest.raises(RuntimeError, match="requires InvocationContext"):
            await action.execute({})

    async def test_execute_maps_executor_output_and_input_fields(self):
        """Code executor output should be stored in last_response and node_responses."""
        executor = _ScriptedExecutor(output="42")
        ctx = SimpleNamespace(session=SimpleNamespace(id="session-1"))
        action = _build_action(executor, ctx=ctx)

        result = await action.execute({"unused": True})

        assert result[STATE_KEY_LAST_RESPONSE] == "42"
        assert result[STATE_KEY_NODE_RESPONSES] == {"code-node": "42"}
        assert len(executor.calls) == 1
        called_ctx, execution_input = executor.calls[0]
        assert called_ctx is ctx
        assert execution_input.execution_id == "session-1"
        assert len(execution_input.code_blocks) == 1
        assert execution_input.code_blocks[0].language == "python"
        assert execution_input.code_blocks[0].code == "print(42)"

    async def test_execute_converts_none_output_to_empty_string(self):
        """None code output should normalize to an empty response string."""
        executor = _ScriptedExecutor(output=None)
        ctx = SimpleNamespace(session=SimpleNamespace(id="session-2"))
        action = _build_action(executor, ctx=ctx)

        result = await action.execute({})

        assert result[STATE_KEY_LAST_RESPONSE] == ""
        assert result[STATE_KEY_NODE_RESPONSES] == {"code-node": ""}
