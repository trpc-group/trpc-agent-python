# Package safety provides a pre-execution safety guard for command and
# code-execution tools.
#
# Usage::
#
#   from trpc_agent_sdk.tools.safety import scan, default_policy, ToolSafetyFilter
#
#   policy = default_policy()
#   report = scan(Request(command="rm -rf /"), policy)
#   assert report.decision == DECISION_DENY
#
# Mirrors trpc-agent-go/tool/safety/.

from ._types import (
    Decision, RiskLevel, Finding, Report, AuditEvent,
    Policy, Request, CodeBlock,
    DECISION_ALLOW, DECISION_DENY, DECISION_ASK, DECISION_NEEDS_HUMAN_REVIEW,
    RISK_LOW, RISK_MEDIUM, RISK_HIGH, RISK_CRITICAL,
    decision_rank, risk_rank, finding_beats,
)
from ._policy import default_policy, load_policy
from ._scanner import scan
from ._permission import ToolSafetyFilter, SAFETY_FILTER_NAME

__all__ = [
    # Types.
    "Decision", "RiskLevel", "Finding", "Report", "AuditEvent",
    "Policy", "Request", "CodeBlock",
    # Constants.
    "DECISION_ALLOW", "DECISION_DENY", "DECISION_ASK", "DECISION_NEEDS_HUMAN_REVIEW",
    "RISK_LOW", "RISK_MEDIUM", "RISK_HIGH", "RISK_CRITICAL",
    # Core API.
    "scan", "default_policy", "load_policy",
    "decision_rank", "risk_rank", "finding_beats",
    # Filter integration.
    "ToolSafetyFilter", "SAFETY_FILTER_NAME",
]
