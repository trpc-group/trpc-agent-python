"""Aggregate independent risk analyzers into one fail-closed decision."""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from ..agent.models import Decision, ExecutionBudget, ExecutionRequest, FilterDecision
from .analyzer import (
    analyze_command,
    analyze_environment,
    analyze_network,
    analyze_paths,
    analyze_resources,
)
from .models import AnalysisResult, RiskLevel
from .policy import GovernancePolicy, load_policy

DecisionSink = Callable[[FilterDecision], None]


class FilterEngine:
    def __init__(
        self,
        workspace_root: str,
        *,
        policy: GovernancePolicy | None = None,
        budget: ExecutionBudget | None = None,
        decision_sink: DecisionSink | None = None,
    ) -> None:
        self.workspace_root = workspace_root
        self.policy = policy or load_policy()
        self.budget = budget or ExecutionBudget()
        self.decision_sink = decision_sink

    def check(self, request: ExecutionRequest) -> FilterDecision:
        try:
            results = [
                analyze_command(request, self.policy),
                analyze_paths(request, self.policy, self.workspace_root),
                analyze_network(request, self.policy),
                analyze_environment(request, self.policy),
                analyze_resources(request, self.policy, self.budget),
            ]
            result = self._aggregate(results)
            decision = FilterDecision(
                decision=result.decision,
                reason_code=result.matched_rule,
                reason=result.reason,
                risk_level=result.risk_level.name.lower(),
                matched_rule=result.matched_rule,
                task_id=request.task_id,
                command_digest=hashlib.sha256("\0".join(request.command).encode()).hexdigest(),
            )
        except Exception as exc:
            decision = FilterDecision(
                decision=Decision.DENY,
                reason_code="filter_internal_error",
                reason=f"Governance evaluation failed: {type(exc).__name__}",
                risk_level="high",
                matched_rule="filter_internal_error",
                task_id=request.task_id,
            )
        if decision.decision == Decision.ALLOW:
            self.budget.calls_used += 1
        if self.decision_sink:
            self.decision_sink(decision)
        return decision

    @staticmethod
    def _aggregate(results: list[AnalysisResult]) -> AnalysisResult:
        priority = {Decision.ALLOW: 0, Decision.NEEDS_HUMAN_REVIEW: 1, Decision.DENY: 2}
        return max(results, key=lambda item: (priority[item.decision], int(item.risk_level)))
