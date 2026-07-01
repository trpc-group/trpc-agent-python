# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Policy loading for tool script safety checks."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel
from pydantic import Field

from .models import SafetyDecision
from .models import SafetySeverity

SAFETY_POLICY_ENV = "TRPC_AGENT_TOOL_SAFETY_POLICY"
DEFAULT_POLICY_FILE = Path(__file__).with_name("tool_safety_policy.yaml")


class SafetyPolicy(BaseModel):
    """Configuration used by the safety checker."""

    enabled: bool = Field(default=True, description="Whether safety checks are enabled.")
    default_decision: SafetyDecision = Field(
        default=SafetyDecision.ALLOW,
        description="Decision used when findings do not match deny/review thresholds.",
    )
    deny_severities: List[SafetySeverity] = Field(
        default_factory=lambda: [SafetySeverity.HIGH, SafetySeverity.CRITICAL],
        description="Finding severities that produce a deny decision.",
    )
    review_severities: List[SafetySeverity] = Field(
        default_factory=lambda: [SafetySeverity.MEDIUM],
        description="Finding severities that require human review.",
    )
    enabled_rules: List[str] = Field(
        default_factory=list,
        description="If set, only these rule ids are enabled.",
    )
    disabled_rules: List[str] = Field(default_factory=list, description="Rule ids to disable.")
    allowed_domains: List[str] = Field(default_factory=list, description="Domains allowed for network access rules.")
    blocked_paths: List[str] = Field(default_factory=list, description="Paths blocked for filesystem rules.")
    allowed_commands: List[str] = Field(default_factory=list, description="Commands allowed for command rules.")
    max_timeout: Optional[float] = Field(default=None, description="Maximum allowed timeout in seconds.")
    max_output_size: Optional[int] = Field(default=None, description="Maximum allowed output size in bytes.")
    severity: Dict[str, Any] = Field(default_factory=dict, description="Default and per-rule severities.")
    rule_configs: Dict[str, Dict[str, Any]] = Field(default_factory=dict, description="Per-rule configuration.")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Extension metadata.")

    def is_rule_enabled(self, rule_id: str) -> bool:
        """Return whether a rule should run under this policy."""
        if not self.enabled:
            return False
        if rule_id in self.disabled_rules:
            return False
        if self.enabled_rules and rule_id not in self.enabled_rules:
            return False
        return True

    def rule_config(self, rule_id: str) -> Dict[str, Any]:
        """Return the configuration for one rule."""
        return self.rule_configs.get(rule_id, {})

    def rule_value(self, rule_id: str, key: str, default: Any = None) -> Any:
        """Return a per-rule value, falling back to the global policy value."""
        config = self.rule_config(rule_id)
        if key in config:
            return config[key]
        return getattr(self, key, default)

    def rule_list(self, rule_id: str, key: str) -> List[str]:
        """Return a string list from per-rule or global policy config."""
        return _as_string_list(self.rule_value(rule_id, key, []))

    def rule_severity(self, rule_id: str, default: SafetySeverity = SafetySeverity.MEDIUM) -> SafetySeverity:
        """Return the configured severity for one rule."""
        config = self.rule_config(rule_id)
        value = config.get("severity")
        if value is None:
            value = self.severity.get(rule_id) or self.severity.get("default")
        return _to_severity(value, default)

    def is_command_allowed(self, rule_id: str, command: str) -> bool:
        """Return whether a command is explicitly allowed for a rule."""
        command = _normalize_command(command)
        if not command:
            return False
        allowed_commands = self.rule_list(rule_id, "allowed_commands")
        return any(_command_matches(command, allowed) for allowed in allowed_commands)

    def is_domain_allowed(self, rule_id: str, domain: str) -> bool:
        """Return whether a domain is explicitly allowed for a rule."""
        domain = _normalize_domain(domain)
        if not domain:
            return False
        allowed_domains = self.rule_list(rule_id, "allowed_domains")
        return any(_domain_matches(domain, allowed) for allowed in allowed_domains)

    def is_path_blocked(self, rule_id: str, path: str) -> bool:
        """Return whether a path is blocked for a rule."""
        if not path:
            return False
        blocked_paths = self.rule_list(rule_id, "blocked_paths")
        return any(_path_matches(path, pattern) for pattern in blocked_paths)

    def rule_max_timeout(self, rule_id: str, default: float) -> float:
        """Return the configured max timeout for one rule."""
        value = self.rule_value(rule_id, "max_timeout", default)
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def rule_max_output_size(self, rule_id: str, default: Optional[int] = None) -> Optional[int]:
        """Return the configured max output size for one rule."""
        value = self.rule_value(rule_id, "max_output_size", default)
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return default


class PolicyLoader:
    """Load :class:`SafetyPolicy` from dictionaries, files, or environment."""

    @staticmethod
    def from_dict(data: Optional[Dict[str, Any]]) -> SafetyPolicy:
        """Create a policy from a dictionary."""
        return SafetyPolicy.model_validate(_normalize_policy_data(data or {}))

    @staticmethod
    def from_default_file() -> SafetyPolicy:
        """Load the bundled default policy file."""
        if not DEFAULT_POLICY_FILE.exists():
            return SafetyPolicy()
        return PolicyLoader.from_file(DEFAULT_POLICY_FILE)

    @staticmethod
    def from_file(path: str | Path) -> SafetyPolicy:
        """Load a policy from a JSON or YAML file."""
        policy_path = Path(path)
        with policy_path.open("r", encoding="utf-8") as fp:
            if policy_path.suffix.lower() in {".yaml", ".yml"}:
                data = yaml.safe_load(fp) or {}
            else:
                data = json.load(fp)
        return PolicyLoader.from_dict(data)

    @staticmethod
    def from_env(env_var: str = SAFETY_POLICY_ENV) -> SafetyPolicy:
        """Load a policy from an environment variable pointing to a policy file."""
        path = os.environ.get(env_var, "").strip()
        if not path:
            return PolicyLoader.from_default_file()
        return PolicyLoader.from_file(path)


def _normalize_policy_data(data: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(data)
    for key in ("deny_severities", "review_severities"):
        if key in normalized:
            normalized[key] = [_to_severity(value) for value in _as_list(normalized[key])]
    return normalized


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    return [value]


def _as_string_list(value: Any) -> List[str]:
    return [str(item) for item in _as_list(value) if str(item).strip()]


def _to_severity(value: Any, default: SafetySeverity = SafetySeverity.MEDIUM) -> SafetySeverity:
    if isinstance(value, SafetySeverity):
        return value
    if isinstance(value, str):
        try:
            return SafetySeverity(value.lower())
        except ValueError:
            return default
    return default


def _normalize_command(command: str) -> str:
    return command.strip().rsplit("/", 1)[-1]


def _command_matches(command: str, allowed: str) -> bool:
    allowed = allowed.strip()
    if allowed == "*":
        return True
    return command == _normalize_command(allowed) or command == allowed


def _normalize_domain(domain: str) -> str:
    value = domain.strip().lower()
    if "://" in value:
        value = urlparse(value).hostname or ""
    return value.strip(".")


def _domain_matches(domain: str, allowed: str) -> bool:
    allowed_domain = _normalize_domain(allowed)
    if allowed_domain == "*":
        return True
    return domain == allowed_domain or domain.endswith(f".{allowed_domain}")


def _path_matches(path: str, pattern: str) -> bool:
    normalized_path = _normalize_path(path)
    normalized_pattern = _normalize_path(pattern)
    if normalized_pattern == "*":
        return True
    if not normalized_pattern:
        return False
    if normalized_pattern in {".env", ".ssh"}:
        return normalized_pattern in _path_parts(normalized_path)
    return normalized_path == normalized_pattern or normalized_path.startswith(f"{normalized_pattern.rstrip('/')}/")


def _normalize_path(path: str) -> str:
    return path.strip().replace("\\", "/").rstrip("/")


def _path_parts(path: str) -> list[str]:
    return [part for part in path.split("/") if part]
