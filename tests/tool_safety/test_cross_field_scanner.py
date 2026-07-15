"""Tests for the cross-field scanner."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.tools.safety._guard import ToolSafetyGuard
from trpc_agent_sdk.tools.safety._models import SafetyDecision, SafetyScanRequest, ScriptLanguage
from trpc_agent_sdk.tools.safety._policy import load_safety_policy_dict


@pytest.fixture
def guard(strict_policy_dict):
    return ToolSafetyGuard(load_safety_policy_dict(strict_policy_dict))


def test_denied_cwd_blocks(guard):
    request = SafetyScanRequest(
        tool_name="t",
        language=ScriptLanguage.BASH,
        script="echo hi",
        cwd="/etc",
    )
    report = guard.scan(request)
    assert "FILE002_DENIED_WRITE" in report.rule_ids
    assert report.decision == SafetyDecision.DENY


def test_escaping_cwd_blocks(guard):
    request = SafetyScanRequest(
        tool_name="t",
        language=ScriptLanguage.BASH,
        script="echo hi",
        cwd="../../etc",
    )
    report = guard.scan(request)
    assert report.decision == SafetyDecision.DENY


def test_timeout_over_limit_blocks(guard):
    request = SafetyScanRequest(
        tool_name="t",
        language=ScriptLanguage.PYTHON,
        script="print('hi')",
        requested_timeout_seconds=120,
    )
    report = guard.scan(request)
    assert "RES003_LONG_SLEEP" in report.rule_ids
    assert report.decision == SafetyDecision.DENY


def test_denied_executable_in_argv_blocks(guard):
    request = SafetyScanRequest(
        tool_name="t",
        language=ScriptLanguage.BASH,
        script="echo hi",
        argv=("sudo", "ls"),
    )
    report = guard.scan(request)
    assert any("PROC001_PROCESS_EXEC" == r for r in report.rule_ids)
    assert report.decision == SafetyDecision.DENY


def test_unused_sensitive_env_does_not_trigger_review(guard):
    request = SafetyScanRequest(
        tool_name="t",
        language=ScriptLanguage.PYTHON,
        script="print('hi')",
        env={"API_TOKEN": "abc"},
    )
    report = guard.scan(request)
    assert "SECRET001_LOG_SINK" not in report.rule_ids
    assert report.decision == SafetyDecision.ALLOW


def test_output_budget_enforced(guard):
    request = SafetyScanRequest(
        tool_name="t",
        language=ScriptLanguage.PYTHON,
        script="print('hi')",
        requested_output_bytes=4096,
    )
    report = guard.scan(request)
    assert "RES005_LARGE_WRITE" in report.rule_ids
    assert report.decision == SafetyDecision.DENY


def test_execution_capable_without_mapping_reviews(strict_policy_dict):
    """A tool flagged execution_capable but absent from policy.tools and
    the builtin adapter set must produce a review finding."""

    guard = ToolSafetyGuard(load_safety_policy_dict(strict_policy_dict))
    request = SafetyScanRequest(
        tool_name="weird_tool",
        language=ScriptLanguage.UNKNOWN,
        script="",
        metadata={"execution_capable": True,
                  "adapter_id": "weird_tool"},  # not in policy.tools
    )
    report = guard.scan(request)
    assert "PARSE001_UNCERTAIN" in report.rule_ids
    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
