"""Tests for the guard aggregation and decision engine."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.tools.safety._guard import ToolSafetyGuard
from trpc_agent_sdk.tools.safety._models import SafetyDecision, SafetyScanRequest, ScriptLanguage
from trpc_agent_sdk.tools.safety._policy import load_safety_policy_dict


def test_allow_path_uses_safe000(strict_policy_dict):
    guard = ToolSafetyGuard(load_safety_policy_dict(strict_policy_dict))
    report = guard.scan(SafetyScanRequest(
        tool_name="t", language=ScriptLanguage.PYTHON,
        script="print('hello')",
    ))
    assert report.decision == SafetyDecision.ALLOW
    assert report.rule_ids == ("SAFE000",)
    assert report.risk_level.label() == "info"


def test_report_has_required_fields(strict_policy_dict):
    guard = ToolSafetyGuard(load_safety_policy_dict(strict_policy_dict))
    report = guard.scan(SafetyScanRequest(
        tool_name="t", language=ScriptLanguage.PYTHON,
        script="import shutil\nshutil.rmtree('/tmp/x')",
    ))
    assert report.decision == SafetyDecision.DENY
    assert report.risk_level.label() in {"critical", "high", "medium"}
    assert report.rule_ids
    assert report.findings
    f = report.findings[0]
    assert f.evidence.snippet
    assert f.recommendation
    assert report.policy_hash
    assert report.script_sha256
    assert report.scan_duration_ms >= 0


def test_deduplicate_keeps_unique(strict_policy_dict):
    guard = ToolSafetyGuard(load_safety_policy_dict(strict_policy_dict))
    script = "import os, subprocess\nos.system('rm -rf /')\nsubprocess.run('rm -rf /', shell=True)\n"
    report = guard.scan(SafetyScanRequest(
        tool_name="t", language=ScriptLanguage.PYTHON, script=script,
    ))
    rule_ids = [f.rule_id for f in report.findings]
    # Same rule can appear multiple times for distinct evidence, but the
    # rule_ids tuple is sorted+unique.
    assert report.rule_ids == tuple(sorted(set(rule_ids)))


def test_policy_hash_propagates(strict_policy_dict):
    guard = ToolSafetyGuard(load_safety_policy_dict(strict_policy_dict))
    request = SafetyScanRequest(
        tool_name="t", language=ScriptLanguage.PYTHON,
        script="print('a')",
    )
    report = guard.scan(request)
    assert report.policy_hash == guard.policy.hash


def test_duplicate_rule_ids_rejected(strict_policy_dict):
    from trpc_agent_sdk.tools.safety._python_scanner import PythonScannerRule
    from trpc_agent_sdk.tools.safety._exceptions import SafetyGuardError

    policy = load_safety_policy_dict(strict_policy_dict)
    with pytest.raises(SafetyGuardError):
        ToolSafetyGuard(
            policy,
            rules=[PythonScannerRule(), PythonScannerRule()],
        )


def test_empty_script_yields_allow(strict_policy_dict):
    guard = ToolSafetyGuard(load_safety_policy_dict(strict_policy_dict))
    report = guard.scan(SafetyScanRequest(
        tool_name="t", language=ScriptLanguage.PYTHON, script="",
    ))
    assert report.decision == SafetyDecision.ALLOW
