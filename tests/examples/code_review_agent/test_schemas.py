# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for code review dry-run schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from examples.code_review_agent.agent.schemas import Confidence
from examples.code_review_agent.agent.schemas import FindingSource
from examples.code_review_agent.agent.schemas import ReviewFinding
from examples.code_review_agent.agent.schemas import Severity


def test_valid_finding_serializes_to_json() -> None:
    finding = ReviewFinding(
        severity=Severity.HIGH,
        category="secrets",
        file="src/config.py",
        line=2,
        title="Hard-coded secret",
        evidence="API_KEY = <REDACTED_SECRET>",
        recommendation="Move it to a secret manager.",
        confidence=Confidence.HIGH,
        source=FindingSource.FAKE_MODEL,
    )

    raw = finding.model_dump_json()

    assert '"severity":"high"' in raw
    assert '"source":"fake_model"' in raw


def test_invalid_severity_fails_validation() -> None:
    with pytest.raises(ValidationError):
        ReviewFinding(
            severity="urgent",
            category="secrets",
            file="src/config.py",
            line=2,
            title="Hard-coded secret",
            evidence="evidence",
            recommendation="recommendation",
            confidence="high",
            source="fake_model",
        )


def test_extra_fields_are_forbidden() -> None:
    with pytest.raises(ValidationError):
        ReviewFinding(
            severity="high",
            category="secrets",
            file="src/config.py",
            line=2,
            title="Hard-coded secret",
            evidence="evidence",
            recommendation="recommendation",
            confidence="high",
            source="fake_model",
            unexpected=True,
        )
