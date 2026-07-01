# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Wrapper / decorator for applying safety checks to any callable.

This allows the safety guard to be used outside of the filter pipeline —
for example, wrapping a plain ``ToolABC.run_async`` implementation or a
standalone function.

Usage as a decorator::

    from trpc_agent_sdk.tools.safety import safety_wrapper

    @safety_wrapper(tool_name="my_script_runner")
    async def my_tool_run(tool_context, args):
        script = args["script"]
        ...

Usage as a context manager::

    from trpc_agent_sdk.tools.safety import SafetyWrapper

    async with SafetyWrapper(tool_name="bash_tool") as guard:
        guard.check(script_content, script_type=ScriptType.BASH)
        # If we reach here the script was ALLOWED or NEEDS_HUMAN_REVIEW.
        await execute(script_content)
"""

from __future__ import annotations

import functools
from contextlib import asynccontextmanager
from typing import Any
from typing import AsyncIterator
from typing import Callable
from typing import Optional

from ._audit import AuditLogger
from ._policy import SafetyPolicy
from ._policy import get_policy
from ._scanner import SafetyScanner
from ._telemetry import set_safety_span_attributes
from ._types import Decision
from ._types import SafetyScanReport
from ._types import ScriptType


class SafetyWrapper:
    """Standalone wrapper that can be used to check scripts outside of filters.

    Args:
        tool_name: Name logged in reports.
        policy: Optional policy override.
        audit_log_path: Path to JSONL audit file.
        raise_on_deny: If True (default), raise ``SafetyDeniedError`` when
                       the decision is DENY.
    """

    def __init__(
        self,
        tool_name: str = "wrapped_tool",
        *,
        policy: Optional[SafetyPolicy] = None,
        audit_log_path: Optional[str] = None,
        raise_on_deny: bool = True,
    ) -> None:
        self._tool_name = tool_name
        self._policy = policy or get_policy()
        self._scanner = SafetyScanner(self._policy)
        self._audit = AuditLogger(audit_log_path)
        self._raise_on_deny = raise_on_deny
        self._last_report: Optional[SafetyScanReport] = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def last_report(self) -> Optional[SafetyScanReport]:
        """The most recent scan report, or None if no scan has been run."""
        return self._last_report

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(
        self,
        script_content: str,
        *,
        script_type: Optional[ScriptType] = None,
        command_args: Optional[list[str]] = None,
        working_directory: Optional[str] = None,
        environment_variables: Optional[dict[str, str]] = None,
        **extra_metadata,
    ) -> SafetyScanReport:
        """Run the safety scan and optionally raise on DENY.

        Args:
            script_content: The script or command text to scan.
            script_type: Python / Bash / Unknown (auto-detect).
            command_args: CLI arguments, if any.
            working_directory: Target working directory.
            environment_variables: Env vars set before execution.
            **extra_metadata: Stored in ``scan_input.extra_metadata``.

        Returns:
            ``SafetyScanReport``

        Raises:
            SafetyDeniedError: If ``raise_on_deny`` is True and the decision is DENY.
        """
        from ._types import SafetyScanInput

        scan_input = SafetyScanInput(
            script_content=script_content,
            script_type=script_type or ScriptType.UNKNOWN,
            command_args=command_args,
            working_directory=working_directory,
            environment_variables=environment_variables,
            tool_name=self._tool_name,
            extra_metadata=extra_metadata,
        )

        report = self._scanner.scan(scan_input)
        self._last_report = report

        # Audit
        self._audit.log_event(report)

        # Telemetry
        set_safety_span_attributes(report)

        if report.decision == Decision.DENY and self._raise_on_deny:
            raise SafetyDeniedError(report)

        return report

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def guard(
        self,
        script_content: str,
        *,
        script_type: Optional[ScriptType] = None,
        **kwargs,
    ) -> AsyncIterator[SafetyWrapper]:
        """Async context manager that scans on entry.

        Usage::

            async with wrapper.guard(script) as g:
                # g.last_report contains the scan result
                if g.last_report.decision != Decision.DENY:
                    await do_execute(script)
        """
        self.check(script_content, script_type=script_type, **kwargs)
        try:
            yield self
        finally:
            pass


class SafetyDeniedError(RuntimeError):
    """Raised when the safety guard blocks a script."""

    def __init__(self, report: SafetyScanReport) -> None:
        self.report = report
        super().__init__(report.summary)


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------


def safety_wrapper(
    tool_name: str = "",
    *,
    script_arg_name: str = "script",
    policy: Optional[SafetyPolicy] = None,
    audit_log_path: Optional[str] = None,
    raise_on_deny: bool = True,
):
    """Decorator that applies safety checks before a function executes.

    The decorated function's keyword argument named *script_arg_name* is
    scanned before the function body runs.

    Args:
        tool_name: Name for audit / reports.
        script_arg_name: Name of the kwarg that contains the script text.
        policy: Optional policy override.
        audit_log_path: Path to JSONL audit file.
        raise_on_deny: Raise ``SafetyDeniedError`` on DENY.

    Example::

        @safety_wrapper(tool_name="my_runner", script_arg_name="code")
        async def my_func(*, tool_context, args):
            code = args["code"]
            ...
    """

    def decorator(func: Callable) -> Callable:
        wrapper_inst = SafetyWrapper(
            tool_name=tool_name or func.__name__,
            policy=policy,
            audit_log_path=audit_log_path,
            raise_on_deny=raise_on_deny,
        )

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            script = kwargs.get(script_arg_name)
            if script is None:
                # Try to find it in positional args (e.g. tool_context, args)
                for arg in args:
                    if isinstance(arg, dict) and script_arg_name in arg:
                        script = arg[script_arg_name]
                        break
            if script and isinstance(script, str):
                wrapper_inst.check(script)
            return await func(*args, **kwargs)

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            script = kwargs.get(script_arg_name)
            if script is None:
                for arg in args:
                    if isinstance(arg, dict) and script_arg_name in arg:
                        script = arg[script_arg_name]
                        break
            if script and isinstance(script, str):
                wrapper_inst.check(script)
            return func(*args, **kwargs)

        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator
