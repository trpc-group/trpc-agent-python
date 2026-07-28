# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Program runner and CodeExecutor wrapper tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from typing_extensions import override

from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import BaseProgramRunner
from trpc_agent_sdk.code_executors import CodeBlock
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import CodeExecutionResult
from trpc_agent_sdk.code_executors import WorkspaceInfo
from trpc_agent_sdk.code_executors import WorkspaceRunProgramSpec
from trpc_agent_sdk.code_executors import WorkspaceRunResult
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools.safety import GuardedCodeExecutor
from trpc_agent_sdk.tools.safety import GuardedProgramRunner
from trpc_agent_sdk.tools.safety import SafetyAuditEvent
from trpc_agent_sdk.tools.safety import SafetyGuard
from trpc_agent_sdk.tools.safety import SafetyScanner
from trpc_agent_sdk.types import Outcome


class RecordingAuditSink:

    def __init__(self):
        self.events: list[SafetyAuditEvent] = []

    def emit(self, event: SafetyAuditEvent) -> None:
        self.events.append(event)


class FakeProgramRunner(BaseProgramRunner):

    def __init__(self, result: WorkspaceRunResult | None = None):
        super().__init__()
        self.calls = 0
        self.result = result or WorkspaceRunResult(stdout="ok", exit_code=0)

    @override
    async def run_program(self, ws, spec, ctx=None):
        del ws, spec, ctx
        self.calls += 1
        return self.result


class FakeCodeExecutor(BaseCodeExecutor):
    calls: int = 0

    @override
    async def execute_code(
        self,
        invocation_context: InvocationContext,
        code_execution_input: CodeExecutionInput,
    ) -> CodeExecutionResult:
        del invocation_context, code_execution_input
        self.calls += 1
        return CodeExecutionResult(outcome=Outcome.OUTCOME_OK, output="ok")


@pytest.mark.asyncio
async def test_program_runner_blocks_shell_payload_before_delegate():
    sink = RecordingAuditSink()
    delegate = FakeProgramRunner()
    runner = GuardedProgramRunner(delegate, SafetyGuard(SafetyScanner(), sink), tool_name="SkillRun")

    result = await runner.run_program(
        WorkspaceInfo(id="ws", path="/tmp/work"),
        WorkspaceRunProgramSpec(cmd="bash", args=["-c", "rm -rf /"], cwd="/tmp/work"),
    )

    assert result.exit_code == 126
    assert '"decision":"deny"' in result.stderr
    assert delegate.calls == 0
    assert len(sink.events) == 1


@pytest.mark.asyncio
async def test_program_runner_allows_safe_command():
    sink = RecordingAuditSink()
    delegate = FakeProgramRunner()
    runner = GuardedProgramRunner(delegate, SafetyGuard(SafetyScanner(), sink), tool_name="SkillRun")

    result = await runner.run_program(
        WorkspaceInfo(id="ws", path="/tmp/work"),
        WorkspaceRunProgramSpec(cmd="echo", args=["hello"], cwd="/tmp/work"),
    )

    assert result.exit_code == 0
    assert result.stdout == "ok"
    assert delegate.calls == 1
    assert len(sink.events) == 1


@pytest.mark.asyncio
async def test_program_runner_scans_skill_venv_prelude_and_real_command():
    sink = RecordingAuditSink()
    delegate = FakeProgramRunner()
    runner = GuardedProgramRunner(delegate, SafetyGuard(SafetyScanner(), sink), tool_name="SkillRun")
    prelude = ('export PATH=/tmp/work/.venv/bin:"$PATH"; '
               'if [ -z "$VIRTUAL_ENV" ]; then export VIRTUAL_ENV=/tmp/work/.venv; fi; ')

    safe_result = await runner.run_program(
        WorkspaceInfo(id="ws", path="/tmp/work"),
        WorkspaceRunProgramSpec(cmd="bash", args=["-c", prelude + "echo ok"], cwd="/tmp/work"),
    )
    blocked_result = await runner.run_program(
        WorkspaceInfo(id="ws", path="/tmp/work"),
        WorkspaceRunProgramSpec(cmd="bash", args=["-c", prelude + "rm -rf /"], cwd="/tmp/work"),
    )

    assert safe_result.exit_code == 0
    assert blocked_result.exit_code == 126
    assert delegate.calls == 1


@pytest.mark.asyncio
async def test_code_executor_blocks_dangerous_python():
    sink = RecordingAuditSink()
    delegate = FakeCodeExecutor()
    executor = GuardedCodeExecutor(delegate, SafetyGuard(SafetyScanner(), sink))

    result = await executor.execute_code(
        MagicMock(spec=InvocationContext),
        CodeExecutionInput(code_blocks=[CodeBlock(
            language="python",
            code='import shutil\nshutil.rmtree("/")',
        )]),
    )

    assert result.outcome == Outcome.OUTCOME_FAILED
    assert '"decision":"deny"' in result.output
    assert delegate.calls == 0


@pytest.mark.asyncio
async def test_code_executor_allows_safe_python():
    sink = RecordingAuditSink()
    delegate = FakeCodeExecutor()
    executor = GuardedCodeExecutor(delegate, SafetyGuard(SafetyScanner(), sink))

    result = await executor.execute_code(
        MagicMock(spec=InvocationContext),
        CodeExecutionInput(code_blocks=[CodeBlock(language="python", code='print("ok")')]),
    )

    assert result.outcome == Outcome.OUTCOME_OK
    assert result.output == "ok"
    assert delegate.calls == 1


@pytest.mark.asyncio
async def test_code_executor_audits_unsupported_language_as_review():
    sink = RecordingAuditSink()
    delegate = FakeCodeExecutor()
    executor = GuardedCodeExecutor(delegate, SafetyGuard(SafetyScanner(), sink))

    result = await executor.execute_code(
        MagicMock(spec=InvocationContext),
        CodeExecutionInput(code_blocks=[CodeBlock(language="ruby", code='puts "ok"')]),
    )

    assert result.outcome == Outcome.OUTCOME_FAILED
    assert '"decision":"needs_human_review"' in result.output
    assert delegate.calls == 0
    assert len(sink.events) == 1
