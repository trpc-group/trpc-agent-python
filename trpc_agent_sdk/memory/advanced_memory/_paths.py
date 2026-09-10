# Tencent is pleased to support the open source ecosystem.
#
# Copyright (C) 2026 Tencent. All rights reserved.
# Licensed under Apache-2.0.
"""Safe path resolution for long-term Advanced Memory."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from ._config import AdvancedMemoryServiceConfig

_SAFE_COMPONENT_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_component(value: str, *, field_name: str) -> str:
    if value != value.strip() or any(ord(character) < 32 for character in value):
        raise ValueError(f"{field_name} must not contain surrounding or control whitespace")
    normalized = _SAFE_COMPONENT_PATTERN.sub("_", value.strip()).strip("._")
    if not normalized:
        raise ValueError(f"{field_name} must contain at least one safe character")
    return normalized


def _collision_safe_component(value: str, *, field_name: str) -> str:
    stripped = value.strip()
    normalized = _safe_component(stripped, field_name=field_name)
    if normalized == stripped:
        return normalized
    digest = hashlib.sha256(stripped.encode("utf-8")).hexdigest()[:12]
    return f"{normalized}-{digest}"


@dataclass(frozen=True)
class MemoryScope:
    """Identify the application and user that own memory."""

    app_name: str
    user_id: str

    def __post_init__(self) -> None:
        _safe_component(self.app_name, field_name="app_name")
        _safe_component(self.user_id, field_name="user_id")

    @property
    def storage_key(self) -> str:
        return repr((self.app_name, self.user_id))


@dataclass(frozen=True)
class AdvancedMemoryPaths:
    """Build paths for long-term memory only."""

    config: AdvancedMemoryServiceConfig
    scope: MemoryScope | None = None

    def for_scope(self, app_name: str, user_id: str) -> "AdvancedMemoryPaths":
        return AdvancedMemoryPaths(self.config, MemoryScope(app_name, user_id))

    @property
    def tenant_root_dir(self) -> Path:
        if self.scope is None:
            return self.config.root_dir
        return (self.config.root_dir / "tenants" /
                _collision_safe_component(self.scope.app_name, field_name="app_name") /
                _collision_safe_component(self.scope.user_id, field_name="user_id"))

    @property
    def scope_key(self) -> str:
        return self.scope.storage_key if self.scope is not None else "legacy\0global"

    @property
    def memory_dir(self) -> Path:
        return self.tenant_root_dir / self.config.memory_dir_name

    @property
    def memory_index_path(self) -> Path:
        return self.memory_dir / self.config.memory_index_name

    def memory_topic_path(self, topic_name: str) -> Path:
        safe_name = _collision_safe_component(topic_name, field_name="topic_name")
        if not safe_name.lower().endswith(".md"):
            safe_name = f"{safe_name}.md"
        if safe_name == self.config.memory_index_name:
            raise ValueError("Topic file cannot overwrite the memory index")
        return self.memory_dir / safe_name

    def storage_reference(self, resource: str, *, topic_name: str | None = None) -> str:
        if resource == "memory_index":
            path = self.memory_index_path
        elif resource == "memory_topic" and topic_name is not None:
            path = self.memory_topic_path(topic_name)
        else:
            raise ValueError(f"Unknown long-term memory resource: {resource}")
        if self.config.storage_backend == "local":
            return str(path)
        if self.scope is None:
            raise ValueError("A scoped path is required for non-local memory storage")
        app = _collision_safe_component(self.scope.app_name, field_name="app_name")
        user = _collision_safe_component(self.scope.user_id, field_name="user_id")
        if self.config.storage_backend == "redis":
            key = f"{self.config.redis_key_prefix}:{{{app}:{user}}}:memory:{path.name}"
            return f"advanced-memory://redis/{key}"
        return f"advanced-memory://sql/{app}/{user}/memory/{path.name}"

    def ensure_base_directories(self) -> None:
        self.memory_dir.mkdir(parents=True, exist_ok=True)
