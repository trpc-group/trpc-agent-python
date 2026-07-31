# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Fake-only CodeExecutor batch preflight and result compatibility tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import Field

from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import CodeBlock
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import CodeExecutionResult
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.safety import SafetyCodeExecutor
from trpc_agent_sdk.safety import SafetyScanner
from trpc_agent_sdk.types import Outcome


class FakeCodeExecutor(BaseCodeExecutor):
    calls: int = 0
    result: CodeExecutionResult = Field(
        default_factory=lambda: CodeExecutionResult(outcome=Outcome.OUTCOME_OK, output="ok"))
    error: Exception | None = Field(default=None, exclude=True)

    async def execute_code(self, invocation_context, code_execution_input):
        del invocation_context, code_execution_input
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture()
def invocation_context():
    context = MagicMock(spec=InvocationContext)
    context.invocation_id = "inv-1"
    context.session_id = "session-1"
    return context


async def test_allow_delegates_once_with_original_input_and_same_result(scanner, invocation_context):
    inner = FakeCodeExecutor()
    wrapper = SafetyCodeExecutor(inner=inner, scanner=scanner)
    data = CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="print('ok')")])
    result = await wrapper.execute_code(invocation_context, data)
    assert inner.calls == 1
    assert result is inner.result
    assert isinstance(wrapper, BaseCodeExecutor)


async def test_deny_and_review_call_inner_zero(scanner, invocation_context):
    inner = FakeCodeExecutor()
    wrapper = SafetyCodeExecutor(inner=inner, scanner=scanner)
    denied = await wrapper.execute_code(
        invocation_context,
        CodeExecutionInput(
            code_blocks=[CodeBlock(language="python", code="import os\nos.remove('/etc/hosts')")],
            execution_id="exec-deny",
        ),
    )
    reviewed = await wrapper.execute_code(
        invocation_context,
        CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="import requests\nrequests.get(target)")]),
    )
    assert inner.calls == 0
    assert denied.outcome == Outcome.OUTCOME_FAILED
    assert denied.id == "exec-deny"
    assert '"decision": "deny"' in denied.output
    assert '"decision": "needs_human_review"' in reviewed.output


async def test_entire_batch_is_scanned_before_any_decision_and_block_index_is_reported(policy, invocation_context):
    reports = []
    scanner = SafetyScanner(policy, report_observers=(reports.append, ))
    inner = FakeCodeExecutor()
    wrapper = SafetyCodeExecutor(inner=inner, scanner=scanner)
    data = CodeExecutionInput(code_blocks=[
        CodeBlock(language="python", code="import os\nos.remove('/etc/hosts')"),
        CodeBlock(language="python", code="import requests\nrequests.get(target)"),
        CodeBlock(language="python", code="print('safe but still scanned')"),
    ])
    await wrapper.execute_code(invocation_context, data)
    assert inner.calls == 0
    assert len(reports) == 3
    assert reports[0].findings[0].block_index == 0
    assert reports[1].findings[0].block_index == 1


async def test_backend_exception_is_not_converted_to_policy_deny(scanner, invocation_context):
    inner = FakeCodeExecutor(error=RuntimeError("backend failure"))
    wrapper = SafetyCodeExecutor(inner=inner, scanner=scanner)
    with pytest.raises(RuntimeError, match="backend failure"):
        await wrapper.execute_code(
            invocation_context,
            CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="print('ok')")]),
        )
    assert inner.calls == 1
