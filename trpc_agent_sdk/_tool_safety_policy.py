# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Policy configuration for tool safety review."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from typing import Any
from typing import Iterable
from typing import Mapping

import yaml


class SafetyPolicyError(ValueError):
    """Raised when a tool safety policy file is invalid."""


_DEFAULT_ALLOWED_DOMAINS: tuple[str, ...] = ()
_DEFAULT_BLOCKED_PATHS: dict[str, tuple[str, ...]] = {
    "read_dotenv": (".env", ),
    "read_ssh": ("~/.ssh", ".ssh/"),
}
_DEFAULT_ALLOWED_COMMANDS: tuple[str, ...] = ()
_DEFAULT_MAX_TIMEOUT = 60
_DEFAULT_MAX_OUTPUT_SIZE = 10_000
_DEFAULT_RISK_LEVELS: dict[str, str] = {
    "safe_python": "none",
    "dangerous_delete": "critical",
    "read_dotenv": "high",
    "read_ssh": "critical",
    "subprocess_execution": "high",
    "os_system_execution": "high",
    "package_install": "medium",
    "npm_install": "medium",
    "apt_install": "medium",
    "infinite_loop": "high",
    "sensitive_output": "high",
    "wget_network": "high",
    "aiohttp_network": "high",
    "socket_network": "high",
    "fork_bomb": "critical",
    "bash_pipe": "medium",
    "shell_injection": "medium",
    "excessive_concurrency": "high",
    "large_file_write": "high",
    "human_review_required": "medium",
    "network_allowlist": "none",
    "network_not_allowlisted": "high",
}


@dataclass(frozen=True)
class ToolSafetyPolicy:
    """Configuration used by the tool safety reviewer."""

    allowed_domains: tuple[str, ...] = _DEFAULT_ALLOWED_DOMAINS
    blocked_paths: Mapping[str, tuple[str, ...]] | None = None
    allowed_commands: tuple[str, ...] = _DEFAULT_ALLOWED_COMMANDS
    max_timeout: int = _DEFAULT_MAX_TIMEOUT
    max_output_size: int = _DEFAULT_MAX_OUTPUT_SIZE
    risk_levels: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_domains",
                           tuple(sorted(_coerce_string_tuple(
                               self.allowed_domains,
                               "allowed_domains",
                           ))))
        blocked_paths = self.blocked_paths if self.blocked_paths is not None else _DEFAULT_BLOCKED_PATHS
        object.__setattr__(self, "blocked_paths", _coerce_blocked_paths(blocked_paths))
        object.__setattr__(self, "allowed_commands",
                           tuple(sorted(_coerce_string_tuple(
                               self.allowed_commands,
                               "allowed_commands",
                           ))))
        object.__setattr__(self, "max_timeout", _coerce_positive_int(self.max_timeout, "max_timeout"))
        object.__setattr__(self, "max_output_size", _coerce_positive_int(
            self.max_output_size,
            "max_output_size",
        ))
        risk_levels = dict(_DEFAULT_RISK_LEVELS)
        if self.risk_levels is not None:
            risk_levels.update(_coerce_string_mapping(self.risk_levels, "risk_levels"))
        object.__setattr__(self, "risk_levels", risk_levels)

    @classmethod
    def default(cls) -> "ToolSafetyPolicy":
        """Return the default safety policy."""
        return cls()

    def with_allowed_domains(self, domains: Iterable[str]) -> "ToolSafetyPolicy":
        """Return a copy with a different domain allowlist."""
        return replace(self, allowed_domains=tuple(domains))

    def risk_level_for(self, rule_id: str) -> str:
        """Return configured risk level for *rule_id*."""
        return self.risk_levels.get(rule_id, "medium")  # type: ignore[union-attr]

    def blocked_paths_for(self, rule_id: str) -> tuple[str, ...]:
        """Return configured blocked path fragments for *rule_id*."""
        return self.blocked_paths.get(rule_id, ())  # type: ignore[union-attr]


def load_tool_safety_policy(path: str | Path | None = None) -> ToolSafetyPolicy:
    """Load a tool safety policy from YAML, or return defaults."""
    if path is None:
        return ToolSafetyPolicy.default()

    policy_path = Path(path)
    if not policy_path.exists():
        return ToolSafetyPolicy.default()

    try:
        raw = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SafetyPolicyError(f"Invalid tool safety policy YAML: {exc}") from exc
    except OSError as exc:
        raise SafetyPolicyError(f"Unable to read tool safety policy {policy_path}: {exc}") from exc

    if raw is None:
        return ToolSafetyPolicy.default()
    if not isinstance(raw, dict):
        raise SafetyPolicyError("Invalid tool safety policy: top-level YAML value must be a mapping")

    allowed_keys = {
        "allowed_domains",
        "blocked_paths",
        "allowed_commands",
        "max_timeout",
        "max_output_size",
        "risk_levels",
    }
    unknown = sorted(set(raw) - allowed_keys)
    if unknown:
        raise SafetyPolicyError(f"Invalid tool safety policy: unknown field(s): {', '.join(unknown)}")

    defaults = ToolSafetyPolicy.default()
    return ToolSafetyPolicy(
        allowed_domains=raw.get("allowed_domains", defaults.allowed_domains),
        blocked_paths=raw.get("blocked_paths", defaults.blocked_paths),
        allowed_commands=raw.get("allowed_commands", defaults.allowed_commands),
        max_timeout=raw.get("max_timeout", defaults.max_timeout),
        max_output_size=raw.get("max_output_size", defaults.max_output_size),
        risk_levels=raw.get("risk_levels", defaults.risk_levels),
    )


def _coerce_string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple, set)):
        raise SafetyPolicyError(f"Invalid tool safety policy: {field_name} must be a list of strings")
    result = []
    for item in value:
        if not isinstance(item, str):
            raise SafetyPolicyError(f"Invalid tool safety policy: {field_name} must contain only strings")
        cleaned = item.strip()
        if cleaned:
            result.append(cleaned)
    return tuple(result)


def _coerce_blocked_paths(value: object) -> dict[str, tuple[str, ...]]:
    if isinstance(value, (list, tuple, set)):
        return {"read_dotenv": _coerce_string_tuple(value, "blocked_paths")}
    if not isinstance(value, Mapping):
        raise SafetyPolicyError("Invalid tool safety policy: blocked_paths must be a mapping or list of strings")

    result: dict[str, tuple[str, ...]] = {}
    for rule_id, paths in value.items():
        if not isinstance(rule_id, str):
            raise SafetyPolicyError("Invalid tool safety policy: blocked_paths keys must be strings")
        result[rule_id] = _coerce_string_tuple(paths, f"blocked_paths.{rule_id}")
    return result


def _coerce_string_mapping(value: object, field_name: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise SafetyPolicyError(f"Invalid tool safety policy: {field_name} must be a mapping")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise SafetyPolicyError(f"Invalid tool safety policy: {field_name} keys and values must be strings")
        result[key] = item
    return result


def _coerce_positive_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise SafetyPolicyError(f"Invalid tool safety policy: {field_name} must be a positive integer")
    return value
