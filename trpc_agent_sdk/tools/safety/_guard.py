# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Pre-execution guard, wrappers, and Tool Filter integration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Mapping

from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import CodeExecutionResult
from trpc_agent_sdk.code_executors import create_code_execution_result
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import FilterHandleType
from trpc_agent_sdk.filter import FilterResult
from trpc_agent_sdk.filter import register_tool_filter
from trpc_agent_sdk.tools._context_var import get_tool_var

from ._audit import ToolSafetyAuditLogger
from ._policy import ToolSafetyPolicy
from ._scanner import ToolSafetyScanner
from ._scanner import normalize_language
from ._telemetry import apply_tool_safety_span_attributes
from ._types import SafetyDecision
from ._types import ToolSafetyReport
from ._types import ToolSafetyScanRequest


class ToolSafetyBlockedError(PermissionError):
    """Raised when a script is blocked by the safety guard."""

    def __init__(self, report: ToolSafetyReport):
        self.report = report
        super().__init__(report.summary)


@dataclass
class ToolSafetyGuard:
    """Reusable pre-execution safety guard."""

    policy: ToolSafetyPolicy | None = None
    scanner: ToolSafetyScanner | None = None
    audit_log_path: str | Path | None = None
    apply_telemetry: bool = True

    def __post_init__(self) -> None:
        if self.policy is None:
            self.policy = ToolSafetyPolicy()
        if self.scanner is None:
            self.scanner = ToolSafetyScanner(self.policy)
        if self.audit_log_path is None and self.policy.audit_log_path:
            self.audit_log_path = self.policy.audit_log_path

    def scan(self, request: ToolSafetyScanRequest) -> ToolSafetyReport:
        """Scan a request, emit audit/telemetry, and return the report."""
        report = self.scanner.scan(request)  # type: ignore[union-attr]
        self._emit(report)
        return report

    def check(self, request: ToolSafetyScanRequest) -> ToolSafetyReport:
        """Scan and raise if policy says the script must not proceed."""
        report = self.scan(request)
        if self.should_block(report):
            raise ToolSafetyBlockedError(report)
        return report

    async def run_if_allowed(
        self,
        request: ToolSafetyScanRequest,
        runner: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Run an async callback only when the scan permits execution."""
        self.check(request)
        return await runner()

    def should_block(self, report: ToolSafetyReport) -> bool:
        """Return whether a report should block execution."""
        if report.decision == SafetyDecision.DENY:
            return True
        if report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW and self.policy and self.policy.block_on_review:
            return True
        return False

    def _emit(self, report: ToolSafetyReport) -> None:
        if self.apply_telemetry:
            apply_tool_safety_span_attributes(report)
        if self.audit_log_path:
            ToolSafetyAuditLogger(self.audit_log_path).write(report, blocked=self.should_block(report))


class SafetyGuardedCodeExecutor(BaseCodeExecutor):
    """Wrapper that scans code blocks before delegating to another executor."""

    delegate: BaseCodeExecutor
    guard: ToolSafetyGuard

    def __init__(self, *, delegate: BaseCodeExecutor, guard: ToolSafetyGuard | None = None, **data: Any):
        super().__init__(delegate=delegate, guard=guard or ToolSafetyGuard(), **data)

    async def execute_code(
        self,
        invocation_context: InvocationContext,
        code_execution_input: CodeExecutionInput,
    ) -> CodeExecutionResult:
        """Scan every code block before delegating to the wrapped executor."""
        blocks = code_execution_input.code_blocks
        if not blocks and code_execution_input.code:
            blocks = []
        for block in blocks:
            request = ToolSafetyScanRequest(
                script=block.code,
                language=block.language,
                cwd=self._work_dir(),
                tool_metadata={
                    "name": "CodeExecutor",
                    "executor": self.delegate.__class__.__name__,
                },
            )
            try:
                self.guard.check(request)
            except ToolSafetyBlockedError as exc:
                return create_code_execution_result(
                    stderr=f"Tool safety guard blocked code execution: {exc.report.to_json(indent=None)}")
        if code_execution_input.code and not code_execution_input.code_blocks:
            request = ToolSafetyScanRequest(
                script=code_execution_input.code,
                language="python",
                cwd=self._work_dir(),
                tool_metadata={
                    "name": "CodeExecutor",
                    "executor": self.delegate.__class__.__name__,
                },
            )
            try:
                self.guard.check(request)
            except ToolSafetyBlockedError as exc:
                return create_code_execution_result(
                    stderr=f"Tool safety guard blocked code execution: {exc.report.to_json(indent=None)}")
        return await self.delegate.execute_code(invocation_context, code_execution_input)

    def _work_dir(self) -> str | None:
        return getattr(self.delegate, "work_dir", None)


@register_tool_filter("tool_safety_guard")
class ToolSafetyFilter(BaseFilter):
    """Tool Filter that scans script-like tool arguments before execution."""

    def __init__(self, guard: ToolSafetyGuard | None = None, policy_path: str | Path | None = None):
        super().__init__()
        if guard is not None:
            self.guard = guard
        elif policy_path is not None:
            self.guard = ToolSafetyGuard(policy=ToolSafetyPolicy.load(policy_path))
        else:
            self.guard = ToolSafetyGuard()

    async def run(self, ctx: AgentContext, req: Any, handle: FilterHandleType) -> FilterResult:
        extracted = extract_script_from_tool_args(req)
        if extracted is None:
            result = await handle()
            return result if isinstance(result, FilterResult) else FilterResult(rsp=result)

        script, language, cwd, command_args = extracted
        tool = get_tool_var()
        tool_name = getattr(tool, "name", "") if tool else ""
        request = ToolSafetyScanRequest(
            script=script,
            language=language,
            command_args=command_args,
            cwd=cwd,
            env={},
            tool_metadata={"name": tool_name},
        )
        report = self.guard.scan(request)
        if self.guard.should_block(report):
            return FilterResult(
                rsp={
                    "success": False,
                    "error": "TOOL_SAFETY_GUARD_BLOCKED",
                    "safety_report": report.to_dict(),
                },
                is_continue=False,
            )
        result = await handle()
        return result if isinstance(result, FilterResult) else FilterResult(rsp=result)


def extract_script_from_tool_args(args: Any) -> tuple[str, str, str | None, list[str]] | None:
    """Extract a script payload from common tool argument shapes."""
    if not isinstance(args, Mapping):
        return None

    cwd = str(args["cwd"]) if args.get("cwd") else None
    if "command" in args and args.get("command"):
        command = str(args["command"])
        return command, "bash", cwd, command_to_args(command)

    for key in ("script", "code", "source"):
        if key in args and args.get(key):
            language = str(args.get("language") or args.get("lang") or infer_language_from_key(key, str(args[key])))
            return str(args[key]), normalize_language(language), cwd, []

    if "code_blocks" in args and isinstance(args["code_blocks"], list) and args["code_blocks"]:
        first = args["code_blocks"][0]
        if isinstance(first, Mapping) and first.get("code"):
            return (
                str(first["code"]),
                normalize_language(str(first.get("language") or "python")),
                cwd,
                [],
            )
    return None


def command_to_args(command: str) -> list[str]:
    """Best-effort command split for report context."""
    try:
        return list(__import__("shlex").split(command))
    except ValueError:
        return command.split()


def infer_language_from_key(key: str, script: str) -> str:
    """Infer script language when a tool only supplies generic code text."""
    if key == "script" and script.lstrip().startswith(("#!/bin/bash", "#!/usr/bin/env bash")):
        return "bash"
    if key == "script" and any(token in script for token in ("#!/bin/sh", "set -e", "fi\n", "done\n")):
        return "bash"
    return "python"
