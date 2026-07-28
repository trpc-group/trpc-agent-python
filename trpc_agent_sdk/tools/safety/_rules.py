# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Built-in rule metadata shared by Python and Bash analyzers."""

from __future__ import annotations

from dataclasses import dataclass

from ._models import RiskCategory
from ._models import RiskLevel
from ._models import SafetyDecision
from ._models import SafetyFinding
from ._policy import SafetyPolicy


@dataclass(frozen=True)
class RuleSpec:
    """Stable metadata for one built-in safety rule."""

    rule_id: str
    category: RiskCategory
    risk_level: RiskLevel
    action: SafetyDecision
    message: str
    recommendation: str


RULE_SPECS: dict[str, RuleSpec] = {
    "FILE-001":
    RuleSpec(
        "FILE-001",
        RiskCategory.FILE,
        RiskLevel.CRITICAL,
        SafetyDecision.DENY,
        "Dangerous deletion targets a protected or out-of-workspace path.",
        "Restrict deletion to an explicit disposable path inside the workspace.",
    ),
    "FILE-002":
    RuleSpec(
        "FILE-002",
        RiskCategory.FILE,
        RiskLevel.HIGH,
        SafetyDecision.NEEDS_HUMAN_REVIEW,
        "Recursive deletion inside the workspace requires approval.",
        "Confirm the target is disposable and approve this invocation explicitly.",
    ),
    "FILE-003":
    RuleSpec(
        "FILE-003",
        RiskCategory.FILE,
        RiskLevel.CRITICAL,
        SafetyDecision.DENY,
        "The script accesses a credential or sensitive configuration path.",
        "Remove credential access or provide only the minimum required value through a secret manager.",
    ),
    "FILE-004":
    RuleSpec(
        "FILE-004",
        RiskCategory.FILE,
        RiskLevel.CRITICAL,
        SafetyDecision.DENY,
        "The script may overwrite a protected system or credential path.",
        "Write only to an explicit path inside the workspace.",
    ),
    "NET-001":
    RuleSpec(
        "NET-001",
        RiskCategory.NETWORK,
        RiskLevel.HIGH,
        SafetyDecision.DENY,
        "The script connects to a domain that is not allowlisted.",
        "Add a reviewed hostname to the policy allowlist or remove the outbound request.",
    ),
    "NET-002":
    RuleSpec(
        "NET-002",
        RiskCategory.NETWORK,
        RiskLevel.HIGH,
        SafetyDecision.NEEDS_HUMAN_REVIEW,
        "The network destination cannot be determined statically.",
        "Use a literal allowlisted hostname or require human approval.",
    ),
    "PROC-001":
    RuleSpec(
        "PROC-001",
        RiskCategory.PROCESS,
        RiskLevel.CRITICAL,
        SafetyDecision.DENY,
        "The script uses a shell, command substitution, dynamic execution, or privilege escalation.",
        "Use a direct argument list with shell disabled and remove privileged execution.",
    ),
    "PROC-002":
    RuleSpec(
        "PROC-002",
        RiskCategory.PROCESS,
        RiskLevel.HIGH,
        SafetyDecision.DENY,
        "The script invokes a command that is not allowlisted.",
        "Use an allowlisted command or update the reviewed command policy.",
    ),
    "PROC-003":
    RuleSpec(
        "PROC-003",
        RiskCategory.PROCESS,
        RiskLevel.HIGH,
        SafetyDecision.NEEDS_HUMAN_REVIEW,
        "The command is built dynamically and cannot be verified.",
        "Use a literal argument list or require human approval.",
    ),
    "PROC-004":
    RuleSpec(
        "PROC-004",
        RiskCategory.PROCESS,
        RiskLevel.HIGH,
        SafetyDecision.NEEDS_HUMAN_REVIEW,
        "The script starts a background process.",
        "Run the process in the foreground with an explicit timeout or require approval.",
    ),
    "DEP-001":
    RuleSpec(
        "DEP-001",
        RiskCategory.DEPENDENCY,
        RiskLevel.MEDIUM,
        SafetyDecision.NEEDS_HUMAN_REVIEW,
        "The script changes installed dependencies or the host environment.",
        "Pin and review the dependency, then approve the installation explicitly.",
    ),
    "RES-001":
    RuleSpec(
        "RES-001",
        RiskCategory.RESOURCE,
        RiskLevel.CRITICAL,
        SafetyDecision.DENY,
        "A fork-bomb pattern can exhaust process resources.",
        "Remove recursive process creation and enforce runtime PID limits.",
    ),
    "RES-002":
    RuleSpec(
        "RES-002",
        RiskCategory.RESOURCE,
        RiskLevel.MEDIUM,
        SafetyDecision.NEEDS_HUMAN_REVIEW,
        "The script may consume excessive time or concurrency.",
        "Add a finite bound below the configured limit or require approval.",
    ),
    "RES-003":
    RuleSpec(
        "RES-003",
        RiskCategory.RESOURCE,
        RiskLevel.HIGH,
        SafetyDecision.DENY,
        "A statically identifiable write exceeds the configured size limit.",
        "Reduce the write size and enforce a runtime disk quota.",
    ),
    "SECRET-001":
    RuleSpec(
        "SECRET-001",
        RiskCategory.SECRET,
        RiskLevel.CRITICAL,
        SafetyDecision.DENY,
        "A sensitive value flows to output, a file, or a network sink.",
        "Remove the secret from the sink and pass only a redacted or scoped value.",
    ),
    "POLICY-001":
    RuleSpec(
        "POLICY-001",
        RiskCategory.POLICY,
        RiskLevel.HIGH,
        SafetyDecision.DENY,
        "The requested timeout exceeds the configured maximum.",
        "Lower the timeout or update the policy after review.",
    ),
    "POLICY-002":
    RuleSpec(
        "POLICY-002",
        RiskCategory.POLICY,
        RiskLevel.HIGH,
        SafetyDecision.DENY,
        "The script exceeds the configured parsing size or line limit.",
        "Reduce the script size or scan smaller units independently.",
    ),
    "PARSE-001":
    RuleSpec(
        "PARSE-001",
        RiskCategory.PARSER,
        RiskLevel.MEDIUM,
        SafetyDecision.NEEDS_HUMAN_REVIEW,
        "The input cannot be parsed reliably by the lightweight analyzer.",
        "Simplify the input or require human review and sandboxed execution.",
    ),
}


def make_finding(
    rule_id: str,
    policy: SafetyPolicy,
    evidence: str,
    *,
    line_number: int | None = None,
    column: int | None = None,
) -> SafetyFinding | None:
    """Create a finding after applying rule enablement and action overrides."""

    spec = RULE_SPECS[rule_id]
    override = policy.rule_overrides.get(rule_id)
    if override is not None and not override.enabled:
        return None
    action = override.action if override is not None and override.action is not None else spec.action
    return SafetyFinding(
        rule_id=rule_id,
        category=spec.category,
        risk_level=spec.risk_level,
        action=action,
        message=spec.message,
        evidence=evidence,
        line_number=line_number,
        column=column,
        recommendation=spec.recommendation,
    )
