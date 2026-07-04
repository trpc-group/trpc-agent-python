# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Built-in safety rule catalog and decision helpers."""

from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Sequence

from pydantic import BaseModel

from ._policy import SafetyPolicy
from ._redaction import redact_evidence
from ._types import RiskLevel
from ._types import RiskType
from ._types import SafetyDecision
from ._types import ScanFinding


class RuleDefinition(BaseModel):
    """Static metadata for a safety rule."""

    rule_id: str
    risk_type: RiskType
    risk_level: RiskLevel
    decision: SafetyDecision
    message: str
    recommendation: str


def _rule(
    rule_id: str,
    risk_type: RiskType,
    risk_level: RiskLevel,
    decision: SafetyDecision,
    message: str,
    recommendation: str,
) -> RuleDefinition:
    return RuleDefinition(
        rule_id=rule_id,
        risk_type=risk_type,
        risk_level=risk_level,
        decision=decision,
        message=message,
        recommendation=recommendation,
    )


DEFAULT_RULE_DEFINITIONS: dict[str, RuleDefinition] = {
    "FILE_RECURSIVE_DELETE": _rule(
        "FILE_RECURSIVE_DELETE",
        RiskType.FILE_OPERATION,
        RiskLevel.CRITICAL,
        SafetyDecision.DENY,
        "Recursive deletion of files or directories was detected.",
        "Remove recursive deletion or require an isolated workspace with explicit approval.",
    ),
    "FILE_SYSTEM_OVERWRITE": _rule(
        "FILE_SYSTEM_OVERWRITE",
        RiskType.FILE_OPERATION,
        RiskLevel.HIGH,
        SafetyDecision.DENY,
        "Potential overwrite of a protected system path was detected.",
        "Avoid writing to system paths; write only inside an approved workspace directory.",
    ),
    "FILE_SENSITIVE_READ": _rule(
        "FILE_SENSITIVE_READ",
        RiskType.FILE_OPERATION,
        RiskLevel.CRITICAL,
        SafetyDecision.DENY,
        "Read access to a sensitive credential or configuration file was detected.",
        "Remove reads of sensitive files such as .env, SSH keys, or cloud credentials.",
    ),
    "FILE_FORBIDDEN_PATH_ACCESS": _rule(
        "FILE_FORBIDDEN_PATH_ACCESS",
        RiskType.FILE_OPERATION,
        RiskLevel.HIGH,
        SafetyDecision.DENY,
        "Access to a path denied by the safety policy was detected.",
        "Change the command to avoid denied paths or update the policy after review.",
    ),
    "NET_NON_WHITELIST_EGRESS": _rule(
        "NET_NON_WHITELIST_EGRESS",
        RiskType.NETWORK_EGRESS,
        RiskLevel.HIGH,
        SafetyDecision.DENY,
        "Network egress to a non-allowlisted domain was detected.",
        "Use an allowlisted domain or add the domain to policy only after review.",
    ),
    "NET_DYNAMIC_EGRESS_REVIEW": _rule(
        "NET_DYNAMIC_EGRESS_REVIEW",
        RiskType.NETWORK_EGRESS,
        RiskLevel.MEDIUM,
        SafetyDecision.NEEDS_HUMAN_REVIEW,
        "Dynamic network destination construction was detected.",
        "Review the destination construction or replace it with a static allowlisted domain.",
    ),
    "PROC_OS_SYSTEM": _rule(
        "PROC_OS_SYSTEM",
        RiskType.PROCESS_EXECUTION,
        RiskLevel.MEDIUM,
        SafetyDecision.NEEDS_HUMAN_REVIEW,
        "Process execution through os.system or equivalent was detected.",
        "Review the command and prefer structured subprocess invocation without shell expansion.",
    ),
    "PROC_SUBPROCESS_SHELL": _rule(
        "PROC_SUBPROCESS_SHELL",
        RiskType.PROCESS_EXECUTION,
        RiskLevel.HIGH,
        SafetyDecision.NEEDS_HUMAN_REVIEW,
        "Subprocess execution with shell=True was detected.",
        "Avoid shell=True or require human review for the exact command and inputs.",
    ),
    "PROC_SHELL_PIPE_OR_CHAIN": _rule(
        "PROC_SHELL_PIPE_OR_CHAIN",
        RiskType.PROCESS_EXECUTION,
        RiskLevel.MEDIUM,
        SafetyDecision.NEEDS_HUMAN_REVIEW,
        "Shell pipeline or command chaining was detected.",
        "Review shell metacharacters and split commands into explicit safe steps when possible.",
    ),
    "PROC_BACKGROUND_PROCESS": _rule(
        "PROC_BACKGROUND_PROCESS",
        RiskType.PROCESS_EXECUTION,
        RiskLevel.MEDIUM,
        SafetyDecision.NEEDS_HUMAN_REVIEW,
        "Background process execution was detected.",
        "Avoid background processes unless lifecycle and cleanup are explicitly controlled.",
    ),
    "PROC_PRIVILEGE_ESCALATION": _rule(
        "PROC_PRIVILEGE_ESCALATION",
        RiskType.PROCESS_EXECUTION,
        RiskLevel.HIGH,
        SafetyDecision.DENY,
        "Privilege escalation or permission-changing command was detected.",
        "Remove sudo, su, unsafe chmod, or ownership changes from tool-executed scripts.",
    ),
    "POLICY_DENIED_COMMAND": _rule(
        "POLICY_DENIED_COMMAND",
        RiskType.POLICY_VIOLATION,
        RiskLevel.HIGH,
        SafetyDecision.DENY,
        "Command denied by the safety policy was detected.",
        "Remove the denied command or update policy only after review.",
    ),
    "DEP_PIP_INSTALL": _rule(
        "DEP_PIP_INSTALL",
        RiskType.DEPENDENCY_INSTALL,
        RiskLevel.MEDIUM,
        SafetyDecision.NEEDS_HUMAN_REVIEW,
        "Python dependency installation was detected.",
        "Review dependency source and pinning before allowing package installation.",
    ),
    "DEP_NPM_INSTALL": _rule(
        "DEP_NPM_INSTALL",
        RiskType.DEPENDENCY_INSTALL,
        RiskLevel.MEDIUM,
        SafetyDecision.NEEDS_HUMAN_REVIEW,
        "JavaScript dependency installation was detected.",
        "Review package source, lockfile impact, and install scope before proceeding.",
    ),
    "DEP_SYSTEM_INSTALL": _rule(
        "DEP_SYSTEM_INSTALL",
        RiskType.DEPENDENCY_INSTALL,
        RiskLevel.HIGH,
        SafetyDecision.DENY,
        "System package installation was detected.",
        "Do not install system packages from tool execution without an approved runtime image.",
    ),
    "RES_INFINITE_LOOP": _rule(
        "RES_INFINITE_LOOP",
        RiskType.RESOURCE_ABUSE,
        RiskLevel.MEDIUM,
        SafetyDecision.NEEDS_HUMAN_REVIEW,
        "Potential infinite loop was detected.",
        "Add bounded iteration, timeouts, or explicit cancellation conditions.",
    ),
    "RES_FORK_BOMB": _rule(
        "RES_FORK_BOMB",
        RiskType.RESOURCE_ABUSE,
        RiskLevel.CRITICAL,
        SafetyDecision.DENY,
        "Fork bomb or explosive process spawning pattern was detected.",
        "Remove process spawning recursion and rely on runtime resource limits.",
    ),
    "RES_LONG_SLEEP": _rule(
        "RES_LONG_SLEEP",
        RiskType.RESOURCE_ABUSE,
        RiskLevel.MEDIUM,
        SafetyDecision.NEEDS_HUMAN_REVIEW,
        "Long sleep or wait duration was detected.",
        "Shorten waits or use an explicit timeout approved by policy.",
    ),
    "RES_LARGE_WRITE": _rule(
        "RES_LARGE_WRITE",
        RiskType.RESOURCE_ABUSE,
        RiskLevel.MEDIUM,
        SafetyDecision.NEEDS_HUMAN_REVIEW,
        "Potential large or unbounded file write was detected.",
        "Bound output size and write only to approved workspace paths.",
    ),
    "LEAK_SECRET_LITERAL": _rule(
        "LEAK_SECRET_LITERAL",
        RiskType.SENSITIVE_LEAK,
        RiskLevel.HIGH,
        SafetyDecision.DENY,
        "Secret-like literal data may be written, logged, or sent.",
        "Remove hard-coded secrets and use approved secret management without exposing values.",
    ),
    "LEAK_ENV_SECRET": _rule(
        "LEAK_ENV_SECRET",
        RiskType.SENSITIVE_LEAK,
        RiskLevel.HIGH,
        SafetyDecision.DENY,
        "Sensitive environment variable output or exfiltration was detected.",
        "Do not print, write, or send sensitive environment variable values.",
    ),
    "PARSER_FALLBACK_USED": _rule(
        "PARSER_FALLBACK_USED",
        RiskType.PARSER_WARNING,
        RiskLevel.LOW,
        SafetyDecision.NEEDS_HUMAN_REVIEW,
        "The script could not be fully parsed and fallback scanning was used.",
        "Review the script manually or fix syntax so the scanner can analyze it precisely.",
    ),
}

_RISK_LEVEL_ORDER: dict[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}


def iter_rule_definitions() -> tuple[RuleDefinition, ...]:
    """Return built-in rules in stable insertion order."""

    return tuple(DEFAULT_RULE_DEFINITIONS.values())


def get_rule_definition(rule_id: str) -> RuleDefinition:
    """Return a built-in rule definition by id."""

    try:
        return DEFAULT_RULE_DEFINITIONS[rule_id]
    except KeyError as ex:
        raise KeyError(f"Unknown safety rule: {rule_id}") from ex


def is_rule_enabled(rule_id: str, policy: SafetyPolicy) -> bool:
    """Return whether a rule is enabled by policy."""

    get_rule_definition(rule_id)
    override = policy.rules.get(rule_id)
    return True if override is None else override.enabled


def apply_rule_policy(rule: RuleDefinition, policy: SafetyPolicy) -> RuleDefinition:
    """Apply a policy override to a rule without mutating the catalog."""

    override = policy.rules.get(rule.rule_id)
    if override is None:
        return rule

    updates = {}
    if override.decision is not None:
        updates["decision"] = override.decision
    if override.risk_level is not None:
        updates["risk_level"] = override.risk_level
    if not updates:
        return rule
    return rule.model_copy(update=updates)


def make_finding(
    rule_id: str,
    evidence: object,
    policy: SafetyPolicy,
    *,
    message: str | None = None,
    recommendation: str | None = None,
    line: int | None = None,
    column: int | None = None,
    metadata: dict[str, object] | None = None,
    redacted: bool | None = None,
) -> ScanFinding:
    """Create a ScanFinding with policy overrides and redacted evidence."""

    rule = apply_rule_policy(get_rule_definition(rule_id), policy)
    raw_evidence = str(evidence or "")
    safe_evidence = redact_evidence(raw_evidence, max_chars=policy.max_evidence_chars)
    return ScanFinding(
        rule_id=rule.rule_id,
        risk_type=rule.risk_type,
        risk_level=rule.risk_level,
        decision=rule.decision,
        message=message or rule.message,
        evidence=safe_evidence,
        line=line,
        column=column,
        recommendation=recommendation or rule.recommendation,
        redacted=(safe_evidence != raw_evidence) if redacted is None else redacted,
        metadata=metadata or {},
    )


def merge_findings(findings: Sequence[ScanFinding] | Iterable[ScanFinding]) -> tuple[SafetyDecision, RiskLevel]:
    """Merge finding decisions and risk levels into a final report summary."""

    items = list(findings)
    if not items:
        return SafetyDecision.ALLOW, RiskLevel.LOW

    decision = SafetyDecision.ALLOW
    for finding in items:
        if finding.decision == SafetyDecision.DENY:
            decision = SafetyDecision.DENY
            break
        if finding.decision == SafetyDecision.NEEDS_HUMAN_REVIEW:
            decision = SafetyDecision.NEEDS_HUMAN_REVIEW

    risk_level = max((finding.risk_level for finding in items), key=lambda level: _RISK_LEVEL_ORDER[level])
    return decision, risk_level


def should_block_decision(decision: SafetyDecision, policy: SafetyPolicy) -> bool:
    """Return whether a decision should block execution under policy."""

    if decision == SafetyDecision.DENY:
        return True
    if decision == SafetyDecision.NEEDS_HUMAN_REVIEW:
        return policy.review_blocks_execution
    return False
