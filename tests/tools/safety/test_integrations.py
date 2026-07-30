#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

import json
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import Field

from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import CodeBlock
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import create_code_execution_result
from trpc_agent_sdk.tools.safety import MemoryAuditSink
from trpc_agent_sdk.tools.safety import SafetyDecision
from trpc_agent_sdk.tools.safety import SafetyGuardedCodeExecutor
from trpc_agent_sdk.tools.safety import ScriptLanguage
from trpc_agent_sdk.tools.safety import ScriptPayload
from trpc_agent_sdk.tools.safety import ScriptScanRequest
from trpc_agent_sdk.tools.safety import ToolMetadata
from trpc_agent_sdk.tools.safety import ToolSafetyGuard
from trpc_agent_sdk.tools.safety import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety import ToolScriptSafetyFilter
from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner


class RecordingExecutor(BaseCodeExecutor):
    calls: int = 0
    output: str = "delegate output"
    timeout: float = 30
    environment: dict[str, str] = Field(default_factory=dict)

    async def execute_code(self, invocation_context, code_execution_input):
        del invocation_context, code_execution_input
        self.calls += 1
        return create_code_execution_result(stdout=self.output)


class FailingAuditSink:

    def emit(self, event):
        del event
        raise OSError("secret path and details must not escape")


class ExplodingScanner(ToolScriptSafetyScanner):

    def scan(self, request):
        del request
        raise RuntimeError("raw secret scanner details")


def test_filter_is_available_from_the_tool_filter_registry():
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            ("from trpc_agent_sdk.filter import get_tool_filter;"
             "from trpc_agent_sdk.tools.safety import ToolScriptSafetyFilter;"
             "assert isinstance(get_tool_filter('tool_script_safety'), ToolScriptSafetyFilter)"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert process.returncode == 0, process.stderr


async def _run_filter(filter_, args):
    calls = 0

    async def handle():
        nonlocal calls
        calls += 1
        return {"ok": True}

    result = await filter_.run(MagicMock(), args, handle)
    return result, calls


@pytest.mark.asyncio
async def test_filter_allows_safe_script_and_writes_one_audit_event():
    audit = MemoryAuditSink()
    filter_ = ToolScriptSafetyFilter(guard=ToolSafetyGuard(audit_sink=audit))

    result, calls = await _run_filter(filter_, {"script": "echo hello", "language": "bash"})

    assert calls == 1
    assert result.rsp == {"ok": True}
    assert len(audit.events) == 1
    assert audit.events[0].decision == SafetyDecision.ALLOW
    assert audit.events[0].execution_blocked is False


@pytest.mark.asyncio
async def test_filter_ignores_non_script_tool_calls():
    filter_ = ToolScriptSafetyFilter()

    result, calls = await _run_filter(filter_, {"query": "hello"})

    assert calls == 1
    assert result.rsp == {"ok": True}


@pytest.mark.asyncio
async def test_filter_blocks_review_without_approver_and_ignores_spoofed_approval():
    audit = MemoryAuditSink()
    filter_ = ToolScriptSafetyFilter(guard=ToolSafetyGuard(audit_sink=audit))

    result, calls = await _run_filter(
        filter_,
        {
            "script": "printf ok | tee output.txt",
            "language": "bash",
            "human_approved": True,
            "metadata": {
                "human_approved": True
            },
        },
    )

    assert calls == 0
    assert result.is_continue is False
    assert result.rsp["error"] == "TOOL_SAFETY_REVIEW_REQUIRED"
    assert result.rsp["review_required"] is True
    assert audit.events[0].execution_blocked is True


@pytest.mark.asyncio
async def test_filter_blocks_deny_before_handler():
    filter_ = ToolScriptSafetyFilter()

    result, calls = await _run_filter(filter_, {"command": "rm -rf /tmp/data"})

    assert calls == 0
    assert result.rsp["error"] == "TOOL_SAFETY_BLOCKED"
    assert result.rsp["safety_report"]["decision"] == "deny"


@pytest.mark.asyncio
async def test_filter_scans_every_script_field_and_cannot_be_hidden_by_a_safe_field():
    filter_ = ToolScriptSafetyFilter()

    result, calls = await _run_filter(
        filter_,
        {
            "script": "echo safe",
            "command": "rm -rf /tmp/data",
        },
    )

    assert calls == 0
    assert result.rsp["safety_report"]["decision"] == "deny"
    assert "FILE_RECURSIVE_DELETE" in result.rsp["safety_report"]["rule_ids"]


@pytest.mark.asyncio
async def test_filter_scans_command_arguments():
    filter_ = ToolScriptSafetyFilter()

    result, calls = await _run_filter(
        filter_,
        {
            "command": "rm",
            "command_args": ["--recursive", "--force", "/tmp/data"],
        },
    )

    assert calls == 0
    assert result.rsp["safety_report"]["decision"] == "deny"
    assert "FILE_RECURSIVE_DELETE" in result.rsp["safety_report"]["rule_ids"]


@pytest.mark.asyncio
async def test_audit_failure_fails_closed():
    filter_ = ToolScriptSafetyFilter(guard=ToolSafetyGuard(audit_sink=FailingAuditSink()))

    result, calls = await _run_filter(filter_, {"script": "echo hello", "language": "bash"})

    assert calls == 0
    serialized = json.dumps(result.rsp)
    assert "secret path" not in serialized
    assert "AUDIT_WRITE_ERROR" in serialized


@pytest.mark.asyncio
async def test_scanner_failure_fails_closed_without_exception_details():
    guard = ToolSafetyGuard(scanner=ExplodingScanner(), audit_sink=MemoryAuditSink())
    filter_ = ToolScriptSafetyFilter(guard=guard)

    result, calls = await _run_filter(filter_, {"script": "echo hello", "language": "bash"})

    assert calls == 0
    serialized = json.dumps(result.rsp)
    assert "raw secret" not in serialized
    assert "SCAN_INTERNAL_ERROR" in serialized


@pytest.mark.asyncio
async def test_bad_script_argument_shape_fails_closed():
    audit = MemoryAuditSink()
    filter_ = ToolScriptSafetyFilter(guard=ToolSafetyGuard(audit_sink=audit))

    result, calls = await _run_filter(filter_, {"script": ["echo", "hello"]})

    assert calls == 0
    assert result.rsp["safety_report"]["decision"] == "deny"
    assert len(audit.events) == 1
    assert audit.events[0].execution_blocked is True


@pytest.mark.asyncio
async def test_code_executor_scans_all_blocks_and_blocks_delegate_once():
    delegate = RecordingExecutor()
    audit = MemoryAuditSink()
    wrapper = SafetyGuardedCodeExecutor(delegate=delegate, guard=ToolSafetyGuard(audit_sink=audit))

    result = await wrapper.execute_code(
        MagicMock(),
        CodeExecutionInput(code_blocks=[
            CodeBlock(language="python", code="print('safe')"),
            CodeBlock(language="bash", code="rm -rf /tmp/data"),
        ]),
    )

    assert delegate.calls == 0
    assert result.outcome.name == "OUTCOME_FAILED"
    assert "TOOL_SAFETY_BLOCKED" in result.output
    assert len(audit.events) == 1


@pytest.mark.asyncio
async def test_code_executor_scans_legacy_code_and_preserves_small_output():
    delegate = RecordingExecutor(output="short output")
    wrapper = SafetyGuardedCodeExecutor(
        delegate=delegate,
        guard=ToolSafetyGuard(audit_sink=MemoryAuditSink()),
    )

    result = await wrapper.execute_code(
        MagicMock(),
        CodeExecutionInput(code="print('safe')"),
    )

    assert delegate.calls == 1
    assert "short output" in result.output
    assert "truncated" not in result.output


@pytest.mark.asyncio
async def test_code_executor_delegates_inputs_without_executable_code():
    delegate = RecordingExecutor()
    wrapper = SafetyGuardedCodeExecutor(
        delegate=delegate,
        guard=ToolSafetyGuard(audit_sink=MemoryAuditSink()),
    )

    result = await wrapper.execute_code(MagicMock(), CodeExecutionInput())

    assert delegate.calls == 1
    assert "delegate output" in result.output


@pytest.mark.asyncio
async def test_code_executor_uses_nested_runtime_timeout():
    delegate = RecordingExecutor(timeout=0)
    object.__setattr__(delegate, "_cfg", SimpleNamespace(execute_timeout=25))
    wrapper = SafetyGuardedCodeExecutor(
        delegate=delegate,
        guard=ToolSafetyGuard(audit_sink=MemoryAuditSink()),
    )

    result = await wrapper.execute_code(
        MagicMock(),
        CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="print('safe')")]),
    )

    assert delegate.calls == 1
    assert result.outcome.name == "OUTCOME_OK"


@pytest.mark.asyncio
async def test_code_executor_accepts_delegate_without_environment():
    delegate = RecordingExecutor()
    object.__setattr__(delegate, "environment", None)
    wrapper = SafetyGuardedCodeExecutor(
        delegate=delegate,
        guard=ToolSafetyGuard(audit_sink=MemoryAuditSink()),
    )

    result = await wrapper.execute_code(
        MagicMock(),
        CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="print('safe')")]),
    )

    assert delegate.calls == 1
    assert result.outcome.name == "OUTCOME_OK"


@pytest.mark.asyncio
async def test_code_executor_rejects_invalid_delegate_environment():
    delegate = RecordingExecutor()
    object.__setattr__(delegate, "environment", {"TOKEN": 7})
    wrapper = SafetyGuardedCodeExecutor(
        delegate=delegate,
        guard=ToolSafetyGuard(audit_sink=MemoryAuditSink()),
    )

    result = await wrapper.execute_code(
        MagicMock(),
        CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="print('safe')")]),
    )

    assert delegate.calls == 0
    assert "SCAN_INPUT_ERROR" in result.output


@pytest.mark.asyncio
async def test_code_executor_fails_closed_for_unsupported_language():
    delegate = RecordingExecutor()
    audit = MemoryAuditSink()
    wrapper = SafetyGuardedCodeExecutor(delegate=delegate, guard=ToolSafetyGuard(audit_sink=audit))

    result = await wrapper.execute_code(
        MagicMock(),
        CodeExecutionInput(code_blocks=[CodeBlock(language="ruby", code="puts 'hello'")]),
    )

    assert delegate.calls == 0
    assert "SCAN_INPUT_ERROR" in result.output
    assert len(audit.events) == 1
    assert audit.events[0].execution_blocked is True


@pytest.mark.asyncio
async def test_code_executor_allows_safe_code_and_caps_output():
    delegate = RecordingExecutor(output="x" * 200)
    scanner = ToolScriptSafetyScanner()
    scanner.policy.max_output_bytes = 80
    wrapper = SafetyGuardedCodeExecutor(
        delegate=delegate,
        guard=ToolSafetyGuard(scanner=scanner, audit_sink=MemoryAuditSink()),
    )

    result = await wrapper.execute_code(
        MagicMock(),
        CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="print('safe')")]),
    )

    assert delegate.calls == 1
    assert len(result.output.encode("utf-8")) <= 80
    assert "truncated" in result.output


@pytest.mark.asyncio
async def test_code_executor_requires_a_positive_delegate_timeout():
    delegate = RecordingExecutor(timeout=0)
    wrapper = SafetyGuardedCodeExecutor(
        delegate=delegate,
        guard=ToolSafetyGuard(audit_sink=MemoryAuditSink()),
    )

    result = await wrapper.execute_code(
        MagicMock(),
        CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="print('safe')")]),
    )

    assert delegate.calls == 0
    assert "RESOURCE_TIMEOUT_REQUIRED" in result.output


@pytest.mark.asyncio
async def test_code_executor_scans_and_redacts_delegate_environment():
    secret = "abc"
    delegate = RecordingExecutor(environment={"API_TOKEN": secret})
    scanner = ToolScriptSafetyScanner(ToolSafetyPolicy(allowed_domains=["api.example.com"]))
    wrapper = SafetyGuardedCodeExecutor(
        delegate=delegate,
        guard=ToolSafetyGuard(scanner=scanner, audit_sink=MemoryAuditSink()),
    )

    result = await wrapper.execute_code(
        MagicMock(),
        CodeExecutionInput(code_blocks=[
            CodeBlock(
                language="python",
                code="import requests\nrequests.post('https://api.example.com', data='abc')",
            )
        ]),
    )

    assert delegate.calls == 0
    assert "SENSITIVE_EXFILTRATION" in result.output
    assert secret not in result.output
    assert "[REDACTED]" in result.output


@pytest.mark.asyncio
async def test_code_executor_caps_output_smaller_than_truncation_marker():
    delegate = RecordingExecutor(output="你好" * 20)
    scanner = ToolScriptSafetyScanner()
    scanner.policy.max_output_bytes = 8
    wrapper = SafetyGuardedCodeExecutor(
        delegate=delegate,
        guard=ToolSafetyGuard(scanner=scanner, audit_sink=MemoryAuditSink()),
    )

    result = await wrapper.execute_code(
        MagicMock(),
        CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="print('safe')")]),
    )

    assert len(result.output.encode("utf-8")) <= 8


def test_audit_events_never_contain_script_or_environment_values():
    secret = "super-sensitive-value"
    audit = MemoryAuditSink()
    guard = ToolSafetyGuard(audit_sink=audit)
    request = ScriptScanRequest(
        payloads=[ScriptPayload(language=ScriptLanguage.BASH, content=f"echo token={secret}")],
        env={"API_TOKEN": secret},
        metadata=ToolMetadata(name="bash"),
    )

    guard.check(request)

    assert secret not in audit.events[0].model_dump_json()
    assert "echo token" not in audit.events[0].model_dump_json()
