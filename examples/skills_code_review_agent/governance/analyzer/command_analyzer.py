"""Command allowlist and shell-composition analysis."""

import os

from ...agent.models import Decision, ExecutionRequest
from ..models import AnalysisResult, RiskLevel
from ..policy import GovernancePolicy


def analyze_command(request: ExecutionRequest, policy: GovernancePolicy) -> AnalysisResult:
    if not request.command:
        return AnalysisResult(
            decision=Decision.DENY,
            risk_level=RiskLevel.HIGH,
            reason="No command was provided.",
            matched_rule="command_empty",
        )
    executable = os.path.basename(request.command[0])
    if executable in policy.denied_commands:
        return AnalysisResult(
            decision=Decision.DENY,
            risk_level=RiskLevel.HIGH,
            reason=f"Command {executable} is explicitly forbidden.",
            matched_rule="command_denied",
        )
    tokens = [os.path.basename(token) for token in request.command]
    if "|" in request.command and any(token in {"bash", "sh"} for token in tokens):
        return AnalysisResult(
            decision=Decision.DENY,
            risk_level=RiskLevel.HIGH,
            reason="Piping command output into a shell is forbidden.",
            matched_rule="shell_pipe_execution",
        )
    versioned_python = executable.startswith("python3.") and "python3" in policy.allowed_commands
    if executable not in policy.allowed_commands and not versioned_python:
        return AnalysisResult(
            decision=Decision.DENY,
            risk_level=RiskLevel.HIGH,
            reason=f"Command {executable} is not allowlisted.",
            matched_rule="command_not_allowed",
        )
    if any(argument in policy.dangerous_arguments for argument in request.command[1:]):
        return AnalysisResult(
            decision=Decision.NEEDS_HUMAN_REVIEW,
            risk_level=RiskLevel.MEDIUM,
            reason="Inline executable code requires human approval.",
            matched_rule="inline_code",
        )
    return AnalysisResult()
