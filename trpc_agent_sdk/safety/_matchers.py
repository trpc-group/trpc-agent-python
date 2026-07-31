# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Deterministic domain, path, and command matching helpers."""

from __future__ import annotations

import posixpath
from pathlib import PurePosixPath
from typing import Iterable
from urllib.parse import urlsplit


def normalize_domain(value: str) -> str:
    """Extract a lower-case hostname from a URL, host, or scp-style target."""
    text = value.strip().lower()
    if not text:
        return ""
    if "://" in text:
        return (urlsplit(text).hostname or "").rstrip(".")
    if "@" in text:
        text = text.rsplit("@", 1)[1]
    if text.startswith("["):
        end = text.find("]")
        return text[1:end] if end >= 0 else text
    if ":" in text and text.count(":") == 1:
        text = text.split(":", 1)[0]
    return text.rstrip(".")


def domain_matches(value: str, patterns: Iterable[str]) -> bool:
    """Match exact hosts or ``*.example.com`` on DNS label boundaries."""
    host = normalize_domain(value)
    if not host:
        return False
    for raw_pattern in patterns:
        pattern = raw_pattern.strip().lower().rstrip(".")
        if pattern.startswith("*."):
            suffix = pattern[2:]
            if host != suffix and host.endswith("." + suffix):
                return True
        elif host == pattern:
            return True
    return False


def normalize_path(value: str, cwd: str = "") -> str:
    """Normalize separators and dot components without touching the filesystem."""
    path = value.strip().replace("\\", "/")
    if path.startswith("~/"):
        path = "/home/<user>/" + path[2:]
    if cwd and not path.startswith("/"):
        path = posixpath.join(cwd.replace("\\", "/"), path)
    normalized = posixpath.normpath(path)
    return normalized if normalized != "." else ""


def path_matches(value: str, patterns: Iterable[str], cwd: str = "") -> bool:
    """Match path prefixes on component boundaries, never by raw substring."""
    normalized = normalize_path(value, cwd)
    parts = PurePosixPath(normalized).parts
    for raw_pattern in patterns:
        pattern = normalize_path(raw_pattern)
        if not pattern:
            continue
        pattern_parts = PurePosixPath(pattern).parts
        if parts[:len(pattern_parts)] == pattern_parts:
            return True
        if pattern.startswith("/home/<user>/") and "/.ssh" in normalized:
            relative = pattern.split("/home/<user>", 1)[1]
            if relative and relative in normalized:
                return True
    return False


def command_matches(command: str, patterns: Iterable[str]) -> bool:
    """Match normalized executable basename or exact configured command."""
    normalized = command.strip().replace("\\", "/")
    basename = normalized.rsplit("/", 1)[-1]
    return any(pattern.strip() in {normalized, basename} for pattern in patterns)
