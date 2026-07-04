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
        entries = self._extract_scan_entries(args, kwargs)
        if not entries:
            return None

        cwd = str(kwargs.get("cwd", ""))
        env = kwargs.get("env") if isinstance(kwargs.get("env"), dict) else {}
        metadata = {key: kwargs[key] for key in ("timeout", "max_output_bytes") if key in kwargs}
        for script, language, command_args in entries:
            report = self.scanner.scan_script(
                script,
                language,
                command_args=command_args,
                cwd=cwd,
                env=env,
                tool_name=self.tool_name,
                tool_metadata=metadata,
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

    def _extract_scan_entries(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> list[tuple[str, str, list[str]]]:
        entries: list[tuple[str, str, list[str]]] = []
        for payload in _iter_payloads(kwargs):
            command_args = _extract_command_args(payload)

            code_blocks = _request_value(payload, "code_blocks", None)
            if code_blocks:
                for block in code_blocks:
                    code = _request_value(block, "code", "")
                    language = _request_value(block, "language", "unknown") or "unknown"
                    if code:
                        entries.append((str(code), str(language), []))

            for key, language in (
                ("python_code", "python"),
                ("bash_code", "bash"),
                ("bash", "bash"),
                ("command", "bash"),
                ("cmd", "bash"),
            ):
                value = _request_value(payload, key, "")
                if value:
                    entries.append((str(value), language, command_args))

            for key in ("script", "code"):
                value = _request_value(payload, key, "")
                if value:
                    language = _request_value(payload, "language", self.language) or self.language
                    entries.append((str(value), str(language), command_args))

            if command_args and not any(
                _request_value(payload, key, "")
                for key in ("python_code", "bash_code", "bash", "command", "cmd", "script", "code")
            ):
                entries.append(("", self.language, command_args))

        if args and isinstance(args[0], str):
            command_args = _extract_command_args(kwargs)
            positional_command_args = _coerce_command_args(args[1]) if len(args) > 1 else []
            entries.append((args[0], self.language, command_args or positional_command_args))
        for arg in args:
            if isinstance(arg, (dict, list, tuple)):
                for payload in _iter_payloads(arg):
                    command_args = _extract_command_args(payload)
                    for key, language in (
                        ("python_code", "python"),
                        ("bash_code", "bash"),
                        ("command", "bash"),
                        ("cmd", "bash"),
                    ):
                        value = _request_value(payload, key, "")
                        if value:
                            entries.append((str(value), language, command_args))
        return _dedupe_entries(entries)


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


def _extract_command_args(payload: Any) -> list[str]:
    for key in ("command_args", "argv", "args"):
        coerced = _coerce_command_args(_request_value(payload, key, None))
        if coerced:
            return coerced
    return []


def _request_value(req: Any, key: str, default: Any = None) -> Any:
    if isinstance(req, dict):
        return req.get(key, default)
    return getattr(req, key, default)


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


def _iter_payloads(req: Any):
    seen: set[int] = set()

    def walk(value: Any):
        marker = id(value)
        if marker in seen:
            return
        seen.add(marker)
        yield value
        if isinstance(value, dict):
            for nested in value.values():
                if isinstance(nested, (dict, list, tuple)):
                    yield from walk(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                if isinstance(nested, (dict, list, tuple)):
                    yield from walk(nested)

    yield from walk(req)


def _dedupe_entries(entries: list[tuple[str, str, list[str]]]) -> list[tuple[str, str, list[str]]]:
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    deduped: list[tuple[str, str, list[str]]] = []
    for entry in entries:
        key = (entry[0], entry[1], tuple(entry[2]))
        if key not in seen:
            seen.add(key)
            deduped.append(entry)
    return deduped
