# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Dedup + noise control (issue requirement 6)."""

from codereview.findings import Finding
from codereview.findings import dedup_findings
from codereview.findings import severity_distribution
from codereview.findings import split_noise


def _finding(**overrides) -> Finding:
    base = dict(severity="high", category="security_risk", file="a.py", line=10,
                title="t", evidence="e", recommendation="r", confidence=0.9,
                source="static_rule", rule_id="SEC001")
    base.update(overrides)
    return Finding(**base)


def test_same_file_line_category_reported_once():
    first = _finding(rule_id="SEC001", severity="high", confidence=0.9)
    second = _finding(rule_id="SEC009", severity="critical", confidence=0.88)
    kept, removed = dedup_findings([first, second])
    assert len(kept) == 1 and removed == 1
    # highest severity wins; the losing rule id is preserved
    assert kept[0].rule_id == "SEC009"
    assert kept[0].severity == "critical"
    assert kept[0].extra["also_matched"] == ["SEC001"]


def test_different_line_or_category_not_deduped():
    items = [
        _finding(line=10),
        _finding(line=11),
        _finding(line=10, category="resource_leak", rule_id="RES001"),
        _finding(line=10, file="b.py"),
    ]
    kept, removed = dedup_findings(items)
    assert len(kept) == 4 and removed == 0


def test_dedup_keeps_higher_confidence_within_same_severity():
    low = _finding(rule_id="SEC003", confidence=0.75)
    high = _finding(rule_id="SEC001", confidence=0.9)
    kept, _ = dedup_findings([low, high])
    assert kept[0].rule_id == "SEC001"


def test_low_confidence_never_mixes_into_findings():
    confident = _finding(confidence=0.9)
    borderline = _finding(line=20, confidence=0.7)
    noisy = _finding(line=30, confidence=0.5)
    findings, needs_review = split_noise([confident, borderline, noisy], min_confidence=0.7)
    assert {finding.line for finding in findings} == {10, 20}
    assert {finding.line for finding in needs_review} == {30}
    assert all(finding.confidence >= 0.7 for finding in findings)
    assert all(finding.confidence < 0.7 for finding in needs_review)


def test_severity_distribution_counts_all_levels():
    dist = severity_distribution([_finding(), _finding(line=11, severity="critical")])
    assert dist == {"critical": 1, "high": 1, "medium": 0, "low": 0, "info": 0}
