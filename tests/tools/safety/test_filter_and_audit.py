# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import patch

from trpc_agent_sdk.abc import FilterResult
from trpc_agent_sdk.context import new_agent_context
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import get_tool_filter
from trpc_agent_sdk.filter import register_tool_filter
from trpc_agent_sdk.filter import run_filters
from trpc_agent_sdk.tools._context_var import reset_tool_var
from trpc_agent_sdk.tools._context_var import set_tool_var
from trpc_agent_sdk.tools.safety import RiskLevel
from trpc_agent_sdk.tools.safety import SafetyAuditEvent
from trpc_agent_sdk.tools.safety import SafetyDecision
from trpc_agent_sdk.tools.safety import SafetyPolicy
from trpc_agent_sdk.tools.safety import SafetyReport
from trpc_agent_sdk.tools.safety import ScanTarget
from trpc_agent_sdk.tools.safety import ScriptLanguage
from trpc_agent_sdk.tools.safety import ToolSafetyFilter


class DummyTool:
    def __init__(self, name: str = "workspace_exec", description: str = "dummy tool"):
        self.name = name
        self.description = description


class RecordingAuditLogger:
    def __init__(self):
        self.events: list[SafetyAuditEvent] = []

    def emit(self, event: SafetyAuditEvent) -> None:
        self.events.append(event)


def _ensure_safety_filter_registered() -> None:
    if get_tool_filter("tool_safety_guard") is None:
        register_tool_filter("tool_safety_guard")(ToolSafetyFilter)


def _enable_caplog_logger(name: str) -> None:
    logging.disable(logging.NOTSET)
    target_logger = logging.getLogger(name)
    target_logger.disabled = False
    target_logger.propagate = True


class StaticScanner:
    def __init__(
        self,
        policy: SafetyPolicy,
        *,
        decision: SafetyDecision = SafetyDecision.ALLOW,
        risk_level: RiskLevel = RiskLevel.LOW,
    ):
        self.policy = policy
        self.decision = decision
        self.risk_level = risk_level
        self.targets: list[ScanTarget] = []

    def scan(self, target: ScanTarget) -> SafetyReport:
        self.targets.append(target)
        return SafetyReport(
            decision=self.decision,
            risk_level=self.risk_level,
            findings=[],
            elapsed_ms=1.25,
            blocked=False,
            language=target.language,
            policy_name=self.policy.name,
            metadata={"target_tool": target.tool_name} if target.tool_name else {},
        )


class RaisingScanner:
    def __init__(self, policy: SafetyPolicy):
        self.policy = policy

    def scan(self, target: ScanTarget) -> SafetyReport:
        raise RuntimeError("secret-token-value")


class RecordingFilter(BaseFilter):
    def __init__(self):
        super().__init__()
        self.before_called = False

    async def _before(self, ctx, req, rsp: FilterResult):
        self.before_called = True
        return None


def _run_filter(
    filter_: ToolSafetyFilter,
    req: dict[str, Any],
    *,
    tool: DummyTool | None = None,
    extra_filters: list[BaseFilter] | None = None,
) -> tuple[Any, Any, list[str]]:
    calls: list[str] = []
    ctx = new_agent_context(metadata={"function_call_id": "fc-1", "agent_name": "agent-a"})

    async def handle():
        calls.append("handler")
        return {"ok": True}

    async def run():
        token = set_tool_var(tool or DummyTool())
        try:
            filters = [filter_]
            if extra_filters:
                filters.extend(extra_filters)
            result = await run_filters(ctx, req, filters, handle)
            return result, ctx, calls
        finally:
            reset_tool_var(token)

    return asyncio.run(run())


class TestToolSafetyFilter:
    def test_allow_calls_handler_and_records_report(self):
        policy = SafetyPolicy()
        scanner = StaticScanner(policy)
        audit_logger = RecordingAuditLogger()
        filter_ = ToolSafetyFilter(scanner=scanner, audit_logger=audit_logger)

        with patch("trpc_agent_sdk.tools.safety._filter.set_safety_span_attributes") as mock_span:
            result, ctx, calls = _run_filter(
                filter_,
                {"code": "print('ok')", "language": "python"},
                tool=DummyTool("python_tool"),
            )

        assert result == {"ok": True}
        assert calls == ["handler"]
        assert len(scanner.targets) == 1
        assert scanner.targets[0].language == ScriptLanguage.PYTHON
        assert ctx.metadata["tool_safety.last_report"]["decision"] == "allow"
        assert audit_logger.events[0].tool_name == "python_tool"
        assert audit_logger.events[0].function_call_id == "fc-1"
        mock_span.assert_called_once()

    def test_deny_blocks_before_handler(self):
        policy = SafetyPolicy()
        scanner = StaticScanner(policy, decision=SafetyDecision.DENY, risk_level=RiskLevel.HIGH)
        audit_logger = RecordingAuditLogger()
        filter_ = ToolSafetyFilter(scanner=scanner, audit_logger=audit_logger)

        result, ctx, calls = _run_filter(filter_, {"command": "rm -rf ~/.ssh"})

        assert calls == []
        assert result["ok"] is False
        assert result["blocked"] is True
        assert result["error"] == "Tool execution blocked by safety policy"
        assert result["safety_report"]["decision"] == "deny"
        assert result["safety_report"]["blocked"] is True
        assert ctx.metadata["tool_safety.last_report"]["blocked"] is True
        assert audit_logger.events[0].blocked is True

    def test_review_blocks_by_default_and_can_be_nonblocking(self):
        blocking_policy = SafetyPolicy()
        blocking_filter = ToolSafetyFilter(
            scanner=StaticScanner(
                blocking_policy,
                decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                risk_level=RiskLevel.MEDIUM,
            )
        )

        blocked, _, blocked_calls = _run_filter(blocking_filter, {"command": "cat data.txt | sort"})

        assert blocked_calls == []
        assert blocked["blocked"] is True
        assert blocked["safety_report"]["decision"] == "needs_human_review"

        nonblocking_policy = SafetyPolicy(review_blocks_execution=False)
        audit_logger = RecordingAuditLogger()
        nonblocking_filter = ToolSafetyFilter(
            scanner=StaticScanner(
                nonblocking_policy,
                decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                risk_level=RiskLevel.MEDIUM,
            ),
            audit_logger=audit_logger,
        )

        allowed, _, allowed_calls = _run_filter(nonblocking_filter, {"command": "cat data.txt | sort"})

        assert allowed == {"ok": True}
        assert allowed_calls == ["handler"]
        assert audit_logger.events[0].decision == SafetyDecision.NEEDS_HUMAN_REVIEW
        assert audit_logger.events[0].blocked is False

    def test_registry_name_uses_default_policy_instance(self):
        _ensure_safety_filter_registered()

        registered = get_tool_filter("tool_safety_guard")

        assert isinstance(registered, ToolSafetyFilter)
        assert getattr(registered, "_policy").name == "default"

    def test_env_values_are_not_passed_to_scan_target_or_audit(self):
        policy = SafetyPolicy()
        scanner = StaticScanner(policy)
        audit_logger = RecordingAuditLogger()
        filter_ = ToolSafetyFilter(scanner=scanner, audit_logger=audit_logger)
        secret = "secret-token-value"

        with patch("trpc_agent_sdk.tools.safety._filter.set_safety_span_attributes") as mock_span:
            result, _, _ = _run_filter(
                filter_,
                {
                    "command": "echo $OPENAI_API_KEY",
                    "stdin": "stdin body that must not reach audit",
                    "env": {"OPENAI_API_KEY": secret},
                },
            )

        assert result == {"ok": True}
        assert scanner.targets[0].env == {"OPENAI_API_KEY": ""}
        dumped_audit = "".join(event.model_dump_json() for event in audit_logger.events)
        dumped_span_calls = str(mock_span.call_args_list)
        assert secret not in dumped_audit
        assert secret not in dumped_span_calls
        assert "stdin body" not in dumped_audit
        assert "echo $OPENAI_API_KEY" not in dumped_audit

    def test_non_script_tool_args_are_not_scanned_or_audited(self):
        policy = SafetyPolicy()
        scanner = StaticScanner(policy)
        audit_logger = RecordingAuditLogger()
        filter_ = ToolSafetyFilter(scanner=scanner, audit_logger=audit_logger)

        result, _, calls = _run_filter(filter_, {"query": "hello"})

        assert result == {"ok": True}
        assert calls == ["handler"]
        assert scanner.targets == []
        assert audit_logger.events == []

    def test_timeout_is_mapped_without_treating_yield_time_as_timeout(self):
        policy = SafetyPolicy()
        scanner = StaticScanner(policy)
        filter_ = ToolSafetyFilter(scanner=scanner)

        result, _, _ = _run_filter(filter_, {"command": "echo ok", "timeout": 15, "yield_time_ms": 999})

        assert result == {"ok": True}
        assert scanner.targets[0].timeout_seconds == 15.0

    def test_shell_tools_default_to_shell_language(self):
        policy = SafetyPolicy()
        scanner = StaticScanner(policy)
        filter_ = ToolSafetyFilter(scanner=scanner)

        result, _, _ = _run_filter(filter_, {"command": "echo ok"}, tool=DummyTool("skill_run"))

        assert result == {"ok": True}
        assert scanner.targets[0].language == ScriptLanguage.SHELL

    def test_fail_closed_blocks_and_logs_without_exception_detail(self, caplog):
        policy = SafetyPolicy(fail_closed=True)
        filter_ = ToolSafetyFilter(scanner=RaisingScanner(policy), audit_logger=RecordingAuditLogger())
        _enable_caplog_logger("trpc_agent_sdk.tools.safety._filter")
        caplog.set_level(logging.WARNING, logger="trpc_agent_sdk.tools.safety._filter")

        result, _, calls = _run_filter(filter_, {"command": "echo ok"})

        assert calls == []
        assert result["blocked"] is True
        assert result["error"] == "Tool safety scan failed closed"
        assert result["safety_report"]["decision"] == "deny"
        assert "RuntimeError" in caplog.text
        assert "secret-token-value" not in caplog.text

    def test_fail_open_allows_and_logs_without_exception_detail(self, caplog):
        policy = SafetyPolicy(fail_closed=False)
        audit_logger = RecordingAuditLogger()
        filter_ = ToolSafetyFilter(scanner=RaisingScanner(policy), audit_logger=audit_logger)
        _enable_caplog_logger("trpc_agent_sdk.tools.safety._filter")
        caplog.set_level(logging.WARNING, logger="trpc_agent_sdk.tools.safety._filter")

        with patch("trpc_agent_sdk.tools.safety._filter.set_safety_span_attributes") as mock_span:
            result, _, calls = _run_filter(filter_, {"command": "echo ok"})

        assert result == {"ok": True}
        assert calls == ["handler"]
        assert audit_logger.events == []
        mock_span.assert_not_called()
        assert "RuntimeError" in caplog.text
        assert "secret-token-value" not in caplog.text

    def test_blocked_safety_filter_prevents_later_filters(self):
        policy = SafetyPolicy()
        safety_filter = ToolSafetyFilter(
            scanner=StaticScanner(policy, decision=SafetyDecision.DENY, risk_level=RiskLevel.HIGH)
        )
        later_filter = RecordingFilter()

        result, _, calls = _run_filter(safety_filter, {"command": "rm -rf ~/.ssh"}, extra_filters=[later_filter])

        assert result["blocked"] is True
        assert calls == []
        assert later_filter.before_called is False
