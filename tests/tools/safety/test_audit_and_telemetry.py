# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Audit and telemetry tests."""

import json
import os
import stat
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from trpc_agent_sdk.tools.safety import CompositeAuditSink
from trpc_agent_sdk.tools.safety import JsonlAuditSink
from trpc_agent_sdk.tools.safety import LoggingAuditSink
from trpc_agent_sdk.tools.safety import RiskCategory
from trpc_agent_sdk.tools.safety import RiskLevel
from trpc_agent_sdk.tools.safety import SafetyAuditError
from trpc_agent_sdk.tools.safety import SafetyAuditDegradedError
from trpc_agent_sdk.tools.safety import SafetyDecision
from trpc_agent_sdk.tools.safety import SafetyFinding
from trpc_agent_sdk.tools.safety import SafetyReport
from trpc_agent_sdk.tools.safety._audit import create_audit_event
from trpc_agent_sdk.tools.safety._audit import emit_report
from trpc_agent_sdk.tools.safety._audit import _open_secure_file
from trpc_agent_sdk.tools.safety._audit import _shared_sink_lock
from trpc_agent_sdk.tools.safety._audit import set_safety_span_attributes
from trpc_agent_sdk.tools.safety._audit import _PATH_LOCKS


def _report():
    return SafetyReport(
        decision=SafetyDecision.DENY,
        risk_level=RiskLevel.HIGH,
        duration_ms=1.5,
        redacted=True,
        summary="blocked",
        max_output_bytes=100,
    )


class _FailingSink:

    def emit(self, event):
        del event
        raise OSError("secret failure detail")


class _MemorySink:

    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


def test_jsonl_audit_has_required_fields(tmp_path):
    path = tmp_path / "audit.jsonl"
    sink = JsonlAuditSink(path)
    event = create_audit_event(_report(), "Bash", True)

    sink.emit(event)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["tool_name"] == "Bash"
    assert data["execution_blocked"] is True
    assert data["redacted"] is True


def test_path_lock_is_weakly_held(tmp_path):
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    key = str((tmp_path / "audit.jsonl").resolve())
    assert key in _PATH_LOCKS
    del sink


def test_jsonl_audit_secures_existing_file(tmp_path):
    path = tmp_path / "audit.jsonl"
    if os.name == "posix":
        path.write_text("", encoding="utf-8")
        path.chmod(0o644)

    JsonlAuditSink(path).emit(create_audit_event(_report(), "Bash", True))

    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_jsonl_audit_secures_new_parent_directories(tmp_path):
    path = tmp_path / "nested" / "deeper" / "audit.jsonl"

    JsonlAuditSink(path).emit(create_audit_event(_report(), "Bash", True))

    assert path.parent.exists()
    assert path.parent.parent.exists()
    if os.name == "posix":
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_jsonl_audit_secures_existing_parent_directory(tmp_path):
    parent = tmp_path / "audit"
    parent.mkdir()
    parent.chmod(0o755)

    JsonlAuditSink(parent / "audit.jsonl").emit(create_audit_event(_report(), "Bash", True))

    assert stat.S_IMODE(parent.stat().st_mode) == 0o700


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_jsonl_audit_does_not_chmod_cwd_for_plain_relative_path(monkeypatch, tmp_path):
    tmp_path.chmod(0o755)
    monkeypatch.chdir(tmp_path)

    JsonlAuditSink("audit.jsonl").emit(create_audit_event(_report(), "Bash", True))

    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o755


def test_jsonl_audit_applies_posix_fchmod(monkeypatch, tmp_path):
    path = tmp_path / "audit.jsonl"
    fchmod = MagicMock()
    monkeypatch.setattr(
        "trpc_agent_sdk.tools.safety._audit.os.name",
        "posix",
        raising=False,
    )
    monkeypatch.setattr("trpc_agent_sdk.tools.safety._audit.os.fchmod", fchmod)

    with _open_secure_file(path) as stream:
        stream.write("audit\n")

    fchmod.assert_called_once()


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink contract")
def test_jsonl_audit_rejects_symlink(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("unchanged", encoding="utf-8")
    audit = tmp_path / "audit.jsonl"
    audit.symlink_to(target)

    with pytest.raises(SafetyAuditError):
        JsonlAuditSink(audit).emit(create_audit_event(_report(), "Bash", True))

    assert target.read_text(encoding="utf-8") == "unchanged"


def test_jsonl_audit_closes_descriptor_when_identity_check_fails(tmp_path):
    path = tmp_path / "audit.jsonl"
    event = create_audit_event(_report(), "Bash", True)
    with patch("trpc_agent_sdk.tools.safety._audit.os.path.samestat", return_value=False):
        with pytest.raises(SafetyAuditError):
            JsonlAuditSink(path).emit(event)


def test_composite_uses_fallback():
    fallback = _MemorySink()
    sink = CompositeAuditSink(_FailingSink(), fallback)
    event = create_audit_event(_report(), "Bash", True)

    with pytest.raises(SafetyAuditDegradedError):
        sink.emit(event)

    assert fallback.events[0].execution_blocked is True
    assert fallback.events[0].decision == SafetyDecision.DENY


def test_composite_fallback_marks_rewritten_event_redacted():
    fallback = _MemorySink()
    sink = CompositeAuditSink(_FailingSink(), fallback)
    report = SafetyReport(
        decision=SafetyDecision.ALLOW,
        risk_level=RiskLevel.NONE,
        duration_ms=1,
        redacted=False,
        summary="safe",
        max_output_bytes=100,
    )
    with pytest.raises(SafetyAuditDegradedError):
        sink.emit(create_audit_event(report, "Bash", False))
    assert fallback.events[0].redacted is True


def test_composite_fails_closed_when_both_sinks_fail():
    sink = CompositeAuditSink(_FailingSink(), _FailingSink())
    with pytest.raises(SafetyAuditError, match="all tool safety audit sinks failed"):
        sink.emit(create_audit_event(_report(), "Bash", True))


def test_composite_primary_success_and_logging_failure():
    primary = _MemorySink()
    event = create_audit_event(_report(), "Bash", True)
    CompositeAuditSink(primary).emit(event)
    assert primary.events == [event]
    with patch("trpc_agent_sdk.tools.safety._audit.logger.warning", side_effect=RuntimeError):
        with pytest.raises(SafetyAuditError, match="fallback audit failed"):
            LoggingAuditSink().emit(event)


def test_unhashable_audit_sink_uses_fallback_lock():
    assert _shared_sink_lock([]) is _shared_sink_lock([])


def test_telemetry_sets_required_attributes():
    span = MagicMock()
    with patch("trpc_agent_sdk.tools.safety._audit.trace.get_current_span", return_value=span):
        set_safety_span_attributes(_report())

    attributes = {call.args[0]: call.args[1] for call in span.set_attribute.call_args_list}
    assert attributes["tool.safety.decision"] == "deny"
    assert attributes["tool.safety.risk_level"] == "high"
    assert attributes["tool.safety.execution_blocked"] is True


def test_telemetry_failure_does_not_raise():
    span = MagicMock()
    span.set_attribute.side_effect = RuntimeError("telemetry unavailable")
    with patch("trpc_agent_sdk.tools.safety._audit.trace.get_current_span", return_value=span):
        set_safety_span_attributes(_report())


def test_audit_boundary_redacts_tool_name():
    event = create_audit_event(
        _report(),
        "tool password='top secret phrase'",
        True,
    )
    assert "top secret phrase" not in event.model_dump_json()
    assert event.redacted is True


def test_audit_boundary_discards_secret_exception_chain():

    class _SecretFailingSink:

        def emit(self, event):
            del event
            raise RuntimeError("password='top secret phrase'")

    with pytest.raises(SafetyAuditError) as captured:
        emit_report(_SecretFailingSink(), _report(), "Bash")
    assert captured.value.__cause__ is None
    assert "top secret phrase" not in str(captured.value)


def test_telemetry_marks_sanitized_rule_id_as_redacted():
    report = _report().model_copy(
        update={
            "redacted":
            False,
            "findings": [
                SafetyFinding(
                    category=RiskCategory.POLICY,
                    risk_level=RiskLevel.HIGH,
                    rule_id="password='top secret phrase'",
                    evidence="blocked",
                    recommendation="remove secret",
                    decision=SafetyDecision.DENY,
                )
            ],
        })
    span = MagicMock()
    with patch("trpc_agent_sdk.tools.safety._audit.trace.get_current_span", return_value=span):
        set_safety_span_attributes(report)
    attributes = {call.args[0]: call.args[1] for call in span.set_attribute.call_args_list}
    assert attributes["tool.safety.redacted"] is True
    assert "top secret phrase" not in attributes["tool.safety.rule_id"]
