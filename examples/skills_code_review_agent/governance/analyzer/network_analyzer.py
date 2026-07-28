"""Default-deny network policy."""

from ...agent.models import Decision, ExecutionRequest
from ..models import AnalysisResult, RiskLevel
from ..policy import GovernancePolicy


def analyze_network(request: ExecutionRequest, policy: GovernancePolicy) -> AnalysisResult:
    denied = set(request.network_targets) - policy.allowed_network_targets
    if denied:
        return AnalysisResult(
            decision=Decision.DENY,
            risk_level=RiskLevel.HIGH,
            reason="Network target is not allowlisted.",
            matched_rule="network_not_allowed",
        )
    return AnalysisResult()
