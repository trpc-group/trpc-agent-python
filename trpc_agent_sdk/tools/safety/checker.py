# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Checker framework for tool script safety checks."""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from typing import Iterable
from typing import List
from typing import Optional

from .decision import DecisionEngine
from .models import Finding
from .models import SafetyDecision
from .models import SafetyResult
from .models import ToolExecutionRequest
from .policy import PolicyLoader
from .policy import SafetyPolicy


class Rule(ABC):
    """Abstract interface for a safety rule."""

    @property
    @abstractmethod
    def rule_id(self) -> str:
        """Unique rule identifier."""

    @abstractmethod
    async def check(self, request: ToolExecutionRequest, policy: SafetyPolicy) -> List[Finding]:
        """Check a request and return findings."""


class SafetyChecker:
    """Run enabled safety rules and turn findings into a decision."""

    def __init__(
        self,
        rules: Optional[Iterable[Rule]] = None,
        policy: Optional[SafetyPolicy] = None,
        decision_engine: Optional[DecisionEngine] = None,
    ):
        self._rules: list[Rule] = list(rules or [])
        self._policy = policy or PolicyLoader.from_env()
        self._decision_engine = decision_engine or DecisionEngine()

    @property
    def rules(self) -> list[Rule]:
        """Return registered rules."""
        return self._rules

    @property
    def policy(self) -> SafetyPolicy:
        """Return the default policy."""
        return self._policy

    @property
    def decision_engine(self) -> DecisionEngine:
        """Return the decision engine."""
        return self._decision_engine

    def add_rule(self, rule: Rule) -> None:
        """Register one rule."""
        self._rules.append(rule)

    async def check(
        self,
        request: ToolExecutionRequest,
        policy: Optional[SafetyPolicy] = None,
    ) -> SafetyResult:
        """Run enabled rules against a request."""
        active_policy = policy or self._policy
        if not active_policy.enabled:
            return SafetyResult(decision=SafetyDecision.ALLOW, request=request)

        findings: list[Finding] = []
        for rule in self._rules:
            if not active_policy.is_rule_enabled(rule.rule_id):
                continue
            rule_findings = await rule.check(request, active_policy)
            findings.extend(rule_findings or [])

        decision = self._decision_engine.decide(findings, active_policy)
        return SafetyResult(decision=decision, findings=findings, request=request)
