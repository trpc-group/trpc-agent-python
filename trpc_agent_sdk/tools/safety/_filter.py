# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool Filter integration for pre-execution script checks."""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
import re
import shlex
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Optional
from typing import Union

from pydantic import BaseModel
from trpc_agent_sdk.abc import FilterResult
from trpc_agent_sdk.abc import FilterType
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.tools._context_var import get_tool_var

from ._extractor import extract_safety_requests
from ._guard import ToolSafetyGuard
from ._models import SafetyDecision
from ._models import SafetyReport
from ._models import SafetyScanRequest
from ._redaction import contains_secret_literal

ReviewerResult = Union[bool, Awaitable[bool]]
SafetyReviewer = Callable[[SafetyReport], ReviewerResult]
SafetyRequestExtractor = Callable[..., Union[SafetyScanRequest, Sequence[SafetyScanRequest], None]]

_SCRIPT_FIELDS = {"chars", "cmd", "code", "command", "script", "source", "stdin"}
_ENVIRONMENT_FIELDS = {"env", "environment"}
_ARGUMENT_FIELDS = {"args", "argv"}
_OUTPUT_FIELDS = {"formatted_output", "output", "result", "stderr", "stdout"}
_SENSITIVE_FIELDS = {
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "password",
    "passwd",
    "private_key",
    "secret",
    "set_cookie",
    "token",
}
_REDACTED_VALUE = "<redacted>"
_CAMEL_ACRONYM_RE = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL_BOUNDARY_RE = re.compile(r"([a-z0-9])([A-Z])")


def _hash_placeholder(value: Any) -> str:
    try:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        serialized = repr(value)
    digest = hashlib.sha256(serialized.encode("utf-8", errors="replace")).hexdigest()
    return f"<redacted sha256:{digest}>"


def _redact_environment(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _REDACTED_VALUE for key in value}
    return _hash_placeholder(value)


def _normalize_field_name(value: Any) -> str:
    name = str(value).replace("-", "_")
    name = _CAMEL_ACRONYM_RE.sub(r"\1_\2", name)
    return _CAMEL_BOUNDARY_RE.sub(r"\1_\2", name).lower()


def _sensitive_name(value: str) -> bool:
    padded = f"_{_normalize_field_name(value)}_"
    return any(f"_{field}_" in padded for field in _SENSITIVE_FIELDS)


def _sensitive_argument_flag(value: Any) -> tuple[bool, bool]:
    if not isinstance(value, str) or not value.startswith("-"):
        return False, False
    flag, separator, _ = value.lstrip("-").partition("=")
    normalized = _normalize_field_name(flag)
    return _sensitive_name(normalized), bool(separator)


def _redact_command_arguments(value: Any) -> Any:
    if isinstance(value, str):
        try:
            arguments = shlex.split(value)
        except ValueError:
            return _hash_placeholder(value)
        if any(_sensitive_argument_flag(argument)[0] for argument in arguments):
            return _hash_placeholder(value)
        return sanitize_telemetry_args(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        sanitized = []
        redact_next = False
        for item in value:
            if redact_next:
                sanitized.append(_REDACTED_VALUE)
                redact_next = False
                continue
            sensitive, inline_value = _sensitive_argument_flag(item)
            if not sensitive:
                sanitized.append(sanitize_telemetry_args(item))
                continue
            if inline_value:
                sanitized.append(f"{str(item).split('=', 1)[0]}={_REDACTED_VALUE}")
            else:
                sanitized.append(item)
                redact_next = True
        return sanitized
    return sanitize_telemetry_args(value)


def _effective_tool_args(tool: Any, args: Any) -> Any:
    """Apply trusted tool defaults and fixed overrides before authorization."""

    if not isinstance(args, Mapping):
        return args
    effective = dict(args)
    fixed_overrides = getattr(tool, "_run_tool_kwargs", None)
    if isinstance(fixed_overrides, Mapping):
        effective.update(fixed_overrides)

    timeout_key = next((key for key in ("timeout_seconds", "timeout_sec", "timeout") if key in effective), "timeout")
    timeout = effective.get(timeout_key)
    default_timeout = getattr(tool, "_timeout", None)
    if default_timeout is None:
        default_timeout = getattr(tool, "DEFAULT_TIMEOUT_SECONDS", None)
    zero_uses_default = hasattr(tool, "_timeout") or getattr(tool, "ZERO_TIMEOUT_USES_DEFAULT", False)
    use_tool_timeout = timeout is None or timeout == "" or (timeout == 0 and zero_uses_default)
    if default_timeout is not None and use_tool_timeout:
        effective[timeout_key] = default_timeout
    tool_cwd = getattr(tool, "cwd", None)
    requested_cwd = effective.get("cwd")
    if tool_cwd:
        if requested_cwd is None or requested_cwd == "":
            effective["cwd"] = str(tool_cwd)
        elif not Path(requested_cwd).is_absolute():
            effective["cwd"] = str(Path(tool_cwd) / requested_cwd)
    return effective


def sanitize_telemetry_args(args: Any) -> Any:
    """Return a deep redacted copy suitable for generic tool telemetry.

    Script-bearing fields are replaced as a whole with a hash placeholder.
    Environment names remain useful for diagnostics, but every value is
    replaced. The input object is never mutated.
    """

    if isinstance(args, BaseModel):
        return sanitize_telemetry_args(args.model_dump(mode="python"))
    if isinstance(args, Mapping):
        sanitized = {}
        for key, value in args.items():
            normalized = _normalize_field_name(key)
            if normalized in _SCRIPT_FIELDS:
                sanitized[key] = _hash_placeholder(value)
            elif normalized in _ENVIRONMENT_FIELDS:
                sanitized[key] = _redact_environment(value)
            elif normalized in _ARGUMENT_FIELDS:
                sanitized[key] = _redact_command_arguments(value)
            elif normalized in _OUTPUT_FIELDS:
                sanitized[key] = _hash_placeholder(value)
            elif _sensitive_name(normalized):
                sanitized[key] = _REDACTED_VALUE
            else:
                sanitized[key] = sanitize_telemetry_args(value)
        return sanitized
    if isinstance(args, Sequence) and not isinstance(args, (str, bytes, bytearray)):
        return [sanitize_telemetry_args(item) for item in args]
    if isinstance(args, str) and contains_secret_literal(args):
        return _hash_placeholder(args)
    return args


def _blocked_response(report: SafetyReport) -> dict[str, Any]:
    primary_rule_id = getattr(report, "rule_id", None) or (report.rule_ids[0] if report.rule_ids else None)
    return {
        "error": "tool_safety_blocked",
        "message": "Tool execution was blocked by the configured safety policy.",
        "blocked": True,
        "decision": report.decision.value,
        "risk_level": report.risk_level.value,
        "rule_id": primary_rule_id,
        "rule_ids": list(report.rule_ids),
        "safety_report": report.model_dump(mode="json"),
    }


class ToolSafetyFilter(BaseFilter):
    """Run a :class:`ToolSafetyGuard` before a script-capable tool."""

    run_last_before_handler = True

    def __init__(
        self,
        guard: ToolSafetyGuard,
        *,
        reviewer: Optional[SafetyReviewer] = None,
        extractor: SafetyRequestExtractor = extract_safety_requests,
        name: str = "tool_safety",
    ) -> None:
        super().__init__()
        self._guard = guard
        self._reviewer = reviewer
        self._extractor = extractor
        self._name = name
        self._type = FilterType.TOOL

    def sanitize_telemetry_args(self, args: Any) -> Any:
        """Redact generic tool span arguments after this filter runs."""

        return sanitize_telemetry_args(args)

    def sanitize_telemetry_response(self, response: Any) -> Any:
        """Hash the complete tool response before generic tracing."""

        return _hash_placeholder(response)

    async def _before(self, ctx: AgentContext, req: Any, rsp: FilterResult) -> None:
        """Scan, optionally review, and stop the chain for blocked reports."""

        del ctx
        tool = get_tool_var()
        tool_name = getattr(tool, "name", "unknown_tool") or "unknown_tool"
        try:
            extracted = self._extractor(_effective_tool_args(tool, req), tool_name=tool_name)
            if extracted is None:
                return
            if isinstance(extracted, SafetyScanRequest):
                scan_requests = [extracted]
            elif isinstance(extracted, Sequence) and not isinstance(extracted, (str, bytes, bytearray)):
                scan_requests = list(extracted)
            else:
                raise TypeError("tool safety extractor must return SafetyScanRequest, a sequence, or None")
            if not scan_requests:
                return
            if not all(isinstance(item, SafetyScanRequest) for item in scan_requests):
                raise TypeError("tool safety extractor returned a non-SafetyScanRequest item")
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Tool safety input extraction failed for %s: %s", tool_name, type(exc).__name__)
            report = self._guard.failure_report(tool_name=tool_name, error=exc, rule_id="SCAN-INPUT")
            rsp.rsp = _blocked_response(report)
            rsp.is_continue = False
            rsp.error = None
            return

        # Delay recording until an optional human decision is finalized so
        # every execution emits exactly one audit event.
        reports = []
        for scan_request in scan_requests:
            try:
                reports.append(self._guard.scan(scan_request, record=False))
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning("Tool safety scanner failed for %s: %s", tool_name, type(exc).__name__)
                reports.append(
                    self._guard.failure_report(
                        tool_name=tool_name,
                        error=exc,
                        request=scan_request,
                        record=False,
                    ))
        try:
            report = self._guard.merge_reports(reports)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Tool safety report aggregation failed for %s: %s", tool_name, type(exc).__name__)
            report = self._guard.failure_report(tool_name=tool_name, error=exc, record=False)
        if report.decision is SafetyDecision.NEEDS_HUMAN_REVIEW and self._reviewer is not None:
            approved = False
            try:
                review_result = self._reviewer(report)
                if inspect.isawaitable(review_result):
                    review_result = await review_result
                if type(review_result) is not bool:  # pylint: disable=unidiomatic-typecheck
                    raise TypeError("tool safety reviewer must return bool")
                approved = review_result
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning("Tool safety reviewer failed for %s: %s", tool_name, exc)
            report = self._guard.finalize_review(report, approved, record=True)
        else:
            self._guard.record(report)

        if report.blocked:
            rsp.rsp = _blocked_response(report)
            rsp.is_continue = False
            rsp.error = None
