# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License Version 2.0.
"""Bash lexical and policy rules for the tool script safety guard."""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Iterable

from ._models import Decision
from ._models import RiskLevel
from ._models import SafetyFinding
from ._policy import ToolSafetyPolicy
from ._rule_utils import URL_RE
from ._rule_utils import add_finding
from ._rule_utils import add_line_finding
from ._rule_utils import domain_allowed
from ._rule_utils import path_is_denied

_INSTALL_RE = re.compile(
    r"(?i)(?:^|[\s;&|])(?:sudo\s+)?(?:pip3?|python\d*(?:\.\d+)?\s+-m\s+pip|npm|yarn|pnpm|apt(?:-get)?|"
    r"apk|yum|dnf|brew)\s+(?:install|add)\b")
_NETWORK_COMMAND_RE = re.compile(r"(?i)(?:^|[\s;&|])(curl|wget)\b")
_SENSITIVE_VARIABLE_RE = re.compile(
    r"(?i)\$(?:\{)?[a-z0-9_]*(?:api[_-]?key|token|password|passwd|secret|private[_-]?key|credential)[a-z0-9_]*(?:\})?")
_SHELL_KEYWORDS = {
    "case",
    "do",
    "done",
    "elif",
    "else",
    "esac",
    "fi",
    "for",
    "function",
    "if",
    "in",
    "select",
    "then",
    "time",
    "until",
    "while",
    "{",
    "}",
}
_SHELL_BUILTINS = {
    ".",
    ":",
    "[",
    "cd",
    "declare",
    "export",
    "false",
    "local",
    "read",
    "readonly",
    "return",
    "set",
    "shift",
    "source",
    "test",
    "true",
    "typeset",
    "unset",
}


def scan_bash(
    script: str,
    policy: ToolSafetyPolicy,
    secrets: Iterable[str],
) -> list[SafetyFinding]:
    """Scan Bash source line-by-line without executing shell expansion."""
    findings: list[SafetyFinding] = []
    for line_number, raw_line in enumerate(script.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        tokens = _shell_tokens(line)
        _scan_paths(script, line, line_number, tokens, findings, policy, secrets)
        _scan_deletion(script, line, line_number, findings, policy, secrets)
        _scan_network(script, line, line_number, findings, policy, secrets)
        _scan_process_syntax(script, line, line_number, findings, policy, secrets)
        _scan_dependencies(script, line, line_number, findings, policy, secrets)
        _scan_resources(script, line, line_number, findings, policy, secrets)
        _scan_secret_output(line, line_number, findings, policy)
        _scan_commands(line, line_number, findings, policy)
    return findings


def _scan_paths(
    script: str,
    line: str,
    line_number: int,
    tokens: list[str],
    findings: list[SafetyFinding],
    policy: ToolSafetyPolicy,
    secrets: Iterable[str],
) -> None:
    candidates = [token.strip("<>(){}") for token in tokens]
    if not any(path_is_denied(candidate, policy) for candidate in candidates):
        return
    add_line_finding(
        findings,
        policy,
        script,
        line_number,
        secrets,
        category="dangerous_file_operation",
        rule_id="FILE-002",
        title="Command accesses a path protected by policy",
        risk_level=RiskLevel.HIGH,
        decision=Decision.DENY,
        recommendation="Use a scoped workspace and inject only files required by the tool.",
    )


def _scan_deletion(
    script: str,
    line: str,
    line_number: int,
    findings: list[SafetyFinding],
    policy: ToolSafetyPolicy,
    secrets: Iterable[str],
) -> None:
    recursive_force = re.search(
        r"(?i)\brm\b[^\n]*(?:-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r|--recursive[^\n]*--force|--force[^\n]*--recursive)",
        line,
    )
    if not recursive_force:
        return
    add_line_finding(
        findings,
        policy,
        script,
        line_number,
        secrets,
        category="dangerous_file_operation",
        rule_id="FILE-001",
        title="Recursive forced deletion detected",
        risk_level=RiskLevel.CRITICAL,
        decision=Decision.DENY,
        recommendation="Replace recursive deletion with reviewed, workspace-scoped cleanup.",
    )


def _scan_network(
    script: str,
    line: str,
    line_number: int,
    findings: list[SafetyFinding],
    policy: ToolSafetyPolicy,
    secrets: Iterable[str],
) -> None:
    if not _NETWORK_COMMAND_RE.search(line):
        return
    urls = URL_RE.findall(line)
    if not urls:
        add_line_finding(
            findings,
            policy,
            script,
            line_number,
            secrets,
            category="network_egress",
            rule_id="NETWORK-002",
            title="Dynamic network target cannot be verified",
            risk_level=RiskLevel.MEDIUM,
            decision=Decision.NEEDS_HUMAN_REVIEW,
            recommendation="Use a literal allowlisted URL or validate the resolved hostname before connecting.",
        )
        return
    if all(domain_allowed(url, policy) for url in urls):
        return
    add_line_finding(
        findings,
        policy,
        script,
        line_number,
        secrets,
        category="network_egress",
        rule_id="NETWORK-001",
        title="Network target is not allowlisted",
        risk_level=RiskLevel.HIGH,
        decision=Decision.DENY,
        recommendation="Add the reviewed hostname to allowed_domains or remove the outbound request.",
    )


def _scan_process_syntax(
    script: str,
    line: str,
    line_number: int,
    findings: list[SafetyFinding],
    policy: ToolSafetyPolicy,
    secrets: Iterable[str],
) -> None:
    if re.search(r"(?<!\|)\|(?!\|)", line) or re.search(r"(?<!&)&(?!&)", line):
        add_line_finding(
            findings,
            policy,
            script,
            line_number,
            secrets,
            category="process_execution",
            rule_id="PROCESS-003",
            title="Shell pipeline or background process detected",
            risk_level=RiskLevel.MEDIUM,
            decision=Decision.NEEDS_HUMAN_REVIEW,
            recommendation="Review every pipeline stage and run it with bounded process supervision.",
        )
    if re.search(r"(?i)(?:^|[\s;&|])sudo(?:\s|$)", line):
        add_line_finding(
            findings,
            policy,
            script,
            line_number,
            secrets,
            category="process_execution",
            rule_id="PROCESS-005",
            title="Privilege escalation command detected",
            risk_level=RiskLevel.CRITICAL,
            decision=Decision.DENY,
            recommendation="Remove privilege escalation and run with a least-privilege sandbox identity.",
        )
    if re.search(r"(?i)(?:^|[\s;&|])eval\s", line) or re.search(r"(?i)(?:bash|sh)\s+-c\s+[\"']?[^\"']*(?:\$|`)", line):
        add_line_finding(
            findings,
            policy,
            script,
            line_number,
            secrets,
            category="process_execution",
            rule_id="PROCESS-002",
            title="Dynamic shell evaluation detected",
            risk_level=RiskLevel.HIGH,
            decision=Decision.DENY,
            recommendation="Avoid eval and shell -c; pass validated arguments directly to the executable.",
        )


def _scan_dependencies(
    script: str,
    line: str,
    line_number: int,
    findings: list[SafetyFinding],
    policy: ToolSafetyPolicy,
    secrets: Iterable[str],
) -> None:
    if not _INSTALL_RE.search(line):
        return
    add_line_finding(
        findings,
        policy,
        script,
        line_number,
        secrets,
        category="dependency_install",
        rule_id="DEPENDENCY-001",
        title="Runtime dependency installation detected",
        risk_level=RiskLevel.HIGH,
        decision=Decision.DENY,
        recommendation="Build reviewed dependencies into an immutable execution image.",
    )


def _scan_resources(
    script: str,
    line: str,
    line_number: int,
    findings: list[SafetyFinding],
    policy: ToolSafetyPolicy,
    secrets: Iterable[str],
) -> None:
    if re.search(r"(?i)\bwhile\s+(?:true|:)\s*;?\s*do\b|\bfor\s*\(\(\s*;\s*;\s*\)\)", line):
        add_line_finding(
            findings,
            policy,
            script,
            line_number,
            secrets,
            category="resource_abuse",
            rule_id="RESOURCE-001",
            title="Unbounded loop detected",
            risk_level=RiskLevel.HIGH,
            decision=Decision.DENY,
            recommendation="Add a bounded condition, cancellation check, and runtime timeout.",
        )
    if re.search(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;?\s*:", line):
        add_line_finding(
            findings,
            policy,
            script,
            line_number,
            secrets,
            category="resource_abuse",
            rule_id="RESOURCE-003",
            title="Fork bomb pattern detected",
            risk_level=RiskLevel.CRITICAL,
            decision=Decision.DENY,
            recommendation="Remove recursive process creation and enforce a sandbox PID limit.",
        )
    sleep_match = re.search(r"(?i)(?:^|[\s;&|])sleep\s+(\d+(?:\.\d+)?)", line)
    if sleep_match and float(sleep_match.group(1)) > policy.max_sleep_seconds:
        add_line_finding(
            findings,
            policy,
            script,
            line_number,
            secrets,
            category="resource_abuse",
            rule_id="RESOURCE-002",
            title="Long sleep exceeds policy",
            risk_level=RiskLevel.MEDIUM,
            decision=Decision.NEEDS_HUMAN_REVIEW,
            recommendation="Use a shorter delay or an externally cancellable scheduler.",
        )


def _scan_secret_output(
    line: str,
    line_number: int,
    findings: list[SafetyFinding],
    policy: ToolSafetyPolicy,
) -> None:
    if not _SENSITIVE_VARIABLE_RE.search(line):
        return
    if not re.search(r"(?i)(?:^|[\s;&|])(echo|printf|curl|wget|tee)\b|[>]{1,2}", line):
        return
    add_finding(
        findings,
        policy,
        category="sensitive_data_exposure",
        rule_id="SECRET-001",
        title="Sensitive value may be written or transmitted",
        risk_level=RiskLevel.HIGH,
        decision=Decision.DENY,
        evidence=f"line {line_number}: [REDACTED sensitive shell variable in output]",
        recommendation="Remove the secret from output and pass credentials through a scoped secret provider.",
        line_number=line_number,
        redacted=True,
    )


def _scan_commands(
    line: str,
    line_number: int,
    findings: list[SafetyFinding],
    policy: ToolSafetyPolicy,
) -> None:
    if not policy.review_unknown_commands:
        return
    for command in _extract_commands(line):
        if command in policy.allowed_commands or command in _SHELL_BUILTINS or command in _SHELL_KEYWORDS:
            continue
        dynamic = "$" in command or "`" in command
        title = "Dynamic command requires review" if dynamic else "Command is not in allowed_commands"
        evidence = f"line {line_number}: command={'[dynamic]' if dynamic else command}"
        add_finding(
            findings,
            policy,
            category="process_execution",
            rule_id="PROCESS-004",
            title=title,
            risk_level=RiskLevel.MEDIUM,
            decision=Decision.NEEDS_HUMAN_REVIEW,
            evidence=evidence,
            recommendation="Use an allowed command or obtain human approval for this executable.",
            line_number=line_number,
            redacted=dynamic,
        )


def _extract_commands(line: str) -> list[str]:
    commands = []
    for segment in re.split(r"\|\||&&|[|;&]", line):
        tokens = _shell_tokens(segment)
        while tokens and ("=" in tokens[0] and not tokens[0].startswith(("=", "/"))):
            tokens.pop(0)
        if not tokens:
            continue
        command = tokens[0]
        if command in _SHELL_KEYWORDS and len(tokens) > 1:
            continue
        commands.append(Path(command).name)
    return commands


def _shell_tokens(line: str) -> list[str]:
    try:
        return shlex.split(line, comments=True, posix=True)
    except ValueError:
        return line.split()
