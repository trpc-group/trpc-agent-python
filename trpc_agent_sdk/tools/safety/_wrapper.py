# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Generic callable wrapper for tool script safety scanning."""

from __future__ import annotations

import inspect
import shlex
from functools import wraps
from typing import Any
from typing import Callable

from trpc_agent_sdk.log import logger

from ._audit import write_audit_event
from ._policy import ToolSafetyPolicy
from ._scanner import ToolScriptSafetyScanner
from ._telemetry import record_safety_attributes


class ToolSafetyWrapper:
    """Wrap sync or async callables with a pre-execution safety scan."""

    def __init__(
        self,
        *,
        policy: ToolSafetyPolicy | None = None,
        policy_path: str = "",
        audit_log_path: str = "",
        language: str = "unknown",
        tool_name: str = "wrapped_tool",
        block_on_review: bool | None = None,
    ) -> None:
        self.policy = policy or (ToolSafetyPolicy.from_file(policy_path) if policy_path else ToolSafetyPolicy.default())
        if block_on_review is not None:
            self.policy.block_on_review = block_on_review
        self.audit_log_path = audit_log_path
        self.language = language
        self.tool_name = tool_name
        self.scanner = ToolScriptSafetyScanner(self.policy)

    def wrap(self, func: Callable[..., Any]) -> Callable[..., Any]:
        """Return a safety-wrapped callable."""
        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                blocked = self._blocked_result(args, kwargs)
                if blocked is not None:
                    return blocked
                return await func(*args, **kwargs)

            return async_wrapper

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            blocked = self._blocked_result(args, kwargs)
            if blocked is not None:
                return blocked
            return func(*args, **kwargs)

        return sync_wrapper

    def _blocked_result(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any] | None:
        script, language, command_args = self._extract_script(args, kwargs)
        if not script and not command_args:
            return None

        report = self.scanner.scan_script(
            script,
            language,
            command_args=command_args,
            cwd=str(kwargs.get("cwd", "")),
            env=kwargs.get("env") if isinstance(kwargs.get("env"), dict) else {},
            tool_name=self.tool_name,
            tool_metadata={
                key: kwargs[key]
                for key in ("timeout", "max_output_bytes")
                if key in kwargs
            },
        )
        record_safety_attributes(report)
        if self.audit_log_path:
            try:
                write_audit_event(report, self.audit_log_path)
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning("tool safety audit write failed: %s", exc)
        if self.policy.should_block(report.decision):
            return {
                "success": False,
                "error": "SAFETY_GUARD_BLOCKED",
                "safety_report": report.to_dict(),
        }
        return None

    def _extract_script(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[str, str, list[str]]:
        command_args = _extract_command_args(kwargs)
        for key, language in (
            ("python_code", "python"),
            ("bash_code", "bash"),
            ("command", "bash"),
            ("cmd", "bash"),
            ("script", self.language),
            ("code", self.language),
        ):
            value = kwargs.get(key)
            if value:
                return str(value), language, command_args
        if args and isinstance(args[0], str):
            positional_command_args = _coerce_command_args(args[1]) if len(args) > 1 else []
            return args[0], self.language, command_args or positional_command_args
        return "", self.language, command_args


def with_tool_safety(func: Callable[..., Any] | None = None, **kwargs: Any) -> Callable[..., Any]:
    """Wrap a callable with ToolSafetyWrapper.

    Can be used as ``with_tool_safety(func, ...)`` or ``@with_tool_safety(...)``.
    """
    wrapper = ToolSafetyWrapper(**kwargs)
    if func is not None:
        return wrapper.wrap(func)

    def decorator(inner: Callable[..., Any]) -> Callable[..., Any]:
        return wrapper.wrap(inner)

    return decorator


def _extract_command_args(kwargs: dict[str, Any]) -> list[str]:
    for key in ("command_args", "argv", "args"):
        coerced = _coerce_command_args(kwargs.get(key))
        if coerced:
            return coerced
    return []


def _coerce_command_args(value: Any) -> list[str]:
    if value is None or isinstance(value, dict):
        return []
    if isinstance(value, str):
        try:
            return shlex.split(value)
        except ValueError:
            return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return []
