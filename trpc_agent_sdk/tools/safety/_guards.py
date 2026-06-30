# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool filter and executor wrappers for script safety."""

from __future__ import annotations

import json
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from typing import Optional

from opentelemetry import trace
from pydantic import Field

from trpc_agent_sdk.abc import FilterResult
from trpc_agent_sdk.code_executors._base_code_executor import BaseCodeExecutor
from trpc_agent_sdk.code_executors._types import CodeBlock
from trpc_agent_sdk.code_executors._types import CodeExecutionInput
from trpc_agent_sdk.code_executors._types import CodeExecutionResult
from trpc_agent_sdk.code_executors._types import create_code_execution_result
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import register_tool_filter
from trpc_agent_sdk.tools._context_var import get_tool_var

from ._policy import load_policy
from ._scanner import SafetyScanner
from ._types import SafetyAuditEvent
from ._types import SafetyPolicy
from ._types import SafetyReport
from ._types import ScriptLanguage

_REPORT_METADATA_KEY = "tool_safety.last_report"


@register_tool_filter("tool_safety_guard")
class ToolSafetyFilter(BaseFilter):
    """Filter that scans script-like tool arguments before execution."""

    def __init__(
        self,
        policy_path: Optional[str] = None,
        policy: Optional[SafetyPolicy] = None,
        scanner: Optional[SafetyScanner] = None,
    ) -> None:
        super().__init__()
        self.policy = policy or load_policy(policy_path)
        self.scanner = scanner or SafetyScanner(self.policy)

    async def _before(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        content, language, cwd, env = _extract_scan_target(req)
        tool = get_tool_var()
        tool_name = getattr(tool, "name", "") or ""

        report = self.scanner.scan(
            content=content,
            language=language,
            tool_name=tool_name,
            cwd=cwd,
            env=env,
            metadata=_metadata_from_request(req, "tool_filter"),
        )
        ctx.with_metadata(_REPORT_METADATA_KEY, report)
        _set_span_attributes(report)
        _write_audit_event(self.policy, report, cwd=cwd)

        if report.blocked:
            rsp.rsp = _blocked_response(report)
            rsp.is_continue = False


class SafetyGuardedCodeExecutor(BaseCodeExecutor):
    """CodeExecutor wrapper that scans code blocks before delegating execution."""

    delegate: BaseCodeExecutor
    policy: SafetyPolicy = Field(default_factory=SafetyPolicy)

    def __init__(
        self,
        *,
        delegate: BaseCodeExecutor,
        policy_path: Optional[str] = None,
        policy: Optional[SafetyPolicy] = None,
        **data: Any,
    ) -> None:
        effective_policy = policy or load_policy(policy_path)
        data.setdefault("optimize_data_file", delegate.optimize_data_file)
        data.setdefault("stateful", delegate.stateful)
        data.setdefault("error_retry_attempts", delegate.error_retry_attempts)
        data.setdefault("execute_once_per_invocation", delegate.execute_once_per_invocation)
        data.setdefault("code_block_delimiters", delegate.code_block_delimiters)
        data.setdefault("execution_result_delimiters", delegate.execution_result_delimiters)
        data.setdefault("workspace_runtime", delegate.workspace_runtime)
        data.setdefault("ignore_codes", delegate.ignore_codes)
        super().__init__(delegate=delegate, policy=effective_policy, **data)

    async def execute_code(
        self,
        invocation_context: InvocationContext,
        code_execution_input: CodeExecutionInput,
    ) -> CodeExecutionResult:
        scanner = SafetyScanner(self.policy)
        blocks = code_execution_input.code_blocks
        if not blocks and code_execution_input.code:
            blocks = [CodeBlock(language="python", code=code_execution_input.code)]

        for block in blocks:
            report = scanner.scan(
                content=block.code,
                language=block.language,
                tool_name="CodeExecutor",
                metadata={"source": "code_executor"},
            )
            _set_span_attributes(report)
            _write_audit_event(self.policy, report)
            if report.blocked:
                return create_code_execution_result(stderr=_blocked_message(report))

        return await self.delegate.execute_code(invocation_context, code_execution_input)


def _extract_scan_target(req: Any) -> tuple[str, ScriptLanguage, str, dict[str, Any] | None]:
    if not isinstance(req, dict):
        return str(req or ""), ScriptLanguage.UNKNOWN, "", None

    content = ""
    language = ScriptLanguage.UNKNOWN
    for key in ("command", "script", "code", "bash", "shell"):
        value = req.get(key)
        if isinstance(value, str):
            content = value
            if key in {"command", "bash", "shell"}:
                language = ScriptLanguage.BASH
            break

    if not content:
        text_parts = []
        for key, value in req.items():
            if isinstance(value, str) and any(token in key.lower() for token in ("command", "script", "code")):
                text_parts.append(value)
        content = "\n".join(text_parts)

    raw_language = req.get("language") or req.get("lang")
    if isinstance(raw_language, str):
        lowered = raw_language.lower()
        if lowered in {"python", "py", "python3"}:
            language = ScriptLanguage.PYTHON
        elif lowered in {"bash", "sh", "shell"}:
            language = ScriptLanguage.BASH

    cwd = ""
    for key in ("cwd", "working_dir", "work_dir"):
        value = req.get(key)
        if isinstance(value, str):
            cwd = value
            break

    env = req.get("env")
    if not isinstance(env, dict):
        env = None

    return content, language, cwd, env


def _metadata_from_request(req: Any, source: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {"source": source}
    if isinstance(req, dict):
        for key in ("timeout", "timeout_seconds", "max_output_bytes", "max_output_size", "output_limit"):
            if key in req:
                metadata[key] = req[key]
    return metadata


def _set_span_attributes(report: SafetyReport) -> None:
    span = trace.get_current_span()
    span.set_attribute("tool.safety.decision", report.decision.value)
    span.set_attribute("tool.safety.risk_level", report.risk_level.value)
    span.set_attribute("tool.safety.rule_ids", ",".join(report.rule_ids))
    span.set_attribute("tool.safety.blocked", report.blocked)
    span.set_attribute("tool.safety.redacted", report.redacted)


def _write_audit_event(policy: SafetyPolicy, report: SafetyReport, cwd: str = "") -> None:
    if not policy.audit_log_path:
        return
    event = SafetyAuditEvent(
        timestamp=datetime.now(timezone.utc).isoformat(),
        tool_name=report.tool_name,
        decision=report.decision,
        risk_level=report.risk_level,
        rule_ids=report.rule_ids,
        elapsed_ms=report.elapsed_ms,
        redacted=report.redacted,
        blocked=report.blocked,
        language=report.language,
        cwd=cwd,
    )
    path = Path(policy.audit_log_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event.model_dump(mode="json"), ensure_ascii=False) + "\n")


def _blocked_response(report: SafetyReport) -> dict[str, Any]:
    return {
        "success": False,
        "error": "TOOL_SAFETY_BLOCKED",
        "safety_report": report.model_dump(mode="json"),
    }


def _blocked_message(report: SafetyReport) -> str:
    return json.dumps(_blocked_response(report), ensure_ascii=False)
