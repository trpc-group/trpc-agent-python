# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Audit event and JSONL sink tests."""

from __future__ import annotations

import json
from unittest.mock import MagicMock
from unittest.mock import patch

from trpc_agent_sdk.tools.safety import JsonlAuditSink
from trpc_agent_sdk.tools.safety import LoggerAuditSink
from trpc_agent_sdk.tools.safety import SafetyAuditEvent
from trpc_agent_sdk.tools.safety import SafetyDecision
from trpc_agent_sdk.tools.safety import SafetyScanRequest
from trpc_agent_sdk.tools.safety import SafetyScanner
from trpc_agent_sdk.tools.safety import ScriptLanguage
from trpc_agent_sdk.tools.safety._audit import record_safety_telemetry


def test_audit_event_contains_required_fields_without_evidence():
    report = SafetyScanner().scan(
        SafetyScanRequest(
            content='import shutil\nshutil.rmtree("/")',
            language=ScriptLanguage.PYTHON,
        ))

    event = SafetyAuditEvent.from_report("DangerousTool", report)
    data = event.model_dump(mode="json")

    assert data["tool_name"] == "DangerousTool"
    assert data["decision"] == SafetyDecision.DENY.value
    assert data["execution_blocked"] is True
    assert data["rule_id"] == "FILE-001"
    assert data["rule_ids"] == ["FILE-001"]
    assert "evidence" not in data
    assert "content" not in data


def test_jsonl_sink_writes_one_parseable_line(tmp_path):
    report = SafetyScanner().scan(SafetyScanRequest(content='print("ok")', language=ScriptLanguage.PYTHON))
    event = SafetyAuditEvent.from_report("SafeTool", report)
    path = tmp_path / "audit.jsonl"

    JsonlAuditSink(path).emit(event)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["decision"] == "allow"
    assert data["rule_id"] == "ALLOW-000"


def test_default_logger_sink_emits_structured_event():
    report = SafetyScanner().scan(SafetyScanRequest(content='print("ok")', language=ScriptLanguage.PYTHON))
    event = SafetyAuditEvent.from_report("SafeTool", report)

    with patch("trpc_agent_sdk.tools.safety._audit.logger.info") as info:
        LoggerAuditSink().emit(event)

    assert info.call_count == 1
    assert "tool_safety_audit" in info.call_args.args[0]
    assert '"tool_name":"SafeTool"' in info.call_args.args[1]


def test_telemetry_contains_bounded_fields_without_source():
    report = SafetyScanner().scan(
        SafetyScanRequest(
            content='import shutil\nshutil.rmtree("/")',
            language=ScriptLanguage.PYTHON,
        ))
    span = MagicMock()
    span.is_recording.return_value = True

    with patch("trpc_agent_sdk.tools.safety._audit.trace.get_current_span", return_value=span):
        record_safety_telemetry(report)

    attributes = {call.args[0]: call.args[1] for call in span.set_attribute.call_args_list}
    assert attributes["tool.safety.decision"] == "deny"
    assert attributes["tool.safety.rule_id"] == "FILE-001"
    assert "shutil.rmtree" not in json.dumps(attributes)
