# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Configuration models and YAML loading for tool safety policies."""

from __future__ import annotations

import ipaddress
import math
from pathlib import Path
import re

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
import yaml

from ._models import SafetyDecision

_BUILT_IN_RULE_IDS = {
    "ALLOW-000",
    "FILE-001",
    "FILE-002",
    "FILE-003",
    "FILE-004",
    "NET-001",
    "NET-002",
    "PROC-001",
    "PROC-002",
    "PROC-003",
    "PROC-004",
    "DEP-001",
    "RES-001",
    "RES-002",
    "RES-003",
    "SECRET-001",
    "POLICY-001",
    "POLICY-002",
    "PARSE-001",
    "POLICY-INPUT-001",
    "PROC-UNKNOWN-001",
}

POLICY_API_VERSION = "trpc-agent.io/tool-safety/v1"
POLICY_KIND = "ToolSafetyPolicy"


class NetworkPolicy(BaseModel):
    """Network destination allowlist."""

    model_config = ConfigDict(extra="forbid")

    allowed_domains: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1"], )
    allow_subdomains: bool = False

    @field_validator("allowed_domains")
    @classmethod
    def normalize_domains(cls, values: list[str]) -> list[str]:
        """Normalize, validate, and deduplicate domain names."""

        result: list[str] = []
        for value in values:
            domain = value.strip().lower().rstrip(".")
            if not domain:
                raise ValueError("allowed_domains cannot contain blank values")
            try:
                ipaddress.ip_address(domain)
            except ValueError:
                if any(character in domain for character in "/:@*"):
                    raise ValueError("allowed_domains entries must be hostnames or IP addresses")
                try:
                    ascii_domain = domain.encode("idna").decode("ascii")
                except UnicodeError as ex:
                    raise ValueError("allowed_domains contains an invalid IDNA hostname") from ex
                labels = ascii_domain.split(".")
                if (len(ascii_domain) > 253
                        or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)):
                    raise ValueError("allowed_domains contains an invalid hostname")
                domain = ascii_domain
            if domain not in result:
                result.append(domain)
        return result


class CommandsPolicy(BaseModel):
    """Commands that may run when no other rule is triggered."""

    model_config = ConfigDict(extra="forbid")

    allowed: list[str] = Field(default_factory=lambda: [
        "ls",
        "pwd",
        "cat",
        "echo",
        "head",
        "tail",
        "wc",
        "grep",
        "find",
        "sort",
        "uniq",
    ], )

    @field_validator("allowed")
    @classmethod
    def normalize_commands(cls, values: list[str]) -> list[str]:
        """Normalize and deduplicate executable names."""

        result: list[str] = []
        for value in values:
            command = value.strip()
            if not command:
                raise ValueError("allowed commands cannot contain blank values")
            if "/" in command and not Path(command).is_absolute():
                raise ValueError("path-qualified allowed commands must be absolute")
            if command not in result:
                result.append(command)
        return result


class PathsPolicy(BaseModel):
    """Sensitive paths and deletion boundaries."""

    model_config = ConfigDict(extra="forbid")

    denied: list[str] = Field(default_factory=lambda: [
        "~/.ssh",
        "~/.aws",
        "~/.kube",
        "~/.gcloud",
        "~/.netrc",
        "~/.npmrc",
        "~/.pypirc",
        "~/.curlrc",
        "~/.wgetrc",
        "~/.git-credentials",
        "~/.docker/config.json",
        ".env",
        "credentials.json",
        "secrets.json",
        "/etc/shadow",
        "/etc/sudoers",
    ], )
    workspace_only_delete: bool = True

    @field_validator("denied")
    @classmethod
    def validate_paths(cls, values: list[str]) -> list[str]:
        """Reject blank path entries and preserve user-readable forms."""

        result: list[str] = []
        for value in values:
            path = value.strip()
            if not path:
                raise ValueError("denied paths cannot contain blank values")
            if path not in result:
                result.append(path)
        return result


class LimitsPolicy(BaseModel):
    """Static and execution-context limits."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    max_script_size_bytes: int = Field(default=1_048_576, gt=0)
    max_script_lines: int = Field(default=10_000, gt=0)
    max_timeout_seconds: float = Field(default=30, gt=0)
    max_output_size_bytes: int = Field(default=1_048_576, gt=0)
    max_concurrency: int = Field(default=32, gt=0)
    max_sleep_seconds: float = Field(default=30, gt=0)
    max_static_write_size_bytes: int = Field(default=104_857_600, gt=0)

    @field_validator("max_timeout_seconds", "max_sleep_seconds")
    @classmethod
    def validate_finite_limits(cls, value: float) -> float:
        """Reject NaN and infinities even when models are built programmatically."""

        if not math.isfinite(value):
            raise ValueError("floating point limits must be finite")
        return value


class RuleOverride(BaseModel):
    """User-configurable enablement and action for one built-in rule."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    action: SafetyDecision | None = None


class SafetyPolicy(BaseModel):
    """Validated policy consumed by :class:`SafetyScanner`."""

    model_config = ConfigDict(extra="forbid")

    api_version: str = POLICY_API_VERSION
    kind: str = POLICY_KIND
    version: str = "1"
    policy_id: str = "default"
    network: NetworkPolicy = Field(default_factory=NetworkPolicy)
    commands: CommandsPolicy = Field(default_factory=CommandsPolicy)
    paths: PathsPolicy = Field(default_factory=PathsPolicy)
    limits: LimitsPolicy = Field(default_factory=LimitsPolicy)
    rule_overrides: dict[str, RuleOverride] = Field(default_factory=dict)

    @field_validator("api_version")
    @classmethod
    def validate_api_version(cls, value: str) -> str:
        """Reject policy documents for unsupported schemas."""

        if value != POLICY_API_VERSION:
            raise ValueError(f"unsupported policy api_version: {value!r}")
        return value

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str) -> str:
        """Require the policy document kind."""

        if value != POLICY_KIND:
            raise ValueError(f"policy kind must be {POLICY_KIND!r}")
        return value

    @field_validator("version", "policy_id")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        """Require non-empty policy identifiers for reports and audit events."""

        normalized = value.strip()
        if not normalized:
            raise ValueError("policy version and policy_id cannot be blank")
        return normalized

    @field_validator("rule_overrides")
    @classmethod
    def validate_rule_overrides(cls, values: dict[str, RuleOverride]) -> dict[str, RuleOverride]:
        """Reject misspelled rule ids instead of silently ignoring policy."""

        unknown = sorted(set(values) - _BUILT_IN_RULE_IDS)
        if unknown:
            raise ValueError(f"unknown rule ids: {', '.join(unknown)}")
        return values

    @classmethod
    def from_yaml(cls, path: str | Path) -> "SafetyPolicy":
        """Load and validate a policy from a YAML file."""

        policy_path = Path(path)
        try:
            raw = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as ex:
            raise ValueError(f"failed to load safety policy {policy_path}: {ex}") from ex
        if raw is None:
            raise ValueError(f"safety policy {policy_path} cannot be empty")
        if not isinstance(raw, dict):
            raise ValueError(f"safety policy {policy_path} must contain a YAML mapping")
        if "api_version" not in raw or "kind" not in raw:
            raise ValueError(f"safety policy {policy_path} must declare api_version and kind")
        return cls.model_validate(raw)
