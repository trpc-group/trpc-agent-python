# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool Filter integration for pre-execution safety checks."""

from __future__ import annotations

import math
from typing import Any
from typing import Protocol

from trpc_agent_sdk.abc import FilterResult
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import FilterType
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.tools._context_var import get_tool_var

from ._audit import AuditSink
from ._guard import SafetyGuard
from ._models import SafetyDecision
from ._models import SafetyReport
from ._models import SafetyScanRequest
from ._models import ScriptLanguage
from ._scanner import SafetyScanner


class OutputLimiter(Protocol):
    """Explicit adapter for a known Tool output contract."""

    def limit(self, value: Any, max_bytes: int) -> Any:
        """Return the same output shape with bounded content."""


class BlockResponseAdapter(Protocol):
    """Explicit adapter for a Tool's blocked-response contract."""

    def blocked(self, report: SafetyReport) -> Any:
        """Return a Tool-compatible value without executing its handler."""


class ReportBlockResponseAdapter:
    """Return the structured safety report as a JSON-compatible mapping."""

    def blocked(self, report: SafetyReport) -> Any:
        return report.model_dump(mode="json")


class BashToolBlockResponseAdapter:
    """Preserve the existing BashTool error response shape."""

    def blocked(self, report: SafetyReport) -> Any:
        return {
            "success": False,
            "error": f"TOOL_SAFETY_BLOCKED: {report.decision.value}",
            "return_code": 126,
            "safety_report": report.model_dump(mode="json"),
        }


class FieldOutputLimiter:
    """Limit a string or bytes response, optionally inside one dict field."""

    def __init__(self, field: str | None = None):
        self.field = field

    def limit(self, value: Any, max_bytes: int) -> Any:
        if self.field is None:
            return self._limit_value(value, max_bytes)
        if not isinstance(value, dict) or self.field not in value:
            return value
        limited = dict(value)
        new_value = self._limit_value(value[self.field], max_bytes)
        if new_value != value[self.field]:
            limited[self.field] = new_value
            limited["output_truncated"] = True
        return limited

    @staticmethod
    def _limit_value(value: Any, max_bytes: int) -> Any:
        if isinstance(value, bytes):
            return value[:max_bytes]
        if isinstance(value, str):
            raw = value.encode("utf-8")
            if len(raw) <= max_bytes:
                return value
            return raw[:max_bytes].decode("utf-8", errors="ignore")
        if isinstance(value, (list, tuple)):
            limited: list[Any] = []
            remaining = max_bytes
            for item in value:
                if not isinstance(item, (bytes, str)):
                    limited.append(item)
                    continue
                if remaining <= 0:
                    break
                item_limited = FieldOutputLimiter._limit_value(item, remaining)
                limited.append(item_limited)
                remaining -= len(item_limited if isinstance(item_limited, bytes) else item_limited.encode("utf-8"))
            return tuple(limited) if isinstance(value, tuple) else limited
        return value


class ToolSafetyFilter(BaseFilter):
    """Scan configured Tool arguments before the Tool handler runs."""

    def __init__(
        self,
        scanner: SafetyScanner,
        *,
        language: ScriptLanguage | str,
        content_field: str,
        argv_field: str | None = None,
        cwd_field: str | None = None,
        timeout_field: str | None = None,
        default_timeout_seconds: float | None = None,
        env_field: str | None = None,
        tool_name: str | None = None,
        audit_sink: AuditSink | None = None,
        block_response_adapter: BlockResponseAdapter | None = None,
        output_limiter: OutputLimiter | None = None,
    ):
        super().__init__()
        if not content_field.strip():
            raise ValueError("content_field cannot be blank")
        if tool_name is not None and not tool_name.strip():
            raise ValueError("tool_name cannot be blank")
        if (default_timeout_seconds is not None
                and (not math.isfinite(default_timeout_seconds) or default_timeout_seconds <= 0)):
            raise ValueError("default_timeout_seconds must be positive")
        self.name = "tool_safety"
        self.type = FilterType.TOOL
        self._guard = SafetyGuard(scanner, audit_sink)
        self._language = ScriptLanguage(language)
        self._content_field = content_field
        self._argv_field = argv_field
        self._cwd_field = cwd_field
        self._timeout_field = timeout_field
        self._default_timeout_seconds = default_timeout_seconds
        self._env_field = env_field
        self._tool_name = tool_name
        self._block_response_adapter = block_response_adapter or ReportBlockResponseAdapter()
        self._output_limiter = output_limiter

    async def _before(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        del ctx
        tool = get_tool_var()
        tool_name = self._tool_name or getattr(tool, "name", None)
        apply_default_timeout = False
        if not tool_name:
            tool_name = "unknown_tool"
        try:
            if not isinstance(req, dict):
                raise ValueError("Tool arguments must be a mapping.")
            content = req.get(self._content_field)
            if not isinstance(content, str):
                raise ValueError(f"Expected string field {self._content_field!r}.")
            cwd = self._field(req, self._cwd_field)
            if cwd is None:
                cwd = getattr(tool, "cwd", None)
            timeout = self._field(req, self._timeout_field)
            if timeout is None:
                timeout = self._default_timeout_seconds
                if self._timeout_field is not None and timeout is not None:
                    apply_default_timeout = True
            env = self._field(req, self._env_field) or {}
            argv = self._field(req, self._argv_field) or []
            if not isinstance(env, dict):
                raise ValueError("The configured env field must be a mapping.")
            if not isinstance(argv, list) or not all(isinstance(value, str) for value in argv):
                raise ValueError("The configured argv field must be a list of strings.")
            if timeout is not None and not isinstance(timeout, (int, float)):
                raise ValueError("The configured timeout field must be numeric.")

            report = self._guard.check(
                SafetyScanRequest(
                    content=content,
                    language=self._language,
                    argv=argv,
                    cwd=str(cwd) if cwd is not None else None,
                    env={
                        str(key): str(value)
                        for key, value in env.items()
                    },
                    timeout_seconds=float(timeout) if timeout is not None else None,
                    tool_name=str(tool_name),
                    metadata={"filter": self.name},
                ))
        except (TypeError, ValueError) as ex:
            report = self._guard.invalid_request(str(ex), tool_name=str(tool_name))
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("tool safety filter failed: %s", type(ex).__name__)
            report = self._guard.invalid_request("internal adapter failure", tool_name=str(tool_name))
        if (report.decision == SafetyDecision.ALLOW and apply_default_timeout and isinstance(req, dict)
                and self._timeout_field is not None):
            req[self._timeout_field] = timeout
        if report.decision != SafetyDecision.ALLOW:
            try:
                rsp.rsp = self._block_response_adapter.blocked(report)
            except Exception as ex:  # pylint: disable=broad-except
                logger.error("tool safety block response adapter failed: %s", type(ex).__name__)
                rsp.rsp = report.model_dump(mode="json")
            rsp.error = None
            rsp.is_continue = False

    async def _after(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        del ctx, req
        if self._output_limiter is not None and rsp.error is None:
            limit = self._guard.policy.limits.max_output_size_bytes
            try:
                rsp.rsp = self._output_limiter.limit(rsp.rsp, limit)
            except Exception as ex:  # pylint: disable=broad-except
                logger.error("tool safety output limiter failed: %s", type(ex).__name__)

    @staticmethod
    def _field(req: dict[str, Any], field: str | None) -> Any:
        return req.get(field) if field else None
