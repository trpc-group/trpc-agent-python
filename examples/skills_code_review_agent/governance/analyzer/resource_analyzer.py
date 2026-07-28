"""Per-request and cumulative execution budget analysis."""

from ...agent.models import Decision, ExecutionBudget, ExecutionRequest
from ..models import AnalysisResult, RiskLevel
from ..policy import GovernancePolicy


def analyze_resources(
    request: ExecutionRequest,
    policy: GovernancePolicy,
    budget: ExecutionBudget,
) -> AnalysisResult:
    if request.timeout > policy.max_timeout_seconds or request.memory_limit_mb > policy.max_memory_mb:
        return AnalysisResult(
            decision=Decision.NEEDS_HUMAN_REVIEW,
            risk_level=RiskLevel.MEDIUM,
            reason="Requested resources exceed the per-execution policy.",
            matched_rule="resource_limit_exceeded",
        )
    if budget.calls_used >= budget.max_calls or budget.seconds_used + request.timeout > budget.max_total_seconds:
        return AnalysisResult(
            decision=Decision.DENY,
            risk_level=RiskLevel.HIGH,
            reason="Execution budget is exhausted.",
            matched_rule="budget_exceeded",
        )
    return AnalysisResult()
