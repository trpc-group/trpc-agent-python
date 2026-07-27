# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Safety policy configuration with YAML loading support."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
import re
from typing import Any
from typing import Dict
from typing import List

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
    def _path_has_extension(path: str) -> bool:
        """Return True if the path basename contains a dot after position 0.

        Hidden directories like .ssh, .aws, .kube return False (dot at
        position 0). File entries like docker.sock, cert.pem return True.
        """
        basename = path.rstrip("/").rsplit("/", 1)[-1]
        return "." in basename[1:]

    def is_path_denied(self, path_text: str) -> bool:
        """Return True if path_text is a proper sub-path of any denied entry.

        A path that equals a denied directory exactly (e.g. cwd="/root")
        is not denied — only paths inside it (e.g. "/root/.ssh") are.
        File-like entries (e.g. /var/run/docker.sock) are denied on
        exact match as well as sub-path match.
        """
        for denied in self.denied_paths:
            if path_text == denied:
                # Exact match: deny if the entry looks like a file
                # (e.g. /var/run/docker.sock), but allow if directory-like
                # (e.g. cwd="/etc" — being IN the denied dir is allowed).
                if self._path_has_extension(denied):
                    return True
                continue
            if path_text.startswith(denied + "/") or path_text.startswith(denied + "\\"):
                return True
        return False

    def is_domain_allowed(self, domain: str) -> bool:
        """Return True if domain is in network_allowlist."""
        return domain in self.network_allowlist
