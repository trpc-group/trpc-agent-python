# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""SafetyGuardedCodeExecutor tests."""

import asyncio
from unittest.mock import MagicMock

import pytest

from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import create_code_execution_result
from trpc_agent_sdk.tools.safety import SafetyGuardedCodeExecutor
from trpc_agent_sdk.tools.safety import ToolSafetyViolation
from trpc_agent_sdk.tools.safety import ToolScriptSafetyGuard
from trpc_agent_sdk.tools.safety import ToolSafetyPolicy


class _MemorySink:

    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


class _Executor(BaseCodeExecutor):
    calls: int = 0
    delay: float = 0
    output: str = "ok"

    async def execute_code(self, invocation_context, code_execution_input):
        del invocation_context, code_execution_input
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return create_code_execution_result(stdout=self.output)


def _wrapper(delegate, timeout=10, output_bytes=100):
    policy = ToolSafetyPolicy(
        max_timeout_seconds=timeout,
        max_output_bytes=output_bytes,
        allowed_commands=["echo"],
    )
    return SafetyGuardedCodeExecutor(
        delegate=delegate,
        guard=ToolScriptSafetyGuard(policy),
        audit_sink=_MemorySink(),
    )


@pytest.mark.asyncio
async def test_safe_code_delegates():
    delegate = _Executor()
    wrapper = _wrapper(delegate)

    result = await wrapper.execute_code(MagicMock(), CodeExecutionInput(code="print('ok')"))

    assert delegate.calls == 1
    assert "ok" in result.output


@pytest.mark.asyncio
async def test_dangerous_code_is_blocked():
    delegate = _Executor()
    wrapper = _wrapper(delegate)

    with pytest.raises(ToolSafetyViolation) as error:
        await wrapper.execute_code(
            MagicMock(),
            CodeExecutionInput(code="import shutil; shutil.rmtree('/tmp/data')"),
        )

    assert delegate.calls == 0
    assert error.value.report.decision.value == "deny"


@pytest.mark.asyncio
async def test_wrapper_enforces_timeout():
    wrapper = _wrapper(_Executor(delay=1.1), timeout=1)

    result = await wrapper.execute_code(MagicMock(), CodeExecutionInput(code="print('ok')"))

    assert "timed out" in result.output


@pytest.mark.asyncio
async def test_timeout_result_also_obeys_output_limit():
    wrapper = _wrapper(_Executor(delay=1.1), timeout=1, output_bytes=20)
    result = await wrapper.execute_code(MagicMock(), CodeExecutionInput(code="print('ok')"))
    assert len(result.output.encode("utf-8")) <= 20


@pytest.mark.asyncio
async def test_wrapper_limits_output():
    wrapper = _wrapper(_Executor(output="x" * 100), output_bytes=20)

    result = await wrapper.execute_code(MagicMock(), CodeExecutionInput(code="print('ok')"))

    assert len(result.output.encode("utf-8")) <= 20


def test_wrapper_mirrors_execute_once_capability():
    delegate = _Executor(execute_once_per_invocation=True)
    assert _wrapper(delegate).execute_once_per_invocation is True
