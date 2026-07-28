# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Configuration models and YAML loading for tool safety policies."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
import yaml

from ._models import SafetyDecision

_BUILT_IN_RULE_IDS = {
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
}


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
        ".env",
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

    model_config = ConfigDict(extra="forbid")

    max_script_size_bytes: int = Field(default=1_048_576, gt=0)
    max_script_lines: int = Field(default=10_000, gt=0)
    max_timeout_seconds: float = Field(default=30, gt=0)
    max_output_size_bytes: int = Field(default=1_048_576, gt=0)
    max_concurrency: int = Field(default=32, gt=0)
    max_sleep_seconds: float = Field(default=30, gt=0)
    max_static_write_size_bytes: int = Field(default=104_857_600, gt=0)


class RuleOverride(BaseModel):
    """User-configurable enablement and action for one built-in rule."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    action: SafetyDecision | None = None


class SafetyPolicy(BaseModel):
    """Validated policy consumed by :class:`SafetyScanner`."""

    model_config = ConfigDict(extra="forbid")

    version: str = "1"
    network: NetworkPolicy = Field(default_factory=NetworkPolicy)
    commands: CommandsPolicy = Field(default_factory=CommandsPolicy)
    paths: PathsPolicy = Field(default_factory=PathsPolicy)
    limits: LimitsPolicy = Field(default_factory=LimitsPolicy)
    rule_overrides: dict[str, RuleOverride] = Field(default_factory=dict)

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        """Require a non-empty policy version for reports and audit events."""

        version = value.strip()
        if not version:
            raise ValueError("policy version cannot be blank")
        return version

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
            raw = {}
        if not isinstance(raw, dict):
            raise ValueError(f"safety policy {policy_path} must contain a YAML mapping")
        return cls.model_validate(raw)
