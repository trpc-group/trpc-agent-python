# Copyright (C) 2026 Tencent. All rights reserved.
# tRPC-Agent-Python is licensed under Apache-2.0.
from __future__ import annotations

import pytest

from trpc_agent_sdk.code_executors import CodeBlock
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import UnsafeLocalCodeExecutor

from trpc_agent_sdk.tools.safety._code_executor_guard import SafetyGuardedCodeExecutor


class _SpyExecutor(UnsafeLocalCodeExecutor):
    """Records whether execute_code actually ran the block."""
    from pydantic import PrivateAttr
    _executed_codes: list[str] = PrivateAttr(default_factory=list)

    def __init__(self, **data):
        super().__init__(**data)

    @property
    def executed_codes(self) -> list[str]:
        return self._executed_codes

    async def execute_code(self, invocation_context, input_data):
        self.executed_codes.extend(b.code for b in input_data.code_blocks)
        from trpc_agent_sdk.code_executors import create_code_execution_result
        return create_code_execution_result(stdout="ok")


@pytest.mark.asyncio
async def test_dangerous_block_is_blocked():
    spy = _SpyExecutor()
    guard = SafetyGuardedCodeExecutor(delegate=spy)
    inp = CodeExecutionInput(code_blocks=[
        CodeBlock(language="python", code="exec('rm -rf /')"),
    ])
    result = await guard.execute_code(None, inp)
    assert spy.executed_codes == []          # delegate never ran it
    assert "TOOL_SAFETY_BLOCKED" in (result.output or "")


@pytest.mark.asyncio
async def test_safe_block_runs():
    spy = _SpyExecutor()
    guard = SafetyGuardedCodeExecutor(delegate=spy)
    inp = CodeExecutionInput(code_blocks=[
        CodeBlock(language="python", code="print('hello')"),
    ])
    await guard.execute_code(None, inp)
    assert spy.executed_codes == ["print('hello')"]


@pytest.mark.asyncio
async def test_mixed_blocks_partial():
    spy = _SpyExecutor()
    guard = SafetyGuardedCodeExecutor(delegate=spy)
    inp = CodeExecutionInput(code_blocks=[
        CodeBlock(language="python", code="print('safe')"),
        CodeBlock(language="python", code="eval('x')"),
    ])
    result = await guard.execute_code(None, inp)
    assert spy.executed_codes == ["print('safe')"]   # only safe one ran
    assert "TOOL_SAFETY_BLOCKED" in (result.output or "")
