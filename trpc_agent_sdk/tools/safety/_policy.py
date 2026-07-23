# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Policy loading and matching for tool script safety scanning."""

from __future__ import annotations

import fnmatch
from dataclasses import fields
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


@dataclass
class ToolSafetyPolicy:
    """Configurable policy used by the script safety scanner."""

    allowed_domains: list[str]
    allowed_commands: list[str]
    denied_paths: list[str]
    max_timeout_seconds: int
    max_output_bytes: int
    deny_dependency_install: bool = True
    deny_privilege_escalation: bool = True
    review_unknown_network: bool = True
    review_process_execution: bool = True
    review_shell_features: bool = True
    long_sleep_seconds: int = 300

    @classmethod
    def default(cls) -> "ToolSafetyPolicy":
        return cls(
            allowed_domains=[],
            allowed_commands=["cat", "echo", "grep", "head", "ls", "pwd", "tail", "wc"],
            denied_paths=[
                "~/.ssh",
                "~/.aws",
                "~/.config/gcloud",
                ".env",
                ".token",
                "token.txt",
                "token.*",
                "*_token",
                "*_token.*",
                "credentials",
                "credentials.*",
                "*.pem",
                "*.key",
                "/etc/passwd",
                "/etc/shadow",
                "/root",
            ],
            max_timeout_seconds=300,
            max_output_bytes=1024 * 1024,
        )

    @classmethod
    def from_file(cls, path: str | Path, *, strict: bool = False) -> "ToolSafetyPolicy":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls.from_dict(data, strict=strict)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, strict: bool = False) -> "ToolSafetyPolicy":
        if not isinstance(data, dict):
            raise ValueError("Tool safety policy must be a mapping.")
        if strict:
            cls._validate_strict(data)
        default = cls.default()
        return cls(
            allowed_domains=list(data.get("allowed_domains", default.allowed_domains) or []),
            allowed_commands=list(data.get("allowed_commands", default.allowed_commands) or []),
            denied_paths=list(data.get("denied_paths", default.denied_paths) or []),
            max_timeout_seconds=int(data.get("max_timeout_seconds", default.max_timeout_seconds)),
            max_output_bytes=int(data.get("max_output_bytes", default.max_output_bytes)),
            deny_dependency_install=bool(data.get("deny_dependency_install", default.deny_dependency_install)),
            deny_privilege_escalation=bool(data.get("deny_privilege_escalation", default.deny_privilege_escalation)),
            review_unknown_network=bool(data.get("review_unknown_network", default.review_unknown_network)),
            review_process_execution=bool(data.get("review_process_execution", default.review_process_execution)),
            review_shell_features=bool(data.get("review_shell_features", default.review_shell_features)),
            long_sleep_seconds=int(data.get("long_sleep_seconds", default.long_sleep_seconds)),
        )

    @classmethod
    def _validate_strict(cls, data: dict[str, Any]) -> None:
        allowed_keys = {field.name for field in fields(cls)}
        unknown_keys = sorted(set(data) - allowed_keys)
        if unknown_keys:
            raise ValueError(f"Unknown tool safety policy field(s): {', '.join(unknown_keys)}")

        list_fields = {"allowed_domains", "allowed_commands", "denied_paths"}
        bool_fields = {
            "deny_dependency_install",
            "deny_privilege_escalation",
            "review_unknown_network",
            "review_process_execution",
            "review_shell_features",
        }
        int_fields = {"max_timeout_seconds", "max_output_bytes", "long_sleep_seconds"}

        for key in list_fields & data.keys():
            value = data[key]
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError(f"{key} must be a list of strings.")
        for key in bool_fields & data.keys():
            if not isinstance(data[key], bool):
                raise ValueError(f"{key} must be a boolean.")
        for key in int_fields & data.keys():
            value = data[key]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{key} must be a non-negative integer.")

    def is_domain_allowed(self, domain: str) -> bool:
        normalized = domain.lower().strip(".")
        for allowed in self.allowed_domains:
            allowed_domain = allowed.lower().strip(".")
            if normalized == allowed_domain or normalized.endswith(f".{allowed_domain}"):
                return True
        return False

    def is_url_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        return bool(host and self.is_domain_allowed(host))

    def is_command_allowed(self, command: str) -> bool:
        return command in set(self.allowed_commands)

    def is_path_denied(self, path_text: str) -> bool:
        normalized = path_text.strip().strip("'\"")
        if not normalized:
            return False

        expanded = str(Path(normalized).expanduser())
        candidates = {_normalize_path_pattern(normalized), _normalize_path_pattern(expanded)}
        for denied in self.denied_paths:
            denied_normalized = _normalize_path_pattern(denied)
            denied_expanded = _normalize_path_pattern(str(Path(denied).expanduser()))
            for candidate in candidates:
                if fnmatch.fnmatch(candidate, denied_normalized) or fnmatch.fnmatch(candidate, denied_expanded):
                    return True
                if candidate == denied_expanded or candidate.startswith(f"{denied_expanded}/"):
                    return True
                if denied_normalized in {".env", "*/.env"} and (candidate == ".env" or candidate.endswith("/.env")):
                    return True
        return False


def _normalize_path_pattern(path_text: str) -> str:
    """Normalize user/script paths for cross-platform policy matching."""
    return path_text.replace("\\", "/").rstrip("/")
