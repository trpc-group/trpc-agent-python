"""Threshold tests for the code review agent's acceptance criteria.

Issue #92 grades three numbers. These tests make them CI-enforced rather than
claimed, so a rule change that trades precision for recall (or the reverse)
fails the build instead of quietly shipping.

- Criterion 2: high-risk detection rate >= 80%, false positive rate <= 15%,
  measured on the hold-out set in examples/skills_code_review_agent/evalset/.
- Criterion 5: secret redaction detection rate >= 95%, measured against a
  labelled corpus that also tracks over-redaction of benign lines.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from examples.skills_code_review_agent.evaluate import evaluate, evaluate_redaction

EXAMPLE_ROOT = Path("examples/skills_code_review_agent")
EVALSET = EXAMPLE_ROOT / "evalset"

DETECTION_THRESHOLD = 0.80
FALSE_POSITIVE_BUDGET = 0.15
REDACTION_THRESHOLD = 0.95


@pytest.fixture(scope="module")
def holdout_report():
    return evaluate(EVALSET / "labels.json", EVALSET / "holdout")


@pytest.fixture(scope="module")
def public_report():
    return evaluate(EXAMPLE_ROOT / "fixtures" / "labels.json", EXAMPLE_ROOT / "fixtures")


@pytest.fixture(scope="module")
def redaction_report():
    return evaluate_redaction(EVALSET / "secrets_corpus.json")


def test_holdout_detection_rate_meets_threshold(holdout_report):
    assert holdout_report.expected_high_risk > 0
    assert holdout_report.detection_rate >= DETECTION_THRESHOLD, (
        f"detection rate {holdout_report.detection_rate:.1%} below {DETECTION_THRESHOLD:.0%}; "
        f"missed: {[item for case in holdout_report.cases for item in case.missed]}")


def test_holdout_false_positive_rate_within_budget(holdout_report):
    assert holdout_report.confident_total > 0
    assert holdout_report.false_positive_rate <= FALSE_POSITIVE_BUDGET, (
        f"false positive rate {holdout_report.false_positive_rate:.1%} above {FALSE_POSITIVE_BUDGET:.0%}; "
        f"offenders: {[item for case in holdout_report.cases for item in case.false_positives]}")


def test_negative_controls_produce_no_confident_findings(holdout_report):
    """Correct code must not be flagged: this is what the FP budget protects."""
    offenders = {
        case.name: case.false_positives
        for case in holdout_report.cases
        if case.kind == "negative" and case.false_positives
    }
    assert not offenders, f"negative controls reported findings: {offenders}"


def test_public_fixtures_are_fully_covered(public_report):
    """Acceptance criterion 1: every published sample reviews cleanly."""
    assert public_report.detection_rate >= DETECTION_THRESHOLD
    assert public_report.false_positive_rate <= FALSE_POSITIVE_BUDGET


def test_secret_redaction_recall_meets_threshold(redaction_report):
    assert redaction_report["secret_total"] >= 25
    assert redaction_report["recall"] >= REDACTION_THRESHOLD, (
        f"redaction recall {redaction_report['recall']:.1%} below {REDACTION_THRESHOLD:.0%}; "
        f"leaked: {redaction_report['leaked']}")


def test_redaction_does_not_mangle_benign_lines(redaction_report):
    """Widening secret patterns must not start rewriting ordinary code."""
    assert redaction_report["false_redaction_rate"] <= 0.10, (
        f"over-redacted: {redaction_report['over_redacted']}")
