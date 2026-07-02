# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0
"""Wrapper helpers showing how to attach the safety filter to existing tools
and code executors without modifying SDK internals.

Four shapes are provided:
- wrap_tool: returns a new tool with the safety filter prepended.
- SafeCodeExecutor: returns a new BaseCodeExecutor subclass whose execute_code
  runs the scanner before delegating to the wrapped executor.
- safety_wrapper: decorator that scans a function's script argument before
  the function body runs (sync or async).
- SafetyReviewedSkillRunner: wraps a skill runner callable with pre-execution
  safety scanning, covering the Skill execution path.
"""
from __future__ import annotations

import asyncio
import functools
from typing import Any
from typing import Optional

from .audit import AuditLogger
from .policy import PolicyConfig
from .scanner import SafetyScanner
from .tool_filter import ToolSafetyFilter
from .types import Decision
from .types import ScanInput


def wrap_tool(tool, policy: PolicyConfig, *, audit_path: Optional[str] = None):
    """Return *tool* with a :class:`ToolSafetyFilter` prepended to its filters.

    *tool* must be a ``trpc_agent_sdk.tools.BaseTool``; imported lazily so this
    module does not hard-depend on the full SDK dependency tree.
    """
    safety_filter = ToolSafetyFilter(policy=policy, audit_path=audit_path, tool_name=tool.name)
    tool.add_one_filter(safety_filter, force=True)
    return tool


def SafeCodeExecutor(inner, policy: PolicyConfig, *, audit_path: Optional[str] = None):
    """Create a code-executor wrapper that scans code before delegating.

    Returns a new instance of a dynamically built ``BaseCodeExecutor`` subclass
    whose ``execute_code`` runs the safety scanner first. Built lazily so the
    ``trpc_agent_sdk.code_executors`` import (which may pull optional deps like
    docker) only happens when this wrapper is actually used.
    """
    from trpc_agent_sdk.code_executors import BaseCodeExecutor
    from trpc_agent_sdk.code_executors import create_code_execution_result
    from .audit import AuditLogger

    scanner = SafetyScanner(policy=policy)
    audit = AuditLogger(audit_path)

    class _SafeCodeExecutor(BaseCodeExecutor):
        async def execute_code(self, invocation_context, input_data):
            code = input_data.code or "\n".join(b.code for b in input_data.code_blocks)
            report = scanner.scan(ScanInput(script=code, language="python", tool_name="code_executor"))
            audit.log(report, intercepted=report.blocked)
            if report.blocked:
                return create_code_execution_result(
                    stderr=f"TOOL_SAFETY_DENY: {report.rule_ids}"
                )
            return await inner.execute_code(invocation_context, input_data)

    return _SafeCodeExecutor()


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class SafetyDeniedError(RuntimeError):
    """Raised when a safety wrapper blocks a script (decision == DENY)."""

    def __init__(self, report):
        self.report = report
        rule_ids = report.rule_ids if report.rule_ids else ["unknown"]
        super().__init__(f"script denied by rule(s) {rule_ids}")


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------


def safety_wrapper(tool_name="unknown", *, script_arg="script",
                   policy=None, audit_path=None, raise_on_deny=True):
    """Decorator: scan the *script_arg* of a function before it runs.

    Works on both sync and async functions. When the scan decision is DENY
    and *raise_on_deny* is True, raises :class:`SafetyDeniedError`.

    The decorated function's keyword argument named *script_arg* (or a key
    inside a dict positional argument) is scanned before the body runs.

    Example::

        @safety_wrapper(tool_name="runner", script_arg="code")
        async def execute(*, tool_context, args):
            ...

    Args:
        tool_name: Name used in audit/report.
        script_arg: Name of the kwarg (or key in a dict positional arg)
            that holds the script text.
        policy: Optional PolicyConfig; uses defaults when None.
        audit_path: Optional path for JSONL audit log.
        raise_on_deny: Raise SafetyDeniedError on DENY (default True).
    """
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


# ---------------------------------------------------------------------------
# Skill runner wrapper
# ---------------------------------------------------------------------------


class SafetyReviewedSkillRunner:
    """Wrap a skill runner callable with pre-execution safety scanning.

    Covers the Skill execution path: works with any callable that accepts
    ``(tool_context, args)`` or has a ``run_async(tool_context=, args=)``
    method. When the scan decision is DENY (or NEEDS_HUMAN_REVIEW when
    *block_review* is True), execution is skipped and a blocked-response
    dict is returned.

    Example::

        safe_skill = SafetyReviewedSkillRunner(
            my_skill_runner, policy=policy, tool_name="skill_run",
        )
        result = await safe_skill.run(tool_context, args)
    """

    def __init__(self, runner, policy, *, audit_path=None,
                 block_review=False, tool_name="skill_run"):
        self._runner = runner
        self._scanner = SafetyScanner(policy=policy)
        self._audit = AuditLogger(audit_path)
        self._block_review = block_review
        self._tool_name = tool_name

    async def run(self, tool_context, args):
        """Scan skill args and delegate to the wrapped runner when allowed."""
        script = self._extract_script(args)
        if script:
            report = self._scanner.scan(
                ScanInput(script=script, tool_name=self._tool_name)
            )
            self._audit.log(report, intercepted=report.blocked)
            if report.decision == Decision.DENY:
                return {"success": False, "error": "SKILL_BLOCKED",
                        "safety": report.to_dict()}
            if report.decision == Decision.NEEDS_HUMAN_REVIEW and self._block_review:
                return {"success": False, "error": "SKILL_NEEDS_REVIEW",
                        "safety": report.to_dict()}

        # Delegate to the wrapped runner.
        if hasattr(self._runner, "run_async"):
            result = self._runner.run_async(tool_context=tool_context, args=args)
        else:
            result = self._runner(tool_context, args)
        if hasattr(result, "__await__"):
            return await result
        return result

    @staticmethod
    def _extract_script(args):
        """Pull script content from common skill arg shapes."""
        if not isinstance(args, dict):
            return None
        for key in ("script", "code", "command", "cmd"):
            val = args.get(key)
            if isinstance(val, str):
                return val
        return None
