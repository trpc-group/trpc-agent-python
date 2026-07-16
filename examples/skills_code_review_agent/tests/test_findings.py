# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the Finding model, dedup and confidence gating."""
from review.findings import Finding, dedupe, gate, severity_distribution


def make(severity="high", category="security", file="a.py", line=1,
         confidence=0.9, source="static", title="t"):
    return Finding(severity=severity, category=category, file=file, line=line,
                   title=title, confidence=confidence, source=source)


def test_dedup_same_file_line_category():
    kept, dropped = dedupe([make(title="eval"), make(title="exec", confidence=0.8)])
    assert len(kept) == 1 and len(dropped) == 1
    assert kept[0].confidence == 0.9


def test_dedup_merges_sources():
    kept, _ = dedupe([make(source="static"), make(source="llm", confidence=0.7)])
    assert kept[0].source == "static+llm"


def test_dedup_keeps_highest_severity():
    kept, _ = dedupe([make(severity="medium"), make(severity="critical", confidence=0.5)])
    assert kept[0].severity == "critical"


def test_no_dedup_across_lines():
    kept, dropped = dedupe([make(line=1), make(line=2)])
    assert len(kept) == 2 and dropped == []


def test_gate_splits_low_confidence():
    reported, needs = gate([make(confidence=0.9), make(line=2, confidence=0.5)])
    assert len(reported) == 1 and len(needs) == 1
    assert needs[0].confidence == 0.5


def test_severity_distribution():
    dist = severity_distribution([make(), make(line=2, severity="low")])
    assert dist == {"high": 1, "low": 1}
