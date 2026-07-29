#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Unit tests for stable finding deduplication and four-bucket routing."""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from code_review.dedup import (  # noqa: E402
    FindingBucket,
    bucket_for_confidence,
    deduplicate_findings,
    route_findings,
)


def _candidate(**overrides: Any) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "severity": "high",
        "category": "security",
        "file": "src/app.py",
        "line": 8,
        "title": "Avoid dynamic evaluation",
        "evidence": "eval(payload)",
        "recommendation": "Use a typed parser.",
        "confidence": 0.85,
        "source": "rule-engine",
        "line_side": "new",
        "rule_id": "security.dynamic-eval",
    }
    candidate.update(overrides)
    return candidate


def _stable_json(value: Any) -> str:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def test_dedup_key_is_file_line_category_and_severity_wins_first() -> None:
    candidates = [
        _candidate(
            severity="high",
            confidence=0.99,
            rule_id="security.regex",
        ),
        _candidate(
            severity="critical",
            confidence=0.81,
            rule_id="security.ast",
            source="ast",
        ),
    ]

    result = deduplicate_findings(candidates)

    assert len(result) == 1
    assert result[0]["rule_id"] == "security.ast"
    assert result[0]["dedup_key"] == "src/app.py:8:security"
    assert result[0]["extra"]["also_matched"] == ["security.regex"]


def test_confidence_then_evidence_specificity_selects_primary() -> None:
    confidence_winner = deduplicate_findings(
        [
            _candidate(confidence=0.82, rule_id="security.low-confidence"),
            _candidate(confidence=0.91, rule_id="security.high-confidence"),
        ]
    )
    evidence_winner = deduplicate_findings(
        [
            _candidate(
                evidence="eval call",
                rule_id="security.generic-evidence",
            ),
            _candidate(
                evidence="result = eval(untrusted_payload)",
                rule_id="security.specific-evidence",
            ),
        ]
    )

    assert confidence_winner[0]["rule_id"] == "security.high-confidence"
    assert evidence_winner[0]["rule_id"] == "security.specific-evidence"


def test_also_matched_is_unique_sorted_and_preserves_primary_extra() -> None:
    candidates = [
        _candidate(
            rule_id="security.primary",
            source="ast",
            confidence=0.92,
            extra={
                "line_side_note": "changed",
                "also_matched": [
                    "security.previous",
                    "security.secondary",
                ],
            },
        ),
        _candidate(rule_id="security.secondary"),
        _candidate(rule_id="security.tertiary"),
        _candidate(rule_id="security.secondary"),
    ]

    result = deduplicate_findings(candidates)

    assert result[0]["extra"] == {
        "line_side_note": "changed",
        "also_matched": [
            "security.previous",
            "security.secondary",
            "security.tertiary",
        ],
    }


def test_candidate_order_cannot_change_canonical_json() -> None:
    candidates = [
        _candidate(rule_id="security.zeta", confidence=0.82),
        _candidate(rule_id="security.alpha", confidence=0.82),
        _candidate(
            file="src/worker.py",
            line=3,
            category="async-errors",
            severity="medium",
            confidence=0.76,
            rule_id="async.unawaited-coroutine",
        ),
    ]

    outputs = {
        _stable_json(route_findings(permutation))
        for permutation in itertools.permutations(candidates)
    }

    assert len(outputs) == 1


def test_exact_priority_ties_use_canonical_content_as_tiebreaker() -> None:
    candidates = [
        _candidate(
            evidence="eval(alpha)",
            rule_id="security.same-rule",
            extra={"origin": "regex"},
        ),
        _candidate(
            evidence="eval(bravo)",
            rule_id="security.same-rule",
            extra={"origin": "syntax"},
        ),
    ]

    forward = _stable_json(deduplicate_findings(candidates))
    reverse = _stable_json(deduplicate_findings(reversed(candidates)))

    assert forward == reverse


def test_bucket_boundaries_are_exhaustive_and_non_overlapping() -> None:
    confidences = [0.0, 0.49, 0.50, 0.79, 0.80, 1.0]

    assert [bucket_for_confidence(value) for value in confidences] == [
        FindingBucket.SUPPRESSED,
        FindingBucket.SUPPRESSED,
        FindingBucket.NEEDS_HUMAN_REVIEW,
        FindingBucket.NEEDS_HUMAN_REVIEW,
        FindingBucket.FINDINGS,
        FindingBucket.FINDINGS,
    ]

    result = route_findings(
        [
            _candidate(file=f"src/{index}.py", confidence=confidence)
            for index, confidence in enumerate(confidences)
        ]
    )
    assert len(result.findings) == 2
    assert len(result.needs_human_review) == 2
    assert len(result.suppressed) == 2


def test_severity_never_raises_confidence_bucket_and_warnings_are_separate() -> None:
    result = route_findings(
        [
            _candidate(
                severity="critical",
                confidence=0.2,
                category="runtime-warning",
            )
        ],
        warnings=[
            {
                "code": "sandbox_timeout",
                "message": "The registered check timed out.",
                "stage": "sandbox",
            }
        ],
    )

    assert result.findings == ()
    assert result.needs_human_review == ()
    assert len(result.suppressed) == 1
    assert result.suppressed[0]["bucket"] == "suppressed"
    assert result.warnings == (
        {
            "code": "sandbox_timeout",
            "message": "The registered check timed out.",
            "stage": "sandbox",
        },
    )


def test_invalid_confidence_and_finding_shaped_warning_are_rejected() -> None:
    with pytest.raises(ValueError, match="confidence"):
        bucket_for_confidence(1.01)

    with pytest.raises(ValueError, match="runtime warning"):
        route_findings([], warnings=[_candidate()])
