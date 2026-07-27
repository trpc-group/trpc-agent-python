# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Execution adapters, Tool Filter and CodeExecutor wrapper."""

from __future__ import annotations

import asyncio
import math
import os
from pathlib import Path
from typing import Any

from trpc_agent_sdk.abc import FilterResult
from trpc_agent_sdk.abc import FilterType
from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import create_code_execution_result
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.tools._context_var import get_tool_var
from trpc_agent_sdk.types import CodeExecutionResult

from ._audit import AuditSink
from ._audit import emit_report
from ._audit import SafetyAuditError
from ._audit import set_safety_span_attributes
from ._models import SafetyDecision
from ._models import SafetyReport
from ._models import ScriptLanguage
from ._models import ScriptPayload
from ._models import ScriptScanRequest
from ._models import ToolMetadata
from ._models import ToolSafetyPolicy
from ._sanitizer import truncate_text
from ._scanner import ToolScriptSafetyGuard

_TOOL_TIMEOUT_ARGS = {
    "bash": "timeout",
    "workspace_exec": "timeout_sec",
    "skill_run": "timeout",
    "skill_exec": "timeout",
}
_BASH_TOOL_NAMES = frozenset(_TOOL_TIMEOUT_ARGS)
_GENERIC_EXECUTION_FIELDS = {
    "execute_command": ("command", ),
    "execute_code": ("code", ),
    "run_script": ("script", ),
}
_PYTHON_LANGUAGES = frozenset({"py", "python", "python3"})
_BASH_LANGUAGES = frozenset({"bash", "sh", "shell", "zsh"})


def _language(value: str, default: ScriptLanguage) -> ScriptLanguage:
    normalized = value.strip().lower()
    if normalized in _PYTHON_LANGUAGES:
        return ScriptLanguage.PYTHON
    if normalized in _BASH_LANGUAGES:
        return ScriptLanguage.BASH
    return default


def _timeout(args: dict[str, Any], name: str, policy: ToolSafetyPolicy) -> tuple[float | None, float, str | None]:
    arg_name = _TOOL_TIMEOUT_ARGS.get(name)
    if arg_name is None:
        arg_name = "timeout_sec" if "timeout_sec" in args else ("timeout" if "timeout" in args else None)
    raw = args.get(arg_name) if arg_name else None
    requested = float(raw) if isinstance(raw, (int, float)) else None
    if requested is not None and not math.isfinite(requested):
        return None, float(policy.max_timeout_seconds), arg_name
    if requested is None or requested <= 0:
        return requested, float(policy.max_timeout_seconds), arg_name
    return requested, min(requested, float(policy.max_timeout_seconds)), arg_name


def _timeout_value(tool_name: str, request: ScriptScanRequest, original: Any) -> int | float:
    value = request.effective_timeout_seconds
    if request.timeout_arg_name == "timeout_sec":
        return value
    if isinstance(original, int) and not isinstance(original, bool):
        return int(value)
    if original is None and tool_name.lower() in _BASH_TOOL_NAMES:
        return int(value)
    return value


def adapt_tool_request(tool: Any, args: dict[str, Any], policy: ToolSafetyPolicy) -> ScriptScanRequest:
    """Adapt supported execution Tool arguments."""
    name = str(getattr(tool, "name", "")).lower()
    metadata = ToolMetadata(
        name=str(getattr(tool, "name", tool.__class__.__name__)),
        description=str(getattr(tool, "description", "")),
    )
    requested, effective, timeout_arg = _timeout(args, name, policy)
    supported_fields = _GENERIC_EXECUTION_FIELDS.get(name)
    if name in _BASH_TOOL_NAMES:
        supported_fields = ("command", "code", "script")
    inferred_fields = supported_fields or ("command", "code", "script")
    payloads = []
    for field_name in ("command", "code", "script"):
        if field_name not in inferred_fields:
            continue
        content = args.get(field_name)
        if not isinstance(content, str) or not content.strip():
            continue
        default_language = (ScriptLanguage.BASH if field_name == "command" else ScriptLanguage.PYTHON)
        language = _language(str(args.get("language", "")), default_language)
        argv = args.get("argv", [])
        payloads.append(
            ScriptPayload(
                language=language,
                content=content,
                source=f"{metadata.name}.{field_name}",
                argv=[str(item) for item in argv] if isinstance(argv, list) else [],
                stdin=str(args.get("stdin", "")) if field_name == "command" else "",
            ))
    applicable = bool(payloads) or name in _BASH_TOOL_NAMES or name in _GENERIC_EXECUTION_FIELDS
    tool_cwd = str(getattr(tool, "cwd", "") or "")
    requested_cwd = str(args.get("cwd") or "")
    cwd = requested_cwd or tool_cwd
    if name in _BASH_TOOL_NAMES and cwd:
        cwd_path = Path(cwd)
        if requested_cwd and not cwd_path.is_absolute():
            cwd_path = Path(tool_cwd) / cwd_path
        cwd = str(cwd_path.resolve())
    local_home = str(Path.home()) if name in _BASH_TOOL_NAMES else None
    local_root = Path(cwd).anchor if name in _BASH_TOOL_NAMES and cwd else None
    env = args.get("env")
    env_keys = sorted(str(key) for key in env) if isinstance(env, dict) else []
    return ScriptScanRequest(
        payloads=payloads,
        cwd=cwd,
        execution_home=local_home,
        execution_root=local_root,
        env_keys=env_keys,
        metadata=metadata,
        requested_timeout_seconds=requested,
        effective_timeout_seconds=effective,
        timeout_arg_name=timeout_arg,
        max_output_bytes=policy.max_output_bytes,
        applicable=applicable,
        background=bool(args.get("background", False)),
        tty=bool(args.get("tty", False)),
    )


def adapt_code_execution_input(
    value: CodeExecutionInput,
    metadata: ToolMetadata,
    policy: ToolSafetyPolicy,
) -> ScriptScanRequest:
    """Adapt all payloads from CodeExecutionInput."""
    payloads = []
    if value.code:
        payloads.append(
            ScriptPayload(
                language=ScriptLanguage.PYTHON,
                content=value.code,
                source="CodeExecutionInput.code",
            ))
    for index, block in enumerate(value.code_blocks):
        if block.code:
            payloads.append(
                ScriptPayload(
                    language=_language(block.language, ScriptLanguage.PYTHON),
                    content=block.code,
                    source=f"CodeExecutionInput.code_blocks[{index}]",
                ))
    return ScriptScanRequest(
        payloads=payloads,
        metadata=metadata,
        requested_timeout_seconds=None,
        effective_timeout_seconds=float(policy.max_timeout_seconds),
        max_output_bytes=policy.max_output_bytes,
    )


def adapt_cli_request(
    payload: ScriptPayload,
    metadata: ToolMetadata,
    policy: ToolSafetyPolicy,
    cwd: str = "",
) -> ScriptScanRequest:
    """Adapt CLI input."""
    resolved_cwd = str(Path(cwd or os.getcwd()).resolve())
    return ScriptScanRequest(
        payloads=[payload],
        cwd=resolved_cwd,
        execution_home=str(Path.home()),
        execution_root=Path(resolved_cwd).anchor,
        metadata=metadata,
        effective_timeout_seconds=float(policy.max_timeout_seconds),
        max_output_bytes=policy.max_output_bytes,
    )


FILTER_NAME = "tool_script_safety"
AUDIT_FAILURE_ERROR = "TOOL_SAFETY_AUDIT_FAILED"


class ToolSafetyFilter(BaseFilter):
    """Pre-execution safety scan and post-execution output limit."""

    def __init__(self, guard: ToolScriptSafetyGuard, audit_sink: AuditSink):
        super().__init__()
        self._type = FilterType.TOOL
        self._name = FILTER_NAME
        self._guard = guard
        self._audit_sink = audit_sink

    async def _before(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        """Scan and stop unsafe execution before the handler."""
        del ctx
        request = None
        tool = get_tool_var()
        tool_name = str(getattr(tool, "name", "unknown_tool"))
        try:
            if not isinstance(req, dict):
                raise ValueError("tool safety filter requires dictionary arguments")
            request = adapt_tool_request(tool, req, self._guard.policy)
            report = self._guard.scan(request)
        except Exception as error:  # pylint: disable=broad-except
            report = self._guard.error_report(error)
        try:
            emit_report(self._audit_sink, report, tool_name)
        except SafetyAuditError:
            rsp.rsp = {
                "error": AUDIT_FAILURE_ERROR,
                "decision": SafetyDecision.DENY.value,
                "execution_blocked": True,
            }
            rsp.is_continue = False
            return
        set_safety_span_attributes(report)
        if report.decision != SafetyDecision.ALLOW:
            rsp.rsp = report.as_dict()
            rsp.is_continue = False
            return
        if request and request.applicable and request.timeout_arg_name:
            req[request.timeout_arg_name] = _timeout_value(
                tool_name,
                request,
                req.get(request.timeout_arg_name),
            )

    async def _after(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        """Limit returned output after an allowed execution."""
        del ctx, req
        rsp.rsp = self.finalize_response(rsp.rsp)

    def finalize_response(self, response: Any) -> Any:
        """Limit output after an allowed execution."""
        return self._guard.limit_output(response)

    @classmethod
    def from_policy(
        cls,
        path: str,
        audit_sink: AuditSink,
    ) -> "ToolSafetyFilter":
        """Create a filter from YAML."""
        return cls(ToolScriptSafetyGuard.from_policy(path), audit_sink)


class ToolSafetyViolation(RuntimeError):
    """Raised when a CodeExecutor request is blocked."""

    def __init__(self, report: SafetyReport):
        super().__init__(report.summary)
        self.report = report


class SafetyGuardedCodeExecutor(BaseCodeExecutor):
    """Drop-in wrapper around an existing CodeExecutor."""

    delegate: BaseCodeExecutor
    guard: Any
    audit_sink: Any

    def model_post_init(self, context: Any) -> None:
        """Mirror delegate capabilities used by executor consumers."""
        del context
        self.optimize_data_file = self.delegate.optimize_data_file
        self.stateful = self.delegate.stateful
        self.error_retry_attempts = self.delegate.error_retry_attempts
        self.execute_once_per_invocation = self.delegate.execute_once_per_invocation
        self.code_block_delimiters = list(self.delegate.code_block_delimiters)
        self.execution_result_delimiters = list(self.delegate.execution_result_delimiters)
        self.workspace_runtime = self.delegate.workspace_runtime
        self.ignore_codes = list(self.delegate.ignore_codes)

    async def execute_code(
        self,
        invocation_context: InvocationContext,
        code_execution_input: CodeExecutionInput,
    ) -> CodeExecutionResult:
        """Scan, audit, enforce timeout, then delegate."""
        metadata = ToolMetadata(name=self.delegate.__class__.__name__)
        request = None
        try:
            request = adapt_code_execution_input(code_execution_input, metadata, self.guard.policy)
            report = self.guard.scan(request)
        except Exception as error:  # pylint: disable=broad-except
            report = self.guard.error_report(error)
        try:
            emit_report(self.audit_sink, report, metadata.name)
        except SafetyAuditError as error:
            # Audit failure is a safety failure: never execute without a durable
            # record, and expose the same structured review result as scan errors.
            report = self.guard.error_report(error)
            set_safety_span_attributes(report)
            raise ToolSafetyViolation(report) from error
        set_safety_span_attributes(report)
        if report.decision != SafetyDecision.ALLOW:
            raise ToolSafetyViolation(report)
        if request is None:
            raise ToolSafetyViolation(report)
        try:
            result = await asyncio.wait_for(
                self.delegate.execute_code(invocation_context, code_execution_input),
                timeout=request.effective_timeout_seconds,
            )
        except asyncio.TimeoutError:
            result = create_code_execution_result(
                stderr="Code execution exceeded the tool safety timeout.",
                is_timed_out=True,
            )
        return self._limit_result(result, request.max_output_bytes)

    @staticmethod
    def _limit_result(result: CodeExecutionResult, max_output_bytes: int) -> CodeExecutionResult:
        output, _ = truncate_text(result.output or "", max_output_bytes)
        return result.model_copy(update={"output": output})
