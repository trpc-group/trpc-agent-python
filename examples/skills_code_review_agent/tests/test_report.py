# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Business tests for review report routing and rendering."""

from __future__ import annotations

import json
from pathlib import Path

from examples.skills_code_review_agent.agent import FilterDecision
from examples.skills_code_review_agent.agent import FilterEvent
from examples.skills_code_review_agent.agent import Finding
from examples.skills_code_review_agent.agent import FindingCategory
from examples.skills_code_review_agent.agent import FindingSeverity
from examples.skills_code_review_agent.agent import FindingSource
from examples.skills_code_review_agent.agent import InputType
from examples.skills_code_review_agent.agent import RuntimeKind
from examples.skills_code_review_agent.agent import SandboxRun
from examples.skills_code_review_agent.agent import SandboxStatus
from examples.skills_code_review_agent.agent import build_review_report
from examples.skills_code_review_agent.agent import dedupe_findings
from examples.skills_code_review_agent.agent import parse_unified_diff
from examples.skills_code_review_agent.agent import route_findings
from examples.skills_code_review_agent.agent import write_review_report
from examples.skills_code_review_agent.tests.secret_samples import generic_password_value


def test_dedupe_findings_uses_fingerprint():
    finding = _finding("fp-1")

    assert dedupe_findings([finding, _finding("fp-1")]) == [finding]


def test_dedupe_findings_generates_missing_fingerprint():
    finding = _finding(None)
    original = finding.to_dict()

    deduped = dedupe_findings([finding])

    assert deduped[0].fingerprint
    assert deduped[0] is not finding
    assert finding.to_dict() == original


def test_dedupe_findings_does_not_mutate_sensitive_evidence():
    sensitive_value = generic_password_value()
    finding = _finding(None, evidence=f"password={sensitive_value}")
    original = finding.to_dict()

    deduped = dedupe_findings([finding])

    assert "[REDACTED]" in deduped[0].evidence
    assert finding.to_dict() == original


def test_route_findings_splits_low_confidence_to_warnings():
    high = _finding("high", confidence=0.9)
    low = _finding("low", confidence=0.5)

    findings, warnings, needs_human_review = route_findings([high, low], [], [])

    assert findings == [high]
    assert warnings == [low]
    assert needs_human_review == []


def test_route_findings_adds_human_review_for_filter_and_sandbox():
    event = FilterEvent(
        task_id="task",
        decision=FilterDecision.NEEDS_HUMAN_REVIEW,
        reason="container runtime is unavailable",
        target="python rule_runner.py",
    )
    sandbox = SandboxRun(task_id="task", runtime=RuntimeKind.CONTAINER, command="python", status=SandboxStatus.FAILED)

    _findings, _warnings, needs_human_review = route_findings([], [event], [sandbox])

    assert {item.category for item in needs_human_review} == {FindingCategory.GOVERNANCE, FindingCategory.SANDBOX}


def test_write_review_report_outputs_json_and_markdown_without_secrets(tmp_path: Path):
    input_summary = parse_unified_diff("", task_id="task", input_type=InputType.FIXTURE)
    sensitive_value = generic_password_value()
    finding = _finding("secret", evidence=f"password={sensitive_value}", confidence=0.9)
    report = build_review_report(
        task_id="task",
        input_summary=input_summary,
        findings=[finding],
        filter_events=[],
        sandbox_runs=[],
        total_duration_ms=12,
    )

    json_path, md_path = write_review_report(tmp_path, report)

    report_json = json.loads(json_path.read_text(encoding="utf-8"))
    report_md = md_path.read_text(encoding="utf-8")
    assert report_json["metrics"]["finding_count"] == 1
    assert "Fix Suggestions" in report_md
    assert sensitive_value not in json.dumps(report_json)
    assert sensitive_value not in report_md


def _finding(fingerprint: str | None, *, confidence: float = 0.9, evidence: str = "eval(user_input)") -> Finding:
    return Finding(
        severity=FindingSeverity.HIGH,
        category=FindingCategory.SECURITY,
        file="app.py",
        line=1,
        title="Risk",
        evidence=evidence,
        recommendation="Avoid risky code.",
        confidence=confidence,
        source=FindingSource.RULE,
        fingerprint=fingerprint,
    )
