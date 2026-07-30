# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Execution wrappers that apply safety scans before delegating."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import PurePosixPath
import shlex
import time
from typing import Any
import uuid

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
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.types import Outcome

from ._audit import AuditSink
from ._audit import LoggerAuditSink
from ._audit import record_safety_telemetry
from ._bash_analyzer import extract_shell_command
from ._decision import aggregate_report
from ._models import AnalysisStatus
from ._models import SafetyAuditEvent
from ._models import SafetyDecision
from ._models import SafetyReport
from ._models import SafetyScanRequest
from ._models import ScriptLanguage
from ._policy import SafetyPolicy
from ._scanner import SafetyScanner
from ._rules import make_finding

_BLOCKED_EXIT_CODE = 126
_TIMED_OUT_EXIT_CODE = 124


def _consume_late_execution(task: asyncio.Future[Any]) -> None:
    """Retrieve a late result after cooperative cancellation."""

    try:
        task.exception()
    except asyncio.CancelledError:
        pass
    except Exception as ex:  # pylint: disable=broad-except
        logger.error("cancelled guarded execution failed late: %s", type(ex).__name__)


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
        self.policy = getattr(scanner, "policy", SafetyPolicy())
        self.audit_sink = audit_sink if audit_sink is not None else LoggerAuditSink()

    def check(self, request: SafetyScanRequest, *, tool_name: str | None = None) -> SafetyReport:
        """Scan and record exactly one pre-execution decision."""

        return self.check_invocation([request], tool_name=tool_name)

    async def check_async(
        self,
        request: SafetyScanRequest,
        *,
        tool_name: str | None = None,
    ) -> SafetyReport:
        """Scan synchronously and emit audit IO without blocking the event loop."""

        return await self.check_invocation_async([request], tool_name=tool_name)

    def check_invocation(
        self,
        requests: list[SafetyScanRequest],
        *,
        tool_name: str | None = None,
        invocation_id: str | None = None,
        block_indices: list[int] | None = None,
        unsupported_blocks: list[tuple[int, str, str]] | None = None,
    ) -> SafetyReport:
        """Scan all blocks and emit one aggregate audit and telemetry record."""

        resolved_name, report = self._scan_invocation(
            requests,
            tool_name=tool_name,
            invocation_id=invocation_id,
            block_indices=block_indices,
            unsupported_blocks=unsupported_blocks,
        )
        self._record(resolved_name, report)
        return report

    def _scan_invocation(
        self,
        requests: list[SafetyScanRequest],
        *,
        tool_name: str | None = None,
        invocation_id: str | None = None,
        block_indices: list[int] | None = None,
        unsupported_blocks: list[tuple[int, str, str]] | None = None,
    ) -> tuple[str, SafetyReport]:
        """Build one aggregate report without emitting its audit event."""

        unsupported_blocks = unsupported_blocks or []
        block_indices = list(range(len(requests))) if block_indices is None else block_indices
        if len(block_indices) != len(requests):
            raise ValueError("block_indices must match the number of scan requests")
        all_indices = [*block_indices, *(item[0] for item in unsupported_blocks)]
        if any(index < 0 for index in all_indices) or len(all_indices) != len(set(all_indices)):
            raise ValueError("block indices must be unique non-negative integers")
        if not requests and not unsupported_blocks:
            resolved_name = (tool_name or "").strip()
            if not resolved_name:
                raise ValueError("tool_name is required when a safety guard is attached to execution")
            return resolved_name, self._single_status_report(
                rule_id="POLICY-INPUT-001",
                evidence="tool arguments do not match the safety input contract",
                status=AnalysisStatus.UNSUPPORTED,
            )
        started = time.perf_counter()
        invocation_id = invocation_id or uuid.uuid4().hex
        resolved_name = (tool_name or (requests[0].tool_name if requests else None) or "").strip()
        if not resolved_name:
            raise ValueError("tool_name is required when a safety guard is attached to execution")
        findings = []
        statuses: list[AnalysisStatus] = [AnalysisStatus.UNSUPPORTED for _ in unsupported_blocks]
        digest = self._invocation_digest(requests, unsupported_blocks)
        for block_index, _content, _language in unsupported_blocks:
            finding = make_finding(
                "PARSE-001",
                self.policy,
                "unsupported code language",
                block_id=f"block-{block_index}",
            )
            if finding is not None:
                findings.append(finding)
        for block_index, original in zip(block_indices, requests):
            request = original
            if request.tool_name != resolved_name:
                request = request.model_copy(update={"tool_name": resolved_name})
            try:
                report = self.scanner.scan(request)
            except Exception as ex:  # pylint: disable=broad-except
                logger.error("tool safety scanner failed: %s", type(ex).__name__)
                finding = make_finding(
                    "PARSE-001",
                    self.policy,
                    "internal analyzer failure",
                    block_id=f"block-{block_index}",
                )
                if finding is not None:
                    findings.append(finding)
                statuses.append(AnalysisStatus.INTERNAL_ERROR)
                continue
            statuses.append(report.analysis_status)
            findings.extend(
                finding.model_copy(update={"block_id": f"block-{block_index}"}) for finding in report.findings)
        status = self._aggregate_status(statuses)
        report = aggregate_report(
            findings,
            started=started,
            digest=digest,
            policy=self.policy,
            analysis_status=status,
            invocation_id=invocation_id,
            blocks_scanned=len(requests) + len(unsupported_blocks),
        )
        return resolved_name, report

    async def check_invocation_async(
        self,
        requests: list[SafetyScanRequest],
        *,
        tool_name: str | None = None,
        invocation_id: str | None = None,
        block_indices: list[int] | None = None,
        unsupported_blocks: list[tuple[int, str, str]] | None = None,
    ) -> SafetyReport:
        """Scan an invocation and emit audit IO without blocking the event loop."""

        resolved_name, report = self._scan_invocation(
            requests,
            tool_name=tool_name,
            invocation_id=invocation_id,
            block_indices=block_indices,
            unsupported_blocks=unsupported_blocks,
        )
        await self._record_async(resolved_name, report)
        return report

    @staticmethod
    def _aggregate_status(statuses: list[AnalysisStatus]) -> AnalysisStatus:
        priority = {
            AnalysisStatus.COMPLETE: 0,
            AnalysisStatus.UNSUPPORTED: 1,
            AnalysisStatus.PARSE_ERROR: 2,
            AnalysisStatus.BUDGET_EXCEEDED: 3,
            AnalysisStatus.INTERNAL_ERROR: 4,
        }
        return max(statuses, key=priority.__getitem__, default=AnalysisStatus.INTERNAL_ERROR)

    @staticmethod
    def _invocation_digest(
        requests: list[SafetyScanRequest],
        unsupported_blocks: list[tuple[int, str, str]] | None = None,
    ) -> str:
        digest = hashlib.sha256()
        for request in requests:
            for value in (request.language.value, request.content):
                raw = value.encode("utf-8")
                digest.update(len(raw).to_bytes(8, "big"))
                digest.update(raw)
        for block_index, content, language in unsupported_blocks or []:
            for value in (str(block_index), language, content):
                raw = value.encode("utf-8")
                digest.update(len(raw).to_bytes(8, "big"))
                digest.update(raw)
        return digest.hexdigest()

    def _single_status_report(
        self,
        *,
        rule_id: str,
        evidence: str,
        status: AnalysisStatus,
    ) -> SafetyReport:
        started = time.perf_counter()
        digest = hashlib.sha256(b"").hexdigest()
        finding = make_finding(rule_id, self.policy, evidence)
        report = aggregate_report(
            [finding] if finding is not None else [],
            started=started,
            digest=digest,
            policy=self.policy,
            analysis_status=status,
            invocation_id=uuid.uuid4().hex,
        )
        return report

    def unsupported_language(self, content: str, language: str, *, tool_name: str) -> SafetyReport:
        """Fail closed and audit a code block outside the supported languages."""

        del content, language
        report = self._single_status_report(
            rule_id="PARSE-001",
            evidence="unsupported code language",
            status=AnalysisStatus.UNSUPPORTED,
        )
        self._record(tool_name, report)
        return report

    def invalid_request(self, reason: str, *, tool_name: str) -> SafetyReport:
        """Return and audit a fail-closed report for an invalid adapter input."""

        del reason
        report = self._single_status_report(
            rule_id="POLICY-INPUT-001",
            evidence="tool arguments do not match the safety input contract",
            status=AnalysisStatus.UNSUPPORTED,
        )
        self._record(tool_name, report)
        return report

    async def invalid_request_async(self, reason: str, *, tool_name: str) -> SafetyReport:
        """Audit an invalid asynchronous adapter request off the event loop."""

        del reason
        report = self._single_status_report(
            rule_id="POLICY-INPUT-001",
            evidence="tool arguments do not match the safety input contract",
            status=AnalysisStatus.UNSUPPORTED,
        )
        await self._record_async(tool_name, report)
        return report

    def _record(self, tool_name: str, report: SafetyReport) -> None:
        event = SafetyAuditEvent.from_report(tool_name, report)
        try:
            self.audit_sink.emit(event)
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("failed to emit tool safety audit event: %s", type(ex).__name__)
        record_safety_telemetry(report)

    async def _record_async(self, tool_name: str, report: SafetyReport) -> None:
        event = SafetyAuditEvent.from_report(tool_name, report)
        try:
            await asyncio.to_thread(self.audit_sink.emit, event)
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("failed to emit tool safety audit event: %s", type(ex).__name__)
        finally:
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
        effective_timeout = spec.timeout or self._guard.policy.limits.max_timeout_seconds
        effective_spec = spec.model_copy(update={"timeout": effective_timeout})
        apply_provider_env = getattr(
            self._runner,
            "_apply_provider_env",
            None,
        )
        if callable(apply_provider_env):
            effective_spec = apply_provider_env(effective_spec, ctx)
        content, argv, positional_zero = self._program_content(effective_spec)
        metadata = {"workspace_id": ws.id}
        if positional_zero is not None:
            metadata["bash_positional_zero"] = positional_zero
            metadata["bash_positional_arguments"] = "true"
        try:
            request = SafetyScanRequest(
                content=content,
                language=ScriptLanguage.BASH,
                argv=argv,
                cwd=effective_spec.cwd or ws.path,
                env=effective_spec.env,
                timeout_seconds=effective_timeout,
                tool_name=self._tool_name,
                metadata=metadata,
            )
        except (TypeError, ValueError):
            report = await self._guard.invalid_request_async(
                "invalid program runner request",
                tool_name=self._tool_name,
            )
        else:
            report = await self._guard.check_async(request)
        if report.decision != SafetyDecision.ALLOW:
            return WorkspaceRunResult(
                stderr=report.model_dump_json(),
                exit_code=_BLOCKED_EXIT_CODE,
                duration=report.duration_ms / 1000,
            )
        task = asyncio.create_task(self._runner.run_program(ws, effective_spec, ctx))
        try:
            done, _ = await asyncio.wait(
                {task},
                timeout=effective_timeout,
            )
        except asyncio.CancelledError:
            task.cancel()
            task.add_done_callback(_consume_late_execution)
            raise
        if task not in done:
            task.cancel()
            task.add_done_callback(_consume_late_execution)
            return WorkspaceRunResult(
                stderr=("program execution exceeded the tool safety policy timeout; "
                        "cancellation was requested, but only the runtime or sandbox "
                        "can guarantee process termination"),
                exit_code=_TIMED_OUT_EXIT_CODE,
                duration=effective_timeout,
                timed_out=True,
            )
        result = task.result()
        limit = self._guard.policy.limits.max_output_size_bytes
        stdout, _ = _truncate_text(result.stdout, limit)
        remaining = max(0, limit - len(stdout.encode("utf-8")))
        stderr, _ = _truncate_text(result.stderr, remaining)
        if stdout == result.stdout and stderr == result.stderr:
            return result
        return result.model_copy(update={"stdout": stdout, "stderr": stderr})

    @staticmethod
    def _program_content(spec: WorkspaceRunProgramSpec) -> tuple[str, list[str], str | None]:
        command = spec.cmd.rsplit("/", 1)[-1]
        if command in {"bash", "sh", "zsh"}:
            invocation = extract_shell_command(
                spec.args,
                default_positional_zero=command,
            )
            if invocation is not None:
                return invocation.content, list(invocation.argv), invocation.positional_zero
        return shlex.quote(spec.cmd), spec.args, None


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

        requests: list[SafetyScanRequest] = []
        block_indices: list[int] = []
        unsupported_blocks: list[tuple[int, str, str]] = []
        try:
            if len(blocks) + len(code_execution_input.input_files) > 256:
                raise ValueError("code execution input exceeds the block limit")
            for index, block in enumerate(blocks):
                language = self._language(block.language)
                if language is None:
                    unsupported_blocks.append((index, block.code, block.language))
                    continue
                requests.append(
                    SafetyScanRequest(
                        content=block.code,
                        language=language,
                        timeout_seconds=self.guard.policy.limits.max_timeout_seconds,
                        tool_name=self.tool_name,
                        metadata={"execution_id": code_execution_input.execution_id or ""},
                    ))
                block_indices.append(index)

            next_index = len(blocks)
            for offset, input_file in enumerate(code_execution_input.input_files):
                name = input_file.name.strip()
                normalized_name = PurePosixPath(name.replace("\\", "/"))
                if (not name or len(name) > 4096 or normalized_name.is_absolute() or ".." in normalized_name.parts):
                    raise ValueError("input file name is outside the execution workspace")
                language = self._input_file_language(name, input_file.mime_type)
                if language is None:
                    continue
                block_index = next_index + offset
                if input_file.truncated:
                    unsupported_blocks.append((
                        block_index,
                        input_file.content,
                        f"truncated {language.value} input file",
                    ))
                    continue
                requests.append(
                    SafetyScanRequest(
                        content=input_file.content,
                        language=language,
                        timeout_seconds=self.guard.policy.limits.max_timeout_seconds,
                        tool_name=self.tool_name,
                        metadata={"execution_id": code_execution_input.execution_id or ""},
                    ))
                block_indices.append(block_index)

            report = await self.guard.check_invocation_async(
                requests,
                tool_name=self.tool_name,
                block_indices=block_indices,
                unsupported_blocks=unsupported_blocks,
            )
        except (TypeError, ValueError):
            report = await self.guard.invalid_request_async(
                "invalid code executor request",
                tool_name=self.tool_name,
            )
        if report.decision != SafetyDecision.ALLOW:
            return CodeExecutionResult(
                outcome=Outcome.OUTCOME_FAILED,
                output=report.model_dump_json(),
            )

        task = asyncio.create_task(self.executor.execute_code(
            invocation_context,
            code_execution_input,
        ))
        try:
            done, _ = await asyncio.wait(
                {task},
                timeout=self.guard.policy.limits.max_timeout_seconds,
            )
        except asyncio.CancelledError:
            task.cancel()
            task.add_done_callback(_consume_late_execution)
            raise
        if task not in done:
            task.cancel()
            task.add_done_callback(_consume_late_execution)
            return CodeExecutionResult(
                outcome=Outcome.OUTCOME_FAILED,
                output=("Code execution exceeded the tool safety policy timeout "
                        f"of {self.guard.policy.limits.max_timeout_seconds:g}s; "
                        "cancellation was requested, but only the runtime or sandbox "
                        "can guarantee process termination"),
            )
        result = task.result()
        limit = self.guard.policy.limits.max_output_size_bytes
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

    @staticmethod
    def _input_file_language(
        name: str,
        mime_type: str,
    ) -> ScriptLanguage | None:
        suffix = PurePosixPath(name.replace("\\", "/")).suffix.lower()
        normalized_mime = mime_type.strip().lower()
        if suffix == ".py" or normalized_mime in {
                "application/x-python",
                "text/x-python",
        }:
            return ScriptLanguage.PYTHON
        if suffix in {".bash", ".sh", ".zsh"} or normalized_mime in {
                "application/x-sh",
                "text/x-shellscript",
        }:
            return ScriptLanguage.BASH
        return None
