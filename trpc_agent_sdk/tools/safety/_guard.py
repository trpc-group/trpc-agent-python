# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Execution wrappers that apply safety scans before delegating."""

from __future__ import annotations

import hashlib
import shlex
from typing import Any

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
from trpc_agent_sdk.types import Outcome

from ._audit import AuditSink
from ._audit import LoggerAuditSink
from ._audit import record_safety_telemetry
from ._models import SafetyAuditEvent
from ._models import SafetyDecision
from ._models import RiskLevel
from ._models import SafetyReport
from ._models import SafetyScanRequest
from ._models import ScriptLanguage
from ._scanner import SafetyScanner
from ._rules import make_finding

_BLOCKED_EXIT_CODE = 126


def _truncate_text(value: str, max_bytes: int) -> tuple[str, bool]:
    raw = value.encode("utf-8")
    if len(raw) <= max_bytes:
        return value, False
    return raw[:max_bytes].decode("utf-8", errors="ignore"), True


class SafetyGuard:
    """Shared scan, audit, and telemetry facade for execution wrappers."""

    def __init__(
        self,
        scanner: SafetyScanner,
        audit_sink: AuditSink | None = None,
    ):
        self.scanner = scanner
        self.audit_sink = audit_sink if audit_sink is not None else LoggerAuditSink()

    def check(self, request: SafetyScanRequest, *, tool_name: str | None = None) -> SafetyReport:
        """Scan and record exactly one pre-execution decision."""

        resolved_name = (tool_name or request.tool_name or "").strip()
        if not resolved_name:
            raise ValueError("tool_name is required when a safety guard is attached to execution")
        if request.tool_name != resolved_name:
            request = request.model_copy(update={"tool_name": resolved_name})
        report = self.scanner.scan(request)
        self._record(resolved_name, report)
        return report

    def unsupported_language(self, content: str, language: str, *, tool_name: str) -> SafetyReport:
        """Fail closed and audit a code block outside the supported languages."""

        finding = make_finding(
            "PARSE-001",
            self.scanner.policy,
            f"unsupported language: {language!r}",
        )
        findings = [finding] if finding is not None else []
        report = SafetyReport(
            decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
            risk_level=RiskLevel.MEDIUM,
            findings=findings,
            duration_ms=0,
            sanitized=True,
            policy_version=self.scanner.policy.version,
            input_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )
        self._record(tool_name, report)
        return report

    def _record(self, tool_name: str, report: SafetyReport) -> None:
        event = SafetyAuditEvent.from_report(tool_name, report)
        self.audit_sink.emit(event)
        record_safety_telemetry(report)


class GuardedProgramRunner(BaseProgramRunner):
    """Protect a workspace program runner without changing its interface."""

    def __init__(
        self,
        runner: BaseProgramRunner,
        guard: SafetyGuard,
        *,
        tool_name: str = "Skill",
    ):
        super().__init__()
        if not tool_name.strip():
            raise ValueError("tool_name cannot be blank")
        self._runner = runner
        self._guard = guard
        self._tool_name = tool_name

    @override
    async def run_program(
        self,
        ws: WorkspaceInfo,
        spec: WorkspaceRunProgramSpec,
        ctx: InvocationContext | None = None,
    ) -> WorkspaceRunResult:
        content, argv = self._program_content(spec)
        report = self._guard.check(
            SafetyScanRequest(
                content=content,
                language=ScriptLanguage.BASH,
                argv=argv,
                cwd=spec.cwd or ws.path,
                env=spec.env,
                timeout_seconds=spec.timeout or None,
                tool_name=self._tool_name,
                metadata={"workspace_id": ws.id},
            ))
        if report.decision != SafetyDecision.ALLOW:
            return WorkspaceRunResult(
                stderr=report.model_dump_json(),
                exit_code=_BLOCKED_EXIT_CODE,
                duration=report.duration_ms / 1000,
            )
        result = await self._runner.run_program(ws, spec, ctx)
        limit = self._guard.scanner.policy.limits.max_output_size_bytes
        stdout, _ = _truncate_text(result.stdout, limit)
        stderr, _ = _truncate_text(result.stderr, limit)
        if stdout == result.stdout and stderr == result.stderr:
            return result
        return result.model_copy(update={"stdout": stdout, "stderr": stderr})

    @staticmethod
    def _program_content(spec: WorkspaceRunProgramSpec) -> tuple[str, list[str]]:
        command = spec.cmd.rsplit("/", 1)[-1]
        if command in {"bash", "sh", "zsh"} and len(spec.args) >= 2 and spec.args[0] in {"-c", "-lc"}:
            return spec.args[1], spec.args[2:]
        return shlex.quote(spec.cmd), spec.args


class GuardedCodeExecutor(BaseCodeExecutor):
    """Protect an existing code executor and preserve its public contract."""

    executor: BaseCodeExecutor
    guard: SafetyGuard
    tool_name: str = "CodeExecutor"
    model_config = {"arbitrary_types_allowed": True}

    def __init__(
        self,
        executor: BaseCodeExecutor,
        guard: SafetyGuard,
        *,
        tool_name: str = "CodeExecutor",
        **data: Any,
    ):
        if not tool_name.strip():
            raise ValueError("tool_name cannot be blank")
        inherited = {
            "optimize_data_file": executor.optimize_data_file,
            "stateful": executor.stateful,
            "error_retry_attempts": executor.error_retry_attempts,
            "execute_once_per_invocation": executor.execute_once_per_invocation,
            "code_block_delimiters": list(executor.code_block_delimiters),
            "execution_result_delimiters": list(executor.execution_result_delimiters),
            "workspace_runtime": executor.workspace_runtime,
            "ignore_codes": list(executor.ignore_codes),
        }
        inherited.update(data)
        super().__init__(
            executor=executor,
            guard=guard,
            tool_name=tool_name,
            **inherited,
        )

    @override
    async def execute_code(
        self,
        invocation_context: InvocationContext,
        code_execution_input: CodeExecutionInput,
    ) -> CodeExecutionResult:
        blocks = list(code_execution_input.code_blocks)
        if not blocks and code_execution_input.code:
            blocks = [CodeBlock(language="python", code=code_execution_input.code)]

        for block in blocks:
            language = self._language(block.language)
            if language is None:
                report = self.guard.unsupported_language(
                    block.code,
                    block.language,
                    tool_name=self.tool_name,
                )
                return CodeExecutionResult(
                    outcome=Outcome.OUTCOME_FAILED,
                    output=report.model_dump_json(),
                )
            report = self.guard.check(
                SafetyScanRequest(
                    content=block.code,
                    language=language,
                    tool_name=self.tool_name,
                    metadata={"execution_id": code_execution_input.execution_id or ""},
                ))
            if report.decision != SafetyDecision.ALLOW:
                return CodeExecutionResult(
                    outcome=Outcome.OUTCOME_FAILED,
                    output=report.model_dump_json(),
                )

        result = await self.executor.execute_code(invocation_context, code_execution_input)
        limit = self.guard.scanner.policy.limits.max_output_size_bytes
        output, _ = _truncate_text(result.output or "", limit)
        if output == result.output:
            return result
        return result.model_copy(update={"output": output})

    @staticmethod
    def _language(value: str) -> ScriptLanguage | None:
        normalized = value.strip().lower()
        if normalized in {"python", "python3", "py", ""}:
            return ScriptLanguage.PYTHON
        if normalized in {"bash", "sh", "shell", "zsh"}:
            return ScriptLanguage.BASH
        return None
