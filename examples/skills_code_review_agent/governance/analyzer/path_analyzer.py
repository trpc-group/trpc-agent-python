"""Workspace containment and protected-path analysis."""

from pathlib import Path

from ...agent.models import Decision, ExecutionRequest
from ..models import AnalysisResult, RiskLevel
from ..policy import GovernancePolicy


def _inside(path: str, root: str) -> bool:
    candidate, base = Path(path).expanduser().resolve(), Path(root).resolve()
    return candidate == base or candidate.is_relative_to(base)


def analyze_paths(
    request: ExecutionRequest,
    policy: GovernancePolicy,
    workspace_root: str,
) -> AnalysisResult:
    for raw_path in [request.cwd, *request.input_paths]:
        path = Path(raw_path).expanduser()
        if not _inside(str(path), workspace_root):
            return AnalysisResult(
                decision=Decision.DENY,
                risk_level=RiskLevel.HIGH,
                reason="A requested path escapes the workspace.",
                matched_rule="path_escape",
            )
        normalized = path.as_posix().lower()
        if any(marker.lower() in normalized for marker in policy.protected_paths):
            return AnalysisResult(
                decision=Decision.DENY,
                risk_level=RiskLevel.HIGH,
                reason="A protected path was requested.",
                matched_rule="protected_path",
            )
    return AnalysisResult()
