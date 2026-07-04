from unittest.mock import Mock
from unittest.mock import patch

from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner
from trpc_agent_sdk.tools.safety._telemetry import record_safety_attributes


def test_record_safety_attributes_no_active_span_does_not_fail():
    report = ToolScriptSafetyScanner().scan_script("echo ok", "bash", tool_name="unit")
    record_safety_attributes(report)


def test_record_safety_attributes_records_expected_keys():
    report = ToolScriptSafetyScanner().scan_script("cat .env", "bash", tool_name="unit")
    span = Mock()
    span.is_recording.return_value = True

    with patch("opentelemetry.trace.get_current_span", return_value=span):
        record_safety_attributes(report)

    recorded = {call.args[0]: call.args[1] for call in span.set_attribute.call_args_list}
    for key in (
        "tool.safety.scan_id",
        "tool.safety.decision",
        "tool.safety.risk_level",
        "tool.safety.rule_id",
        "tool.safety.blocked",
        "tool.safety.sanitized",
        "tool.safety.tool_name",
        "tool.safety.duration_ms",
    ):
        assert key in recorded
    assert recorded["tool.safety.decision"] == "deny"
    assert recorded["tool.safety.tool_name"] == "unit"
