# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Configuration for the independent Advanced Memory mechanism."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Literal


def _require_positive(**values: int | float) -> None:
    """Require each named numeric setting to be greater than zero."""
    for name, value in values.items():
        if value <= 0:
            raise ValueError(f"{name} must be greater than zero")

def _validate_path_components(values: tuple[str, ...]) -> None:
    """Require safe, single-component names for memory storage paths."""
    for value in values:
        if not value or Path(value).name != value:
            raise ValueError(f"Invalid memory path component: {value!r}")


@dataclass(frozen=True)
class AdvancedMemoryServiceConfig:
    """Configure the independent long-term Advanced Memory service."""

    enabled: bool = True
    root_dir: Path = field(default_factory=Path.cwd)
    storage_backend: Literal["local", "redis", "sql"] = "local"
    redis_url: str | None = None
    redis_key_prefix: str = "advanced-memory:v1"
    redis_is_async: bool = True
    sql_url: str | None = None
    sql_is_async: bool = True
    sql_cleanup_interval_seconds: float = 60.0
    memory_ttl_seconds: int | None = None
    memory_lock_ttl_seconds: int = 30
    memory_lock_acquire_timeout_seconds: float = 10.0
    memory_dir_name: str = "MEMORY"
    memory_index_name: str = "MEMORY.md"
    memory_index_max_lines: int = 200
    memory_index_max_bytes: int = 25_000
    long_term_memory_injection_enabled: bool = True
    memory_focus_instruction: str | None = None
    encoding: str = "utf-8"
    preload_memory_enabled: bool = False
    preload_memory_max_topics: int = 5
    preload_memory_max_chars: int = 50_000
    preload_memory_candidate_limit: int = 200

    def __post_init__(self) -> None:
        """Validate the configuration and normalize the root directory."""
        if self.storage_backend not in {"local", "redis", "sql"}:
            raise ValueError("storage_backend must be one of: local, redis, sql")
        if self.storage_backend == "redis" and not self.redis_url:
            raise ValueError("redis_url is required when storage_backend='redis'")
        if self.storage_backend == "sql" and not self.sql_url:
            raise ValueError("sql_url is required when storage_backend='sql'")
        if not self.redis_key_prefix.strip() or self.redis_key_prefix != self.redis_key_prefix.strip():
            raise ValueError("redis_key_prefix must be a non-empty Redis key prefix")
        if self.memory_ttl_seconds is not None and self.memory_ttl_seconds <= 0:
            raise ValueError("memory_ttl_seconds must be greater than zero when provided")
        if self.memory_lock_ttl_seconds <= 0:
            raise ValueError("memory_lock_ttl_seconds must be greater than zero")
        if self.memory_lock_acquire_timeout_seconds <= 0:
            raise ValueError("memory_lock_acquire_timeout_seconds must be greater than zero")
        if self.sql_cleanup_interval_seconds <= 0:
            raise ValueError("sql_cleanup_interval_seconds must be greater than zero")
        _require_positive(
            memory_index_max_lines=self.memory_index_max_lines,
            memory_index_max_bytes=self.memory_index_max_bytes,
            preload_memory_max_topics=self.preload_memory_max_topics,
            preload_memory_max_chars=self.preload_memory_max_chars,
            preload_memory_candidate_limit=self.preload_memory_candidate_limit,
        )
        _validate_path_components((self.memory_dir_name, self.memory_index_name))
        object.__setattr__(self, "root_dir", self.root_dir.expanduser().resolve())
