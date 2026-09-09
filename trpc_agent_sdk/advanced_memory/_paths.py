# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Safe path resolution for the independent memory mechanism."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from ._config import AdvancedMemoryConfig

_SAFE_COMPONENT_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_component(value: str, *, field_name: str) -> str:
    """Convert an external identifier into a safe path component."""
    if value != value.strip() or any(character.isspace() and character not in {" "} for character in value):
        raise ValueError(f"{field_name} must not contain leading/trailing or control whitespace")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{field_name} must not contain control characters")
    normalized = _SAFE_COMPONENT_PATTERN.sub("_", value.strip()).strip("._")
    if not normalized:
        raise ValueError(f"{field_name} must contain at least one safe character")
    return normalized


def _collision_safe_component(value: str, *, field_name: str) -> str:
    """Add a digest when sanitization could cause path collisions."""
    stripped = value.strip()
    normalized = _safe_component(stripped, field_name=field_name)
    if normalized == stripped:
        return normalized
    digest = hashlib.sha256(stripped.encode("utf-8")).hexdigest()[:12]
    return f"{normalized}-{digest}"


@dataclass(frozen=True)
class MemoryScope:
    """Identify the application and user that own Advanced Memory data."""

    app_name: str
    user_id: str

    def __post_init__(self) -> None:
        _safe_component(self.app_name, field_name="app_name")
        _safe_component(self.user_id, field_name="user_id")

    @property
    def storage_key(self) -> str:
        """Return a stable process-local key for locks and caches."""
        return repr((self.app_name, self.user_id))


@dataclass(frozen=True)
class AdvancedMemoryPaths:
    """Build all disk paths for long-term and session memory."""

    config: AdvancedMemoryConfig
    scope: MemoryScope | None = None

    def for_scope(self, app_name: str, user_id: str) -> "AdvancedMemoryPaths":
        """Return paths rooted in the given application's user namespace."""
        return AdvancedMemoryPaths(self.config, MemoryScope(app_name, user_id))

    @property
    def tenant_root_dir(self) -> Path:
        """Return this scope's root, or the legacy root when unscoped."""
        if self.scope is None:
            return self.config.root_dir
        return (self.config.root_dir / "tenants" /
                _collision_safe_component(self.scope.app_name, field_name="app_name") /
                _collision_safe_component(self.scope.user_id, field_name="user_id"))

    @property
    def scope_key(self) -> str:
        """Return a key suitable for lock and cache partitioning."""
        return self.scope.storage_key if self.scope is not None else "legacy\0global"

    @property
    def memory_dir(self) -> Path:
        """Return the long-term memory directory."""
        return self.tenant_root_dir / self.config.memory_dir_name

    @property
    def session_root_dir(self) -> Path:
        """Return the root directory for session memory."""
        return self.tenant_root_dir / self.config.session_dir_name

    @property
    def memory_index_path(self) -> Path:
        """Return the long-term memory index path."""
        return self.memory_dir / self.config.memory_index_name

    def memory_topic_path(self, topic_name: str) -> Path:
        """Return a safe path for a long-term memory topic."""
        safe_name = _collision_safe_component(topic_name, field_name="topic_name")
        if not safe_name.lower().endswith(".md"):
            safe_name = f"{safe_name}.md"
        if safe_name == self.config.memory_index_name:
            raise ValueError("Topic file cannot overwrite the memory index")
        return self.memory_dir / safe_name

    def session_dir(self, session_id: str) -> Path:
        """Return the isolated storage directory for a session."""
        return self.session_root_dir / _collision_safe_component(
            session_id,
            field_name="session_id",
        )

    def transcript_path(self, session_id: str) -> Path:
        """Return the transcript path for a session."""
        return self.session_dir(session_id) / self.config.transcript_name

    def session_memory_path(self, session_id: str) -> Path:
        """Return the session memory path for a session."""
        return self.session_dir(session_id) / self.config.session_memory_name

    def tool_results_dir(self, session_id: str) -> Path:
        """Return the large tool-result directory for a session."""
        return self.session_dir(session_id) / "tool-results"

    def tool_result_path(self, session_id: str, result_id: str) -> Path:
        """Return a safe JSON path for a large tool result."""
        safe_result_id = _collision_safe_component(result_id, field_name="result_id")
        return self.tool_results_dir(session_id) / f"{safe_result_id}.json"

    def storage_reference(
        self,
        resource: str,
        *,
        session_id: str | None = None,
        topic_name: str | None = None,
        result_id: str | None = None,
    ) -> str:
        """Return a model-visible reference for a stored Advanced Memory resource."""
        if resource == "memory_index":
            local_path = self.memory_index_path
        elif resource == "memory_topic":
            if topic_name is None:
                raise ValueError("topic_name is required for a memory topic reference")
            local_path = self.memory_topic_path(topic_name)
        elif resource == "transcript":
            if session_id is None:
                raise ValueError("session_id is required for a transcript reference")
            local_path = self.transcript_path(session_id)
        elif resource == "session_memory":
            if session_id is None:
                raise ValueError("session_id is required for a session memory reference")
            local_path = self.session_memory_path(session_id)
        elif resource == "tool_result":
            if session_id is None or result_id is None:
                raise ValueError("session_id and result_id are required for a tool result reference")
            local_path = self.tool_result_path(session_id, result_id)
        else:
            raise ValueError(f"Unknown Advanced Memory resource: {resource}")
        if self.config.storage_backend == "local":
            return str(local_path)
        if self.scope is None:
            raise ValueError("A scoped path is required for non-local memory storage")

        app_component = self.tenant_root_dir.parent.name
        user_component = self.tenant_root_dir.name
        if self.config.storage_backend == "redis":
            user_base = f"{self.config.redis_key_prefix}:{{{app_component}:{user_component}}}"
            if resource == "memory_index":
                key = f"{user_base}:memory:index"
            elif resource == "memory_topic":
                key = f"{user_base}:memory:topic:{local_path.name}"
            else:
                safe_session_id = self.session_dir(session_id or "").name
                session_base = f"{self.config.redis_key_prefix}:{{{app_component}:{user_component}:{safe_session_id}}}"
                if resource == "transcript":
                    key = f"{session_base}:transcript"
                elif resource == "session_memory":
                    key = f"{session_base}:summary"
                else:
                    key = f"{session_base}:tool:{result_id}"
            return f"advanced-memory://redis/{key}"

        app_name = self.scope.app_name
        user_id = self.scope.user_id
        if resource == "memory_index":
            suffix = "memory/index"
        elif resource == "memory_topic":
            suffix = f"memory/topic/{local_path.name}"
        elif resource == "transcript":
            suffix = f"{session_id}/transcript"
        elif resource == "session_memory":
            suffix = f"{session_id}/summary"
        else:
            suffix = f"{session_id}/tool/{self.tool_result_path(session_id or '', result_id or '').stem}"
        return f"advanced-memory://sql/{app_name}/{user_id}/{suffix}"

    def ensure_base_directories(self) -> None:
        """Create the long-term and session memory directories."""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.session_root_dir.mkdir(parents=True, exist_ok=True)

    def ensure_session_directory(self, session_id: str) -> Path:
        """Create and return a session's storage directory."""
        path = self.session_dir(session_id)
        path.mkdir(parents=True, exist_ok=True)
        return path
