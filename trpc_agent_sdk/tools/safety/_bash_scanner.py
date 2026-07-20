# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Bash script scanner for the Tool Script Safety Guard system.

This module provides the BashScanner class which performs static analysis
on bash/shell scripts by matching lines against configured risk patterns
from the safety policy.
"""

from __future__ import annotations

import re
from typing import Optional

from trpc_agent_sdk.log import logger

from ._policy import SafetyPolicy
from ._types import RiskCategory
from ._types import RiskLevel
from ._types import RuleMatch
from ._types import ScanInput


class BashScanner:
    """Scanner for bash / shell scripts.

    Analyzes script content line by line, matching against regex patterns
    defined in the SafetyPolicy. Supports all seven risk categories.
    """

    # ── Domain extraction patterns ──
    _DOMAIN_RE = re.compile(r"(?:https?://|ftp://)?([a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
                            r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+(?::\d+)?)")

    # ── Sensitive data patterns (for leak detection) ──
    _SENSITIVE_PATTERNS: list[tuple[str, str]] = [
        ("API_KEY", r'["\']?API_KEY["\']?\s*[:=]\s*["\']'),
        ("api_key", r'["\']?api_key["\']?\s*[:=]\s*["\']'),
        ("API_SECRET", r'["\']?API_SECRET["\']?\s*[:=]\s*["\']'),
        ("access_token", r'["\']?access_token["\']?\s*[:=]\s*["\']'),
        ("secret_key", r'["\']?secret_key["\']?\s*[:=]\s*["\']'),
        ("password", r'["\']?password["\']?\s*[:=]\s*["\']'),
        ("Authorization Bearer", r'Authorization:\s*Bearer\s+'),
        ("PRIVATE_KEY", r"-----BEGIN\s+.*PRIVATE\s+KEY-----"),
    ]

    def __init__(self, policy: SafetyPolicy) -> None:
        """Initialize the scanner with a safety policy.

        Args:
            policy: The loaded SafetyPolicy instance.
        """
        self._policy = policy

    def scan(self, scan_input: ScanInput) -> list[RuleMatch]:
        """Scan a bash script for security risks.

        Args:
            scan_input: The script content and context to scan.

        Returns:
            A list of RuleMatch objects for each detected risk.
        """
        matches: list[RuleMatch] = []
        lines = scan_input.script_content.split("\n")

        for line_no, line in enumerate(lines, start=1):
            line_stripped = line.strip()

            # Skip empty lines and comments
            if not line_stripped or line_stripped.startswith("#"):
                continue

            line_matches = self._scan_line(line_stripped, line_no, scan_input)
            matches.extend(line_matches)

        return matches

    def _scan_line(
        self,
        line: str,
        line_no: int,
        scan_input: ScanInput,
    ) -> list[RuleMatch]:
        """Scan a single line of a bash script.

        Args:
            line: The trimmed line content.
            line_no: The 1-based line number.
            scan_input: Original scan input for context.

        Returns:
            List of RuleMatch objects for this line.
        """
        matches: list[RuleMatch] = []

        # Check each configured rule
        for rule_name, rule_cfg in self._policy.rules.items():
            if not rule_cfg.enabled:
                continue

            # Check regex patterns
            for pattern in rule_cfg.patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    # Determine risk category from rule name
                    category = self._rule_name_to_category(rule_name)
                    matches.append(
                        RuleMatch(
                            rule_id=self._rule_name_to_id(rule_name),
                            risk_category=category,
                            risk_level=rule_cfg.risk_level,
                            evidence=line[:200],
                            line_number=line_no,
                            recommendation=self._get_recommendation(category),
                            masked=False,
                        ))
                    break  # One match per rule per line

            # Check domain whitelist for network egress rules
            if rule_cfg.check_domains and rule_name == "network_egress":
                domain_match = self._check_network_egress(line, scan_input)
                if domain_match:
                    matches.append(domain_match)

        # Check sensitive info leak separately (more granular)
        if self._policy.rules.get("sensitive_info_leak", None) and \
           self._policy.rules["sensitive_info_leak"].enabled:
            leak_match = self._check_sensitive_leak(line, line_no)
            if leak_match:
                matches.append(leak_match)

        return matches

    def _check_network_egress(
        self,
        line: str,
        scan_input: ScanInput,
    ) -> Optional[RuleMatch]:
        """Check if a line attempts to connect to a non-whitelisted domain.

        Args:
            line: The line to check.
            scan_input: Original scan input for context.

        Returns:
            A RuleMatch if a non-whitelisted domain is found, None otherwise.
        """
        domains = self._DOMAIN_RE.findall(line)
        for domain in domains:
            if not self._policy.is_domain_allowed(domain):
                cfg = self._policy.rules.get("network_egress")
                return RuleMatch(
                    rule_id="R003",
                    risk_category=RiskCategory.NETWORK_EGRESS,
                    risk_level=cfg.risk_level if cfg else RiskLevel.HIGH,
                    evidence=f"Non-whitelisted domain: {domain}",
                    line_number=0,
                    recommendation="Remove or replace with a whitelisted domain, "
                    "or add the domain to allowed_domains in the policy",
                    masked=False,
                )
        return None

    def _check_sensitive_leak(
        self,
        line: str,
        line_no: int,
    ) -> Optional[RuleMatch]:
        """Check if a line leaks sensitive information.

        Args:
            line: The line to check.
            line_no: The line number.

        Returns:
            A RuleMatch if sensitive data leak is detected, None otherwise.
        """
        for name, pattern in self._SENSITIVE_PATTERNS:
            if re.search(pattern, line):
                cfg = self._policy.rules.get("sensitive_info_leak")
                return RuleMatch(
                    rule_id="R007",
                    risk_category=RiskCategory.SENSITIVE_INFO_LEAK,
                    risk_level=cfg.risk_level if cfg else RiskLevel.CRITICAL,
                    evidence=f"Potential sensitive data leak: {name}",
                    line_number=line_no,
                    recommendation="Avoid writing secrets to files, logs, or network requests. "
                    "Use environment variables or a secrets manager instead.",
                    masked=True,
                )
        return None

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _rule_name_to_id(rule_name: str) -> str:
        """Map a rule name to its rule ID."""
        mapping = {
            "dangerous_file_operations": "R001",
            "sensitive_file_read": "R002",
            "network_egress": "R003",
            "process_execution": "R004",
            "dependency_installation": "R005",
            "resource_abuse": "R006",
            "sensitive_info_leak": "R007",
        }
        return mapping.get(rule_name, "R000")

    @staticmethod
    def _rule_name_to_category(rule_name: str) -> RiskCategory:
        """Map a rule name to its RiskCategory."""
        mapping = {
            "dangerous_file_operations": RiskCategory.DANGEROUS_FILE_OPERATION,
            "sensitive_file_read": RiskCategory.DANGEROUS_FILE_OPERATION,
            "network_egress": RiskCategory.NETWORK_EGRESS,
            "process_execution": RiskCategory.PROCESS_EXECUTION,
            "dependency_installation": RiskCategory.DEPENDENCY_INSTALLATION,
            "resource_abuse": RiskCategory.RESOURCE_ABUSE,
            "sensitive_info_leak": RiskCategory.SENSITIVE_INFO_LEAK,
        }
        return mapping.get(rule_name, RiskCategory.UNKNOWN)

    @staticmethod
    def _get_recommendation(category: RiskCategory) -> str:
        """Get a default recommendation for a risk category."""
        recommendations = {
            RiskCategory.DANGEROUS_FILE_OPERATION:
            "Remove or replace this file operation. Avoid destructive commands like rm -rf.",
            RiskCategory.NETWORK_EGRESS:
            "Remove network requests to non-whitelisted domains, or add the domain to the policy.",
            RiskCategory.PROCESS_EXECUTION: "Avoid executing system commands directly. Use safe APIs instead.",
            RiskCategory.DEPENDENCY_INSTALLATION:
            "Do not install packages during execution. Pre-install all dependencies.",
            RiskCategory.RESOURCE_ABUSE: "Avoid infinite loops, long sleeps, and resource-exhaustive patterns.",
            RiskCategory.SENSITIVE_INFO_LEAK: "Do not write secrets to files, logs, or network requests.",
        }
        return recommendations.get(category, "Review this line for potential security risks.")
