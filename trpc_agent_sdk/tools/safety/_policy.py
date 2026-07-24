# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Configurable policy for tool script safety scanning."""

from __future__ import annotations

import fnmatch
import os
import warnings
from dataclasses import dataclass
from dataclasses import fields
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from ._types import Decision


@dataclass
class ToolSafetyPolicy:
    """YAML-backed policy used by the static safety scanner."""

    allowed_domains: list[str]
    allowed_commands: list[str]
    denied_paths: list[str]
    max_timeout_seconds: int
    max_output_bytes: int
    long_sleep_seconds: int
    deny_dependency_install: bool
    deny_privilege_escalation: bool
    review_process_execution: bool
    review_unknown_network: bool
    review_dynamic_code: bool
    review_shell_features: bool
    block_on_review: bool

    @classmethod
    def default(cls) -> "ToolSafetyPolicy":
        """Return the default opt-in policy."""
        return cls(
            allowed_domains=[
                "api.example.com",
                "*.trusted.internal",
            ],
            allowed_commands=[
                "python",
                "python3",
                "bash",
                "sh",
                "ls",
                "cat",
                "grep",
                "find",
                "echo",
                "pwd",
                "git",
                "tar",
                "pytest",
            ],
            denied_paths=[
                "~/.ssh",
                "~/.ssh/*",
                ".env",
                "*.env",
                "*.pem",
                "*.key",
                "id_rsa",
                "id_dsa",
                "service_account*.json",
                "/etc/passwd",
                "/etc/shadow",
                "/root",
                "/",
            ],
            max_timeout_seconds=300,
            max_output_bytes=1048576,
            long_sleep_seconds=60,
            deny_dependency_install=True,
            deny_privilege_escalation=True,
            review_process_execution=True,
            review_unknown_network=True,
            review_dynamic_code=True,
            review_shell_features=True,
            block_on_review=False,
        )

    @classmethod
    def from_file(
        cls,
        path: str | os.PathLike[str],
        *,
        strict: bool = False,
    ) -> "ToolSafetyPolicy":
        """Load a policy from YAML, overlaying values on top of defaults."""
        policy = cls.default()
        with open(path, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
        if not isinstance(data, dict):
            raise ValueError("tool safety policy must be a YAML mapping")

        for key, value in validate_policy_data(data, strict=strict).items():
            setattr(policy, key, value)
        return policy

    def is_domain_allowed(self, host: str) -> bool:
        """Return whether a hostname matches the allowlist."""
        hostname = _normalize_host(host)
        if not hostname:
            return False
        for pattern in self.allowed_domains:
            allowed = _normalize_host(pattern)
            if hostname == allowed:
                return True
            if allowed.startswith("*.") and hostname.endswith(allowed[1:]) and hostname != allowed[2:]:
                return True
        return False

    def is_path_denied(self, path: str | os.PathLike[str]) -> bool:
        """Return whether a path matches denied paths or sensitive filename globs."""
        if path is None:
            return False
        path_text = str(path).strip().strip("\"'")
        if not path_text:
            return False

        candidate = _normalize_path(path_text)
        candidate_slash = candidate.replace("\\", "/")
        candidate_name = Path(candidate_slash).name or candidate_slash

        for pattern in self.denied_paths:
            pattern_text = str(pattern).strip().strip("\"'")
            pattern_norm = _normalize_path(pattern_text)
            pattern_slash = pattern_norm.replace("\\", "/")
            pattern_name = Path(pattern_slash).name or pattern_slash
            basename_only_pattern = ("/" not in pattern_text and "\\" not in pattern_text
                                     and not pattern_text.startswith("~") and not os.path.isabs(pattern_text))

            if pattern_text == "/" and candidate_slash in {"/", "\\"}:
                return True
            if fnmatch.fnmatch(candidate_slash.lower(), pattern_slash.lower()):
                return True
            if not basename_only_pattern and not _has_glob(pattern_text) and pattern_text != "/":
                prefix = pattern_slash.rstrip("/") + "/"
                if candidate_slash.lower().startswith(prefix.lower()):
                    return True
            if basename_only_pattern and fnmatch.fnmatch(candidate_name.lower(), pattern_name.lower()):
                return True
        return False

    def is_command_allowed(self, command: str) -> bool:
        """Return whether a command is on the policy allowlist."""
        command_name = Path(str(command).strip().strip("\"'")).name.lower()
        return command_name in {cmd.lower() for cmd in self.allowed_commands}

    def should_block(self, decision: Decision | str) -> bool:
        """Return whether a report decision should block execution."""
        decision_value = decision.value if isinstance(decision, Decision) else decision
        if decision_value == Decision.DENY.value:
            return True
        return decision_value == Decision.NEEDS_HUMAN_REVIEW.value and self.block_on_review


def _normalize_host(host: str) -> str:
    host = str(host or "").strip().lower()
    if "://" in host:
        host = urlparse(host).hostname or ""
    if host.startswith("[") and "]" in host:
        return host.split("]", 1)[0].strip("[]")
    if ":" in host:
        host = host.split(":", 1)[0]
    return host.rstrip(".")


def _normalize_path(path: str) -> str:
    expanded = os.path.expandvars(os.path.expanduser(path))
    return os.path.normpath(expanded)


def _has_glob(pattern: str) -> bool:
    return any(char in pattern for char in "*?[")


def validate_policy_data(data: dict[str, Any], *, strict: bool = False) -> dict[str, Any]:
    """Validate raw YAML policy data and return fields safe to overlay."""
    valid_names = {field.name for field in fields(ToolSafetyPolicy)}
    validated: dict[str, Any] = {}
    for key, value in data.items():
        if key not in valid_names:
            _policy_issue(f"unknown policy key: {key}", strict)
            continue
        if key in {"allowed_domains", "allowed_commands", "denied_paths"}:
            if not _is_string_list(value):
                _policy_issue(f"{key} must be a list of strings", strict)
                continue
        elif key in {"max_timeout_seconds", "max_output_bytes", "long_sleep_seconds"}:
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                _policy_issue(f"{key} must be a non-negative integer", strict)
                continue
        elif key in {
                "deny_dependency_install",
                "deny_privilege_escalation",
                "review_process_execution",
                "review_unknown_network",
                "review_dynamic_code",
                "review_shell_features",
                "block_on_review",
        }:
            if not isinstance(value, bool):
                _policy_issue(f"{key} must be a boolean", strict)
                continue
        validated[key] = value
    return validated


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _policy_issue(message: str, strict: bool) -> None:
    if strict:
        raise ValueError(message)
    warnings.warn(message, UserWarning, stacklevel=3)
