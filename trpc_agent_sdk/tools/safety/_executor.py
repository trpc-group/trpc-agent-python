# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License Version 2.0.
"""CodeExecutor wrapper with pre-execution safety scanning."""

from __future__ import annotations

from typing import Optional
from typing_extensions import override

from pydantic import ConfigDict
from pydantic import Field

from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import CodeBlock
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import CodeExecutionResult
from trpc_agent_sdk.code_executors import create_code_execution_result
from trpc_agent_sdk.context import InvocationContext

from ._audit import NullAuditSink
from ._audit import SafetyAuditSink
from ._models import Decision
from ._models import SafetyScanRequest
from ._scanner import ToolSafetyScanner
from ._telemetry import trace_safety_report


class SafetyGuardedCodeExecutor(BaseCodeExecutor):
    """Compose a scanner in front of any existing CodeExecutor."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    executor: BaseCodeExecutor
    scanner: ToolSafetyScanner = Field(default_factory=ToolSafetyScanner)
    audit_sink: SafetyAuditSink = Field(default_factory=NullAuditSink)
    allow_human_review: bool = False
    tool_name: str = "CodeExecutor"

    def __init__(self, **data):
        executor = data.get("executor")
        if executor is not None:
            for field in (
                    "optimize_data_file",
                    "stateful",
                    "error_retry_attempts",
                    "execute_once_per_invocation",
                    "code_block_delimiters",
                    "execution_result_delimiters",
                    "workspace_runtime",
                    "ignore_codes",
            ):
                data.setdefault(field, getattr(executor, field))
        super().__init__(**data)

    @override
    async def execute_code(
        self,
        invocation_context: InvocationContext,
        code_execution_input: CodeExecutionInput,
    ) -> CodeExecutionResult:
        """Scan every block, then delegate only when all decisions permit execution."""
        blocks = code_execution_input.code_blocks
        if not blocks and code_execution_input.code:
            blocks = [CodeBlock(language="python", code=code_execution_input.code)]
        reports = [
            self.scanner.scan(
                SafetyScanRequest(
                    script=block.code,
                    language=block.language or "python",
                    tool_name=self.tool_name,
                    timeout_seconds=_executor_timeout(self.executor),
                    working_directory=_executor_working_directory(self.executor),
                    environment=_executor_environment(self.executor),
                    tool_metadata={
                        "executor_type": type(self.executor).__name__,
                        "execution_id": code_execution_input.execution_id,
                    },
                )) for block in blocks
        ]
        blocked_report = next(
            (report for report in reports if report.decision == Decision.DENY),
            None,
        )
        if blocked_report is None and not self.allow_human_review:
            blocked_report = next(
                (report for report in reports if report.decision == Decision.NEEDS_HUMAN_REVIEW),
                None,
            )
        execution_blocked = blocked_report is not None
        for report in reports:
            self.audit_sink.emit(report.to_audit_event(blocked=execution_blocked))
            trace_safety_report(report, blocked=execution_blocked)
        if blocked_report is not None:
            code = ("TOOL_SAFETY_BLOCKED"
                    if blocked_report.decision == Decision.DENY else "TOOL_SAFETY_REVIEW_REQUIRED")
            return create_code_execution_result(stderr=f"{code}: {blocked_report.model_dump_json(exclude_none=True)}")

        result = await self.executor.execute_code(invocation_context, code_execution_input)
        return _limit_output(result, self.scanner.policy.max_output_bytes)


def _executor_timeout(executor: BaseCodeExecutor) -> Optional[float]:
    timeout = getattr(executor, "timeout", None)
    return float(timeout) if isinstance(timeout, (int, float)) and timeout > 0 else None


def _executor_working_directory(executor: BaseCodeExecutor) -> Optional[str]:
    for field in ("work_dir", "cwd"):
        value = getattr(executor, field, None)
        if isinstance(value, str) and value:
            return value
    return None


def _executor_environment(executor: BaseCodeExecutor) -> dict[str, str]:
    environment = getattr(executor, "environment", None)
    if not isinstance(environment, dict):
        return {}
    return {str(key): str(value) for key, value in environment.items() if isinstance(key, str)}


def _limit_output(result: CodeExecutionResult, max_bytes: int) -> CodeExecutionResult:
    encoded = result.output.encode("utf-8")
    if len(encoded) <= max_bytes:
        return result
    marker = "\n[tool safety output truncated]"
    marker_bytes = marker.encode("utf-8")
    if max_bytes <= len(marker_bytes):
        result.output = marker_bytes[:max_bytes].decode("utf-8", errors="ignore")
        return result
    available = max(0, max_bytes - len(marker_bytes))
    prefix = encoded[:available].decode("utf-8", errors="ignore")
    result.output = f"{prefix}{marker}"
    return result
