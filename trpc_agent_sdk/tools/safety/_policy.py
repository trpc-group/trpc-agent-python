# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Policy file loader for the Tool Script Safety Guard system.

This module provides the SafetyPolicy class which loads and parses the
``tool_safety_policy.yaml`` configuration file. It supports:

1. Global settings: max timeout, max output size, default decision.
2. Whitelists: allowed domains, allowed commands, forbidden paths.
3. Rule definitions: per-risk-category patterns, decision, risk level.

The policy file can be modified at runtime without restarting the
application — call ``reload()`` to pick up changes.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from dataclasses import field
from typing import Optional

import yaml

from trpc_agent_sdk.log import logger

from ._types import RiskLevel
from ._types import SafetyDecision

# ── Policy Data Classes ───────────────────────────────────────────────────


@dataclass
class RuleConfig:
    """Configuration for a single risk detection rule.

    Attributes:
        enabled: Whether this rule is active.
        decision: The safety decision to return when the rule matches.
        risk_level: Severity level of matches from this rule.
        patterns: List of string patterns to detect (regex or literal).
        check_domains: Whether to check domains against the whitelist.
        trigger_commands: Commands that trigger domain checking.
    """

    enabled: bool = True
    decision: SafetyDecision = SafetyDecision.DENY
    risk_level: RiskLevel = RiskLevel.HIGH
    patterns: list[str] = field(default_factory=list)
    check_domains: bool = False
    trigger_commands: list[str] = field(default_factory=list)


@dataclass
class SafetyPolicy:
    """Loaded and parsed safety policy configuration.

    This is the primary API for accessing policy configuration.
    Instantiate via :meth:`from_file` or :meth:`from_dict`.

    Attributes:
        max_timeout_seconds: Maximum allowed execution time.
        max_output_size_bytes: Maximum allowed output size.
        default_decision: Fallback decision when no rule matches.
        allowed_domains: Domain whitelist (network egress check).
        allowed_commands: Command whitelist (bash).
        forbidden_paths: Path patterns that are forbidden to access.
        rules: Dictionary mapping rule name (str) to RuleConfig.
        policy_version: Version identifier of the loaded policy.
    """

    max_timeout_seconds: int = 300
    max_output_size_bytes: int = 10 * 1024 * 1024  # 10 MB
    default_decision: SafetyDecision = SafetyDecision.NEEDS_HUMAN_REVIEW
    allowed_domains: list[str] = field(default_factory=list)
    allowed_commands: list[str] = field(default_factory=list)
    forbidden_paths: list[str] = field(default_factory=list)
    rules: dict[str, RuleConfig] = field(default_factory=dict)
    policy_version: str = ""

    _file_path: str = ""

    # ── Factory Methods ───────────────────────────────────────────────

    @classmethod
    def from_file(cls, file_path: str) -> SafetyPolicy:
        """Load policy from a YAML file.

        Args:
            file_path: Path to the ``tool_safety_policy.yaml`` file.

        Returns:
            A fully populated SafetyPolicy instance.

        Raises:
            FileNotFoundError: If the file does not exist.
            yaml.YAMLError: If the file contains invalid YAML.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Safety policy file not found: {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        policy = cls.from_dict(raw or {})
        policy._file_path = file_path
        return policy

    @classmethod
    def from_dict(cls, raw: dict) -> SafetyPolicy:
        """Parse policy from a dictionary (useful for testing).

        Args:
            raw: Dictionary parsed from YAML.

        Returns:
            A fully populated SafetyPolicy instance.
        """
        policy = cls()

        # ── Global settings ──
        global_settings = raw.get("global", {})
        if global_settings:
            policy.max_timeout_seconds = int(global_settings.get("max_timeout_seconds", 300))
            policy.max_output_size_bytes = int(global_settings.get("max_output_size_bytes", 10 * 1024 * 1024))
            policy.default_decision = _parse_decision(global_settings.get("default_decision", "needs_human_review"))

        # ── Whitelists ──
        policy.allowed_domains = raw.get("allowed_domains", [])
        policy.allowed_commands = raw.get("allowed_commands", [])
        policy.forbidden_paths = raw.get("forbidden_paths", [])

        # ── Rules ──
        rules_raw = raw.get("rules", {})
        for rule_name, rule_cfg in rules_raw.items():
            if not isinstance(rule_cfg, dict):
                continue
            policy.rules[rule_name] = RuleConfig(
                enabled=rule_cfg.get("enabled", True),
                decision=_parse_decision(rule_cfg.get("decision", "deny")),
                risk_level=_parse_risk_level(rule_cfg.get("risk_level", "high")),
                patterns=rule_cfg.get("patterns", []),
                check_domains=rule_cfg.get("check_domains", False),
                trigger_commands=rule_cfg.get("trigger_commands", []),
            )

        policy.policy_version = raw.get("policy_version", "")

        return policy

    # ── Public Methods ────────────────────────────────────────────────

    def reload(self) -> None:
        """Reload policy from the original file path.

        This method allows hot-reloading the policy configuration
        without restarting the application.

        Raises:
            RuntimeError: If the policy was not loaded from a file.
            FileNotFoundError: If the file no longer exists.
        """
        if not self._file_path:
            raise RuntimeError("Cannot reload: policy was not loaded from a file")
        new_policy = self.from_file(self._file_path)
        self.__dict__.update(new_policy.__dict__)

    def is_domain_allowed(self, domain: str) -> bool:
        """Check if a domain is in the allowed domains whitelist.

        Args:
            domain: The domain to check (e.g. ``api.openai.com``).

        Returns:
            True if the domain is allowed, False otherwise.
        """
        if not self.allowed_domains:
            return False
        return any(domain == d or domain.endswith(f".{d}") for d in self.allowed_domains)

    def is_command_allowed(self, command: str) -> bool:
        """Check if a command is in the allowed commands whitelist.

        Args:
            command: The base command to check (e.g. ``ls``).

        Returns:
            True if the command is allowed, False otherwise.
        """
        if not self.allowed_commands:
            return False
        return command in self.allowed_commands

    def is_path_forbidden(self, path: str) -> bool:
        """Check if a path matches any forbidden path pattern.

        Args:
            path: The file path to check.

        Returns:
            True if the path is forbidden, False otherwise.
        """
        for forbidden in self.forbidden_paths:
            pattern = _glob_to_regex(forbidden)
            if re.search(pattern, path):
                return True
        return False

    def get_rule(self, rule_name: str) -> Optional[RuleConfig]:
        """Get a rule configuration by name.

        Args:
            rule_name: The rule name (e.g. ``dangerous_file_operations``).

        Returns:
            The RuleConfig if found, None otherwise.
        """
        return self.rules.get(rule_name)


# ── Internal Helpers ──────────────────────────────────────────────────────


def _parse_decision(value: str) -> SafetyDecision:
    """Parse a decision string into a SafetyDecision enum."""
    mapping = {
        "allow": SafetyDecision.ALLOW,
        "deny": SafetyDecision.DENY,
        "needs_human_review": SafetyDecision.NEEDS_HUMAN_REVIEW,
    }
    return mapping.get(value.lower(), SafetyDecision.NEEDS_HUMAN_REVIEW)


def _parse_risk_level(value: str) -> RiskLevel:
    """Parse a risk level string into a RiskLevel enum."""
    mapping = {
        "low": RiskLevel.LOW,
        "medium": RiskLevel.MEDIUM,
        "high": RiskLevel.HIGH,
        "critical": RiskLevel.CRITICAL,
    }
    return mapping.get(value.lower(), RiskLevel.HIGH)


def _glob_to_regex(pattern: str) -> str:
    """Convert a simple glob pattern to a regex pattern.

    Supports ``*`` (match any characters except slash) and ``**``
    (match any characters including slash).

    Args:
        pattern: Glob pattern (e.g. ``~/.ssh/*``, ``/etc/**``).

    Returns:
        A compiled regex pattern string.
    """
    # Escape regex special characters except *
    regex = ""
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            # Check for **
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                regex += ".*"
                i += 2
            else:
                regex += "[^/]*"
                i += 1
        elif c in ".^$+?{}[]\\|()":
            regex += "\\" + c
            i += 1
        else:
            regex += c
            i += 1
    return regex
