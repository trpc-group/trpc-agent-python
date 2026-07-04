# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool filter integration for the safety scanner."""

from __future__ import annotations

from typing import Any

from trpc_agent_sdk.abc import FilterResult
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import register_tool_filter
from trpc_agent_sdk.log import logger

from ._audit import write_audit_event
from ._policy import ToolSafetyPolicy
from ._scanner import ToolScriptSafetyScanner
from ._telemetry import record_safety_attributes
from ._types import ToolScriptScanRequest


@register_tool_filter("tool_safety")
class ToolSafetyFilter(BaseFilter):
    """Opt-in tool filter that scans script-like tool inputs before execution."""

    def __init__(
        self,
        policy: ToolSafetyPolicy | None = None,
        *,
        policy_path: str = "",
        audit_log_path: str = "",
        block_on_review: bool | None = None,
    ) -> None:
        super().__init__()
        self.policy = policy or (ToolSafetyPolicy.from_file(policy_path) if policy_path else ToolSafetyPolicy.default())
        if block_on_review is not None:
            self.policy.block_on_review = block_on_review
        self.audit_log_path = audit_log_path
        self.scanner = ToolScriptSafetyScanner(self.policy)

    async def _before(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        """Scan script-bearing tool requests before the handler runs."""
        entries = _extract_scripts(req)
        if not entries:
            return None

        tool_name = _tool_name(req)
        cwd = str(_request_value(req, "cwd", "") or "")
        env = _request_value(req, "env", {}) or {}
        if not isinstance(env, dict):
            env = {}
        metadata = _tool_metadata(req)

        for script, language in entries:
            report = self.scanner.scan(
                ToolScriptScanRequest(
                    script=script,
                    language=language,
                    cwd=cwd,
                    env=env,
                    tool_name=tool_name,
                    tool_metadata=metadata,
                )
            )
            self._record_report(report)
            if self.policy.should_block(report.decision):
                rsp.rsp = {
                    "success": False,
                    "error": "SAFETY_GUARD_BLOCKED",
                    "message": report.summary,
                    "safety_report": report.to_dict(),
                }
                rsp.is_continue = False
                return None
        return None

    def _record_report(self, report) -> None:
        record_safety_attributes(report)
        if not self.audit_log_path:
            return
        try:
            write_audit_event(report, self.audit_log_path)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("tool safety audit write failed: %s", exc)


def _request_value(req: Any, key: str, default: Any = None) -> Any:
    if isinstance(req, dict):
        return req.get(key, default)
    return getattr(req, key, default)


def _extract_scripts(req: Any) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []

    code_blocks = _request_value(req, "code_blocks", None)
    if code_blocks:
        for block in code_blocks:
            code = _request_value(block, "code", "")
            language = _request_value(block, "language", "unknown") or "unknown"
            if code:
                entries.append((str(code), str(language)))

    for key, language in (
        ("python_code", "python"),
        ("bash_code", "bash"),
        ("bash", "bash"),
        ("command", "bash"),
        ("cmd", "bash"),
    ):
        value = _request_value(req, key, "")
        if value:
            entries.append((str(value), language))

    for key in ("script", "code"):
        value = _request_value(req, key, "")
        if value:
            language = _request_value(req, "language", "unknown") or "unknown"
            entries.append((str(value), str(language)))

    return _dedupe_entries(entries)


def _tool_metadata(req: Any) -> dict[str, Any]:
    metadata = _request_value(req, "tool_metadata", {}) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    for key in ("timeout", "max_output_bytes"):
        value = _request_value(req, key, None)
        if value is not None:
            metadata[key] = value
    return metadata


def _tool_name(req: Any) -> str:
    try:
        from trpc_agent_sdk.tools._context_var import get_tool_var

        tool = get_tool_var()
        name = getattr(tool, "name", "")
        if name:
            return str(name)
    except Exception:  # pylint: disable=broad-except
        pass
    return str(_request_value(req, "tool_name", "unknown_tool") or "unknown_tool")


def _dedupe_entries(entries: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[tuple[str, str]] = []
    for entry in entries:
        if entry not in seen:
            seen.add(entry)
            deduped.append(entry)
    return deduped
