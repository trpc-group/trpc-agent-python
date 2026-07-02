# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""Policy configuration loading for the Tool Script Safety Guard.

Reads ``tool_safety_policy.yaml`` into a :class:`PolicyConfig` object. The
policy drives every rule: allow-listed domains, forbidden paths, allowed
commands, thresholds, and the deny/review decision boundaries.

Changing the YAML is sufficient to change behavior — no code edits required.
"""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

import yaml

from .types import Decision
from .types import RiskLevel


@dataclass
class PolicyConfig:
    """In-memory representation of the safety policy.

    Attributes:
        whitelisted_domains: Domains network access is allowed to (suffix match).
        forbidden_paths: Path substrings/regex that must never be touched.
        allowed_commands: Bash commands permitted without further scrutiny.
        max_timeout_seconds: Hard cap on script execution timeout.
        max_output_bytes: Hard cap on captured output size.
        max_file_write_bytes: Threshold above which file writes are flagged.
        deny_risk_level: Findings at or above this level produce a DENY.
        review_risk_level: Findings at or above this level (below deny) produce REVIEW.
        secret_patterns: Regex patterns that look like leaked secrets.
        disabled_rules: Rule ids to skip entirely.
        extra: Free-form per-rule overrides keyed by rule id.
    """
    whitelisted_domains: list[str] = field(default_factory=list)
    forbidden_paths: list[str] = field(default_factory=list)
    allowed_commands: list[str] = field(default_factory=list)
    max_timeout_seconds: int = 300
    max_output_bytes: int = 10 * 1024 * 1024
    max_file_write_bytes: int = 100 * 1024 * 1024
    deny_risk_level: RiskLevel = RiskLevel.HIGH
    review_risk_level: RiskLevel = RiskLevel.MEDIUM
    secret_patterns: list[str] = field(default_factory=list)
    disabled_rules: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PolicyConfig":
        """Build a PolicyConfig from a parsed YAML mapping."""
        data = data or {}
        # Normalize risk levels from strings.
        deny_lvl = _parse_risk_level(data.get("deny_risk_level"), RiskLevel.HIGH)
        review_lvl = _parse_risk_level(data.get("review_risk_level"), RiskLevel.MEDIUM)

        return cls(
            whitelisted_domains=list(data.get("whitelisted_domains", []) or []),
            forbidden_paths=list(data.get("forbidden_paths", []) or []),
            allowed_commands=list(data.get("allowed_commands", []) or []),
            max_timeout_seconds=int(data.get("max_timeout_seconds", 300)),
            max_output_bytes=int(data.get("max_output_bytes", 10 * 1024 * 1024)),
            max_file_write_bytes=int(data.get("max_file_write_bytes", 100 * 1024 * 1024)),
            deny_risk_level=deny_lvl,
            review_risk_level=review_lvl,
            secret_patterns=list(data.get("secret_patterns", []) or []),
            disabled_rules=list(data.get("disabled_rules", []) or []),
            extra=dict(data.get("extra", {}) or {}),
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "PolicyConfig":
        """Load policy from a YAML file on disk."""
        text = Path(path).read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError(f"policy file {path} must contain a YAML mapping at top level")
        return cls.from_dict(data)

    def decision_for(self, max_level: RiskLevel) -> Decision:
        """Map an aggregate risk level to a final decision per policy."""
        order = _RISK_ORDER
        if order[max_level] >= order[self.deny_risk_level]:
            return Decision.DENY
        if order[max_level] >= order[self.review_risk_level]:
            return Decision.NEEDS_HUMAN_REVIEW
        return Decision.ALLOW

    def is_domain_allowed(self, host: str) -> bool:
        """True when *host* matches any whitelisted suffix (empty list => none allowed)."""
        if not self.whitelisted_domains:
            # No allow-list configured: deny all network egress by default.
            return False
        host = (host or "").lower().strip()
        return any(host == d or host.endswith("." + d) for d in self.whitelisted_domains)


_RISK_ORDER = {
    RiskLevel.NONE: 0,
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}


def _parse_risk_level(value: Any, default: RiskLevel) -> RiskLevel:
    if value is None:
        return default
    if isinstance(value, RiskLevel):
        return value
    try:
        return RiskLevel(str(value).lower())
    except ValueError:
        return default
