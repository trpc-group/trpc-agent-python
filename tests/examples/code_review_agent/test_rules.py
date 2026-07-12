# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for deterministic code review rules."""

from __future__ import annotations

from pathlib import Path

from examples.code_review_agent.agent.diff_parser import parse_unified_diff
from examples.code_review_agent.agent.filters import apply_post_filters
from examples.code_review_agent.agent.rules import review_with_rules

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "examples" / "code_review_agent" / "fixtures"


def _findings_for_fixture(name: str):
    parsed = parse_unified_diff((FIXTURES / name).read_text(encoding="utf-8"))
    return review_with_rules(parsed, include_missing_tests=True), parsed


def test_async_resource_leak_fixture_reports_async_category() -> None:
    findings, _ = _findings_for_fixture("async_resource_leak.diff")

    assert any(finding.category == "async" for finding in findings)


def test_db_lifecycle_fixture_reports_database_category() -> None:
    findings, _ = _findings_for_fixture("db_lifecycle.diff")

    assert any(finding.category == "database_lifecycle" for finding in findings)


def test_missing_tests_routes_to_warning_after_filters() -> None:
    findings, parsed = _findings_for_fixture("missing_tests.diff")

    result = apply_post_filters(findings, parsed)

    assert any(warning.category == "test_coverage" for warning in result.warnings)
    assert not any(finding.category == "test_coverage" for finding in result.findings)


def test_sensitive_redaction_fixture_reports_secrets() -> None:
    findings, _ = _findings_for_fixture("sensitive_redaction.diff")

    assert sum(1 for finding in findings if finding.category == "secrets") >= 3
