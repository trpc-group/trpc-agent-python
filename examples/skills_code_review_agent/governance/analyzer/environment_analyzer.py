"""Environment-variable exposure control."""

from ...agent.models import Decision, ExecutionRequest
from ..models import AnalysisResult, RiskLevel
from ..policy import GovernancePolicy


def analyze_environment(request: ExecutionRequest, policy: GovernancePolicy) -> AnalysisResult:
    unknown = set(request.env) - policy.allowed_environment
    if unknown:
        return AnalysisResult(
            decision=Decision.DENY,
            risk_level=RiskLevel.HIGH,
            reason=f"Environment variables are not allowed: {', '.join(sorted(unknown))}.",
            matched_rule="environment_not_allowed",
        )
    return AnalysisResult()
