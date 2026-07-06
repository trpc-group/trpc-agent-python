# Tencent is pleased to support the open source community by making
# tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Unit tests for safety scanner type definitions.

Mirrors trpc-agent-go/tool/safety/safety.go types.
"""

from __future__ import annotations

import dataclasses
import json
import pytest


class TestDecision:
    def test_decision_constants(self):
        from trpc_agent_sdk.tools.safety._types import (
            Decision, DECISION_ALLOW, DECISION_DENY,
            DECISION_ASK, DECISION_NEEDS_HUMAN_REVIEW,
        )
        assert Decision(DECISION_ALLOW) == Decision("allow")
        assert Decision(DECISION_DENY) == Decision("deny")
        assert Decision(DECISION_ASK) == Decision("ask")
        assert Decision(DECISION_NEEDS_HUMAN_REVIEW) == Decision("needs_human_review")

    def test_decision_rank_order(self):
        from trpc_agent_sdk.tools.safety._types import (
            DECISION_ALLOW, DECISION_ASK, DECISION_NEEDS_HUMAN_REVIEW,
            DECISION_DENY, decision_rank,
        )
        assert decision_rank(DECISION_DENY) > decision_rank(DECISION_NEEDS_HUMAN_REVIEW)
        assert decision_rank(DECISION_NEEDS_HUMAN_REVIEW) > decision_rank(DECISION_ASK)
        assert decision_rank(DECISION_ASK) > decision_rank(DECISION_ALLOW)

    def test_decision_str_value(self):
        from trpc_agent_sdk.tools.safety._types import DECISION_ALLOW
        assert DECISION_ALLOW.value == "allow"


class TestRiskLevel:
    def test_risk_level_constants(self):
        from trpc_agent_sdk.tools.safety._types import (
            RISK_LOW, RISK_MEDIUM, RISK_HIGH, RISK_CRITICAL,
        )
        assert RISK_LOW.value == "low"
        assert RISK_MEDIUM.value == "medium"
        assert RISK_HIGH.value == "high"
        assert RISK_CRITICAL.value == "critical"

    def test_risk_rank_order(self):
        from trpc_agent_sdk.tools.safety._types import (
            RISK_LOW, RISK_MEDIUM, RISK_HIGH, RISK_CRITICAL, risk_rank,
        )
        assert risk_rank(RISK_CRITICAL) > risk_rank(RISK_HIGH)
        assert risk_rank(RISK_HIGH) > risk_rank(RISK_MEDIUM)
        assert risk_rank(RISK_MEDIUM) > risk_rank(RISK_LOW)


class TestFinding:
    def test_finding_fields(self):
        from trpc_agent_sdk.tools.safety._types import Finding, DECISION_DENY, RISK_CRITICAL
        f = Finding(
            decision=DECISION_DENY,
            risk_level=RISK_CRITICAL,
            rule_id="test.rule",
            evidence=["/etc/passwd"],
            recommendation="Do not access system files.",
        )
        assert f.decision == DECISION_DENY
        assert f.rule_id == "test.rule"
        assert f.evidence == ["/etc/passwd"]

    def test_finding_beats_by_decision(self):
        from trpc_agent_sdk.tools.safety._types import (
            Finding, finding_beats, DECISION_DENY, DECISION_ALLOW,
            RISK_LOW, RISK_CRITICAL,
        )
        bad = Finding(DECISION_DENY, RISK_LOW, "r1", ["ev"], "rec")
        good = Finding(DECISION_ALLOW, RISK_CRITICAL, "r2", ["ev"], "rec")
        assert finding_beats(bad, good), "deny should beat allow regardless of risk"

    def test_finding_beats_by_risk_when_same_decision(self):
        from trpc_agent_sdk.tools.safety._types import (
            Finding, finding_beats, DECISION_DENY,
            RISK_LOW, RISK_CRITICAL,
        )
        critical = Finding(DECISION_DENY, RISK_CRITICAL, "r1", ["ev"], "rec")
        low = Finding(DECISION_DENY, RISK_LOW, "r2", ["ev"], "rec")
        assert finding_beats(critical, low), "critical should beat low for same decision"


class TestReport:
    def test_report_serialization(self):
        from trpc_agent_sdk.tools.safety._types import (
            Report, Finding, DECISION_ALLOW, RISK_LOW,
        )
        r = Report(
            decision=DECISION_ALLOW,
            risk_level=RISK_LOW,
            recommendation="Safe.",
            tool_name="test_tool",
            command="echo hi",
            backend="workspaceexec",
            blocked=False,
            duration_ms=5,
            redacted=False,
            findings=[],
        )
        data = dataclasses.asdict(r)
        assert data["decision"] == "allow"
        assert data["blocked"] == False

    def test_report_blocked_if_deny(self):
        from trpc_agent_sdk.tools.safety._types import (
            Report, DECISION_DENY, RISK_HIGH,
        )
        r = Report(
            decision=DECISION_DENY,
            risk_level=RISK_HIGH,
            recommendation="Blocked.",
            tool_name="test",
            command="rm -rf /",
            backend="workspaceexec",
            blocked=True,
            duration_ms=1,
            redacted=False,
        )
        assert r.blocked is True

    def test_span_attributes(self):
        from trpc_agent_sdk.tools.safety._types import (
            Report, DECISION_DENY, RISK_CRITICAL,
        )
        r = Report(
            decision=DECISION_DENY,
            risk_level=RISK_CRITICAL,
            rule_id="dangerous.rm_rf",
            recommendation="Denied.",
            tool_name="ws",
            command="rm -rf /",
            backend="workspaceexec",
            blocked=True,
            duration_ms=2,
            redacted=False,
        )
        attrs = r.span_attributes()
        assert attrs["tool.safety.decision"] == "deny"
        assert attrs["tool.safety.risk_level"] == "critical"
        assert attrs["tool.safety.rule_id"] == "dangerous.rm_rf"


class TestAuditEvent:
    def test_audit_event_jsonl(self):
        from trpc_agent_sdk.tools.safety._types import (
            AuditEvent, DECISION_DENY, RISK_HIGH,
        )
        import time
        evt = AuditEvent(
            timestamp=time.time(),
            tool_name="test",
            decision=DECISION_DENY,
            risk_level=RISK_HIGH,
            rule_id="r1",
            duration_ms=5,
            redacted=False,
            blocked=True,
            backend="workspaceexec",
        )
        line = json.dumps(dataclasses.asdict(evt), default=str)
        data = json.loads(line)
        assert data["decision"] == "deny"
        assert data["blocked"] == True


class TestPolicy:
    def test_policy_defaults(self):
        from trpc_agent_sdk.tools.safety._types import Policy
        p = Policy()
        assert p.max_timeout_seconds == 0  # default before DefaultPolicy()
        assert p.max_output_bytes == 0

    def test_policy_fields(self):
        from trpc_agent_sdk.tools.safety._types import Policy
        p = Policy(
            denied_commands=["rm", "sudo"],
            denied_paths=["/etc", ".ssh"],
            network_allowlist=["api.github.com"],
            max_timeout_seconds=300,
        )
        assert "rm" in p.denied_commands
        assert "/etc" in p.denied_paths
        assert "api.github.com" in p.network_allowlist


class TestRequest:
    def test_request_fields(self):
        from trpc_agent_sdk.tools.safety._types import Request
        r = Request(
            tool_name="workspace_exec",
            command="go test ./...",
            cwd=".",
            backend="workspaceexec",
            timeout_seconds=30,
        )
        assert r.tool_name == "workspace_exec"
        assert r.command == "go test ./..."

    def test_request_with_code_blocks(self):
        from trpc_agent_sdk.tools.safety._types import Request, CodeBlock
        r = Request(
            tool_name="execute_code",
            backend="codeexec",
            code_blocks=[CodeBlock(language="python", code="print(1)")],
        )
        assert len(r.code_blocks) == 1
        assert r.code_blocks[0].language == "python"
