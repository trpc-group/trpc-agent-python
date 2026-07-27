# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for AuditEvent and AuditLogger."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from trpc_agent_sdk.tools.safety import AuditEvent
from trpc_agent_sdk.tools.safety import AuditLogger
from trpc_agent_sdk.tools.safety import Decision
from trpc_agent_sdk.tools.safety import RiskLevel
from trpc_agent_sdk.tools.safety import SafetyFinding
from trpc_agent_sdk.tools.safety import SafetyReport
from trpc_agent_sdk.tools.safety import ScanTarget
from trpc_agent_sdk.tools.safety import ScriptLanguage


class TestAuditEvent:

    def test_minimal_construction(self):
        event = AuditEvent(
            tool_name="test_tool",
            decision=Decision.ALLOW,
            risk_level=RiskLevel.LOW,
            duration_ms=5,
            blocked=False,
            sanitized=False,
            target=ScanTarget.TOOL,
            language=ScriptLanguage.PYTHON,
        )
        assert event.tool_name == "test_tool"
        assert event.decision == Decision.ALLOW
        assert event.timestamp

    def test_full_construction(self):
        event = AuditEvent(
            tool_name="my_tool",
            decision=Decision.DENY,
            risk_level=RiskLevel.CRITICAL,
            rule_ids=["R001_BASH_RECURSIVE_DELETE", "R006_API_KEY_LEAK"],
            duration_ms=42,
            blocked=True,
            sanitized=False,
            target=ScanTarget.TOOL,
            language=ScriptLanguage.BASH,
            trace_attributes={"trace_id": "abc123"},
        )
        assert len(event.rule_ids) == 2
        assert event.trace_attributes["trace_id"] == "abc123"


class TestAuditLoggerFromReport:

    def test_transforms_correctly(self):
        finding = SafetyFinding(
            rule_id="R001_BASH_RECURSIVE_DELETE",
            rule_name="Bash Recursive Delete",
            risk_type="dangerous_file_operation",
            risk_level=RiskLevel.CRITICAL,
            evidence="rm -rf /",
            recommendation="Do not recursively delete.",
        )
        report = SafetyReport(
            tool_name="danger_tool",
            decision=Decision.DENY,
            risk_level=RiskLevel.CRITICAL,
            blocked=True,
            sanitized=False,
            duration_ms=15,
            language=ScriptLanguage.BASH,
            target=ScanTarget.TOOL,
            rule_ids=["R001_BASH_RECURSIVE_DELETE"],
            summary="Critical: recursive delete detected.",
            findings=[finding],
            telemetry_attributes={"key": "value"},
        )
        event = AuditLogger.from_report(report)
        assert event.tool_name == "danger_tool"
        assert event.decision == Decision.DENY
        assert event.risk_level == RiskLevel.CRITICAL
        assert event.blocked is True
        assert len(event.rule_ids) == 1
        assert event.trace_attributes == {"key": "value"}

    def test_minimal_report(self):
        report = SafetyReport(
            tool_name="safe_tool",
            decision=Decision.ALLOW,
            risk_level=RiskLevel.LOW,
            blocked=False,
            sanitized=False,
            duration_ms=3,
            language=ScriptLanguage.PYTHON,
            target=ScanTarget.TOOL,
        )
        event = AuditLogger.from_report(report)
        assert event.decision == Decision.ALLOW
        assert event.rule_ids == []


class TestAuditLoggerRecord:

    def test_record_writes_json_line(self):
        report = SafetyReport(
            tool_name="test_tool",
            decision=Decision.ALLOW,
            risk_level=RiskLevel.LOW,
            blocked=False,
            sanitized=False,
            duration_ms=1,
            language=ScriptLanguage.PYTHON,
            target=ScanTarget.TOOL,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = str(Path(tmpdir) / "audit.jsonl")
            logger = AuditLogger(path=log_path)
            event = logger.record(report)
            assert isinstance(event, AuditEvent)

            with open(log_path) as f:
                lines = f.readlines()
            assert len(lines) == 1
            parsed = json.loads(lines[0])
            assert parsed["tool_name"] == "test_tool"
            assert parsed["decision"] == "allow"

    def test_record_appends_multiple(self):
        report1 = SafetyReport(
            tool_name="tool_a",
            decision=Decision.ALLOW,
            risk_level=RiskLevel.LOW,
            blocked=False,
            sanitized=False,
            duration_ms=1,
            language=ScriptLanguage.PYTHON,
            target=ScanTarget.TOOL,
        )
        report2 = SafetyReport(
            tool_name="tool_b",
            decision=Decision.DENY,
            risk_level=RiskLevel.CRITICAL,
            blocked=True,
            sanitized=False,
            duration_ms=2,
            language=ScriptLanguage.BASH,
            target=ScanTarget.TOOL,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = str(Path(tmpdir) / "audit.jsonl")
            logger = AuditLogger(path=log_path)
            logger.record(report1)
            logger.record(report2)

            with open(log_path) as f:
                lines = f.readlines()
            assert len(lines) == 2
            assert "tool_a" in lines[0]
            assert "tool_b" in lines[1]

    def test_creates_parent_directory(self):
        report = SafetyReport(
            tool_name="test",
            decision=Decision.ALLOW,
            risk_level=RiskLevel.LOW,
            blocked=False,
            sanitized=False,
            duration_ms=0,
            language=ScriptLanguage.PYTHON,
            target=ScanTarget.TOOL,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = str(Path(tmpdir) / "sub" / "deep" / "audit.jsonl")
            logger = AuditLogger(path=log_path)
            logger.record(report)
            assert Path(log_path).exists()


class TestAuditLoggerRobustness:

    def test_json_encode_error_does_not_propagate(self):
        """Non-OSError exceptions (e.g., JSON serialization failures) must not block."""
        from unittest.mock import patch

        report = SafetyReport(
            tool_name="test",
            decision=Decision.ALLOW,
            risk_level=RiskLevel.LOW,
            blocked=False,
            sanitized=False,
            duration_ms=0,
            language=ScriptLanguage.PYTHON,
            target=ScanTarget.TOOL,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = str(Path(tmpdir) / "audit.jsonl")
            logger = AuditLogger(path=log_path)
            # Simulate a json.dumps failure by mocking it
            with patch("trpc_agent_sdk.tools.safety._audit.json.dumps", side_effect=ValueError("bad json")):
                event = logger.record(report)
                # Should not raise, should return an AuditEvent
                assert isinstance(event, AuditEvent)
