# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Wrapper integrations for the Tool Script Safety Guard.

Two ways to gate execution without changing core classes:

- :class:`SafeBashTool` subclasses ``BashTool`` and scans the command inside
  ``_run_async_impl`` *before* the real ``asyncio.create_subprocess_shell`` call.
- :func:`guard_code_executor` wraps any ``BaseCodeExecutor`` so code blocks are
  scanned before delegating to the inner executor.

Both block on a ``DENY`` decision, write an auditable event and emit an OTel
span, mirroring :class:`~trpc_agent_sdk.tools.safety.filter.ToolSafetyFilter`.
"""

from __future__ import annotations

import os
from typing import Any
from typing import Optional

from pydantic import PrivateAttr

from trpc_agent_sdk.code_executors._base_code_executor import BaseCodeExecutor
from trpc_agent_sdk.code_executors._types import CodeExecutionInput
from trpc_agent_sdk.code_executors._types import create_code_execution_result
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools.file_tools._bash_tool import BashTool

from .audit import ENV_AUDIT_PATH
from .audit import AuditLogger
from .audit import emit_safety_span
from .engine import SafetyEngine
from .filter import build_blocked_result
from .filter import to_language
from .models import Decision
from .models import Language
from .models import SafetyReport
from .models import ScanInput
from .policy import SafetyPolicy

_BLOCK_DECISIONS = frozenset({Decision.DENY})


def _default_audit(audit_path: Optional[str]) -> AuditLogger:
    return AuditLogger(audit_path or os.environ.get(ENV_AUDIT_PATH))


class SafeBashTool(BashTool):
    """``BashTool`` that runs a safety scan before executing the command."""

    def __init__(
        self,
        cwd: Optional[str] = None,
        whitelist_commands: Optional[list[str]] = None,
        policy: Optional[SafetyPolicy] = None,
        engine: Optional[SafetyEngine] = None,
        audit_path: Optional[str] = None,
    ) -> None:
        super().__init__(cwd=cwd, whitelist_commands=whitelist_commands)
        self._safety_engine = engine or SafetyEngine(policy)
        self._safety_audit = _default_audit(audit_path)

    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        command = args.get("command") or ""
        report = self._safety_engine.scan(
            ScanInput(
                script=command,
                tool_name=self.name,
                language=Language.BASH,
                args=args,
                cwd=args.get("cwd"),
            ))
        blocked = report.decision in _BLOCK_DECISIONS
        self._safety_audit.log(report, blocked)
        emit_safety_span(report, blocked)
        if blocked:
            return build_blocked_result(report)
        return await super()._run_async_impl(tool_context=tool_context, args=args)


class GuardedCodeExecutor(BaseCodeExecutor):
    """A code executor that scans code blocks before delegating to ``inner``."""

    inner: BaseCodeExecutor

    _engine: SafetyEngine = PrivateAttr()
    _audit: AuditLogger = PrivateAttr()

    async def execute_code(self, invocation_context, code_execution_input: CodeExecutionInput):
        for language, code in self._iter_code(code_execution_input):
            if not code.strip():
                continue
            report = self._engine.scan(
                ScanInput(script=code, tool_name="CodeExecutor", language=to_language(language)))
            blocked = report.decision in _BLOCK_DECISIONS
            self._audit.log(report, blocked)
            emit_safety_span(report, blocked)
            if blocked:
                return create_code_execution_result(stderr=self._block_message(report))
        return await self.inner.execute_code(invocation_context, code_execution_input)

    @staticmethod
    def _iter_code(code_execution_input: CodeExecutionInput):
        if code_execution_input.code:
            yield "python", code_execution_input.code
        for block in code_execution_input.code_blocks:
            yield (block.language or "python"), block.code

    @staticmethod
    def _block_message(report: SafetyReport) -> str:
        result = build_blocked_result(report)
        return result["error"]


def guard_code_executor(
    inner: BaseCodeExecutor,
    policy: Optional[SafetyPolicy] = None,
    engine: Optional[SafetyEngine] = None,
    audit_path: Optional[str] = None,
) -> GuardedCodeExecutor:
    """Wrap ``inner`` so its code is safety-scanned before execution."""
    guarded = GuardedCodeExecutor(inner=inner)
    guarded._engine = engine or SafetyEngine(policy)
    guarded._audit = _default_audit(audit_path)
    return guarded
