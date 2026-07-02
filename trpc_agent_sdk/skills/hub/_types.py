# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared data types for Skills Hub source adapters."""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field
from pathlib import PurePosixPath
from typing import Any
from typing import Union


@dataclass
class SkillMeta:
    """Minimal metadata returned by search results."""

    name: str
    description: str
    source: str  # "official", "github", "clawhub", "claude-marketplace", "lobehub", ...
    identifier: str  # source-specific ID (e.g. "openai/skills/skill-creator")
    repo: str | None = None
    path: str | None = None
    tags: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillBundle:
    """A downloaded skill, ready for the caller to write to disk."""

    name: str
    files: dict[str, Union[str, bytes]]  # relative_path -> file content
    source: str
    identifier: str
    metadata: dict[str, Any] = field(default_factory=dict)


def _normalize_bundle_path(path_value: str, *, field_name: str, allow_nested: bool) -> str:
    """Normalize and validate bundle-controlled paths before touching disk."""
    if not isinstance(path_value, str):
        raise ValueError(f"Unsafe {field_name}: expected a string")

    raw = path_value.strip()
    if not raw:
        raise ValueError(f"Unsafe {field_name}: empty path")

    normalized = raw.replace("\\", "/")
    path = PurePosixPath(normalized)
    parts = [part for part in path.parts if part not in ("", ".")]

    if normalized.startswith("/") or path.is_absolute():
        raise ValueError(f"Unsafe {field_name}: {path_value}")
    if not parts or any(part == ".." for part in parts):
        raise ValueError(f"Unsafe {field_name}: {path_value}")
    if re.fullmatch(r"[A-Za-z]:", parts[0]):
        raise ValueError(f"Unsafe {field_name}: {path_value}")
    if not allow_nested and len(parts) != 1:
        raise ValueError(f"Unsafe {field_name}: {path_value}")

    return "/".join(parts)


def validate_skill_name(name: str) -> str:
    return _normalize_bundle_path(name, field_name="skill name", allow_nested=False)


def validate_category_name(category: str) -> str:
    return _normalize_bundle_path(category, field_name="category", allow_nested=False)


def validate_bundle_rel_path(rel_path: str) -> str:
    return _normalize_bundle_path(rel_path, field_name="bundle file path", allow_nested=True)
