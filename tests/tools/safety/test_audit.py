# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for audit logging."""

import json
import tempfile
from pathlib import Path

from trpc_agent_sdk.tools.safety._audit import SafetyAuditLogger
from trpc_agent_sdk.tools.safety._types import AuditEvent
from trpc_agent_sdk.tools.safety._types import Decision
from trpc_agent_sdk.tools.safety._types import ScanReport


class TestSafetyAuditLogger:
    def test_log_writes_valid_jsonl(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            log_path = f.name
        try:
            logger = SafetyAuditLogger(output_path=log_path)
            event = AuditEvent(
                timestamp="2026-07-10T12:00:00Z",
                tool_name="bash_tool",
                decision="deny",
                risk_level="critical",
                rule_ids=["DANGEROUS_DELETE_001"],
                scan_duration_ms=12.5,
                sanitized=False,
                intercepted=True,
                script_hash="abc123",
            )
            logger.log_event(event)

            with open(log_path) as f:
                lines = f.readlines()
            assert len(lines) == 1
            parsed = json.loads(lines[0])
            assert parsed["tool_name"] == "bash_tool"
            assert parsed["decision"] == "deny"
        finally:
            Path(log_path).unlink()

    def test_log_from_scan_report(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            log_path = f.name
        try:
            logger = SafetyAuditLogger(output_path=log_path)
            report = ScanReport(
                decision=Decision.ALLOW,
                findings=[],
                scan_duration_ms=3.1,
            )
            logger.log(report, tool_name="python_tool", script_hash="def456")

            with open(log_path) as f:
                lines = f.readlines()
            assert len(lines) == 1
            parsed = json.loads(lines[0])
            assert parsed["decision"] == "allow"
            assert parsed["intercepted"] is False
            assert parsed["risk_level"] is None
            assert parsed["tool_name"] == "python_tool"
        finally:
            Path(log_path).unlink()

    def test_multiple_events_written(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            log_path = f.name
        try:
            logger = SafetyAuditLogger(output_path=log_path)
            logger.log_event(AuditEvent(
                timestamp="2026-07-10T12:00:00Z",
                tool_name="tool_a",
                decision="allow",
                risk_level=None,
                rule_ids=[],
                scan_duration_ms=1.0,
                sanitized=False,
                intercepted=False,
                script_hash="aaa",
            ))
            logger.log_event(AuditEvent(
                timestamp="2026-07-10T12:01:00Z",
                tool_name="tool_b",
                decision="deny",
                risk_level="high",
                rule_ids=["NETWORK_PYTHON_004"],
                scan_duration_ms=2.0,
                sanitized=False,
                intercepted=True,
                script_hash="bbb",
            ))

            with open(log_path) as f:
                lines = f.readlines()
            assert len(lines) == 2
            parsed_a = json.loads(lines[0])
            parsed_b = json.loads(lines[1])
            assert parsed_a["tool_name"] == "tool_a"
            assert parsed_b["tool_name"] == "tool_b"
        finally:
            Path(log_path).unlink()
