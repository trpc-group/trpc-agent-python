# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unified runtime entry point for the independent memory mechanism."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
import shutil
import threading
from typing import Any

from ._config import AdvancedMemoryServiceConfig
from trpc_agent_sdk.sessions.compact._coordination import CrossLoopLock
from trpc_agent_sdk.sessions.compact._coordination import SessionOperationCoordinator
from ._paths import AdvancedMemoryPaths
from ._paths import MemoryScope
from ._storage import LocalAdvancedMemoryCleanup
from ._storage import LongTermMemoryStore


@dataclass(frozen=True)
class AdvancedMemoryRuntime:
    """Aggregate configuration, paths, and long-term memory storage."""

    config: AdvancedMemoryServiceConfig
    paths: AdvancedMemoryPaths
    coordination: SessionOperationCoordinator
    long_term_memory: LongTermMemoryStore
    _scoped_runtimes: dict[MemoryScope, "ScopedAdvancedMemoryRuntime"] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )
    _scoped_runtimes_lock: threading.Lock = field(
        default_factory=threading.Lock,
        repr=False,
        compare=False,
    )
    _redis_storage: Any | None = field(default=None, repr=False, compare=False)
    _sql_storage: Any | None = field(default=None, repr=False, compare=False)
    _local_cleanup: LocalAdvancedMemoryCleanup | None = field(default=None, repr=False, compare=False)
    _close_lock: CrossLoopLock = field(
        default_factory=CrossLoopLock,
        repr=False,
        compare=False,
    )
    _closed: bool = field(default=False, repr=False, compare=False)

    @classmethod
    def create(cls, config: AdvancedMemoryServiceConfig | None = None) -> "AdvancedMemoryRuntime":
        """Create a runtime isolated from the legacy mechanism."""
        resolved_config = config or AdvancedMemoryServiceConfig()
        paths = AdvancedMemoryPaths(resolved_config)
        redis_storage = None
        sql_storage = None
        local_cleanup = None
        if resolved_config.storage_backend == "redis":
            from trpc_agent_sdk.storage import RedisStorage
            redis_storage = RedisStorage(redis_url=resolved_config.redis_url, is_async=resolved_config.redis_is_async)
        elif resolved_config.storage_backend == "sql":
            from trpc_agent_sdk.storage import SqlStorage
            from ._sql_stores import AdvancedMemorySqlBase
            sql_storage = SqlStorage(
                is_async=resolved_config.sql_is_async,
                db_url=resolved_config.sql_url,
                metadata=AdvancedMemorySqlBase.metadata,
                expire_on_commit=False,
            )
        else:
            local_cleanup = LocalAdvancedMemoryCleanup(resolved_config)
        return cls(
            config=resolved_config,
            paths=paths,
            coordination=SessionOperationCoordinator(),
            long_term_memory=LongTermMemoryStore(resolved_config, paths),
            _redis_storage=redis_storage,
            _sql_storage=sql_storage,
            _local_cleanup=local_cleanup,
        )

    def for_scope(self, app_name: str, user_id: str) -> "ScopedAdvancedMemoryRuntime":
        """Return the stores isolated to one application user."""
        scope = MemoryScope(app_name, user_id)
        with self._scoped_runtimes_lock:
            runtime = self._scoped_runtimes.get(scope)
            if runtime is None:
                paths = self.paths.for_scope(app_name, user_id)
                if self.config.storage_backend == "redis":
                    from trpc_agent_sdk.storage import RedisStorage
                    from ._redis_stores import RedisLongTermMemoryStore

                    storage = self._redis_storage or RedisStorage(
                        redis_url=self.config.redis_url,
                        is_async=self.config.redis_is_async,
                    )
                    long_term_memory = RedisLongTermMemoryStore(self.config, paths, storage)
                elif self.config.storage_backend == "sql":
                    from ._sql_stores import SqlLongTermMemoryStore
                    storage = self._sql_storage
                    if storage is None:
                        raise RuntimeError("SQL Advanced Memory storage is not initialized")
                    long_term_memory = SqlLongTermMemoryStore(self.config, paths, storage)
                else:
                    long_term_memory = LongTermMemoryStore(self.config, paths)
                runtime = ScopedAdvancedMemoryRuntime(
                    root=self,
                    scope=scope,
                    paths=paths,
                    long_term_memory=long_term_memory,
                )
                self._scoped_runtimes[scope] = runtime
            return runtime

    def for_session(self, session: object) -> "ScopedAdvancedMemoryRuntime":
        """Return the scoped runtime for a SessionABC-compatible object."""
        app_name = getattr(session, "app_name", None)
        user_id = getattr(session, "user_id", None)
        if not isinstance(app_name, str) or not isinstance(user_id, str):
            raise ValueError("Advanced Memory requires session app_name and user_id")
        return self.for_scope(app_name, user_id)

    def migrate_legacy(self, app_name: str, user_id: str) -> "ScopedAdvancedMemoryRuntime":
        """Move an old flat Advanced Memory layout into one explicit tenant.

        Refuses to overwrite a tenant that already contains data.
        """
        scoped = self.for_scope(app_name, user_id)
        legacy_paths = self.paths
        target_root = scoped.paths.tenant_root_dir
        if target_root.exists():
            raise FileExistsError(f"Target Advanced Memory tenant already exists: {target_root}")
        if not legacy_paths.memory_dir.exists():
            raise FileNotFoundError("No legacy Advanced Memory directories exist")
        target_root.mkdir(parents=True)
        if legacy_paths.memory_dir.exists():
            shutil.move(str(legacy_paths.memory_dir), str(scoped.paths.memory_dir))
        return scoped

    async def initialize(self) -> bool:
        """Create memory directories only when the mechanism is enabled."""
        if not self.config.enabled:
            return False
        if self.config.storage_backend == "sql":
            if self._sql_storage is None:
                raise RuntimeError("SQL Advanced Memory storage is not initialized")
            async with self._sql_storage.create_db_session():
                pass
            return True
        if self.config.storage_backend == "redis":
            return True
        if self._local_cleanup is not None:
            await self._local_cleanup.start()
        await self.long_term_memory.initialize()
        return True

    async def close(self) -> None:
        """Release shared external backend resources."""
        async with self._close_lock:
            if self._closed:
                return
            if self._local_cleanup is not None:
                await self._local_cleanup.close()
            if self._redis_storage is not None:
                await self._redis_storage.close()
            if self._sql_storage is not None:
                await self._sql_storage.close()
            object.__setattr__(self, "_closed", True)


@dataclass(frozen=True)
class ScopedAdvancedMemoryRuntime:
    """A tenant-bound view of an :class:`AdvancedMemoryRuntime`."""

    root: AdvancedMemoryRuntime
    scope: MemoryScope
    paths: AdvancedMemoryPaths
    long_term_memory: LongTermMemoryStore

    @property
    def config(self) -> AdvancedMemoryServiceConfig:
        """Return the root runtime configuration."""
        return self.root.config

    @property
    def coordination(self) -> SessionOperationCoordinator:
        """Return the shared coordinator."""
        return self.root.coordination

    def session_key(self, session_id: str) -> str:
        """Return a lock/cache key unique across all tenants."""
        return f"{self.scope.storage_key}\0{session_id}"

    async def initialize(self) -> bool:
        """Initialize only this tenant's local directories."""
        if not self.config.enabled:
            return False
        if self.config.storage_backend == "sql" and self.root._sql_cleanup is not None:
            await self.root._sql_cleanup.start()
        if self.config.storage_backend == "local" and self.root._local_cleanup is not None:
            await self.root._local_cleanup.start()
        await self.long_term_memory.initialize()
        return True
