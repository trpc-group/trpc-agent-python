"""SQL implementations of the Advanced Memory storage contracts."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from trpc_agent_sdk.storage import (
    DEFAULT_MAX_KEY_LENGTH,
    DEFAULT_MAX_VARCHAR_LENGTH,
    PreciseTimestamp,
    SqlCondition,
    SqlKey,
    SqlStorage,
)

from ._config import AdvancedMemoryServiceConfig
from ._formats import MemoryDocument, MemoryIndexEntry
from ._formats import limit_memory_index
from ._paths import AdvancedMemoryPaths
from ._storage import prune_memory_index


class AdvancedMemorySqlBase(DeclarativeBase):
    """Metadata owned exclusively by Advanced Memory SQL stores."""


class SqlMemoryIndex(AdvancedMemorySqlBase):
    __tablename__ = "advanced_memory_indexes"

    app_name: Mapped[str] = mapped_column(String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    content: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(PreciseTimestamp, default=func.now(), onupdate=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SqlMemoryTopic(AdvancedMemorySqlBase):
    __tablename__ = "advanced_memory_topics"

    app_name: Mapped[str] = mapped_column(String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    topic_name: Mapped[str] = mapped_column(String(DEFAULT_MAX_VARCHAR_LENGTH), primary_key=True)
    content: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(PreciseTimestamp, default=func.now(), onupdate=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class _SqlStore:

    def __init__(
        self,
        config: AdvancedMemoryServiceConfig,
        paths: AdvancedMemoryPaths,
        storage: SqlStorage,
    ) -> None:
        if paths.scope is None:
            raise ValueError("SQL Advanced Memory storage requires a tenant scope")
        self._config = config
        self._paths = paths
        self._storage = storage
        self._app_name = paths.scope.app_name
        self._user_id = paths.scope.user_id

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)

    def _expiry(self, ttl: int | None) -> datetime | None:
        return self._now() + timedelta(seconds=ttl) if ttl is not None else None

    @staticmethod
    def _expired(value: datetime | None) -> bool:
        if value is None:
            return False
        return value.replace(tzinfo=None) <= datetime.now(timezone.utc).replace(tzinfo=None)

    async def initialize(self) -> None:
        async with self._storage.create_db_session():
            pass

    async def _refresh_memory_scope(self, db: Any) -> None:
        expiry = self._expiry(self._config.memory_ttl_seconds)
        if expiry is None:
            return
        index = await self._storage.get(db, SqlKey(
            key=(self._app_name, self._user_id),
            storage_cls=SqlMemoryIndex,
        ))
        if index is not None:
            index.expires_at = expiry
        topics = await self._storage.query(
            db,
            SqlKey(key=(self._app_name, self._user_id), storage_cls=SqlMemoryTopic),
            SqlCondition(filters=[
                SqlMemoryTopic.app_name == self._app_name,
                SqlMemoryTopic.user_id == self._user_id,
                SqlMemoryTopic.expires_at.is_(None) | (SqlMemoryTopic.expires_at > self._now()),
            ]),
        )
        for topic in topics:
            topic.expires_at = expiry


class SqlLongTermMemoryStore(_SqlStore):

    async def initialize(self) -> None:
        await super().initialize()
        async with self._storage.create_db_session() as db:
            key = SqlKey(key=(self._app_name, self._user_id), storage_cls=SqlMemoryIndex)
            row = await self._storage.get(db, key)
            if row is None:
                await self._storage.add(
                    db,
                    SqlMemoryIndex(
                        app_name=self._app_name,
                        user_id=self._user_id,
                        content="",
                        expires_at=self._expiry(self._config.memory_ttl_seconds),
                    ))
                await self._storage.commit(db)

    async def read_index(self) -> str:
        async with self._storage.create_db_session() as db:
            row = await self._storage.get(db, SqlKey(key=(self._app_name, self._user_id), storage_cls=SqlMemoryIndex))
            if row is None or self._expired(row.expires_at):
                return ""
            await self._refresh_memory_scope(db)
            content = row.content
            valid_topics = await self._storage.query(
                db,
                SqlKey(key=(self._app_name, self._user_id), storage_cls=SqlMemoryTopic),
                SqlCondition(filters=[
                    SqlMemoryTopic.app_name == self._app_name,
                    SqlMemoryTopic.user_id == self._user_id,
                    SqlMemoryTopic.expires_at.is_(None) | (SqlMemoryTopic.expires_at > self._now()),
                ]),
            )
            valid_filenames = {topic.topic_name for topic in valid_topics}
            pruned_content = prune_memory_index(content, valid_filenames)
            if pruned_content != content:
                row.content = pruned_content
                content = pruned_content
            await self._storage.commit(db)
        return limit_memory_index(
            content,
            max_lines=self._config.memory_index_max_lines,
            max_bytes=self._config.memory_index_max_bytes,
            encoding=self._config.encoding,
        )

    async def write_index(self, entries: list[MemoryIndexEntry]) -> None:
        content = "\n".join(entry.to_markdown() for entry in entries)
        if content:
            content += "\n"
        async with self._storage.create_db_session() as db:
            # Keep the tenant's lock row locked until this transaction commits.
            await self._storage.get_for_update(
                db,
                SqlKey(key=(self._app_name, self._user_id), storage_cls=SqlMemoryIndex),
            )
            key = SqlKey(key=(self._app_name, self._user_id), storage_cls=SqlMemoryIndex)
            row = await self._storage.get(db, key)
            if row is None:
                row = SqlMemoryIndex(app_name=self._app_name, user_id=self._user_id)
                await self._storage.add(db, row)
            row.content = content
            row.expires_at = self._expiry(self._config.memory_ttl_seconds)
            await self._refresh_memory_scope(db)
            await self._storage.commit(db)

    def _topic_key(self, topic_name: str) -> tuple[str, str, str]:
        return self._app_name, self._user_id, self._paths.memory_topic_path(topic_name).name

    async def read_topic(self, topic_name: str) -> str | None:
        async with self._storage.create_db_session() as db:
            row = await self._storage.get(db, SqlKey(key=self._topic_key(topic_name), storage_cls=SqlMemoryTopic))
            if row is None or self._expired(row.expires_at):
                return None
            await self._refresh_memory_scope(db)
            await self._storage.commit(db)
            return row.content

    async def read_topic_frontmatter(self, topic_name: str) -> str | None:
        content = await self.read_topic(topic_name)
        if content is None:
            return None
        end = content.find("\n---", 4) if content.startswith("---\n") else -1
        return content[:end + 4] if end >= 0 else content

    async def write_topic(self, topic_name: str, document: MemoryDocument) -> Path:
        name = self._paths.memory_topic_path(topic_name).name
        async with self._storage.create_db_session() as db:
            # Serialize all long-term writes for this app/user scope.
            await self._storage.get_for_update(
                db,
                SqlKey(key=(self._app_name, self._user_id), storage_cls=SqlMemoryIndex),
            )
            key = self._topic_key(name)
            row = await self._storage.get(db, SqlKey(key=key, storage_cls=SqlMemoryTopic))
            if row is None:
                row = SqlMemoryTopic(app_name=key[0], user_id=key[1], topic_name=key[2])
                await self._storage.add(db, row)
            row.content = replace(document, updated_at=self._now().replace(tzinfo=timezone.utc)).to_markdown()
            row.updated_at = self._now()
            row.expires_at = self._expiry(self._config.memory_ttl_seconds)
            await self._refresh_memory_scope(db)
            await self._storage.commit(db)
        return Path(name)

    async def list_topics(self) -> list[Path]:
        async with self._storage.create_db_session() as db:
            rows = await self._storage.query(
                db,
                SqlKey(key=(self._app_name, self._user_id), storage_cls=SqlMemoryTopic),
                SqlCondition(filters=[
                    SqlMemoryTopic.app_name == self._app_name,
                    SqlMemoryTopic.user_id == self._user_id,
                ]),
            )
            rows = [row for row in rows if not self._expired(row.expires_at)]
            await self._refresh_memory_scope(db)
            await self._storage.commit(db)
            return [Path(row.topic_name) for row in sorted(rows, key=lambda item: item.topic_name)]


class SqlAdvancedMemoryCleanup:
    """Periodically remove expired Advanced Memory SQL rows."""

    _models = (
        SqlMemoryIndex,
        SqlMemoryTopic,
    )

    def __init__(self, config: AdvancedMemoryServiceConfig, storage: SqlStorage) -> None:
        self._config = config
        self._storage = storage
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None

    async def start(self) -> None:
        if self._task is not None or self._config.memory_ttl_seconds is None:
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run())

    async def cleanup_once(self) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        async with self._storage.create_db_session() as db:
            for model in self._models:
                await self._storage.delete(
                    db,
                    SqlKey(key=tuple(), storage_cls=model),
                    SqlCondition(filters=[model.expires_at.is_not(None), model.expires_at <= now]),
                )
            indexes = await self._storage.query(
                db,
                SqlKey(key=tuple(), storage_cls=SqlMemoryIndex),
            )
            for index in indexes:
                topics = await self._storage.query(
                    db,
                    SqlKey(
                        key=(index.app_name, index.user_id),
                        storage_cls=SqlMemoryTopic,
                    ),
                    SqlCondition(filters=[
                        SqlMemoryTopic.app_name == index.app_name,
                        SqlMemoryTopic.user_id == index.user_id,
                        SqlMemoryTopic.expires_at.is_(None) | (SqlMemoryTopic.expires_at > now),
                    ]),
                )
                valid_filenames = {topic.topic_name for topic in topics}
                index.content = prune_memory_index(index.content, valid_filenames)
            await self._storage.commit(db)

    async def _run(self) -> None:
        if self._stop_event is None:
            return
        try:
            while not self._stop_event.is_set():
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._config.sql_cleanup_interval_seconds,
                    )
                except asyncio.TimeoutError:
                    await self.cleanup_once()
        except asyncio.CancelledError:
            raise

    async def close(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._stop_event = None


__all__ = [
    "AdvancedMemorySqlBase",
    "SqlAdvancedMemoryCleanup",
    "SqlLongTermMemoryStore",
]
