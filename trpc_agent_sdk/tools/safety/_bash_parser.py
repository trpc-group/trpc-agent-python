# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Bash script safety scanner using regex patterns and shlex tokenization."""

from __future__ import annotations

import re
import shlex
from typing import List
from urllib.parse import urlparse

from ._policy import PolicyConfig
from ._rules import (
    BASH_DANGEROUS_DELETE_PATTERNS,
    BASH_NETWORK_PATTERNS,
    BASH_RESOURCE_PATTERNS,
    BASH_SECRET_PATTERNS,
    BASH_SYSTEM_PATTERNS,
    PYTHON_INSTALL_PATTERNS,
    SENSITIVE_PATH_PATTERNS,
    SENSITIVE_WORD_PATTERNS,
    _SENSITIVE_SUFFIXES,
    sanitize_text,
)
from ._types import RiskLevel
from ._types import RiskType
from ._types import SafetyFinding

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
_HOSTNAME_RE = re.compile(r'\b(nc|netcat|socat)\s+([^\s;|&]+)')


class BashParser:
    """Safety scanner for Bash/shell scripts using regex patterns."""

    def __init__(self, policy: PolicyConfig) -> None:
        self._policy = policy

    def parse(self, script: str) -> List[SafetyFinding]:
        """Scan a bash script and return safety findings."""
        findings: List[SafetyFinding] = []
        lines = script.split("\n")

        for i, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            findings.extend(self._check_dangerous_commands(stripped, i))
            findings.extend(self._check_network_egress(stripped, i))
            findings.extend(self._check_system_commands(stripped, i))
            findings.extend(self._check_dependency_install(stripped, i))
            findings.extend(self._check_resource_abuse(stripped, i))
            findings.extend(self._check_secret_exfiltration(stripped, i))

        # Check command whitelist/blacklist
        findings.extend(self._check_command_policy(script))

        return findings

    def _check_dangerous_commands(self, line: str, line_num: int) -> List[SafetyFinding]:
        findings: List[SafetyFinding] = []
        for pattern, rule_id, risk in BASH_DANGEROUS_DELETE_PATTERNS:
            if pattern.search(line):
                findings.append(
                    SafetyFinding(
                        rule_id=rule_id,
                        rule_name="Dangerous Delete Operation",
                        risk_type=RiskType.DANGEROUS_FILE_OPERATION,
                        risk_level=RiskLevel(risk),
                        evidence=sanitize_text(line, self._policy.secret_patterns),
                        line=line_num,
                        recommendation="Review delete operation. Avoid recursive deletes on system paths.",
                    ))
                return findings

        # Check for sensitive path access (e.g. cat ~/.ssh/id_rsa)
        for sensitive in SENSITIVE_PATH_PATTERNS:
            if sensitive in line and not sensitive.startswith("*"):
                findings.append(
                    SafetyFinding(
                        rule_id="R001_CREDENTIAL_FILE_ACCESS",
                        rule_name="Sensitive Path Access",
                        risk_type=RiskType.DANGEROUS_FILE_OPERATION,
                        risk_level=RiskLevel.HIGH,
                        evidence=sanitize_text(line, self._policy.secret_patterns),
                        line=line_num,
                        recommendation="Review access to sensitive file paths.",
                    ))
                return findings

        # Check for sensitive word patterns (word-boundary match to avoid false positives)
        for word in SENSITIVE_WORD_PATTERNS:
            if re.search(r'\b' + re.escape(word) + r'\b', line):
                findings.append(
                    SafetyFinding(
                        rule_id="R001_CREDENTIAL_FILE_ACCESS",
                        rule_name="Sensitive Path Access",
                        risk_type=RiskType.DANGEROUS_FILE_OPERATION,
                        risk_level=RiskLevel.HIGH,
                        evidence=sanitize_text(line, self._policy.secret_patterns),
                        line=line_num,
                        recommendation="Review access to sensitive file paths.",
                    ))
                return findings
        # Check for sensitive file suffixes (e.g. cat server.pem)
        for token in line.split():
            base = token.strip(";|&\"'")
            for suffix in _SENSITIVE_SUFFIXES:
                if base.endswith(suffix):
                    findings.append(
                        SafetyFinding(
                            rule_id="R001_CREDENTIAL_FILE_ACCESS",
                            rule_name="Sensitive File Access",
                            risk_type=RiskType.DANGEROUS_FILE_OPERATION,
                            risk_level=RiskLevel.HIGH,
                            evidence=sanitize_text(line, self._policy.secret_patterns),
                            line=line_num,
                            recommendation="Review access to sensitive key/certificate files.",
                        ))
                    return findings
        return findings

    def _check_network_egress(self, line: str, line_num: int) -> List[SafetyFinding]:
        findings: List[SafetyFinding] = []

        # Determine if this line uses a network tool
        has_network_tool = False
        for pattern, _rule_id, _risk in BASH_NETWORK_PATTERNS:
            if pattern.search(line):
                has_network_tool = True
                break

        # Check http/https URLs against domain whitelist
        urls_found = list(_URL_RE.finditer(line))
        all_whitelisted = len(urls_found) > 0
        for url_match in urls_found:
            url = url_match.group()
            try:
                hostname = urlparse(url).hostname
                if hostname and not self._policy.is_domain_allowed(hostname):
                    all_whitelisted = False
                    findings.append(
                        SafetyFinding(
                            rule_id="R002_NON_WHITELIST_DOMAIN_ACCESS",
                            rule_name="Non-Whitelisted Domain Access",
                            risk_type=RiskType.NETWORK_EGRESS,
                            risk_level=RiskLevel.HIGH,
                            evidence=sanitize_text(url, self._policy.secret_patterns),
                            line=line_num,
                            recommendation=f"Domain '{hostname}' is not in the network allowlist.",
                        ))
            except Exception:
                all_whitelisted = False

        # Check raw hostnames for nc/netcat/socat (no http:// prefix)
        host_match = _HOSTNAME_RE.search(line)
        if host_match:
            hostname = host_match.group(2)
            if hostname and not self._policy.is_domain_allowed(hostname):
                all_whitelisted = False
                findings.append(
                    SafetyFinding(
                        rule_id="R002_NON_WHITELIST_DOMAIN_ACCESS",
                        rule_name="Non-Whitelisted Domain Access",
                        risk_type=RiskType.NETWORK_EGRESS,
                        risk_level=RiskLevel.HIGH,
                        evidence=sanitize_text(line, self._policy.secret_patterns),
                        line=line_num,
                        recommendation=f"Domain '{hostname}' is not in the network allowlist.",
                    ))

        # Add network tool finding only if domains are not all whitelisted
        if has_network_tool and not all_whitelisted:
            for pattern, rule_id, risk in BASH_NETWORK_PATTERNS:
                if pattern.search(line):
                    findings.append(
                        SafetyFinding(
                            rule_id=rule_id,
                            rule_name="Network Tool Usage",
                            risk_type=RiskType.NETWORK_EGRESS,
                            risk_level=RiskLevel(risk),
                            evidence=sanitize_text(line, self._policy.secret_patterns),
                            line=line_num,
                            recommendation="Review network tool usage. Ensure whitelisted domains only.",
                        ))
                    break

        return findings

    def _check_system_commands(self, line: str, line_num: int) -> List[SafetyFinding]:
        findings: List[SafetyFinding] = []
        for pattern, rule_id, risk in BASH_SYSTEM_PATTERNS:
            if pattern.search(line):
                findings.append(
                    SafetyFinding(
                        rule_id=rule_id,
                        rule_name="System Command Execution",
                        risk_type=RiskType.SYSTEM_COMMAND,
                        risk_level=RiskLevel(risk),
                        evidence=sanitize_text(line, self._policy.secret_patterns),
                        line=line_num,
                        recommendation="Review system command usage.",
                    ))
        return findings

    def _check_dependency_install(self, line: str, line_num: int) -> List[SafetyFinding]:
        if not self._policy.review_package_install:
            return []
        findings: List[SafetyFinding] = []
        for pattern, rule_id in PYTHON_INSTALL_PATTERNS:
            if pattern.search(line):
                findings.append(
                    SafetyFinding(
                        rule_id=rule_id,
                        rule_name="Dependency Installation",
                        risk_type=RiskType.DEPENDENCY_INSTALL,
                        risk_level=RiskLevel.MEDIUM,
                        evidence=sanitize_text(line, self._policy.secret_patterns),
                        line=line_num,
                        recommendation="Dependency installation modifies the runtime. Review required.",
                    ))
                return findings
        return findings

    def _check_resource_abuse(self, line: str, line_num: int) -> List[SafetyFinding]:
        findings: List[SafetyFinding] = []
        for pattern, rule_id, risk in BASH_RESOURCE_PATTERNS:
            match = pattern.search(line)
            if match:
                level = RiskLevel(risk)
                # Special handling: only flag long sleeps exceeding policy timeout
                if rule_id == "R005_LONG_RUNNING_SLEEP":
                    try:
                        sleep_sec = int(match.group(1))
                        if sleep_sec <= self._policy.max_timeout_seconds:
                            continue
                    except ValueError:
                        pass
                findings.append(
                    SafetyFinding(
                        rule_id=rule_id,
                        rule_name="Resource Abuse",
                        risk_type=RiskType.RESOURCE_ABUSE,
                        risk_level=level,
                        evidence=sanitize_text(line, self._policy.secret_patterns),
                        line=line_num,
                        recommendation="Review resource usage pattern.",
                    ))
        return findings

    def _check_secret_exfiltration(self, line: str, line_num: int) -> List[SafetyFinding]:
        findings: List[SafetyFinding] = []
        for pattern, rule_id, risk in BASH_SECRET_PATTERNS:
            if pattern.search(line):
                findings.append(
                    SafetyFinding(
                        rule_id=rule_id,
                        rule_name="Secret Exfiltration",
                        risk_type=RiskType.SECRET_EXFILTRATION,
                        risk_level=RiskLevel(risk),
                        evidence=sanitize_text(line, self._policy.secret_patterns),
                        line=line_num,
                        recommendation="Sensitive information may be leaked. Use environment variables securely.",
                    ))
        return findings

    @staticmethod
    def _strip_comments_and_quotes(text: str) -> str:
        """Remove comment lines and quoted content to reduce false positives."""
        lines = []
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            cleaned = re.sub(r"'[^']*'", "''", stripped)
            cleaned = re.sub(r'"[^"]*"', '""', cleaned)
            lines.append(cleaned)
        return "\n".join(lines)

    def _check_command_policy(self, script: str) -> List[SafetyFinding]:
        findings: List[SafetyFinding] = []

        _SHELL_KEYWORDS = {
            "for",
            "if",
            "while",
            "case",
            "then",
            "do",
            "done",
            "fi",
            "esac",
            "else",
            "elif",
            "in",
            "function",
        }

        # Check each line individually (multi-line scripts can have
        # dangerous commands on non-first lines)
        for line_num, raw_line in enumerate(script.split("\n"), 1):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                lexer = shlex.shlex(stripped, posix=True, punctuation_chars="|;&")
                lexer.whitespace_split = True
                tokens = list(lexer)
            except Exception:
                tokens = stripped.split()
            if not tokens:
                continue

            base_cmd = tokens[0]
            if base_cmd in _SHELL_KEYWORDS:
                continue

            # Denied commands via token-prefix match
            found_denied = False
            for denied in self._policy.denied_commands:
                try:
                    denied_tokens = shlex.split(denied)
                except Exception:
                    denied_tokens = denied.split()
                if tokens[:len(denied_tokens)] == denied_tokens:
                    findings.append(
                        SafetyFinding(
                            rule_id="R003_SYSTEM_COMMAND",
                            rule_name="Denied Command",
                            risk_type=RiskType.SYSTEM_COMMAND,
                            risk_level=RiskLevel.CRITICAL,
                            evidence=sanitize_text(stripped, self._policy.secret_patterns),
                            line=line_num,
                            recommendation=f"Command '{denied}' is denied by safety policy.",
                        ))
                    found_denied = True
                    break  # Break inner loop; continue scanning remaining lines
            if found_denied:
                continue

            # Review commands via token-prefix match
            found_review = False
            for review_cmd in self._policy.review_commands:
                try:
                    review_tokens = shlex.split(review_cmd)
                except Exception:
                    review_tokens = review_cmd.split()
                if tokens[:len(review_tokens)] == review_tokens:
                    findings.append(
                        SafetyFinding(
                            rule_id="R003_SYSTEM_COMMAND",
                            rule_name="Command Requires Review",
                            risk_type=RiskType.SYSTEM_COMMAND,
                            risk_level=RiskLevel.MEDIUM,
                            evidence=sanitize_text(stripped, self._policy.secret_patterns),
                            line=line_num,
                            recommendation=f"Command '{review_cmd}' requires human review per safety policy.",
                        ))
                    found_review = True
                    break  # Break inner loop; continue scanning remaining lines
            if found_review:
                continue

            # Allowed commands check for this line
            if (self._policy.allowed_commands and base_cmd not in _SHELL_KEYWORDS
                    and base_cmd not in self._policy.allowed_commands):
                findings.append(
                    SafetyFinding(
                        rule_id="R003_SYSTEM_COMMAND",
                        rule_name="Command Not Allowed",
                        risk_type=RiskType.SYSTEM_COMMAND,
                        risk_level=RiskLevel.MEDIUM,
                        evidence=sanitize_text(stripped, self._policy.secret_patterns),
                        line=line_num,
                        recommendation=f"Command '{base_cmd}' is not in the allowed commands list.",
                    ))

        # Check for shell pipelines requiring review (whole-script check)
        if self._policy.review_shell_pipelines:
            cleaned = self._strip_comments_and_quotes(script)
            # Strip shell case-block terminators (;;) and fallthrough
            # markers (;;&, ;&) to prevent false pipeline detection on
            # legitimate control structures like case...esac blocks.
            cleaned = cleaned.replace(";;&", "").replace(";;", "").replace(";&", "")
            if "|" in cleaned or ";" in cleaned:
                findings.append(
                    SafetyFinding(
                        rule_id="R003_SHELL_PIPE_EXECUTION",
                        rule_name="Shell Pipeline",
                        risk_type=RiskType.SYSTEM_COMMAND,
                        risk_level=RiskLevel.MEDIUM,
                        evidence=sanitize_text(script.strip(), self._policy.secret_patterns),
                        recommendation="Shell pipelines require human review.",
                    ))

        return findings
