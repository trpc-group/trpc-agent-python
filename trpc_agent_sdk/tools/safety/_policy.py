# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Policy configuration for the Tool Script Safety Guard.

The policy is a plain YAML file (see ``tool_safety_policy.yaml``) that
controls whitelisted domains, allowed commands, forbidden paths, resource
limits and per-rule overrides.  Changing the YAML is enough to change
behaviour — no code changes required.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Optional
from typing import Union

_logger = logging.getLogger(__name__)

import yaml

from ._models import Decision
from ._models import RiskLevel


@dataclass
class RuleOverride:
    """Per-rule configuration override loaded from the policy file.

    Attributes:
        enabled: Whether the rule is active. Defaults to True.
        risk_level: Optional override for the rule's default risk level.
        decision: Optional override for the rule's default decision.
    """

    enabled: bool = True
    risk_level: Optional[RiskLevel] = None
    decision: Optional[Decision] = None


@dataclass
class SafetyPolicy:
    """Configurable safety policy.

    All fields can be set from a YAML file via :meth:`from_yaml` or built
    programmatically via :meth:`default`.
    """

    # Network egress control
    allowed_domains: list[str] = field(default_factory=lambda: [
        "localhost",
        "127.0.0.1",
        "pypi.org",
        "files.pythonhosted.org",
    ])
    """Whitelisted domains for outbound network calls."""

    # Forbidden file paths / patterns
    forbidden_paths: list[str] = field(default_factory=lambda: [
        "~/.ssh",
        "~/.aws",
        "~/.gnupg",
        ".env",
        ".env.local",
        ".env.production",
        "id_rsa",
        "id_ed25519",
        "credentials",
        "credentials.json",
        "/etc/shadow",
        "/etc/passwd",
    ])
    """Paths that scripts must never read, write or delete."""

    # System directories that must never be recursively deleted
    protected_system_dirs: list[str] = field(default_factory=lambda: [
        "/",
        "/etc",
        "/usr",
        "/bin",
        "/sbin",
        "/var",
        "/boot",
        "/sys",
        "/proc",
        "/dev",
        "/root",
        "/home",
        "C:\\",
        "C:\\Windows",
        "C:\\Program Files",
    ])

    # Resource limits
    # NOTE: max_timeout_seconds and max_output_size_mb are consumed by the
    # filter / executor layer (timeout injection and output truncation),
    # not by individual scanner rules.
    max_timeout_seconds: int = 300
    """Maximum allowed execution timeout (enforced by the filter/executor)."""

    max_output_size_mb: int = 50
    """Maximum allowed output size in MB (enforced by the filter)."""

    max_script_lines: int = 5000
    """Hard limit on scanned script length (lines)."""

    max_sleep_seconds: int = 3600
    """Maximum allowed sleep duration; longer sleeps are flagged for review."""

    max_range_size: int = 1_000_000
    """Maximum allowed range() iteration count; larger values are flagged."""

    # Secret detection patterns (regex strings)
    secret_patterns: list[str] = field(default_factory=lambda: [
        # api_key = 'value' or api_key='value' or api_key=value
        r"(?i)(api[_-]?key)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{16,}['\"]?",
        r"(?i)(secret[_-]?key)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{16,}['\"]?",
        r"(?i)(access[_-]?token)\s*[=:]\s*['\"]?[A-Za-z0-9_\-\.]{16,}['\"]?",
        r"(?i)(password|passwd)\s*[=:]\s*['\"]?[^\s'\"]{6,}['\"]?",
        r"-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----",
        r"(?i)(aws_secret_access_key)\s*[=:]\s*['\"]?[A-Za-z0-9/+=]{40}['\"]?",
        # GitHub / OpenAI tokens with known prefixes
        r"ghp_[A-Za-z0-9]{36}",
        r"sk-[A-Za-z0-9]{20,}",
    ])
    """Regex patterns that indicate a hardcoded secret."""

    # Per-rule overrides: rule_id -> RuleOverride
    rule_overrides: dict[str, RuleOverride] = field(default_factory=dict)

    # Whether to redact sensitive values in evidence snippets
    redact_secrets_in_evidence: bool = True

    # When set, scripts above this many lines are flagged for review
    large_script_threshold: int = 1000

    # Commands that are treated as "reading file content" (used by
    # BashDangerousFileOpsRule to detect credential file access).
    credential_read_commands: list[str] = field(default_factory=lambda: [
        "cat",
        "less",
        "more",
        "head",
        "tail",
        "cp",
        "mv",
        "scp",
        "rsync",
        "base64",
        "xargs",
        "awk",
        "sed",
        "grep",
        "type",
    ])

    # Optional Bash command allow-list.  When non-empty, any Bash command
    # whose first token is not in this list is flagged for human review
    # (BASH-COMMAND-WHITELIST rule).  When empty (default) the rule is
    # disabled — no command-whitelist check is performed.
    allowed_commands: list[str] = field(default_factory=list)
    """Optional Bash command allow-list. Empty = disabled (no check)."""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SafetyPolicy":
        """Build a policy from a plain dictionary (parsed YAML).

        Raises:
            ValueError: If a field has an unexpected type (e.g. a string
                where a list is expected).
        """
        # Start from defaults so unspecified keys keep their default value.
        policy = cls()

        def _list_field(key: str) -> Optional[list]:
            """Extract a list field with type validation."""
            if key in data:
                if not isinstance(data[key], list):
                    raise ValueError(f"Policy field '{key}' must be a list, "
                                     f"got {type(data[key]).__name__}")
                return list(data[key])
            return None

        for key, attr in (
            ("allowed_domains", "allowed_domains"),
            ("forbidden_paths", "forbidden_paths"),
            ("protected_system_dirs", "protected_system_dirs"),
            ("secret_patterns", "secret_patterns"),
            ("credential_read_commands", "credential_read_commands"),
            ("allowed_commands", "allowed_commands"),
        ):
            val = _list_field(key)
            if val is not None:
                setattr(policy, attr, val)

        if "max_timeout_seconds" in data:
            policy.max_timeout_seconds = int(data["max_timeout_seconds"])
        if "max_output_size_mb" in data:
            policy.max_output_size_mb = int(data["max_output_size_mb"])
        if "max_script_lines" in data:
            policy.max_script_lines = int(data["max_script_lines"])
        if "max_sleep_seconds" in data:
            policy.max_sleep_seconds = int(data["max_sleep_seconds"])
        if "max_range_size" in data:
            policy.max_range_size = int(data["max_range_size"])
        if "redact_secrets_in_evidence" in data:
            policy.redact_secrets_in_evidence = bool(data["redact_secrets_in_evidence"])
        if "large_script_threshold" in data:
            policy.large_script_threshold = int(data["large_script_threshold"])

        # Parse per-rule overrides
        rules_data = data.get("rules", {})
        if isinstance(rules_data, dict):
            for rule_id, cfg in rules_data.items():
                if not isinstance(cfg, dict):
                    continue
                override = RuleOverride(enabled=cfg.get("enabled", True), )
                if "risk_level" in cfg:
                    try:
                        override.risk_level = RiskLevel(cfg["risk_level"])
                    except ValueError:
                        _logger.warning("Invalid risk_level '%s' for rule %s, using default", cfg["risk_level"],
                                        rule_id)
                if "decision" in cfg:
                    try:
                        override.decision = Decision(cfg["decision"])
                    except ValueError:
                        _logger.warning("Invalid decision '%s' for rule %s, using default", cfg["decision"], rule_id)
                policy.rule_overrides[rule_id] = override
        return policy

    @classmethod
    def from_yaml(cls, path: Union[str, os.PathLike]) -> "SafetyPolicy":
        """Load a policy from a YAML file.

        Args:
            path: Path to the YAML policy file.

        Returns:
            A configured :class:`SafetyPolicy`.

        Raises:
            FileNotFoundError: If the file does not exist.
            yaml.YAMLError: If the file is not valid YAML.
        """
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            raise ValueError(f"Policy file {path} must contain a YAML mapping at the top level")
        return cls.from_dict(data)

    @classmethod
    def default(cls) -> "SafetyPolicy":
        """Return a policy with sensible built-in defaults."""
        return cls()

    def is_domain_allowed(self, domain: str) -> bool:
        """Check whether *domain* is in the whitelist.

        Sub-domain matching is supported: if ``example.com`` is whitelisted,
        ``api.example.com`` is also allowed.
        """
        domain = domain.lower().strip()
        for allowed in self.allowed_domains:
            allowed = allowed.lower().strip()
            if domain == allowed or domain.endswith("." + allowed):
                return True
        return False

    def is_path_forbidden(self, path: str) -> bool:
        """Check whether *path* matches a forbidden path pattern.

        Uses path-boundary matching to avoid false positives: ``.env``
        matches ``.env`` and ``/app/.env`` but **not** ``.environment``;
        ``/etc/passwd`` matches ``/etc/passwd`` but **not**
        ``/etc/passwders``.
        """
        path_lower = path.lower().strip().replace("\\", "/")
        for forbidden in self.forbidden_paths:
            for candidate in (
                    forbidden.lower(),
                    os.path.expanduser(forbidden.lower()),
            ):
                candidate = candidate.replace("\\", "/")
                if not candidate:
                    continue
                # Exact match
                if path_lower == candidate:
                    return True
                # candidate is a suffix path component
                # (e.g. ".env" in "/app/.env")
                if path_lower.endswith("/" + candidate):
                    return True
                # candidate is a prefix directory
                # (e.g. "~/.ssh" in "~/.ssh/id_rsa")
                if path_lower.startswith(candidate + "/"):
                    return True
        return False

    def is_system_dir(self, path: str) -> bool:
        """Check whether *path* is a protected system directory."""
        path_norm = os.path.normpath(path).lower()
        for sys_dir in self.protected_system_dirs:
            if os.path.normpath(sys_dir).lower() == path_norm:
                return True
        return False

    def get_rule_override(self, rule_id: str) -> RuleOverride:
        """Return the override for *rule_id*, or a default (enabled) override."""
        return self.rule_overrides.get(rule_id, RuleOverride())

    def to_dict(self) -> dict[str, Any]:
        """Serialise the policy back to a plain dict (for debugging)."""
        return {
            "allowed_domains": list(self.allowed_domains),
            "forbidden_paths": list(self.forbidden_paths),
            "protected_system_dirs": list(self.protected_system_dirs),
            "max_timeout_seconds": self.max_timeout_seconds,
            "max_output_size_mb": self.max_output_size_mb,
            "max_script_lines": self.max_script_lines,
            "max_sleep_seconds": self.max_sleep_seconds,
            "max_range_size": self.max_range_size,
            "secret_patterns": list(self.secret_patterns),
            "redact_secrets_in_evidence": self.redact_secrets_in_evidence,
            "large_script_threshold": self.large_script_threshold,
            "credential_read_commands": list(self.credential_read_commands),
            "allowed_commands": list(self.allowed_commands),
            "rules": {
                rid: {
                    "enabled": ov.enabled,
                    "risk_level": ov.risk_level.value if ov.risk_level else None,
                    "decision": ov.decision.value if ov.decision else None,
                }
                for rid, ov in self.rule_overrides.items()
            },
        }
