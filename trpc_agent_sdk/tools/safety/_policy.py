# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Strict YAML policy model for tool script safety checks."""

from __future__ import annotations

import fnmatch
from pathlib import Path
import posixpath
from typing import Optional
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator

from ._models import SafetyDecision

DEFAULT_ALLOWED_COMMANDS = [
    "awk",
    "cat",
    "cut",
    "echo",
    "find",
    "git",
    "grep",
    "head",
    "jq",
    "ls",
    "printf",
    "pwd",
    "python",
    "python3",
    "sed",
    "sort",
    "tail",
    "tr",
    "uniq",
    "wc",
]

DEFAULT_DENIED_PATHS = [
    "~/.ssh",
    "~/.aws/credentials",
    "~/.config/gcloud",
    "**/.env",
    "**/.env.*",
    "**/*credentials*",
    "**/*id_rsa*",
    "**/*id_ed25519*",
    "/boot",
    "/etc",
    "/proc",
    "/root",
    "/sys",
]


def _normalize_command_name(command: str) -> str:
    candidate = command.strip().replace("\\", "/").lower()
    if "/" not in candidate:
        return candidate
    normalized = posixpath.normpath(candidate)
    if not normalized.startswith("/") and "/" not in normalized:
        return f"./{normalized}"
    return normalized


class ToolSafetyPolicy(BaseModel):
    """Configuration that changes guard behavior without code changes."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    version: str = "1"
    fail_closed: bool = True
    block_on_review: bool = True
    allowed_domains: list[str] = Field(default_factory=list)
    allowed_commands: list[str] = Field(default_factory=lambda: list(DEFAULT_ALLOWED_COMMANDS))
    denied_paths: list[str] = Field(default_factory=lambda: list(DEFAULT_DENIED_PATHS))
    max_timeout_seconds: float = Field(default=300, gt=0)
    max_output_bytes: int = Field(default=1_048_576, gt=0)
    max_script_bytes: int = Field(default=1_048_576, gt=0)
    long_sleep_seconds: float = Field(default=60, gt=0)
    max_concurrency: int = Field(default=32, gt=0)
    rule_actions: dict[str, SafetyDecision] = Field(default_factory=dict)

    @field_validator("allowed_domains")
    @classmethod
    def _normalize_domains(cls, domains: list[str]) -> list[str]:
        normalized = []
        for domain in domains:
            candidate = domain.strip().lower().rstrip(".")
            if "://" in candidate:
                candidate = (urlsplit(candidate).hostname or "").lower().rstrip(".")
            if candidate.startswith("*."):
                candidate = candidate[2:]
            if not candidate or "/" in candidate or any(char.isspace() for char in candidate):
                raise ValueError(f"invalid allowed domain: {domain!r}")
            normalized.append(candidate)
        return sorted(set(normalized))

    @field_validator("allowed_commands")
    @classmethod
    def _normalize_commands(cls, commands: list[str]) -> list[str]:
        normalized = []
        for command in commands:
            candidate = _normalize_command_name(command)
            if not candidate or any(char.isspace() for char in candidate):
                raise ValueError(f"invalid allowed command: {command!r}")
            normalized.append(candidate)
        return sorted(set(normalized))

    @field_validator("denied_paths")
    @classmethod
    def _validate_denied_paths(cls, paths: list[str]) -> list[str]:
        if any(not path.strip() for path in paths):
            raise ValueError("denied paths cannot contain empty entries")
        return list(dict.fromkeys(path.strip() for path in paths))

    @field_validator("rule_actions")
    @classmethod
    def _normalize_rule_ids(cls, actions: dict[str, SafetyDecision]) -> dict[str, SafetyDecision]:
        normalized = {}
        for rule_id, decision in actions.items():
            candidate = rule_id.strip().upper()
            if not candidate:
                raise ValueError("rule action ids cannot be empty")
            normalized[candidate] = decision
        return normalized

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ToolSafetyPolicy":
        """Load and strictly validate a YAML policy file."""

        policy_path = Path(path)
        try:
            raw_data = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ValueError(f"unable to read safety policy {policy_path}: {exc}") from exc
        except yaml.YAMLError as exc:
            raise ValueError(f"invalid safety policy YAML {policy_path}: {exc}") from exc
        if raw_data is None:
            raw_data = {}
        if not isinstance(raw_data, dict):
            raise ValueError("safety policy root must be a mapping")
        return cls.model_validate(raw_data)

    def action_for(self, rule_id: str, default: SafetyDecision) -> SafetyDecision:
        """Resolve a policy override for a built-in or custom rule."""

        return self.rule_actions.get(rule_id.upper(), default)

    def is_domain_allowed(self, hostname: Optional[str]) -> bool:
        """Match exact domains and subdomains on DNS label boundaries."""

        if not hostname:
            return False
        candidate = hostname.lower().rstrip(".")
        return any(candidate == domain or candidate.endswith(f".{domain}") for domain in self.allowed_domains)

    def is_command_allowed(self, command: str) -> bool:
        """Check a basename or an explicitly allowlisted executable path."""

        candidate = _normalize_command_name(command)
        return candidate in self.allowed_commands

    def is_path_denied(self, raw_path: str) -> bool:
        """Match configured path literals and globs without touching the file system."""

        candidate = raw_path.strip().replace("\\", "/")
        candidate_lower = candidate.lower()
        if candidate_lower.startswith("${home}"):
            candidate_lower = f"~{candidate_lower[7:]}"
        elif candidate_lower.startswith("$home"):
            candidate_lower = f"~{candidate_lower[5:]}"
        # POSIX preserves exactly two leading slashes, but execution commonly
        # resolves ``//etc`` to ``/etc``. Treat all absolute-path prefixes the
        # same so this syntax cannot bypass a denied system directory.
        if candidate_lower.startswith("//"):
            candidate_lower = f"/{candidate_lower.lstrip('/')}"
        candidate_lower = posixpath.normpath(candidate_lower)
        for raw_pattern in self.denied_paths:
            pattern = raw_pattern.replace("\\", "/").lower().rstrip("/")
            if not pattern:
                continue
            if pattern.startswith("**/"):
                suffix = pattern[3:]
                relative_candidate = candidate_lower[2:] if candidate_lower.startswith("./") else candidate_lower
                if fnmatch.fnmatch(candidate_lower, pattern) or fnmatch.fnmatch(relative_candidate, suffix):
                    return True
            elif any(char in pattern for char in "*?["):
                if fnmatch.fnmatch(candidate_lower, pattern):
                    return True
            elif candidate_lower == pattern or candidate_lower.startswith(f"{pattern}/"):
                return True
            elif pattern.startswith("~") and pattern[1:] in candidate_lower:
                return True
        return False


def load_policy(path: Optional[str | Path] = None) -> ToolSafetyPolicy:
    """Load an explicit policy or return the strict built-in defaults."""

    if path is None:
        return ToolSafetyPolicy()
    return ToolSafetyPolicy.from_yaml(path)
