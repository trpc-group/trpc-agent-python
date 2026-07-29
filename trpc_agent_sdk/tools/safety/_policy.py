# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Safety policy configuration with YAML loading support."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
import fnmatch
from pathlib import Path
import re
from typing import Any
from typing import Dict
from typing import List
from urllib.parse import urlparse

import yaml


@dataclass
class PolicyConfig:
    """Configurable safety policy for tool/script execution.

    All fields have conservative defaults. Policies can be loaded from
    a YAML file so operators can tune rules without code changes.

    Note on ``allowed_commands``:
        When non-empty, EVERY command whose base token is not in this list
        generates a MEDIUM-risk finding (R003_SYSTEM_COMMAND, "Command Not
        Allowed").  This acts as a positive allowlist: unlisted commands
        are flagged for review, not blocked.  Shell keywords (for, if,
        while, case, etc.) are exempt from this check.
    """

    allowed_commands: List[str] = field(default_factory=list)
    review_commands: List[str] = field(default_factory=list)
    denied_commands: List[str] = field(default_factory=list)
    denied_paths: List[str] = field(default_factory=list)
    network_allowlist: List[str] = field(default_factory=list)
    env_allowlist: List[str] = field(default_factory=list)
    max_timeout_seconds: int = 300
    max_output_bytes: int = 10 * 1024 * 1024
    max_file_write_bytes: int = 50 * 1024 * 1024
    review_shell_pipelines: bool = True
    review_package_install: bool = True
    secret_patterns: List[str] = field(default_factory=list)

    @classmethod
    def default(cls) -> "PolicyConfig":
        """Return a PolicyConfig with conservative built-in defaults."""
        return cls(
            allowed_commands=[
                "python",
                "python3",
                "pytest",
                "echo",
                "cat",
                "ls",
                "pwd",
                "grep",
                "head",
                "tail",
                "wc",
                "find",
                "mkdir",
                "cp",
                "mv",
            ],
            review_commands=[
                "pip install",
                "npm install",
                "poetry install",
            ],
            denied_commands=[
                "rm -rf /",
                "sudo",
                "shutdown",
                "reboot",
                "dd if=",
            ],
            denied_paths=[
                "/etc",
                "/root",
                "~/.ssh",
                "~/.aws",
                "~/.kube",
            ],
            network_allowlist=[
                "github.com",
                "pypi.org",
                "files.pythonhosted.org",
            ],
            env_allowlist=[
                "PATH",
                "HOME",
                "LANG",
            ],
            max_timeout_seconds=300,
            max_output_bytes=10 * 1024 * 1024,
            max_file_write_bytes=50 * 1024 * 1024,
            review_shell_pipelines=True,
            review_package_install=True,
            secret_patterns=[
                r"(?i)api[_-]?key",
                r"(?i)token",
                r"(?i)password",
                r"-----BEGIN PRIVATE KEY-----",
            ],
        )

    @classmethod
    def validate(cls, config: Dict[str, Any]) -> None:
        """Validate config dict and raise ValueError on type/value errors.

        Checks every key against the corresponding PolicyConfig field:
        - List fields must be lists of str
        - Bool fields must be bool
        - Int fields must be positive int
        - Unknown keys are skipped (will be filtered by from_dict)
        """
        list_str_fields = {
            "allowed_commands",
            "review_commands",
            "denied_commands",
            "denied_paths",
            "network_allowlist",
            "env_allowlist",
            "secret_patterns",
        }
        bool_fields = {"review_shell_pipelines", "review_package_install"}
        positive_int_fields = {"max_timeout_seconds", "max_output_bytes", "max_file_write_bytes"}

        for key, value in config.items():
            if key in list_str_fields:
                if not isinstance(value, list):
                    raise ValueError(f"{key} must be a list, got {type(value).__name__}")
                for i, item in enumerate(value):
                    if not isinstance(item, str):
                        raise ValueError(f"{key}[{i}] must be a str, got {type(item).__name__}")
                    # Pre-compile secret_patterns to catch invalid/unbounded
                    # regex at policy-load time (prevents ReDoS at runtime).
                    if key == "secret_patterns":
                        try:
                            re.compile(item)
                        except re.error as exc:
                            raise ValueError(f"secret_patterns[{i}] is not a valid regex: {exc}") from exc
            elif key in bool_fields:
                if not isinstance(value, bool):
                    raise ValueError(f"{key} must be a bool, got {type(value).__name__}")
            elif key in positive_int_fields:
                if not isinstance(value, int) or isinstance(value, bool):
                    raise ValueError(f"{key} must be an int, got {type(value).__name__}")
                if value <= 0:
                    raise ValueError(f"{key} must be > 0, got {value}")

    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> "PolicyConfig":
        """Construct a PolicyConfig from a dict (partial overrides allowed).

        Unknown keys are ignored. Missing keys retain dataclass defaults.
        Values are validated before construction.
        """
        cls.validate(config)
        field_names = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in config.items() if k in field_names}
        return cls(**filtered)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "PolicyConfig":
        """Load a PolicyConfig from a YAML file.

        Raises:
            FileNotFoundError: If the policy file does not exist.
            ValueError: If the YAML is malformed or not a mapping.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Policy file not found: {path}")
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid YAML in policy file {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"Policy file {path} must contain a YAML mapping, got {type(data).__name__}")
        return cls.from_dict(data)

    def is_command_allowed(self, command: str) -> bool:
        """Return True if command is in allowed_commands."""
        return command in self.allowed_commands

    @staticmethod
    def _normalize_path(path_text: str) -> str:
        """Normalize path text for policy comparisons."""
        normalized = path_text.strip().strip("'\"").replace("\\", "/")
        while "//" in normalized:
            normalized = normalized.replace("//", "/")
        if normalized not in ("/", "~"):
            normalized = normalized.rstrip("/")
        return normalized

    def is_path_denied(self, path_text: str) -> bool:
        """Return True if path_text matches or is under any denied entry."""
        normalized = self._normalize_path(path_text)
        if not normalized:
            return False
        expanded = self._normalize_path(str(Path(normalized).expanduser()))
        candidates = {normalized, expanded}

        for denied in self.denied_paths:
            denied_normalized = self._normalize_path(denied)
            if not denied_normalized:
                continue
            denied_expanded = self._normalize_path(str(Path(denied_normalized).expanduser()))
            denied_candidates = {denied_normalized, denied_expanded}

            for candidate in candidates:
                for denied_candidate in denied_candidates:
                    if fnmatch.fnmatchcase(candidate, denied_candidate):
                        return True
                    if fnmatch.fnmatchcase(candidate, f"{denied_candidate.rstrip('/')}/*"):
                        return True
                    if candidate == denied_candidate or candidate.startswith(f"{denied_candidate.rstrip('/')}/"):
                        return True
        return False

    def is_domain_allowed(self, domain: str) -> bool:
        """Return True if domain or its subdomain is in network_allowlist."""
        normalized = self._normalize_domain(domain)
        if not normalized:
            return False
        for allowed in self.network_allowlist:
            allowed_normalized = self._normalize_domain(allowed)
            if not allowed_normalized:
                continue
            if normalized == allowed_normalized or normalized.endswith(f".{allowed_normalized}"):
                return True
        return False

    @staticmethod
    def _normalize_domain(domain: str) -> str:
        """Normalize host-like policy text for allowlist comparisons."""
        text = domain.strip().strip("'\"").lower().rstrip(".")
        if not text:
            return ""
        parsed = urlparse(text if "://" in text else f"//{text}")
        host = parsed.hostname or text
        return host.lower().rstrip(".")
