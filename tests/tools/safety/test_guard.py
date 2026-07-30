# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Program runner and CodeExecutor wrapper tests."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from typing_extensions import override

from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import BaseProgramRunner
from trpc_agent_sdk.code_executors import CodeBlock
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import CodeExecutionResult
from trpc_agent_sdk.code_executors import CodeFile
from trpc_agent_sdk.code_executors import WorkspaceInfo
from trpc_agent_sdk.code_executors import WorkspaceRunProgramSpec
from trpc_agent_sdk.code_executors import WorkspaceRunResult
from trpc_agent_sdk.code_executors.local import create_local_workspace_runtime
from trpc_agent_sdk.code_executors.local import UnsafeLocalCodeExecutor
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools.safety import GuardedCodeExecutor
from trpc_agent_sdk.tools.safety import GuardedProgramRunner
from trpc_agent_sdk.tools.safety import SafetyAuditEvent
from trpc_agent_sdk.tools.safety import SafetyGuard
from trpc_agent_sdk.tools.safety import SafetyPolicy
from trpc_agent_sdk.tools.safety import SafetyScanner
from trpc_agent_sdk.types import Outcome


class RecordingAuditSink:

    def __init__(self):
        self.events: list[SafetyAuditEvent] = []

    def emit(self, event: SafetyAuditEvent) -> None:
        self.events.append(event)


class FakeProgramRunner(BaseProgramRunner):

    def __init__(
        self,
        result: WorkspaceRunResult | None = None,
        *,
        provider=None,
        enable_provider_env: bool = False,
    ):
        super().__init__(
            provider=provider,
            enable_provider_env=enable_provider_env,
        )
        self.calls = 0
        self.result = result or WorkspaceRunResult(stdout="ok", exit_code=0)
        self.specs: list[WorkspaceRunProgramSpec] = []

    @override
    async def run_program(self, ws, spec, ctx=None):
        del ws
        spec = self._apply_provider_env(spec, ctx)
        self.calls += 1
        self.specs.append(spec)
        return self.result


class SlowProgramRunner(FakeProgramRunner):

    @override
    async def run_program(self, ws, spec, ctx=None):
        del ws, ctx
        self.calls += 1
        self.specs.append(spec)
        await asyncio.sleep(1)
        return self.result


class LateFailingProgramRunner(FakeProgramRunner):

    @override
    async def run_program(self, ws, spec, ctx=None):
        del ws, spec, ctx
        self.calls += 1
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            await asyncio.sleep(0)
            raise RuntimeError("late runner failure")


class FakeCodeExecutor(BaseCodeExecutor):
    calls: int = 0
    last_input: CodeExecutionInput | None = None

    @override
    async def execute_code(
        self,
        invocation_context: InvocationContext,
        code_execution_input: CodeExecutionInput,
    ) -> CodeExecutionResult:
        del invocation_context
        self.calls += 1
        self.last_input = code_execution_input
        return CodeExecutionResult(outcome=Outcome.OUTCOME_OK, output="ok")


class SlowCodeExecutor(FakeCodeExecutor):

    @override
    async def execute_code(
        self,
        invocation_context: InvocationContext,
        code_execution_input: CodeExecutionInput,
    ) -> CodeExecutionResult:
        del invocation_context, code_execution_input
        self.calls += 1
        await asyncio.sleep(1)
        return CodeExecutionResult(outcome=Outcome.OUTCOME_OK, output="late")


class CancellationSuppressingCodeExecutor(FakeCodeExecutor):

    @override
    async def execute_code(
        self,
        invocation_context: InvocationContext,
        code_execution_input: CodeExecutionInput,
    ) -> CodeExecutionResult:
        del invocation_context, code_execution_input
        self.calls += 1
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            await asyncio.sleep(1)
        return CodeExecutionResult(outcome=Outcome.OUTCOME_OK, output="late")


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
@pytest.mark.parametrize(
    "args",
    [
        ["-e", "-c", "rm -rf /"],
        ["--norc", "-c", "rm -rf /"],
        ["--rcfile", "/tmp/bashrc", "-c", "rm -rf /"],
    ],
)
async def test_program_runner_scans_shell_payload_after_interpreter_options(args):
    delegate = FakeProgramRunner()
    runner = GuardedProgramRunner(
        delegate,
        SafetyGuard(SafetyScanner(), RecordingAuditSink()),
        tool_name="SkillRun",
    )

    result = await runner.run_program(
        WorkspaceInfo(id="ws", path="/tmp/work"),
        WorkspaceRunProgramSpec(cmd="bash", args=args, cwd="/tmp/work"),
    )

    assert result.exit_code == 126
    assert '"rule_id":"FILE-001"' in result.stderr
    assert delegate.calls == 0


@pytest.mark.asyncio
async def test_real_local_workspace_runner_is_blocked_before_process_creation(tmp_path):
    runtime = create_local_workspace_runtime(work_root=str(tmp_path))
    runner = GuardedProgramRunner(
        runtime.runner(),
        SafetyGuard(SafetyScanner(), RecordingAuditSink()),
        tool_name="SkillRun",
    )

    result = await runner.run_program(
        WorkspaceInfo(id="ws", path=str(tmp_path)),
        WorkspaceRunProgramSpec(cmd="bash", args=["-c", "rm -rf /"], cwd=str(tmp_path)),
    )

    assert result.exit_code == 126
    assert '"rule_id":"FILE-001"' in result.stderr
    assert not any(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_program_runner_allows_safe_command():
    sink = RecordingAuditSink()
    delegate = FakeProgramRunner()
    runner = GuardedProgramRunner(delegate, SafetyGuard(SafetyScanner(), sink), tool_name="SkillRun")

    spec = WorkspaceRunProgramSpec(cmd="echo", args=["hello"], cwd="/tmp/work")
    result = await runner.run_program(
        WorkspaceInfo(id="ws", path="/tmp/work"),
        spec,
    )

    assert result.exit_code == 0
    assert result.stdout == "ok"
    assert delegate.calls == 1
    assert len(sink.events) == 1
    assert delegate.specs[0].timeout == 30
    assert spec.timeout == 0


@pytest.mark.asyncio
async def test_program_runner_enforces_policy_timeout_on_delegate():
    sink = RecordingAuditSink()
    delegate = SlowProgramRunner()
    policy = SafetyPolicy.model_validate({
        "limits": {
            "max_timeout_seconds": 0.01,
        },
    })
    runner = GuardedProgramRunner(
        delegate,
        SafetyGuard(SafetyScanner(policy), sink),
        tool_name="SkillRun",
    )

    result = await runner.run_program(
        WorkspaceInfo(id="ws", path="/tmp/work"),
        WorkspaceRunProgramSpec(cmd="echo", args=["hello"], cwd="/tmp/work"),
    )

    assert result.exit_code == 124
    assert result.timed_out is True
    assert "exceeded the tool safety policy timeout" in result.stderr
    assert "only the runtime or sandbox can guarantee process termination" in result.stderr
    assert delegate.calls == 1
    assert len(sink.events) == 1


@pytest.mark.asyncio
async def test_program_runner_consumes_late_failure_after_timeout():
    delegate = LateFailingProgramRunner()
    policy = SafetyPolicy.model_validate({
        "limits": {
            "max_timeout_seconds": 0.01,
        },
    })
    runner = GuardedProgramRunner(
        delegate,
        SafetyGuard(SafetyScanner(policy), RecordingAuditSink()),
        tool_name="SkillRun",
    )
    loop = asyncio.get_running_loop()
    unhandled: list[dict] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    try:
        result = await runner.run_program(
            WorkspaceInfo(id="ws", path="/tmp/work"),
            WorkspaceRunProgramSpec(cmd="echo", args=["hello"], cwd="/tmp/work"),
        )
        await asyncio.sleep(0.01)
    finally:
        loop.set_exception_handler(previous_handler)

    assert result.exit_code == 124
    assert delegate.calls == 1
    assert unhandled == []


@pytest.mark.asyncio
async def test_program_runner_models_bash_positional_zero_separately_from_argv():
    delegate = FakeProgramRunner()
    runner = GuardedProgramRunner(
        delegate,
        SafetyGuard(SafetyScanner(), RecordingAuditSink()),
    )

    result = await runner.run_program(
        WorkspaceInfo(id="ws", path="/tmp/work"),
        WorkspaceRunProgramSpec(
            cmd="bash",
            args=[
                "-c",
                'cat "$1"',
                "/etc/shadow",
                "/tmp/safe-one",
            ],
            cwd="/tmp/work",
        ),
    )

    assert result.exit_code == 0
    assert delegate.calls == 1


@pytest.mark.asyncio
async def test_program_runner_blocks_sensitive_bash_positional_zero_when_used():
    delegate = FakeProgramRunner()
    runner = GuardedProgramRunner(
        delegate,
        SafetyGuard(SafetyScanner(), RecordingAuditSink()),
    )

    result = await runner.run_program(
        WorkspaceInfo(id="ws", path="/tmp/work"),
        WorkspaceRunProgramSpec(
            cmd="bash",
            args=["-c", 'echo "$0"', "--api-key=super-secret-value"],
            cwd="/tmp/work",
        ),
    )

    assert result.exit_code == 126
    assert '"rule_id":"SECRET-001"' in result.stderr
    assert "super-secret-value" not in result.stderr
    assert delegate.calls == 0


@pytest.mark.asyncio
async def test_program_runner_scans_provider_environment_before_delegate():
    sink = RecordingAuditSink()
    delegate = FakeProgramRunner(
        provider=lambda _ctx: {"PATH": "/tmp/attacker-bin"},
        enable_provider_env=True,
    )
    runner = GuardedProgramRunner(
        delegate,
        SafetyGuard(SafetyScanner(), sink),
        tool_name="SkillRun",
    )

    result = await runner.run_program(
        WorkspaceInfo(id="ws", path="/tmp/work"),
        WorkspaceRunProgramSpec(
            cmd="echo",
            args=["hello"],
            cwd="/tmp/work",
        ),
    )

    assert result.exit_code == 126
    assert '"decision":"needs_human_review"' in result.stderr
    assert delegate.calls == 0
    assert len(sink.events) == 1


@pytest.mark.asyncio
async def test_program_runner_applies_provider_environment_exactly_once():
    calls = 0

    def provider(_ctx):
        nonlocal calls
        calls += 1
        return {} if calls == 1 else {"PATH": "/tmp/attacker-bin"}

    sink = RecordingAuditSink()
    delegate = FakeProgramRunner(
        provider=provider,
        enable_provider_env=True,
    )
    runner = GuardedProgramRunner(
        delegate,
        SafetyGuard(SafetyScanner(), sink),
        tool_name="SkillRun",
    )

    result = await runner.run_program(
        WorkspaceInfo(id="ws", path="/tmp/work"),
        WorkspaceRunProgramSpec(cmd="echo", args=["hello"], cwd="/tmp/work"),
    )

    assert result.exit_code == 0
    assert calls == 1
    assert delegate.calls == 1
    assert "PATH" not in delegate.specs[0].env
    assert len(sink.events) == 1


@pytest.mark.asyncio
async def test_program_runner_does_not_retry_failed_provider_after_scan():
    calls = 0

    def provider(_ctx):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary provider failure")
        return {"PATH": "/tmp/attacker-bin"}

    sink = RecordingAuditSink()
    delegate = FakeProgramRunner(
        provider=provider,
        enable_provider_env=True,
    )
    runner = GuardedProgramRunner(
        delegate,
        SafetyGuard(SafetyScanner(), sink),
        tool_name="SkillRun",
    )

    result = await runner.run_program(
        WorkspaceInfo(id="ws", path="/tmp/work"),
        WorkspaceRunProgramSpec(cmd="echo", args=["hello"], cwd="/tmp/work"),
    )

    assert result.exit_code == 0
    assert calls == 1
    assert delegate.calls == 1
    assert "PATH" not in delegate.specs[0].env
    assert len(sink.events) == 1


@pytest.mark.asyncio
async def test_program_runner_invalid_timeout_is_audited_and_blocked():
    sink = RecordingAuditSink()
    delegate = FakeProgramRunner()
    runner = GuardedProgramRunner(delegate, SafetyGuard(SafetyScanner(), sink), tool_name="SkillRun")

    result = await runner.run_program(
        WorkspaceInfo(id="ws", path="/tmp/work"),
        WorkspaceRunProgramSpec(
            cmd="echo",
            args=["hello"],
            cwd="/tmp/work",
            timeout=-1,
        ),
    )

    assert result.exit_code == 126
    assert '"rule_id":"POLICY-INPUT-001"' in result.stderr
    assert delegate.calls == 0
    assert len(sink.events) == 1


@pytest.mark.asyncio
async def test_program_runner_limits_combined_utf8_output():
    delegate = FakeProgramRunner(WorkspaceRunResult(
        stdout="你好世界",
        stderr="error",
        exit_code=0,
    ))
    policy = SafetyPolicy.model_validate({"limits": {"max_output_size_bytes": 7}})
    runner = GuardedProgramRunner(
        delegate,
        SafetyGuard(SafetyScanner(policy), RecordingAuditSink()),
        tool_name="SkillRun",
    )

    result = await runner.run_program(
        WorkspaceInfo(id="ws", path="/tmp/work"),
        WorkspaceRunProgramSpec(cmd="echo", args=["hello"], cwd="/tmp/work"),
    )

    assert len((result.stdout + result.stderr).encode("utf-8")) <= 7


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
async def test_real_local_code_executor_is_blocked_before_writing_code(tmp_path):
    executor = GuardedCodeExecutor(
        UnsafeLocalCodeExecutor(work_dir=str(tmp_path)),
        SafetyGuard(SafetyScanner(), RecordingAuditSink()),
    )

    result = await executor.execute_code(
        MagicMock(spec=InvocationContext),
        CodeExecutionInput(
            execution_id="blocked",
            code_blocks=[CodeBlock(language="python", code='import shutil\nshutil.rmtree("/")')],
        ),
    )

    assert result.outcome == Outcome.OUTCOME_FAILED
    assert '"rule_id":"FILE-001"' in result.output
    assert not any(tmp_path.iterdir())


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
async def test_code_executor_invalid_metadata_is_audited_and_blocked():
    sink = RecordingAuditSink()
    delegate = FakeCodeExecutor()
    executor = GuardedCodeExecutor(delegate, SafetyGuard(SafetyScanner(), sink))

    result = await executor.execute_code(
        MagicMock(spec=InvocationContext),
        CodeExecutionInput(
            execution_id="x" * 1025,
            code_blocks=[CodeBlock(language="python", code='print("ok")')],
        ),
    )

    assert result.outcome == Outcome.OUTCOME_FAILED
    assert '"rule_id":"POLICY-INPUT-001"' in result.output
    assert delegate.calls == 0
    assert len(sink.events) == 1


@pytest.mark.asyncio
async def test_code_executor_scans_executable_input_files():
    sink = RecordingAuditSink()
    delegate = FakeCodeExecutor()
    executor = GuardedCodeExecutor(delegate, SafetyGuard(SafetyScanner(), sink))

    result = await executor.execute_code(
        MagicMock(spec=InvocationContext),
        CodeExecutionInput(
            code_blocks=[CodeBlock(language="python", code="import payload")],
            input_files=[
                CodeFile(
                    name="payload.py",
                    content='import shutil\nshutil.rmtree("/")',
                    mime_type="text/x-python",
                )
            ],
        ),
    )

    assert result.outcome == Outcome.OUTCOME_FAILED
    assert '"decision":"deny"' in result.output
    assert delegate.calls == 0
    assert len(sink.events) == 1
    assert sink.events[0].blocks_scanned == 2


@pytest.mark.asyncio
async def test_code_executor_blocks_truncated_executable_input_file():
    sink = RecordingAuditSink()
    delegate = FakeCodeExecutor()
    executor = GuardedCodeExecutor(delegate, SafetyGuard(SafetyScanner(), sink))

    result = await executor.execute_code(
        MagicMock(spec=InvocationContext),
        CodeExecutionInput(
            code_blocks=[CodeBlock(language="python", code='print("ok")')],
            input_files=[
                CodeFile(
                    name="payload.py",
                    content='print("partial")',
                    mime_type="text/x-python",
                    truncated=True,
                )
            ],
        ),
    )

    assert result.outcome == Outcome.OUTCOME_FAILED
    assert '"decision":"needs_human_review"' in result.output
    assert delegate.calls == 0
    assert len(sink.events) == 1


@pytest.mark.asyncio
async def test_code_executor_rejects_input_file_path_traversal():
    sink = RecordingAuditSink()
    delegate = FakeCodeExecutor()
    executor = GuardedCodeExecutor(delegate, SafetyGuard(SafetyScanner(), sink))

    result = await executor.execute_code(
        MagicMock(spec=InvocationContext),
        CodeExecutionInput(
            code_blocks=[CodeBlock(language="python", code='print("ok")')],
            input_files=[CodeFile(
                name="../payload.txt",
                content="data",
                mime_type="text/plain",
            )],
        ),
    )

    assert result.outcome == Outcome.OUTCOME_FAILED
    assert '"rule_id":"POLICY-INPUT-001"' in result.output
    assert delegate.calls == 0
    assert len(sink.events) == 1


@pytest.mark.asyncio
async def test_code_executor_preserves_non_executable_input_files():
    sink = RecordingAuditSink()
    delegate = FakeCodeExecutor()
    executor = GuardedCodeExecutor(delegate, SafetyGuard(SafetyScanner(), sink))
    input_file = CodeFile(
        name="records.csv",
        content="name,value\nsafe,1\n",
        mime_type="text/csv",
    )
    execution_input = CodeExecutionInput(
        code_blocks=[CodeBlock(language="python", code='print("ok")')],
        input_files=[input_file],
    )

    result = await executor.execute_code(
        MagicMock(spec=InvocationContext),
        execution_input,
    )

    assert result.outcome == Outcome.OUTCOME_OK
    assert delegate.calls == 1
    assert delegate.last_input is execution_input
    assert delegate.last_input.input_files == [input_file]
    assert len(sink.events) == 1
    assert sink.events[0].blocks_scanned == 1


@pytest.mark.asyncio
async def test_code_executor_aggregates_multiple_blocks_into_one_audit():
    sink = RecordingAuditSink()
    delegate = FakeCodeExecutor()
    executor = GuardedCodeExecutor(delegate, SafetyGuard(SafetyScanner(), sink))

    result = await executor.execute_code(
        MagicMock(spec=InvocationContext),
        CodeExecutionInput(code_blocks=[
            CodeBlock(language="python", code='print("ok")'),
            CodeBlock(language="bash", code="rm -rf /"),
        ]),
    )

    assert result.outcome == Outcome.OUTCOME_FAILED
    assert delegate.calls == 0
    assert len(sink.events) == 1
    assert sink.events[0].blocks_scanned == 2


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


@pytest.mark.asyncio
async def test_code_executor_aggregates_supported_and_unknown_language_blocks():
    sink = RecordingAuditSink()
    delegate = FakeCodeExecutor()
    executor = GuardedCodeExecutor(delegate, SafetyGuard(SafetyScanner(), sink))

    result = await executor.execute_code(
        MagicMock(spec=InvocationContext),
        CodeExecutionInput(code_blocks=[
            CodeBlock(language="python", code='import shutil\nshutil.rmtree("/")'),
            CodeBlock(language="ruby", code='puts "ok"'),
        ]),
    )

    assert result.outcome == Outcome.OUTCOME_FAILED
    assert '"decision":"deny"' in result.output
    assert delegate.calls == 0
    assert len(sink.events) == 1
    assert sink.events[0].blocks_scanned == 2
    assert {"FILE-001", "PARSE-001"} <= set(sink.events[0].rule_ids)


@pytest.mark.asyncio
async def test_code_executor_enforces_policy_timeout_on_delegate():
    sink = RecordingAuditSink()
    delegate = SlowCodeExecutor()
    policy = SafetyPolicy.model_validate({
        "limits": {
            "max_timeout_seconds": 0.01,
        },
    })
    executor = GuardedCodeExecutor(
        delegate,
        SafetyGuard(SafetyScanner(policy), sink),
    )

    result = await executor.execute_code(
        MagicMock(spec=InvocationContext),
        CodeExecutionInput(code_blocks=[CodeBlock(language="python", code='print("ok")')], ),
    )

    assert result.outcome == Outcome.OUTCOME_FAILED
    assert "exceeded the tool safety policy timeout" in result.output
    assert "only the runtime or sandbox can guarantee process termination" in result.output
    assert delegate.calls == 1
    assert len(sink.events) == 1


@pytest.mark.asyncio
async def test_code_executor_rejects_success_after_cancellation_is_suppressed():
    sink = RecordingAuditSink()
    delegate = CancellationSuppressingCodeExecutor()
    policy = SafetyPolicy.model_validate({
        "limits": {
            "max_timeout_seconds": 0.01,
        },
    })
    executor = GuardedCodeExecutor(
        delegate,
        SafetyGuard(SafetyScanner(policy), sink),
    )

    started = asyncio.get_running_loop().time()
    result = await executor.execute_code(
        MagicMock(spec=InvocationContext),
        CodeExecutionInput(code_blocks=[CodeBlock(language="python", code='print("ok")')], ),
    )
    duration = asyncio.get_running_loop().time() - started

    assert result.outcome == Outcome.OUTCOME_FAILED
    assert "exceeded the tool safety policy timeout" in result.output
    assert duration < 0.5
    assert delegate.calls == 1
    assert len(sink.events) == 1
