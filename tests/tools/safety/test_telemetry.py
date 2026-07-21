from types import SimpleNamespace

from pydantic import BaseModel

from trpc_agent_sdk.tools.safety import _telemetry
from trpc_agent_sdk.tools.safety._filter import sanitize_telemetry_args
from trpc_agent_sdk.tools.safety._models import RiskLevel
from trpc_agent_sdk.tools.safety._models import SafetyDecision
from trpc_agent_sdk.tools.safety._models import SafetyReport
from trpc_agent_sdk.tools.safety._models import ScriptLanguage


def _report() -> SafetyReport:
    return SafetyReport(
        tool_name="shell",
        language=ScriptLanguage.BASH,
        decision=SafetyDecision.DENY,
        risk_level=RiskLevel.CRITICAL,
        rule_ids=["FILE001", "NET001"],
        duration_ms=12.25,
        script_sha256="b" * 64,
        policy_version="1",
        redacted=True,
        blocked=True,
    )


def test_trace_safety_report_sets_only_redacted_summary_attributes(monkeypatch):
    attributes = {}
    span = SimpleNamespace(set_attribute=lambda key, value: attributes.__setitem__(key, value))
    monkeypatch.setattr(_telemetry, "trace", SimpleNamespace(get_current_span=lambda: span))

    _telemetry.trace_safety_report(_report())

    assert attributes == {
        "tool.safety.decision": "deny",
        "tool.safety.risk_level": "critical",
        "tool.safety.rule_id": "FILE001",
        "tool.safety.rule_ids": ("FILE001", "NET001"),
        "tool.safety.blocked": True,
        "tool.safety.redacted": True,
        "tool.safety.duration_ms": 12.25,
    }
    assert not any("script" in key or "env" in key for key in attributes)


def test_trace_safety_report_never_changes_decision_on_span_failure(monkeypatch):

    class BrokenSpan:

        def set_attribute(self, key, value):
            del key, value
            raise RuntimeError("exporter unavailable")

    monkeypatch.setattr(_telemetry, "trace", SimpleNamespace(get_current_span=lambda: BrokenSpan()))

    _telemetry.trace_safety_report(_report())


def test_telemetry_sanitizer_recurses_into_pydantic_models():

    class ToolPayload(BaseModel):
        stdout: str

    sanitized = sanitize_telemetry_args({"payload": ToolPayload(stdout="secret-output")})

    assert "secret-output" not in str(sanitized)
    assert "redacted sha256" in str(sanitized)
