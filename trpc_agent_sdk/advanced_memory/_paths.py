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
class AdvancedMemoryPaths:
    """Build all disk paths for long-term and session memory."""

    config: AdvancedMemoryConfig

    @property
    def memory_dir(self) -> Path:
        """Return the long-term memory directory."""
        return self.config.root_dir / self.config.memory_dir_name

    @property
    def session_root_dir(self) -> Path:
        """Return the root directory for session memory."""
        return self.config.root_dir / self.config.session_dir_name

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

    def ensure_base_directories(self) -> None:
        """Create the long-term and session memory directories."""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.session_root_dir.mkdir(parents=True, exist_ok=True)

    def ensure_session_directory(self, session_id: str) -> Path:
        """Create and return a session's storage directory."""
        path = self.session_dir(session_id)
        path.mkdir(parents=True, exist_ok=True)
        return path
