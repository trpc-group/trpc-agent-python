# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Configurable policy for the tool script safety guard."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_ALLOWED_COMMANDS = [
    "awk",
    "cat",
    "echo",
    "find",
    "git",
    "grep",
    "head",
    "ls",
    "pytest",
    "python",
    "python3",
    "pwd",
    "sed",
    "tail",
    "wc",
]

DEFAULT_DENIED_PATHS = [
    "~/.ssh",
    ".env",
    ".aws/credentials",
    ".config/gcloud",
    "/etc/shadow",
    "/etc/sudoers",
    "id_rsa",
    "id_dsa",
    "id_ed25519",
    "credentials",
]

DEFAULT_SYSTEM_WRITE_PATHS = [
    "/",
    "/bin",
    "/boot",
    "/dev",
    "/etc",
    "/lib",
    "/lib64",
    "/proc",
    "/root",
    "/sbin",
    "/sys",
    "/usr",
    "/var",
]


@dataclass
class ToolSafetyPolicy:
    """Runtime policy for script scanning and pre-execution blocking."""

    name: str = "default"
    version: str = "1"
    allowed_domains: list[str] = field(default_factory=list)
    allowed_commands: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_COMMANDS))
    denied_paths: list[str] = field(default_factory=lambda: list(DEFAULT_DENIED_PATHS))
    system_write_paths: list[str] = field(default_factory=lambda: list(DEFAULT_SYSTEM_WRITE_PATHS))
    max_timeout_seconds: int = 300
    max_output_bytes: int = 2_000_000
    max_sleep_seconds: int = 300
    max_loop_iterations: int = 1_000_000
    max_literal_write_bytes: int = 10_000_000
    max_parallel_tasks: int = 128
    deny_risk_level: str = "high"
    review_risk_level: str = "medium"
    block_on_review: bool = True
    redact_secrets: bool = True
    audit_log_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> "ToolSafetyPolicy":
        """Build a policy from a YAML-style mapping."""
        data = dict(data or {})
        policy_data = data.pop("policy", None)
        if isinstance(policy_data, dict):
            data = {**policy_data, **data}

        known_fields = {field.name for field in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        init_data = {key: value for key, value in data.items() if key in known_fields}
        metadata = {key: value for key, value in data.items() if key not in known_fields}
        if metadata:
            init_data["metadata"] = metadata
        return cls(**init_data)

    @classmethod
    def load(cls, path: str | Path | None) -> "ToolSafetyPolicy":
        """Load a policy from YAML, or return defaults when path is not set."""
        if path is None:
            return cls()
        with Path(path).open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
        if not isinstance(data, dict):
            raise ValueError("tool safety policy must be a YAML mapping")
        policy = cls.from_mapping(data)
        if policy.audit_log_path:
            policy.audit_log_path = str(Path(policy.audit_log_path))
        return policy

    def is_domain_allowed(self, host: str | None) -> bool:
        """Return whether a host is allowed by policy."""
        if not host:
            return False
        normalized_host = host.lower().rstrip(".")
        for domain in self.allowed_domains:
            allowed = str(domain).lower().rstrip(".")
            if not allowed:
                continue
            if allowed.startswith("*."):
                suffix = allowed[2:]
                if normalized_host == suffix or normalized_host.endswith(f".{suffix}"):
                    return True
            elif normalized_host == allowed or normalized_host.endswith(f".{allowed}"):
                return True
        return False

    def is_command_allowed(self, command: str | None) -> bool:
        """Return whether a command executable is allowed."""
        if not command:
            return False
        base = Path(str(command).strip().split()[0]).name.lower()
        return base in {item.lower() for item in self.allowed_commands}

    def is_denied_path(self, value: str | None) -> bool:
        """Return whether a path-like value matches a denied path pattern."""
        if not value:
            return False
        normalized = self._normalize_path_text(value)
        for pattern in self.denied_paths:
            normalized_pattern = self._normalize_path_text(str(pattern))
            if not normalized_pattern:
                continue
            if normalized_pattern == ".env":
                if normalized.endswith("/.env") or normalized == ".env" or "/.env." in normalized:
                    return True
            elif normalized_pattern in normalized:
                return True
        return False

    def is_system_write_path(self, value: str | None) -> bool:
        """Return whether a path-like value targets a protected system path."""
        if not value:
            return False
        normalized = self._normalize_path_text(value)
        for pattern in self.system_write_paths:
            normalized_pattern = self._normalize_path_text(str(pattern))
            if not normalized_pattern:
                continue
            if normalized_pattern == "/":
                if normalized in {"/", "/*", "/."}:
                    return True
                continue
            normalized_pattern = normalized_pattern.rstrip("/")
            if normalized == normalized_pattern or normalized.startswith(f"{normalized_pattern}/"):
                return True
        return False

    @staticmethod
    def _normalize_path_text(value: str) -> str:
        """Normalize a path-like string for simple policy matching."""
        return str(value).strip().strip("'\"").replace("\\", "/").lower()


def load_tool_safety_policy(path: str | Path | None = None) -> ToolSafetyPolicy:
    """Load a tool safety policy from YAML."""
    return ToolSafetyPolicy.load(path)
