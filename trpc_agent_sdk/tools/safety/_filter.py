# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool Filter integration for pre-execution safety checks."""

from __future__ import annotations

from typing import Any
from typing import Protocol

from trpc_agent_sdk.abc import FilterResult
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import FilterType
from trpc_agent_sdk.tools._context_var import get_tool_var

from ._audit import AuditSink
from ._guard import SafetyGuard
from ._models import SafetyDecision
from ._models import SafetyScanRequest
from ._models import ScriptLanguage
from ._scanner import SafetyScanner


class OutputLimiter(Protocol):
    """Explicit adapter for a known Tool output contract."""

    def limit(self, value: Any, max_bytes: int) -> Any:
        """Return the same output shape with bounded content."""


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
        env_field: str | None = None,
        tool_name: str | None = None,
        audit_sink: AuditSink | None = None,
        output_limiter: OutputLimiter | None = None,
    ):
        super().__init__()
        if not content_field.strip():
            raise ValueError("content_field cannot be blank")
        if tool_name is not None and not tool_name.strip():
            raise ValueError("tool_name cannot be blank")
        self.name = "tool_safety"
        self.type = FilterType.TOOL
        self._guard = SafetyGuard(scanner, audit_sink)
        self._language = ScriptLanguage(language)
        self._content_field = content_field
        self._argv_field = argv_field
        self._cwd_field = cwd_field
        self._timeout_field = timeout_field
        self._env_field = env_field
        self._tool_name = tool_name
        self._output_limiter = output_limiter

    async def _before(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        del ctx
        if not isinstance(req, dict):
            raise ValueError("ToolSafetyFilter requires Tool arguments as a dict")
        content = req.get(self._content_field)
        if not isinstance(content, str):
            raise ValueError(f"ToolSafetyFilter expected string field {self._content_field!r}")

        tool = get_tool_var()
        tool_name = self._tool_name or getattr(tool, "name", None)
        if not tool_name:
            raise ValueError("ToolSafetyFilter could not resolve a non-empty tool_name")
        cwd = self._field(req, self._cwd_field)
        if cwd is None:
            cwd = getattr(tool, "cwd", None)
        timeout = self._field(req, self._timeout_field)
        env = self._field(req, self._env_field) or {}
        argv = self._field(req, self._argv_field) or []
        if not isinstance(env, dict):
            raise ValueError("ToolSafetyFilter env field must be a dict")
        if not isinstance(argv, list) or not all(isinstance(value, str) for value in argv):
            raise ValueError("ToolSafetyFilter argv field must be a list of strings")

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
        if report.decision != SafetyDecision.ALLOW:
            rsp.rsp = report.model_dump(mode="json")
            rsp.error = None
            rsp.is_continue = False

    async def _after(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        del ctx, req
        if self._output_limiter is not None and rsp.error is None:
            limit = self._guard.scanner.policy.limits.max_output_size_bytes
            rsp.rsp = self._output_limiter.limit(rsp.rsp, limit)

    @staticmethod
    def _field(req: dict[str, Any], field: str | None) -> Any:
        return req.get(field) if field else None
