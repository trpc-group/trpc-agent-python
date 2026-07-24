# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for code review dry-run filters."""

from __future__ import annotations

from examples.code_review_agent.agent.diff_parser import parse_unified_diff
from examples.code_review_agent.agent.fake_reviewer import review_with_fake_model
from examples.code_review_agent.agent.filters import apply_post_filters
from examples.code_review_agent.agent.filters import fingerprint_finding
from examples.code_review_agent.agent.filters import redact_text
from examples.code_review_agent.agent.schemas import Confidence
from examples.code_review_agent.agent.schemas import FindingSource
from examples.code_review_agent.agent.schemas import ReviewFinding
from examples.code_review_agent.agent.schemas import Severity


def test_redact_text_masks_secret_assignment_value() -> None:
    text = 'API_KEY = "FAKE_TEST_SECRET_VALUE_1234567890"'

    redacted = redact_text(text)

    assert "FAKE_TEST_SECRET_VALUE_1234567890" not in redacted
    assert "<REDACTED_SECRET>" in redacted


def test_apply_filters_keeps_anchored_secret_and_redacts_evidence() -> None:
    parsed = parse_unified_diff("""diff --git a/src/config.py b/src/config.py
--- a/src/config.py
+++ b/src/config.py
@@ -1 +1,2 @@
 DEBUG = False
+API_KEY = "FAKE_TEST_SECRET_VALUE_1234567890"
""")
    raw_findings = review_with_fake_model(parsed)

    findings, warnings, decisions = apply_post_filters(raw_findings, parsed)

    assert len(findings) == 1
    assert warnings == []
    assert "FAKE_TEST_SECRET_VALUE_1234567890" not in findings[0].evidence
    assert findings[0].fingerprint
    assert any(decision.decision == "allow" for decision in decisions)
    assert any(decision.decision == "redact" for decision in decisions)


def test_apply_filters_routes_unanchored_finding_to_warning() -> None:
    parsed = parse_unified_diff("""diff --git a/src/config.py b/src/config.py
--- a/src/config.py
+++ b/src/config.py
@@ -1 +1,2 @@
 DEBUG = False
+TIMEOUT = 30
""")
    finding = ReviewFinding(
        severity=Severity.HIGH,
        category="secrets",
        file="src/config.py",
        line=99,
        title="Hard-coded secret",
        evidence='API_KEY = "FAKE_TEST_SECRET_VALUE_1234567890"',
        recommendation="Move it to a secret manager.",
        confidence=Confidence.HIGH,
        source=FindingSource.FAKE_MODEL,
    )

    findings, warnings, decisions = apply_post_filters([finding], parsed)

    assert findings == []
    assert len(warnings) == 1
    assert warnings[0].needs_human_review is True
    assert any(decision.decision == "needs_human_review" for decision in decisions)


def test_apply_filters_merges_duplicate_findings() -> None:
    parsed = parse_unified_diff("""diff --git a/src/config.py b/src/config.py
--- a/src/config.py
+++ b/src/config.py
@@ -1 +1,2 @@
 DEBUG = False
+API_KEY = "FAKE_TEST_SECRET_VALUE_1234567890"
""")
    finding = review_with_fake_model(parsed)[0]

    findings, warnings, decisions = apply_post_filters([finding, finding], parsed)

    assert len(findings) == 1
    assert warnings == []
    assert any(decision.decision == "merge" for decision in decisions)


def test_fingerprint_is_stable_for_same_finding() -> None:
    finding = ReviewFinding(
        severity=Severity.HIGH,
        category="secrets",
        file="src/config.py",
        line=2,
        title="Hard-coded secret",
        evidence='API_KEY = "FAKE_TEST_SECRET_VALUE_1234567890"',
        recommendation="Move it to a secret manager.",
        confidence=Confidence.HIGH,
        source=FindingSource.FAKE_MODEL,
    )

    assert fingerprint_finding(finding) == fingerprint_finding(finding)
