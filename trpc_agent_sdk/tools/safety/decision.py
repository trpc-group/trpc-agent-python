# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Decision engine for tool script safety findings."""

from __future__ import annotations

from .models import Finding
from .models import SafetyDecision
from .models import SafetySeverity
from .policy import SafetyPolicy


class DecisionEngine:
    """Turn safety findings into an execution decision."""

    def decide(self, findings: list[Finding], policy: SafetyPolicy) -> SafetyDecision:
        """Generate a decision from findings and policy thresholds."""
        if not findings:
            return SafetyDecision.ALLOW

        deny_severities = set(policy.deny_severities or _default_deny_severities())
        if any(finding.severity in deny_severities for finding in findings):
            return SafetyDecision.DENY

        review_severities = set(policy.review_severities or _default_review_severities())
        if any(finding.severity in review_severities for finding in findings):
            return SafetyDecision.NEEDS_HUMAN_REVIEW

        return policy.default_decision


def _default_deny_severities() -> list[SafetySeverity]:
    return [SafetySeverity.HIGH, SafetySeverity.CRITICAL]


def _default_review_severities() -> list[SafetySeverity]:
    return [SafetySeverity.MEDIUM]
