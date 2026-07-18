# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Wrapper helpers for attaching safety scanning without modifying SDK internals."""
from __future__ import annotations

import asyncio
import functools
from typing import Optional

from ._audit import AuditLogger
from ._filter import ToolSafetyFilter
from ._policy import PolicyConfig
from ._scanner import SafetyScanner
from ._types import Decision
from ._types import ScanInput


def wrap_tool(tool, policy: PolicyConfig, *, audit_path: Optional[str] = None):
    """Return *tool* with a :class:`ToolSafetyFilter` prepended to its filters."""
    safety_filter = ToolSafetyFilter(policy=policy, audit_path=audit_path, tool_name=getattr(tool, "name", "tool"))
    tool.add_one_filter(safety_filter, force=True)
    return tool


class SafetyGuardedCodeExecutor:
    """Code-executor wrapper that scans code before delegating.

    Prefer this over the factory-style :func:`safe_code_executor` when you need
    an explicit class. Constructed lazily so optional docker deps are only
    imported when used.
    """

    def __init__(self, inner, policy: PolicyConfig, *, audit_path: Optional[str] = None, block_on_review: bool = False):
        self._inner = inner
        self._scanner = SafetyScanner(policy=policy)
        self._audit = AuditLogger(audit_path)
        self._block_on_review = block_on_review or policy.block_on_review

    async def execute_code(self, invocation_context, input_data):
        from trpc_agent_sdk.code_executors import create_code_execution_result

        code = input_data.code or "\n".join(b.code for b in (input_data.code_blocks or []))
        language = getattr(input_data, "language", None) or "python"
        report = self._scanner.scan(ScanInput(script=code, language=language, tool_name="code_executor"))
        should_block = report.decision == Decision.DENY or (report.decision == Decision.NEEDS_HUMAN_REVIEW
                                                            and self._block_on_review)
        self._audit.log(report, intercepted=should_block)
        if should_block:
            return create_code_execution_result(stderr=f"TOOL_SAFETY_DENY: {report.rule_ids}")
        return await self._inner.execute_code(invocation_context, input_data)


def safe_code_executor(inner, policy: PolicyConfig, *, audit_path: Optional[str] = None, block_on_review: bool = False):
    """Create a code-executor wrapper that scans code before delegating.

    Returns an instance of a dynamically built ``BaseCodeExecutor`` subclass so
    it remains type-compatible with the executor hierarchy.
    """
    from trpc_agent_sdk.code_executors import BaseCodeExecutor
    from trpc_agent_sdk.code_executors import create_code_execution_result

    scanner = SafetyScanner(policy=policy)
    audit = AuditLogger(audit_path)
    block_review = block_on_review or policy.block_on_review

    class _SafeCodeExecutor(BaseCodeExecutor):

        async def execute_code(self, invocation_context, input_data):
            code = input_data.code or "\n".join(b.code for b in (input_data.code_blocks or []))
            language = getattr(input_data, "language", None) or "python"
            report = scanner.scan(ScanInput(script=code, language=language, tool_name="code_executor"))
            should_block = report.decision == Decision.DENY or (report.decision == Decision.NEEDS_HUMAN_REVIEW
                                                                and block_review)
            audit.log(report, intercepted=should_block)
            if should_block:
                return create_code_execution_result(stderr=f"TOOL_SAFETY_DENY: {report.rule_ids}")
            return await inner.execute_code(invocation_context, input_data)

    return _SafeCodeExecutor()


# Backwards-compatible alias used by earlier examples/tests.
SafeCodeExecutor = safe_code_executor


class SafetyDeniedError(RuntimeError):
    """Raised when a safety wrapper blocks a script (decision == DENY)."""

    def __init__(self, report):
        self.report = report
        rule_ids = report.rule_ids if report.rule_ids else ["unknown"]
        super().__init__(f"script denied by rule(s) {rule_ids}")


def safety_wrapper(
    tool_name="unknown",
    *,
    script_arg="script",
    policy=None,
    audit_path=None,
    raise_on_deny=True,
):
    """Decorator: scan the *script_arg* of a function before it runs."""
    if policy is None:
        policy = PolicyConfig()
    _scanner = SafetyScanner(policy=policy)
    _audit = AuditLogger(audit_path)

    def _extract_script(args, kwargs):
        script = kwargs.get(script_arg)
        if script is None:
            for arg in args:
                if isinstance(arg, dict) and script_arg in arg:
                    return arg[script_arg]
        return script

    def _guard(args, kwargs):
        script = _extract_script(args, kwargs)
        if not script or not isinstance(script, str):
            return
        report = _scanner.scan(ScanInput(script=script, tool_name=tool_name))
        _audit.log(report, intercepted=report.blocked)
        if report.decision == Decision.DENY and raise_on_deny:
            raise SafetyDeniedError(report)

    def decorator(func):

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            _guard(args, kwargs)
            return await func(*args, **kwargs)

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            _guard(args, kwargs)
            return func(*args, **kwargs)

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator


class SafetyReviewedSkillRunner:
    """Wrap a skill runner callable with pre-execution safety scanning."""

    def __init__(
        self,
        runner,
        policy,
        *,
        audit_path=None,
        block_review=False,
        tool_name="skill_run",
    ):
        self._runner = runner
        self._scanner = SafetyScanner(policy=policy)
        self._audit = AuditLogger(audit_path)
        self._block_review = block_review or getattr(policy, "block_on_review", False)
        self._tool_name = tool_name

    async def run(self, tool_context, args):
        """Scan skill args and delegate to the wrapped runner when allowed."""
        script = self._extract_script(args)
        if script:
            report = self._scanner.scan(ScanInput(script=script, tool_name=self._tool_name))
            self._audit.log(report, intercepted=report.blocked)
            if report.decision == Decision.DENY:
                return {
                    "success": False,
                    "error": "SKILL_BLOCKED",
                    "safety": report.to_dict(),
                }
            if report.decision == Decision.NEEDS_HUMAN_REVIEW and self._block_review:
                return {
                    "success": False,
                    "error": "SKILL_NEEDS_REVIEW",
                    "safety": report.to_dict(),
                }

        if hasattr(self._runner, "run_async"):
            result = self._runner.run_async(tool_context=tool_context, args=args)
        else:
            result = self._runner(tool_context, args)
        if hasattr(result, "__await__"):
            return await result
        return result

    @staticmethod
    def _extract_script(args):
        if not isinstance(args, dict):
            return None
        for key in ("script", "code", "command", "cmd"):
            val = args.get(key)
            if isinstance(val, str):
                return val
        return None
