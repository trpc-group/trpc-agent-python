# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0
"""Rule: sensitive information leakage.

Flags API keys, tokens, passwords, and private-key material being written to
logs, files, or network requests. Detection combines policy-configured secret
regexes with heuristics for common secret-like names.
"""
from __future__ import annotations

import ast
import re

from .base import SafetyRule
from .base import bash_lines
from .base import evidence_snippet
from .base import get_string_literal
from .base import iter_python_calls
from .base import normalize_language
from .base import parse_python_ast
from ..policy import PolicyConfig
from ..types import RiskLevel
from ..types import SafetyFinding
from ..types import ScanInput


# Default secret regexes (augmented by policy.secret_patterns).
_DEFAULT_SECRET_PATTERNS = [
    # OpenAI-style API key (sk-...)
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    # AWS access key id
    re.compile(r"AKIA[0-9A-Z]{16}"),
    # AWS secret access key (40 base64-ish)
    re.compile(r"(?i)aws(.{0,20})?(secret|sk)[^\n]{0,20}[A-Za-z0-9/+=]{40}"),
    # Generic API key / token assignment
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|auth[_-]?token|secret[_-]?key)\s*[=:]\s*['\"][A-Za-z0-9_\-]{16,}['\"]"),
    # Bearer token
    re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.]{20,}"),
    # Slack token
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    # GitHub token
    re.compile(r"gh[ps]_[A-Za-z0-9]{36}"),
    # JWT
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
]

# Variable names that look like they hold secrets.
_SECRET_NAME_HINTS = {"api_key", "apikey", "token", "password", "passwd", "secret", "access_key", "private_key", "client_secret"}

# Sinks where secrets must not be written.
_LEAK_SINKS_PY = {"print", "logging.info", "logging.debug", "logging.warning", "logging.error", "logging.critical", "logger.info", "logger.debug", "logger.warning", "logger.error", "logger.critical", "open"}


class SecretLeakRule(SafetyRule):
    """Detect sensitive data being written to logs, files, or network."""

    rule_id = "R006_secret_leak"
    rule_name = "Sensitive Information Leakage"
    risk_type = "secret_leak"
    default_level = RiskLevel.CRITICAL
    languages = ("python", "bash")

    def check(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        lang = normalize_language(scan_input)
        findings: list[SafetyFinding] = []
        if lang == "python":
            findings.extend(self._check_python(scan_input, policy))
        findings.extend(self._check_bash(scan_input, policy))
        return findings

    # ----- python -----

    def _check_python(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        tree = parse_python_ast(scan_input.script)
        if tree is None:
            return findings

        patterns = list(_DEFAULT_SECRET_PATTERNS)
        for extra in policy.secret_patterns:
            try:
                patterns.append(re.compile(extra))
            except re.error:
                continue

        # 1. String literals that look like secrets.
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for pat in patterns:
                    if pat.search(node.value):
                        findings.append(self._finding(
                            "Hardcoded secret in string literal",
                            getattr(node, "lineno", None),
                            evidence=_redact(node.value),
                            rec="Move secrets to env vars / secret manager; never hardcode.",
                        ))
                        break

        # 2. Secrets piped into leak sinks (print/logger/open('w')).
        for node, name in iter_python_calls(tree):
            lname = name.lower()
            if lname not in {s.lower() for s in _LEAK_SINKS_PY}:
                continue
            for arg in node.args:
                if isinstance(arg, ast.Name) and _looks_like_secret_name(arg.id):
                    findings.append(self._finding(
                        f"Secret-like variable {arg.id!r} passed to {name}()",
                        node.lineno,
                        evidence=f"{name}(..., {arg.id}, ...)",
                        rec=f"Do not log or write {arg.id}; redact before output.",
                    ))
                elif isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    for pat in patterns:
                        if pat.search(arg.value):
                            findings.append(self._finding(
                                f"Secret literal passed to {name}()",
                                node.lineno,
                                evidence=f"{name}({_redact(arg.value)})",
                                rec="Do not pass secrets to logging/file functions.",
                            ))
                            break
        return findings

    # ----- bash -----

    def _check_bash(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        patterns = list(_DEFAULT_SECRET_PATTERNS)
        for extra in policy.secret_patterns:
            try:
                patterns.append(re.compile(extra))
            except re.error:
                continue

        for lineno, line in bash_lines(scan_input.script):
            # Secret assignments: API_KEY=...
            assign_match = re.match(r"(?i)([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(\S+)", line)
            if assign_match and _looks_like_secret_name(assign_match.group(1)):
                val = assign_match.group(2).strip("'\"")
                if len(val) >= 12:
                    findings.append(self._finding(
                        f"Secret assigned to {assign_match.group(1)!r}",
                        lineno,
                        evidence=f"{assign_match.group(1)}={_redact(val)}",
                        rec="Load secrets from env, not inline assignment.",
                    ))
            # Any secret pattern anywhere in the line.
            for pat in patterns:
                if pat.search(line):
                    findings.append(self._finding(
                        "Secret pattern in command",
                        lineno,
                        evidence=_redact(line),
                        rec="Remove hardcoded secrets from scripts.",
                    ))
                    break
        return findings

    def _finding(self, msg, line, evidence, rec) -> SafetyFinding:
        return SafetyFinding(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            risk_type=self.risk_type,
            risk_level=self.default_level,
            evidence=evidence_snippet(evidence),
            line=line,
            recommendation=rec,
            metadata={"message": msg},
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _looks_like_secret_name(name: str) -> bool:
    lname = name.lower()
    return any(hint in lname for hint in _SECRET_NAME_HINTS)


def _redact(text: str, keep: int = 4) -> str:
    """Redact all but the first *keep* chars of a suspected secret."""
    if len(text) <= keep:
        return "***"
    return text[:keep] + "***"
