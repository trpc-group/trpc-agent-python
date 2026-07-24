# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Policy configuration loading for the Tool Script Safety Guard."""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

import yaml

from ._types import Decision
from ._types import RiskLevel
from ._types import risk_order


@dataclass
class PolicyConfig:
    """In-memory representation of the safety policy.

    Changing the YAML is sufficient to change behavior — no code edits required.
    """

    whitelisted_domains: list[str] = field(default_factory=list)
    forbidden_paths: list[str] = field(default_factory=list)
    allowed_commands: list[str] = field(default_factory=list)
    max_timeout_seconds: int = 300
    # Runtime executor limits (documented placeholders). Static scanning cannot
    # enforce byte caps; keep fields for policy compatibility / future runtime.
    max_output_bytes: int = 10 * 1024 * 1024
    max_file_write_bytes: int = 100 * 1024 * 1024
    deny_risk_level: RiskLevel = RiskLevel.HIGH
    review_risk_level: RiskLevel = RiskLevel.MEDIUM
    secret_patterns: list[str] = field(default_factory=list)
    disabled_rules: list[str] = field(default_factory=list)
    # When True, bash commands not present in allowed_commands are flagged HIGH.
    strict_command_allowlist: bool = False
    # When True, ToolSafetyFilter blocks NEEDS_HUMAN_REVIEW the same as DENY.
    block_on_review: bool = False
    # When True, unknown YAML keys raise ValueError.
    strict_policy: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    _KNOWN_KEYS = frozenset({
        "whitelisted_domains",
        "forbidden_paths",
        "allowed_commands",
        "max_timeout_seconds",
        "max_output_bytes",
        "max_file_write_bytes",
        "deny_risk_level",
        "review_risk_level",
        "secret_patterns",
        "disabled_rules",
        "strict_command_allowlist",
        "block_on_review",
        "strict_policy",
        "extra",
    })

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PolicyConfig":
        """Build a PolicyConfig from a parsed YAML mapping."""
        data = data or {}
        if not isinstance(data, dict):
            raise ValueError("policy must be a mapping")

        strict = bool(data.get("strict_policy", False))
        if strict:
            unknown = set(data.keys()) - cls._KNOWN_KEYS
            if unknown:
                raise ValueError(f"unknown policy keys: {sorted(unknown)}")
        # max_output_bytes / max_file_write_bytes are runtime placeholders
        # (static scanning cannot enforce byte caps). Validate as non-negative
        # ints regardless of strict mode so malformed YAML fails fast.
        for key in ("max_timeout_seconds", "max_output_bytes", "max_file_write_bytes"):
            if key in data and data[key] is not None and int(data[key]) < 0:
                raise ValueError(f"{key} must be non-negative")

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
            strict_command_allowlist=bool(data.get("strict_command_allowlist", False)),
            block_on_review=bool(data.get("block_on_review", False)),
            strict_policy=strict,
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

    @classmethod
    def from_env(cls, env_var: str = "TOOL_SAFETY_POLICY_PATH") -> "PolicyConfig":
        """Load policy from ``TOOL_SAFETY_POLICY_PATH`` or return defaults."""
        import os

        path = (os.environ.get(env_var) or "").strip()
        if not path:
            return cls()
        p = Path(path)
        if not p.is_file():
            raise ValueError(f"{env_var} points to missing policy file: {path}")
        return cls.from_yaml(p)

    @classmethod
    def default(cls) -> "PolicyConfig":
        """Return a default fail-closed policy (no network allow-list)."""
        return cls()

    def decision_for(self, max_level: RiskLevel) -> Decision:
        """Map an aggregate risk level to a final decision per policy."""
        if risk_order(max_level) >= risk_order(self.deny_risk_level):
            return Decision.DENY
        if risk_order(max_level) >= risk_order(self.review_risk_level):
            return Decision.NEEDS_HUMAN_REVIEW
        return Decision.ALLOW

    def is_domain_allowed(self, host: str) -> bool:
        """True when *host* matches any whitelisted suffix (empty list => none allowed)."""
        if not self.whitelisted_domains:
            return False
        host = (host or "").lower().strip()
        # Reject spoofed suffix hosts such as evil-api.github.com.attacker.tld
        # by requiring exact match or a proper DNS label boundary.
        for domain in self.whitelisted_domains:
            d = domain.lower().strip()
            if not d:
                continue
            if host == d or host.endswith("." + d):
                return True
        return False

    def is_command_allowed(self, cmd: str) -> bool:
        """True when *cmd* is present in allowed_commands (case-sensitive basename)."""
        if not self.allowed_commands:
            return False
        base = (cmd or "").split("/")[-1].split("\\")[-1]
        return base in self.allowed_commands


def _parse_risk_level(value: Any, default: RiskLevel) -> RiskLevel:
    if value is None:
        return default
    if isinstance(value, RiskLevel):
        return value
    try:
        return RiskLevel(str(value).lower())
    except ValueError as ex:
        # Fail fast on typos like "superhigh": silently falling back to the
        # default would let users believe a stricter policy is active when it
        # is not. List valid values so the error is actionable.
        valid = [r.value for r in RiskLevel]
        raise ValueError(f"invalid risk level {value!r}; expected one of {valid}") from ex
