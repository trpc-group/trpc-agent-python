"""Tests for trpc_agent_sdk.tools.safety._guard."""

from __future__ import annotations

from typing import Iterable

import pytest

from trpc_agent_sdk.tools.safety._exceptions import (
    SafetyGuardError,
    SafetyScannerError,
)
from trpc_agent_sdk.tools.safety._guard import (
    INTERNAL_ERROR_RULE_ID,
    ToolSafetyGuard,
    _aggregate_decision,
    _aggregate_recommendation,
    _aggregate_risk,
    _deduplicate,
    _new_report_id,
    _stable_rule_ids,
)
from trpc_agent_sdk.tools.safety._models import (
    Evidence,
    RiskCategory,
    RiskLevel,
    SafetyDecision,
    SafetyFinding,
    SafetyReport,
    SafetyScanRequest,
    ScriptLanguage,
    ToolKind,
)
from trpc_agent_sdk.tools.safety._policy import load_safety_policy_dict


def _policy(**overrides):
    return load_safety_policy_dict({"version": "1", **overrides})


def _finding(rule_id: str = "X",
             decision: SafetyDecision = SafetyDecision.DENY,
             risk: RiskLevel = RiskLevel.HIGH,
             snippet: str = "s",
             line: int = 1) -> SafetyFinding:
    return SafetyFinding(
        rule_id=rule_id,
        category=RiskCategory.FILE,
        risk_level=risk,
        decision=decision,
        evidence=Evidence(snippet=snippet, line=line),
        recommendation="rec",
    )


class TestScan:

    def test_safe_request_returns_allow(self, scan_request_factory):
        guard = ToolSafetyGuard(_policy())
        req = scan_request_factory(
            language=ScriptLanguage.PYTHON,
            script="print('hi')",
        )
        report = guard.scan(req)
        assert report.decision == SafetyDecision.ALLOW
        assert report.findings == ()
        assert report.policy_hash == guard.policy_hash
        assert report.policy_version == "1"

    def test_dangerous_python(self, scan_request_factory):
        guard = ToolSafetyGuard(_policy())
        req = scan_request_factory(
            language=ScriptLanguage.PYTHON,
            script="import shutil\nshutil.rmtree('/x')",
        )
        report = guard.scan(req)
        assert report.decision == SafetyDecision.DENY
        assert "FILE001_RECURSIVE_DELETE" in report.rule_ids

    def test_dangerous_bash(self, scan_request_factory):
        guard = ToolSafetyGuard(_policy())
        req = scan_request_factory(
            language=ScriptLanguage.BASH,
            script="rm -rf /tmp/x",
        )
        report = guard.scan(req)
        assert report.decision == SafetyDecision.DENY

    def test_oversized_script_records_size_error(self, scan_request_factory):
        guard = ToolSafetyGuard(_policy(limits={"max_script_bytes": 8}))
        req = scan_request_factory(
            language=ScriptLanguage.PYTHON,
            script="x" * 100,
        )
        report = guard.scan(req)
        assert report.decision == SafetyDecision.DENY
        assert any("script exceeds" in f.evidence.snippet or "scannererror" in f.evidence.snippet.lower()
                   or "SafetyScannerError" in f.evidence.snippet or f.rule_id == INTERNAL_ERROR_RULE_ID
                   for f in report.findings)

    def test_zero_limit_skips_size_check(self, scan_request_factory):
        guard = ToolSafetyGuard(_policy(limits={"max_script_bytes": 0}))
        req = scan_request_factory(
            language=ScriptLanguage.PYTHON,
            script="x" * 1000,
        )
        report = guard.scan(req)
        # No internal error from size; just a normal scan result.
        assert all(f.rule_id != INTERNAL_ERROR_RULE_ID for f in report.findings)


class TestErrorReport:

    def test_error_report_always_fail_closed(self, scan_request_factory):
        guard = ToolSafetyGuard(_policy())
        req = scan_request_factory()
        report = guard.error_report(req, RuntimeError("boom"))
        assert report.decision == SafetyDecision.DENY
        assert report.findings
        assert report.findings[0].rule_id == INTERNAL_ERROR_RULE_ID

    def test_error_report_respects_guard_error_policy(self, scan_request_factory):
        guard = ToolSafetyGuard(_policy(defaults={"guard_error": "needs_human_review"}))
        req = scan_request_factory()
        report = guard.error_report(req, RuntimeError("boom"))
        assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW


class TestRuleValidation:

    def test_duplicate_rule_ids_rejected(self):

        class Bad:
            rule_id = "dup"

            def scan(self, req, pol):
                return []

        with pytest.raises(SafetyGuardError):
            ToolSafetyGuard(_policy(), rules=[Bad(), Bad()])

    def test_missing_rule_id_rejected(self):

        class Bad:
            rule_id = ""

            def scan(self, req, pol):
                return []

        with pytest.raises(SafetyGuardError):
            ToolSafetyGuard(_policy(), rules=[Bad()])

    def test_custom_rules_used(self, scan_request_factory):

        class MyRule:
            rule_id = "my_rule"

            def scan(self, req, pol):
                yield _finding(rule_id="MY001", decision=SafetyDecision.DENY, risk=RiskLevel.HIGH)

        guard = ToolSafetyGuard(_policy(), rules=[MyRule()])
        req = scan_request_factory()
        report = guard.scan(req)
        assert "MY001" in report.rule_ids


class TestRuleProperty:

    def test_returns_copy(self):
        guard = ToolSafetyGuard(_policy())
        first = guard.rules
        second = guard.rules
        assert first == second
        assert first is not second  # defensive copy


class TestPolicyHashStable:

    def test_same_policy_same_hash(self):
        a = ToolSafetyGuard(_policy())
        b = ToolSafetyGuard(_policy())
        assert a.policy_hash == b.policy_hash
        assert a.policy_version == b.policy_version


class TestAggregationHelpers:

    def test_aggregate_decision_returns_worst(self):
        out = _aggregate_decision([
            _finding(decision=SafetyDecision.ALLOW),
            _finding(decision=SafetyDecision.DENY),
        ])
        assert out == SafetyDecision.DENY

    def test_aggregate_decision_empty(self):
        assert _aggregate_decision([]) == SafetyDecision.ALLOW

    def test_aggregate_risk_empty(self):
        assert _aggregate_risk([]) == RiskLevel.INFO

    def test_aggregate_risk_picks_max(self):
        out = _aggregate_risk([
            _finding(risk=RiskLevel.LOW),
            _finding(risk=RiskLevel.CRITICAL),
        ])
        assert out == RiskLevel.CRITICAL

    def test_stable_rule_ids_dedup_and_sort(self):
        ids = _stable_rule_ids([
            _finding(rule_id="c"),
            _finding(rule_id="a"),
            _finding(rule_id="a"),
        ])
        assert ids == ("a", "c")

    def test_aggregate_recommendation_deny(self):
        assert "Block" in _aggregate_recommendation(
            [_finding(decision=SafetyDecision.DENY)],
            SafetyDecision.DENY,
        )

    def test_aggregate_recommendation_review(self):
        assert "review" in _aggregate_recommendation(
            [_finding(decision=SafetyDecision.NEEDS_HUMAN_REVIEW)],
            SafetyDecision.NEEDS_HUMAN_REVIEW,
        )

    def test_aggregate_recommendation_allow(self):
        assert "Proceed" in _aggregate_recommendation(
            [_finding(decision=SafetyDecision.ALLOW)],
            SafetyDecision.ALLOW,
        )


class TestDeduplicate:

    def test_removes_exact_duplicates(self):
        f1 = _finding()
        f2 = _finding()
        out = _deduplicate([f1, f2])
        assert len(out) == 1

    def test_keeps_distinct_lines(self):
        f1 = _finding(line=1)
        f2 = _finding(line=2)
        out = _deduplicate([f1, f2])
        assert len(out) == 2

    def test_sorted_by_risk_then_rule(self):
        high = _finding(rule_id="A", risk=RiskLevel.HIGH)
        critical = _finding(rule_id="B", risk=RiskLevel.CRITICAL)
        out = _deduplicate([high, critical])
        # Critical comes first (-risk_level sorts descending)
        assert out[0].rule_id == "B"


class TestReportId:

    def test_report_id_prefix(self):
        rid = _new_report_id()
        assert rid.startswith("rep-")

    def test_report_id_unique(self):
        ids = {_new_report_id() for _ in range(20)}
        assert len(ids) == 20
