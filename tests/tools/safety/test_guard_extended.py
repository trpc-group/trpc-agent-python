"""Extended tests for ScriptSafetyGuard — covers report/audit file output and edge cases."""

import importlib
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trpc_agent_sdk.tools.safety.guard import (
    ScriptSafetyGuard,
    _emit_audit_log,
    _record_otel,
    _sanitize_evidence,
    _truncate,
    _write_report_and_audit,
)
from trpc_agent_sdk.tools.safety.models import (
    Decision,
    Finding,
    Language,
    RiskCategory,
    SafetyCheckInput,
    SafetyCheckResult,
    Severity,
    ToolMetadata,
)
from trpc_agent_sdk.tools.safety.policy import (
    AuditOutputConfig,
    OutputConfig,
    PolicyConfig,
    ReportOutputConfig,
)
from trpc_agent_sdk.tools.safety.rules._base import rule_registry


@pytest.fixture(autouse=True)
def _ensure_rules_registered():
    """Ensure safety rules are registered."""
    if rule_registry.count == 0:
        importlib.reload(importlib.import_module("trpc_agent_sdk.tools.safety.rules.file_ops"))
        importlib.reload(importlib.import_module("trpc_agent_sdk.tools.safety.rules.network"))
        importlib.reload(importlib.import_module("trpc_agent_sdk.tools.safety.rules.process"))
        importlib.reload(importlib.import_module("trpc_agent_sdk.tools.safety.rules.dependency"))
        importlib.reload(importlib.import_module("trpc_agent_sdk.tools.safety.rules.resource"))
        importlib.reload(importlib.import_module("trpc_agent_sdk.tools.safety.rules.secrets"))


def _make_input(
    script: str = "print('hello')",
    language: Language = Language.PYTHON,
    tool_name: str = "test_tool",
    invocation_id: str = "inv-001",
) -> SafetyCheckInput:
    return SafetyCheckInput(
        script_content=script,
        language=language,
        tool_metadata=ToolMetadata(
            tool_name=tool_name,
            invocation_id=invocation_id,
            agent_name="test_agent",
            user_id="user-001",
        ),
    )


def _make_result(
    decision: Decision = Decision.ALLOW,
    findings: list | None = None,
) -> SafetyCheckResult:
    return SafetyCheckResult(
        decision=decision,
        findings=findings or [],
        scan_duration_ms=1.5,
        scanned_language=Language.PYTHON,
        tool_name="test_tool",
        invocation_id="inv-001",
    )


# ---------------------------------------------------------------------------
# Test _write_report_and_audit
# ---------------------------------------------------------------------------


class TestWriteReportAndAudit:
    """Test the file-based report and audit output logic."""

    def test_report_file_written(self):
        """Verify report JSON file is created when enabled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            policy = PolicyConfig(output=OutputConfig(
                report=ReportOutputConfig(enabled=True, dir=tmpdir),
                audit=AuditOutputConfig(enabled=False),
            ))
            input_data = _make_input()
            result = _make_result()

            _write_report_and_audit(policy, input_data, result)

            files = list(Path(tmpdir).glob("*.json"))
            assert len(files) == 1
            content = json.loads(files[0].read_text())
            assert content["decision"] == "allow"
            assert "timestamp" in content

    def test_audit_jsonl_appended(self):
        """Verify audit JSONL file is created/appended when enabled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_file = os.path.join(tmpdir, "audit.jsonl")
            policy = PolicyConfig(output=OutputConfig(
                report=ReportOutputConfig(enabled=False),
                audit=AuditOutputConfig(enabled=True, file=audit_file),
            ))
            input_data = _make_input()
            result = _make_result()

            # Write twice to verify append
            _write_report_and_audit(policy, input_data, result)
            _write_report_and_audit(policy, input_data, result)

            with open(audit_file, "r") as f:
                lines = f.readlines()
            assert len(lines) == 2
            entry = json.loads(lines[0])
            assert entry["event"] == "safety_check"
            assert entry["decision"] == "allow"

    def test_report_disabled_no_file(self):
        """No report file when disabled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            policy = PolicyConfig(output=OutputConfig(
                report=ReportOutputConfig(enabled=False, dir=tmpdir),
                audit=AuditOutputConfig(enabled=False),
            ))
            input_data = _make_input()
            result = _make_result()

            _write_report_and_audit(policy, input_data, result)

            files = list(Path(tmpdir).glob("*"))
            assert len(files) == 0

    def test_report_with_findings_sanitizes_evidence(self):
        """Evidence in report should be sanitized."""
        with tempfile.TemporaryDirectory() as tmpdir:
            policy = PolicyConfig(output=OutputConfig(
                report=ReportOutputConfig(enabled=True, dir=tmpdir),
                audit=AuditOutputConfig(enabled=False),
            ))
            finding = Finding(
                rule_id="SEC-001",
                category=RiskCategory.SECRETS,
                severity=Severity.HIGH,
                decision=Decision.DENY,
                evidence="api_key='sk-reallyLongSecretValueHere123456'",
            )
            result = _make_result(decision=Decision.DENY, findings=[finding])
            input_data = _make_input()

            _write_report_and_audit(policy, input_data, result)

            files = list(Path(tmpdir).glob("*.json"))
            content = json.loads(files[0].read_text())
            # Evidence should be masked
            assert "sk-reallyLongSecretValueHere123456" not in json.dumps(content)
            assert "****" in json.dumps(content)

    def test_report_dir_created_if_not_exists(self):
        """Report directory should be auto-created."""
        with tempfile.TemporaryDirectory() as tmpdir:
            nested_dir = os.path.join(tmpdir, "sub", "reports")
            policy = PolicyConfig(output=OutputConfig(
                report=ReportOutputConfig(enabled=True, dir=nested_dir),
                audit=AuditOutputConfig(enabled=False),
            ))
            _write_report_and_audit(policy, _make_input(), _make_result())
            assert Path(nested_dir).is_dir()

    def test_report_write_failure_does_not_raise(self):
        """Report write failure should be logged but not raise."""
        policy = PolicyConfig(output=OutputConfig(
            report=ReportOutputConfig(enabled=True, dir="/nonexistent/readonly/path"),
            audit=AuditOutputConfig(enabled=False),
        ))
        # Should not raise
        _write_report_and_audit(policy, _make_input(), _make_result())

    def test_audit_write_failure_does_not_raise(self):
        """Audit write failure should be logged but not raise."""
        policy = PolicyConfig(output=OutputConfig(
            report=ReportOutputConfig(enabled=False),
            audit=AuditOutputConfig(enabled=True, file="/nonexistent/readonly/audit.jsonl"),
        ))
        # Should not raise
        _write_report_and_audit(policy, _make_input(), _make_result())


# ---------------------------------------------------------------------------
# Test _emit_audit_log structured content
# ---------------------------------------------------------------------------


class TestEmitAuditLogExtended:
    """Extended tests for the Python logger audit log."""

    def test_audit_log_with_findings(self, caplog):
        """Audit log includes sanitized findings."""
        import logging

        input_data = _make_input(script="api_key = 'sk-secret1234567890abcdefgh'")
        finding = Finding(
            rule_id="SEC-001",
            category=RiskCategory.SECRETS,
            severity=Severity.HIGH,
            decision=Decision.DENY,
            evidence="api_key = 'sk-secret1234567890abcdefgh'",
            line_number=1,
            description="Hardcoded secret",
        )
        result = SafetyCheckResult(
            decision=Decision.DENY,
            findings=[finding],
            scan_duration_ms=2.0,
            scanned_language=Language.PYTHON,
            tool_name="test_tool",
            invocation_id="inv-x",
        )

        with caplog.at_level(logging.INFO, logger="trpc_agent_sdk.tools.safety.audit"):
            _emit_audit_log(input_data, result)

        audit_records = [r for r in caplog.records if r.name == "trpc_agent_sdk.tools.safety.audit"]
        assert len(audit_records) == 1
        entry = json.loads(audit_records[0].message)
        assert entry["decision"] == "deny"
        assert entry["findings_count"] == 1
        # Evidence should be sanitized in audit log
        f = entry["findings"][0]
        assert "****" in f["evidence"]
        assert f["line_number"] == 1

    def test_audit_log_metadata_fields(self, caplog):
        """Audit log contains agent_name, user_id, script_length."""
        import logging

        input_data = _make_input(script="x = 42")
        result = _make_result()

        with caplog.at_level(logging.INFO, logger="trpc_agent_sdk.tools.safety.audit"):
            _emit_audit_log(input_data, result)

        audit_records = [r for r in caplog.records if r.name == "trpc_agent_sdk.tools.safety.audit"]
        entry = json.loads(audit_records[0].message)
        assert entry["agent_name"] == "test_agent"
        assert entry["user_id"] == "user-001"
        assert entry["script_length"] == len("x = 42")


# ---------------------------------------------------------------------------
# Test _record_otel with mocked OTel
# ---------------------------------------------------------------------------


class TestRecordOtelExtended:
    """Test OTel recording with mocked span."""

    @patch("trpc_agent_sdk.tools.safety.guard.record_check")
    @patch("trpc_agent_sdk.tools.safety.guard.record_scan_duration")
    @patch("trpc_agent_sdk.tools.safety.guard.record_rule_hit")
    def test_otel_span_attributes_set(self, mock_hit, mock_dur, mock_check):
        """Test that OTel span attributes are set when span is recording."""
        mock_span = MagicMock()
        mock_span.is_recording.return_value = True

        mock_trace = MagicMock()
        mock_trace.get_current_span.return_value = mock_span

        # Patch the import inside _record_otel
        import sys
        orig = sys.modules.get("opentelemetry.trace")
        sys.modules["opentelemetry.trace"] = mock_trace
        # Also patch the top-level opentelemetry module
        mock_otel = MagicMock()
        mock_otel.trace = mock_trace
        orig_otel = sys.modules.get("opentelemetry")
        sys.modules["opentelemetry"] = mock_otel

        try:
            finding = Finding(
                rule_id="NET-001",
                category=RiskCategory.NETWORK,
                severity=Severity.HIGH,
                decision=Decision.NEEDS_HUMAN_REVIEW,
            )
            result = SafetyCheckResult(
                decision=Decision.NEEDS_HUMAN_REVIEW,
                findings=[finding],
                scan_duration_ms=3.5,
                scanned_language=Language.PYTHON,
                tool_name="my_tool",
                invocation_id="inv-otel",
            )
            input_data = _make_input()

            _record_otel(input_data, result)

            # Verify span attributes were set
            mock_span.set_attribute.assert_any_call(
                "trpc.python.agent.tool.safety.decision",
                "needs_human_review",
            )
            mock_span.set_attribute.assert_any_call(
                "trpc.python.agent.tool.safety.findings_count",
                1,
            )
            mock_span.set_attribute.assert_any_call(
                "trpc.python.agent.tool.safety.is_blocked",
                False,
            )
        finally:
            # Restore original modules
            if orig is not None:
                sys.modules["opentelemetry.trace"] = orig
            else:
                sys.modules.pop("opentelemetry.trace", None)
            if orig_otel is not None:
                sys.modules["opentelemetry"] = orig_otel
            else:
                sys.modules.pop("opentelemetry", None)

    @patch("trpc_agent_sdk.tools.safety.guard.record_check")
    @patch("trpc_agent_sdk.tools.safety.guard.record_scan_duration")
    @patch("trpc_agent_sdk.tools.safety.guard.record_rule_hit")
    def test_otel_metrics_with_findings(self, mock_hit, mock_dur, mock_check):
        """Verify metrics are recorded for each finding."""
        finding1 = Finding(
            rule_id="SEC-001",
            category=RiskCategory.SECRETS,
            severity=Severity.HIGH,
            decision=Decision.DENY,
        )
        finding2 = Finding(
            rule_id="NET-001",
            category=RiskCategory.NETWORK,
            severity=Severity.HIGH,
            decision=Decision.NEEDS_HUMAN_REVIEW,
        )
        result = SafetyCheckResult(
            decision=Decision.DENY,
            findings=[finding1, finding2],
            scan_duration_ms=5.0,
            scanned_language=Language.PYTHON,
            tool_name="t",
            invocation_id="i",
        )
        input_data = _make_input()
        _record_otel(input_data, result)

        assert mock_hit.call_count == 2
        mock_check.assert_called_once_with(
            decision="deny",
            language="python",
            tool_name="t",
        )


# ---------------------------------------------------------------------------
# Test _sanitize_evidence edge cases
# ---------------------------------------------------------------------------


class TestSanitizeEvidenceExtended:
    """Extended evidence sanitization tests."""

    def test_password_key_masked(self):
        result = _sanitize_evidence("password: 'MyVerySecretPassword123'")
        assert "MyVerySecretPassword123" not in result
        assert "****" in result

    def test_auth_header_masked(self):
        result = _sanitize_evidence("auth = 'BearerTokenLongValue12345678'")
        assert "BearerTokenLongValue12345678" not in result
        assert "****" in result

    def test_short_value_not_masked(self):
        """Values shorter than 8 chars are not masked."""
        result = _sanitize_evidence("key='short'")
        # 'short' is 5 chars — pattern requires 8+ chars after key=
        assert result == "key='short'"

    def test_no_secret_pattern_preserved(self):
        """Normal code without secret patterns is preserved."""
        code = "result = calculate(x, y)"
        assert _sanitize_evidence(code) == code

    def test_multiple_secrets_all_masked(self):
        evidence = "token='abcdefghij1234' secret='xyz789012345'"
        result = _sanitize_evidence(evidence)
        assert "abcdefghij1234" not in result
        assert "xyz789012345" not in result


# ---------------------------------------------------------------------------
# Test guard.check() with report/audit output config
# ---------------------------------------------------------------------------


class TestGuardCheckWithOutput:
    """Test that check() triggers report/audit writing correctly."""

    def test_check_writes_report_when_configured(self):
        """Full check() pipeline writes report when output is enabled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            policy = PolicyConfig(output=OutputConfig(
                report=ReportOutputConfig(enabled=True, dir=tmpdir),
                audit=AuditOutputConfig(enabled=False),
            ))
            guard = ScriptSafetyGuard(policy=policy)
            result = guard.check(_make_input(script="x = 1"))

            assert result.decision == Decision.ALLOW
            files = list(Path(tmpdir).glob("*.json"))
            assert len(files) == 1

    def test_check_writes_audit_when_configured(self):
        """Full check() pipeline appends audit JSONL when output is enabled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_file = os.path.join(tmpdir, "audit.jsonl")
            policy = PolicyConfig(output=OutputConfig(
                report=ReportOutputConfig(enabled=False),
                audit=AuditOutputConfig(enabled=True, file=audit_file),
            ))
            guard = ScriptSafetyGuard(policy=policy)
            guard.check(_make_input(script="y = 2"))
            guard.check(_make_input(script="z = 3"))

            with open(audit_file, "r") as f:
                lines = f.readlines()
            assert len(lines) == 2

    def test_check_with_disabled_output_no_files(self):
        """No files when output is disabled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            policy = PolicyConfig(output=OutputConfig(
                report=ReportOutputConfig(enabled=False, dir=tmpdir),
                audit=AuditOutputConfig(enabled=False, file=os.path.join(tmpdir, "a.jsonl")),
            ))
            guard = ScriptSafetyGuard(policy=policy)
            guard.check(_make_input(script="x = 1"))

            files = list(Path(tmpdir).glob("*"))
            assert len(files) == 0
