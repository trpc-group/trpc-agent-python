# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Built-in rule definitions for the tool safety scanner."""

from __future__ import annotations

from dataclasses import dataclass

from ._types import RiskLevel
from ._types import RiskType
from ._types import SafetyDecision


@dataclass(frozen=True)
class RuleDefinition:
    """Static rule metadata."""

    rule_id: str
    risk_type: RiskType
    risk_level: RiskLevel
    decision: SafetyDecision
    message: str
    recommendation: str


RULES: dict[str, RuleDefinition] = {
    "FILE_RECURSIVE_DELETE":
    RuleDefinition(
        "FILE_RECURSIVE_DELETE",
        RiskType.FILE_OPERATION,
        RiskLevel.CRITICAL,
        SafetyDecision.DENY,
        "Dangerous recursive delete detected.",
        "Avoid recursive deletion or restrict it to an explicitly approved workspace path.",
    ),
    "FILE_SECRET_READ":
    RuleDefinition(
        "FILE_SECRET_READ",
        RiskType.FILE_OPERATION,
        RiskLevel.CRITICAL,
        SafetyDecision.DENY,
        "Sensitive file or credential path access detected.",
        "Do not read credential files from tool-executed scripts.",
    ),
    "FILE_SYSTEM_PATH_WRITE":
    RuleDefinition(
        "FILE_SYSTEM_PATH_WRITE",
        RiskType.FILE_OPERATION,
        RiskLevel.HIGH,
        SafetyDecision.DENY,
        "Write or destructive access to a protected system path detected.",
        "Write only inside the configured workspace or an explicitly approved output directory.",
    ),
    "NET_NON_WHITELIST_EGRESS":
    RuleDefinition(
        "NET_NON_WHITELIST_EGRESS",
        RiskType.NETWORK_EGRESS,
        RiskLevel.CRITICAL,
        SafetyDecision.DENY,
        "Network request to a non-whitelisted domain detected.",
        "Add the domain to allowed_domains only after reviewing the data flow.",
    ),
    "NET_CLIENT_USAGE":
    RuleDefinition(
        "NET_CLIENT_USAGE",
        RiskType.NETWORK_EGRESS,
        RiskLevel.MEDIUM,
        SafetyDecision.NEEDS_HUMAN_REVIEW,
        "Network client usage without a reviewable literal target detected.",
        "Use an explicit whitelisted URL or route the request through a reviewed client.",
    ),
    "PROC_SUBPROCESS":
    RuleDefinition(
        "PROC_SUBPROCESS",
        RiskType.PROCESS_EXECUTION,
        RiskLevel.MEDIUM,
        SafetyDecision.NEEDS_HUMAN_REVIEW,
        "Subprocess execution detected.",
        "Review command construction and avoid shell execution unless required.",
    ),
    "PROC_OS_SYSTEM":
    RuleDefinition(
        "PROC_OS_SYSTEM",
        RiskType.PROCESS_EXECUTION,
        RiskLevel.HIGH,
        SafetyDecision.NEEDS_HUMAN_REVIEW,
        "os.system or equivalent shell execution detected.",
        "Replace shell execution with structured APIs or reviewed command arguments.",
    ),
    "SHELL_PIPELINE":
    RuleDefinition(
        "SHELL_PIPELINE",
        RiskType.PROCESS_EXECUTION,
        RiskLevel.MEDIUM,
        SafetyDecision.NEEDS_HUMAN_REVIEW,
        "Shell pipeline detected.",
        "Review all pipeline stages and ensure untrusted input is not interpolated.",
    ),
    "SHELL_BACKGROUND":
    RuleDefinition(
        "SHELL_BACKGROUND",
        RiskType.PROCESS_EXECUTION,
        RiskLevel.MEDIUM,
        SafetyDecision.NEEDS_HUMAN_REVIEW,
        "Background process execution detected.",
        "Avoid detached processes or enforce timeout and process cleanup.",
    ),
    "SHELL_INJECTION":
    RuleDefinition(
        "SHELL_INJECTION",
        RiskType.PROCESS_EXECUTION,
        RiskLevel.HIGH,
        SafetyDecision.NEEDS_HUMAN_REVIEW,
        "Shell composition pattern that may enable injection detected.",
        "Avoid shell interpolation and pass arguments as structured lists.",
    ),
    "COMMAND_NOT_ALLOWED":
    RuleDefinition(
        "COMMAND_NOT_ALLOWED",
        RiskType.POLICY,
        RiskLevel.HIGH,
        SafetyDecision.DENY,
        "Command is not present in allowed_commands.",
        "Add the command to allowed_commands only after reviewing its behavior.",
    ),
    "PRIVILEGE_ESCALATION":
    RuleDefinition(
        "PRIVILEGE_ESCALATION",
        RiskType.PROCESS_EXECUTION,
        RiskLevel.HIGH,
        SafetyDecision.DENY,
        "Privilege escalation or broad permission change detected.",
        "Remove sudo/su/chmod 777 style operations from tool-executed scripts.",
    ),
    "DEPENDENCY_INSTALL":
    RuleDefinition(
        "DEPENDENCY_INSTALL",
        RiskType.DEPENDENCY_INSTALL,
        RiskLevel.HIGH,
        SafetyDecision.NEEDS_HUMAN_REVIEW,
        "Dependency installation detected.",
        "Pin dependencies and install them through a reviewed environment preparation step.",
    ),
    "RESOURCE_INFINITE_LOOP":
    RuleDefinition(
        "RESOURCE_INFINITE_LOOP",
        RiskType.RESOURCE_ABUSE,
        RiskLevel.HIGH,
        SafetyDecision.NEEDS_HUMAN_REVIEW,
        "Potential infinite loop detected.",
        "Add an explicit bounded condition or timeout.",
    ),
    "RESOURCE_FORK_BOMB":
    RuleDefinition(
        "RESOURCE_FORK_BOMB",
        RiskType.RESOURCE_ABUSE,
        RiskLevel.CRITICAL,
        SafetyDecision.DENY,
        "Fork bomb pattern detected.",
        "Never execute fork bomb patterns.",
    ),
    "RESOURCE_LONG_SLEEP":
    RuleDefinition(
        "RESOURCE_LONG_SLEEP",
        RiskType.RESOURCE_ABUSE,
        RiskLevel.MEDIUM,
        SafetyDecision.NEEDS_HUMAN_REVIEW,
        "Long sleep or timeout-like resource hold detected.",
        "Use bounded waits and enforce the configured timeout.",
    ),
    "RESOURCE_OUTPUT_LIMIT":
    RuleDefinition(
        "RESOURCE_OUTPUT_LIMIT",
        RiskType.RESOURCE_ABUSE,
        RiskLevel.MEDIUM,
        SafetyDecision.NEEDS_HUMAN_REVIEW,
        "Requested output size exceeds the configured policy limit.",
        "Lower the output limit or route large outputs through reviewed artifact storage.",
    ),
    "SENSITIVE_OUTPUT":
    RuleDefinition(
        "SENSITIVE_OUTPUT",
        RiskType.SENSITIVE_LEAK,
        RiskLevel.CRITICAL,
        SafetyDecision.DENY,
        "Sensitive value appears to be written to output, file, or network.",
        "Remove secrets from logs, files, and outbound requests; pass credentials through secure channels.",
    ),
    "SCANNER_ERROR":
    RuleDefinition(
        "SCANNER_ERROR",
        RiskType.UNKNOWN,
        RiskLevel.HIGH,
        SafetyDecision.NEEDS_HUMAN_REVIEW,
        "Safety scanner failed before completing analysis.",
        "Review the script manually or fix the scanner error before execution.",
    ),
}
