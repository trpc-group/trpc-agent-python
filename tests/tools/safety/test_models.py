"""Tests for trpc_agent_sdk.tools.safety._models."""

from __future__ import annotations

import hashlib

import pytest

from trpc_agent_sdk.tools.safety._models import (
    SAFE_RULE_ID,
    EVIDENCE_MAX_CHARS,
    Evidence,
    RiskCategory,
    RiskLevel,
    SafetyAuditEvent,
    SafetyDecision,
    SafetyFinding,
    SafetyReport,
    SafetyScanRequest,
    ScriptLanguage,
    ToolKind,
    _aggregate_decision,
    _aggregate_recommendation,
    _aggregate_risk,
    _stable_rule_ids,
)


class TestEnums:

    def test_tool_kind_values(self):
        assert ToolKind.TOOL.value == "tool"
        assert ToolKind.MCP.value == "mcp"
        assert ToolKind.UNKNOWN.value == "unknown"

    def test_script_language_values(self):
        assert ScriptLanguage.PYTHON.value == "python"
        assert ScriptLanguage.BASH.value == "bash"
        assert ScriptLanguage.UNKNOWN.value == "unknown"

    def test_safety_decision_values(self):
        assert SafetyDecision.ALLOW.value == "allow"
        assert SafetyDecision.NEEDS_HUMAN_REVIEW.value == "needs_human_review"
        assert SafetyDecision.DENY.value == "deny"

    def test_risk_level_ordering(self):
        assert RiskLevel.INFO < RiskLevel.LOW < RiskLevel.MEDIUM
        assert RiskLevel.MEDIUM < RiskLevel.HIGH < RiskLevel.CRITICAL

    def test_risk_level_label(self):
        assert RiskLevel.CRITICAL.label() == "critical"
        assert RiskLevel.LOW.label() == "low"

    def test_risk_category_values(self):
        for cat in ("FILE", "NETWORK", "PROCESS", "DEPENDENCY", "RESOURCE", "SECRET", "ANALYSIS", "SAFE"):
            assert RiskCategory[cat].value == cat.lower()


class TestEvidence:

    def test_defaults(self):
        ev = Evidence()
        assert ev.snippet == ""
        assert ev.line == 0
        assert ev.column == 0
        assert ev.language == ScriptLanguage.UNKNOWN
        assert ev.extras == {}

    def test_extra_forbidden(self):
        with pytest.raises(Exception):
            Evidence(bogus=1)  # type: ignore[call-arg]

    def test_model_dump_json_excludes_none(self):
        ev = Evidence(snippet="x")
        dumped = ev.model_dump_json()
        assert "snippet" in dumped
        # No null fields appear because exclude_none=True is the default
        assert "null" not in dumped


class TestSafetyScanRequest:

    def test_minimal_construction(self):
        req = SafetyScanRequest(tool_name="t")
        assert req.tool_name == "t"
        assert req.tool_kind == ToolKind.UNKNOWN
        assert req.language == ScriptLanguage.UNKNOWN
        assert req.script == ""
        assert req.argv == ()
        assert req.env == {}

    def test_extra_forbidden(self):
        with pytest.raises(Exception):
            SafetyScanRequest(tool_name="t", unknown=1)  # type: ignore[call-arg]

    def test_repr_does_not_leak_script_or_env(self):
        req = SafetyScanRequest(
            tool_name="t",
            script="super secret payload",
            env={"TOKEN": "leak"},
        )
        rendered = repr(req)
        assert "super secret payload" not in rendered
        assert "leak" not in rendered


class _FindingBuilder:
    """Tiny helper to fabricate findings without dragging in the redactor."""

    @staticmethod
    def build(rule_id: str, decision: SafetyDecision, risk: RiskLevel = RiskLevel.MEDIUM) -> SafetyFinding:
        return SafetyFinding(
            rule_id=rule_id,
            category=RiskCategory.ANALYSIS,
            risk_level=risk,
            decision=decision,
            evidence=Evidence(snippet="x"),
            recommendation="rec",
        )


class TestAggregationHelpers:

    def test_aggregate_decision_empty(self):
        assert _aggregate_decision([]) == SafetyDecision.ALLOW

    def test_aggregate_decision_picks_worst(self):
        findings = [
            _FindingBuilder.build("a", SafetyDecision.ALLOW),
            _FindingBuilder.build("b", SafetyDecision.NEEDS_HUMAN_REVIEW),
            _FindingBuilder.build("c", SafetyDecision.DENY),
        ]
        assert _aggregate_decision(findings) == SafetyDecision.DENY

    def test_aggregate_decision_review_over_allow(self):
        findings = [
            _FindingBuilder.build("a", SafetyDecision.ALLOW),
            _FindingBuilder.build("b", SafetyDecision.NEEDS_HUMAN_REVIEW),
        ]
        assert _aggregate_decision(findings) == SafetyDecision.NEEDS_HUMAN_REVIEW

    def test_aggregate_risk_empty(self):
        assert _aggregate_risk([]) == RiskLevel.INFO

    def test_aggregate_risk_max(self):
        findings = [
            _FindingBuilder.build("a", SafetyDecision.DENY, RiskLevel.LOW),
            _FindingBuilder.build("b", SafetyDecision.DENY, RiskLevel.CRITICAL),
        ]
        assert _aggregate_risk(findings) == RiskLevel.CRITICAL

    def test_stable_rule_ids_dedup_and_sort(self):
        findings = [
            _FindingBuilder.build("c", SafetyDecision.DENY),
            _FindingBuilder.build("a", SafetyDecision.DENY),
            _FindingBuilder.build("a", SafetyDecision.DENY),
        ]
        assert _stable_rule_ids(findings) == ("a", "c")

    def test_aggregate_recommendation_empty(self):
        assert _aggregate_recommendation([], SafetyDecision.ALLOW) \
            == "No safety rules matched."

    def test_aggregate_recommendation_deny(self):
        assert "Block" in _aggregate_recommendation(
            [_FindingBuilder.build("a", SafetyDecision.DENY)],
            SafetyDecision.DENY,
        )

    def test_aggregate_recommendation_review(self):
        assert "review" in _aggregate_recommendation(
            [_FindingBuilder.build("a", SafetyDecision.NEEDS_HUMAN_REVIEW)],
            SafetyDecision.NEEDS_HUMAN_REVIEW,
        )

    def test_aggregate_recommendation_allow_with_findings(self):
        rec = _aggregate_recommendation(
            [_FindingBuilder.build("a", SafetyDecision.ALLOW)],
            SafetyDecision.ALLOW,
        )
        assert "Proceed" in rec


class TestSafetyReport:

    def test_frozen(self):
        rep = SafetyReport(
            report_id="r",
            decision=SafetyDecision.ALLOW,
            risk_level=RiskLevel.INFO,
            rule_ids=("SAFE000", ),
            findings=(),
            recommendation="ok",
            policy_hash="p",
            policy_version="1",
            script_sha256="s",
            scan_duration_ms=0.1,
            redacted=False,
        )
        with pytest.raises(Exception):
            rep.decision = SafetyDecision.DENY  # type: ignore[misc]

    def test_extra_forbidden(self):
        with pytest.raises(Exception):
            SafetyReport(
                report_id="r",
                decision=SafetyDecision.ALLOW,
                risk_level=RiskLevel.INFO,
                rule_ids=("SAFE000", ),
                findings=(),
                recommendation="ok",
                policy_hash="p",
                policy_version="1",
                script_sha256="s",
                scan_duration_ms=0.1,
                redacted=False,
                bogus=1,  # type: ignore[call-arg]
            )

    def test_combine_empty_returns_safe(self):
        rep = SafetyReport.combine(
            [],
            report_id="r",
            policy_hash="p",
            policy_version="1",
            scan_duration_ms=1.0,
        )
        assert rep.decision == SafetyDecision.ALLOW
        assert rep.rule_ids == (SAFE_RULE_ID, )

    def test_combine_picks_worst(self):
        rep1 = SafetyReport(
            report_id="r1",
            decision=SafetyDecision.ALLOW,
            risk_level=RiskLevel.LOW,
            rule_ids=("A", ),
            findings=(_FindingBuilder.build("A", SafetyDecision.ALLOW, RiskLevel.LOW), ),
            recommendation="ok",
            policy_hash="p",
            policy_version="1",
            script_sha256="a",
            scan_duration_ms=0.1,
            redacted=False,
        )
        rep2 = SafetyReport(
            report_id="r2",
            decision=SafetyDecision.DENY,
            risk_level=RiskLevel.CRITICAL,
            rule_ids=("B", ),
            findings=(_FindingBuilder.build("B", SafetyDecision.DENY, RiskLevel.CRITICAL), ),
            recommendation="ok",
            policy_hash="p",
            policy_version="1",
            script_sha256="b",
            scan_duration_ms=0.1,
            redacted=True,
        )
        combined = SafetyReport.combine(
            [rep1, rep2],
            report_id="rc",
            policy_hash="p",
            policy_version="1",
            scan_duration_ms=0.5,
        )
        assert combined.decision == SafetyDecision.DENY
        assert combined.risk_level == RiskLevel.CRITICAL
        assert combined.rule_ids == ("A", "B")
        assert combined.redacted is True
        # Combined sha is sha256 of "\n".join(hashes)
        expected_sha = hashlib.sha256(b"a\nb").hexdigest()
        assert combined.script_sha256 == expected_sha


class TestSafetyAuditEvent:

    def test_construct(self):
        ev = SafetyAuditEvent(
            event_id="e",
            timestamp="2024-01-01T00:00:00Z",
            report_id="r",
            tool_name="t",
            tool_kind=ToolKind.UNKNOWN,
            decision=SafetyDecision.ALLOW,
            risk_level=RiskLevel.INFO,
            rule_ids=("SAFE000", ),
            duration_ms=0.5,
            redacted=False,
            execution_blocked=False,
            policy_hash="p",
            policy_version="1",
            script_sha256="s",
        )
        assert ev.scanner_version == "1.0.0"
        assert ev.invocation_id is None


def test_evidence_max_chars_constant():
    assert EVIDENCE_MAX_CHARS == 240
