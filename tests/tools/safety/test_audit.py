# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for tool safety audit events."""

from __future__ import annotations

import json

from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner
from trpc_agent_sdk.tools.safety import build_audit_event
from trpc_agent_sdk.tools.safety import write_audit_event


def test_build_audit_event_contains_monitoring_fields():
    report = ToolScriptSafetyScanner().scan_script("rm -rf /", "bash", tool_name="bash")

    event = build_audit_event(report)
    payload = event.to_dict()

    assert payload["scan_id"]
    assert payload["timestamp"]
    assert payload["tool_name"] == "bash"
    assert payload["decision"] == "deny"
    assert payload["blocked"] is True
    assert "BASH_RECURSIVE_DELETE" in payload["rule_ids"]
    assert payload["trace_attributes"]["tool.safety.decision"] == "deny"


def test_write_audit_event_jsonl(tmp_path):
    report = ToolScriptSafetyScanner().scan_script("rm -rf /", "bash", tool_name="bash")
    audit_path = tmp_path / "audit.jsonl"

    write_audit_event(audit_path, report)

    lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["blocked"] is True
