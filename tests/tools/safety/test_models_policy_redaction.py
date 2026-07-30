#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

from pathlib import Path

import pytest
from pydantic import ValidationError

from trpc_agent_sdk.tools.safety._models import RiskLevel
from trpc_agent_sdk.tools.safety._models import SafetyDecision
from trpc_agent_sdk.tools.safety._models import SafetyFinding
from trpc_agent_sdk.tools.safety._models import SafetyReport
from trpc_agent_sdk.tools.safety._models import RiskCategory
from trpc_agent_sdk.tools.safety._policy import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety._policy import load_policy
from trpc_agent_sdk.tools.safety._redaction import redact_text


def test_report_blocks_every_non_allow_decision():
    finding = SafetyFinding(
        category=RiskCategory.PROCESS_EXECUTION,
        risk_level=RiskLevel.MEDIUM,
        rule_id="PROCESS_DYNAMIC",
        evidence="subprocess.run(command)",
        recommendation="Review the resolved command.",
        decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
    )
    report = SafetyReport(
        decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
        risk_level=RiskLevel.MEDIUM,
        findings=[finding],
        duration_ms=1,
        redacted=False,
        summary="review required",
        policy_version="1",
        review_required=True,
    )

    assert report.blocked is True
    assert report.rule_ids == ["PROCESS_DYNAMIC"]


def test_policy_rejects_unknown_and_duplicate_fields(tmp_path: Path):
    unknown = tmp_path / "unknown.yaml"
    unknown.write_text("surprise: true\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_policy(unknown)

    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text("version: '1'\nversion: '2'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_policy(duplicate)


def test_policy_normalizes_domains_and_changes_from_yaml(tmp_path: Path):
    path = tmp_path / "policy.yaml"
    path.write_text(
        "allowed_domains:\n" "  - API.EXAMPLE.COM.\n" "forbidden_paths:\n" "  - /private\n" "max_timeout_seconds: 12\n",
        encoding="utf-8",
    )

    policy = load_policy(path)

    assert policy.allowed_domains == ["api.example.com"]
    assert policy.forbidden_paths == ["/private"]
    assert policy.max_timeout_seconds == 12


def test_policy_rejects_url_in_domain_allowlist():
    with pytest.raises(ValidationError):
        ToolSafetyPolicy(allowed_domains=["https://example.com/path"])


def test_redaction_removes_known_env_values_and_shaped_credentials():
    text = "token=plain-secret short=abc AKIAABCDEFGHIJKLMNOP and sk-abcdefghijklmnop"

    result, changed = redact_text(text, secrets=["plain-secret", "abc"])

    assert changed is True
    assert "plain-secret" not in result
    assert "abc" not in result
    assert "AKIAABCDEFGHIJKLMNOP" not in result
    assert "sk-abcdefghijklmnop" not in result
    assert result.count("[REDACTED]") >= 4


def test_report_rejects_inconsistent_derived_fields():
    finding = SafetyFinding(
        category=RiskCategory.PROCESS_EXECUTION,
        risk_level=RiskLevel.MEDIUM,
        rule_id="PROCESS_DYNAMIC",
        evidence="subprocess.run(command)",
        recommendation="Review the resolved command.",
        decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
    )

    with pytest.raises(ValidationError, match="rule_ids"):
        SafetyReport(
            decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
            risk_level=RiskLevel.MEDIUM,
            findings=[finding],
            rule_ids=["WRONG"],
            duration_ms=1,
            redacted=False,
            summary="review required",
            policy_version="1",
        )
    with pytest.raises(ValidationError, match="review_required"):
        SafetyReport(
            decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
            risk_level=RiskLevel.MEDIUM,
            findings=[finding],
            duration_ms=1,
            redacted=False,
            summary="review required",
            policy_version="1",
            review_required=False,
        )
