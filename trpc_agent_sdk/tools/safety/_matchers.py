# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Policy matching helpers for safety scanning."""

from __future__ import annotations

from collections.abc import Sequence
import fnmatch
from pathlib import PurePosixPath
from pathlib import PureWindowsPath
import shlex

from ._policy import SafetyPolicy

_WINDOWS_EXTENSIONS = (".exe", ".cmd", ".bat", ".ps1")


def _clean(value: object) -> str:
    return str(value or "").strip()


def matches_any_pattern(value: object, patterns: Sequence[str]) -> bool:
    """Case-insensitive fnmatch over a sequence of patterns."""

    normalized = _clean(value).lower()
    if not normalized:
        return False
    return any(fnmatch.fnmatchcase(normalized, _clean(pattern).lower()) for pattern in patterns if _clean(pattern))


def is_domain_allowed(hostname: str, allowed_domains: Sequence[str]) -> bool:
    """Return whether a hostname is allowed by exact or wildcard domain rules."""

    host = _clean(hostname).rstrip(".").lower()
    if not host:
        return False

    for raw_pattern in allowed_domains:
        pattern = _clean(raw_pattern).rstrip(".").lower()
        if not pattern:
            continue
        if pattern.startswith("*."):
            suffix = pattern[2:]
            if host != suffix and host.endswith(f".{suffix}"):
                return True
            continue
        if host == pattern:
            return True
    return False


def _normalize_path(value: object) -> str:
    path = _clean(value).replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    path = path.rstrip("/") if path not in ("/", "~") else path
    return path.lower()


def _path_pattern_matches(path: str, pattern: str) -> bool:
    if not path or not pattern:
        return False
    if fnmatch.fnmatchcase(path, pattern):
        return True
    if fnmatch.fnmatchcase(path, f"{pattern.rstrip('/')}/*"):
        return True
    if not any(char in pattern for char in "*?[]"):
        return path == pattern or path.startswith(f"{pattern.rstrip('/')}/")
    return False


def is_path_denied(path: str, policy: SafetyPolicy) -> bool:
    """Return whether a path matches policy denied path patterns."""

    normalized = _normalize_path(path)
    patterns = [_normalize_path(pattern) for pattern in policy.denied_paths]
    return any(_path_pattern_matches(normalized, pattern) for pattern in patterns)


def is_env_key_sensitive(key: str, policy: SafetyPolicy) -> bool:
    """Return whether an environment variable key is sensitive by policy."""

    return matches_any_pattern(key, policy.sensitive_env_keys)


def _first_token(command: str | Sequence[str]) -> str:
    if isinstance(command, str):
        value = command.strip()
        if not value:
            return ""
        try:
            parts = shlex.split(value, posix=False)
        except ValueError:
            parts = value.split()
        return parts[0] if parts else ""

    return _clean(command[0]) if command else ""


def get_command_name(command: str | Sequence[str]) -> str:
    """Extract a normalized command name from a command string or argv."""

    token = _first_token(command).strip("\"'")
    if not token:
        return ""
    posix_name = PurePosixPath(token.replace("\\", "/")).name
    windows_name = PureWindowsPath(posix_name).name
    command_name = windows_name.lower()
    for suffix in _WINDOWS_EXTENSIONS:
        if command_name.endswith(suffix):
            return command_name[:-len(suffix)]
    return command_name


def is_command_denied(command: str | Sequence[str], policy: SafetyPolicy) -> bool:
    """Return whether the first command token is denied by policy."""

    return matches_any_pattern(get_command_name(command), policy.denied_commands)


def is_command_allowed(command: str | Sequence[str], policy: SafetyPolicy) -> bool:
    """Return whether the first command token is allowed by policy."""

    if not policy.allowed_commands:
        return True
    return matches_any_pattern(get_command_name(command), policy.allowed_commands)
