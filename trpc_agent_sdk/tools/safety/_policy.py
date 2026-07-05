# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Safety policy models and YAML loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from typing import Literal
from typing import Mapping

from pydantic import BaseModel
from pydantic import Field
import yaml

from ._types import RiskLevel
from ._types import SafetyDecision

DEFAULT_DENIED_PATHS: tuple[str, ...] = (
    "~/.ssh",
    "~/.aws",
    "~/.config/gcloud",
    ".env",
    ".env.*",
    "/etc",
    "/var/run/docker.sock",
    r"C:\Users\*\.ssh",
)

DEFAULT_SENSITIVE_ENV_KEYS: tuple[str, ...] = (
    "*KEY*",
    "*TOKEN*",
    "*SECRET*",
    "*PASSWORD*",
)


class RulePolicy(BaseModel):
    """Per-rule policy override."""

    enabled: bool = True
    decision: SafetyDecision | None = None
    risk_level: RiskLevel | None = None


class SafetyPolicy(BaseModel):
    """Configurable safety policy for tool script scanning."""

    name: str = "default"
    mode: Literal["permissive", "standard", "strict"] = "standard"
    fail_closed: bool = True
    review_blocks_execution: bool = True
    allowed_domains: list[str] = Field(default_factory=list)
    allowed_commands: list[str] = Field(default_factory=list)
    denied_commands: list[str] = Field(default_factory=list)
    denied_paths: list[str] = Field(default_factory=lambda: list(DEFAULT_DENIED_PATHS))
    sensitive_env_keys: list[str] = Field(default_factory=lambda: list(DEFAULT_SENSITIVE_ENV_KEYS))
    max_timeout_seconds: int = 300
    max_output_bytes: int = 1048576
    max_script_lines: int = 2000
    max_sleep_seconds: int = 3600
    max_evidence_chars: int = 200
    rules: dict[str, RulePolicy] = Field(default_factory=dict)


def default_safety_policy() -> SafetyPolicy:
    """Return a fresh default safety policy instance."""

    return SafetyPolicy()


def _policy_from_mapping(data: Mapping[str, Any] | None) -> SafetyPolicy:
    if not data:
        return default_safety_policy()
    return SafetyPolicy.model_validate(dict(data))


def load_safety_policy(path: str | Path) -> SafetyPolicy:
    """Load a safety policy from a YAML file.

    Empty YAML files resolve to the default policy. Invalid YAML or a non-mapping
    top-level document raises ValueError so the scanner layer can decide whether
    to fail closed.
    """

    policy_path = Path(path)
    try:
        loaded = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as ex:
        raise ValueError(f"Invalid safety policy YAML: {ex}") from ex

    if loaded is None:
        return default_safety_policy()
    if not isinstance(loaded, Mapping):
        raise ValueError("Safety policy YAML must contain a mapping at the top level.")
    return _policy_from_mapping(loaded)


def resolve_safety_policy(
    *,
    scanner: Any = None,
    policy: SafetyPolicy | None = None,
    policy_path: str | Path | None = None,
) -> SafetyPolicy:
    """Resolve policy precedence shared by filter and code executor wrapper."""

    if scanner is not None:
        scanner_policy = getattr(scanner, "policy", None)
        if isinstance(scanner_policy, SafetyPolicy):
            return scanner_policy
        if policy is not None:
            return policy
        if policy_path is not None:
            return load_safety_policy(policy_path)
        return default_safety_policy()
    if policy is not None:
        return policy
    if policy_path is not None:
        return load_safety_policy(policy_path)
    return default_safety_policy()
