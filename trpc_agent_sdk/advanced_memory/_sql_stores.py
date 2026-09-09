"""SQL implementations of the Advanced Memory storage contracts."""

from __future__ import annotations

import json
import asyncio
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path
from collections.abc import Mapping
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

from ._config import AdvancedMemoryConfig
from ._formats import MemoryDocument, MemoryIndexEntry, SessionMemoryDocument
from ._paths import AdvancedMemoryPaths


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


class SqlSessionMemory(AdvancedMemorySqlBase):
    __tablename__ = "advanced_memory_session_memory"

    app_name: Mapped[str] = mapped_column(String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    content: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(PreciseTimestamp, default=func.now(), onupdate=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SqlTranscript(AdvancedMemorySqlBase):
    __tablename__ = "advanced_memory_transcripts"

    app_name: Mapped[str] = mapped_column(String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    record_id: Mapped[str] = mapped_column(String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    payload: Mapped[str] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(PreciseTimestamp, default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SqlTranscriptSeen(AdvancedMemorySqlBase):
    __tablename__ = "advanced_memory_transcript_seen"

    dedupe_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    app_name: Mapped[str] = mapped_column(String(DEFAULT_MAX_KEY_LENGTH), index=True)
    user_id: Mapped[str] = mapped_column(String(DEFAULT_MAX_KEY_LENGTH), index=True)
    session_id: Mapped[str] = mapped_column(String(DEFAULT_MAX_KEY_LENGTH), index=True)
    unique_key: Mapped[str] = mapped_column(String(DEFAULT_MAX_VARCHAR_LENGTH))
    unique_value: Mapped[str] = mapped_column(String(DEFAULT_MAX_VARCHAR_LENGTH))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SqlToolResult(AdvancedMemorySqlBase):
    __tablename__ = "advanced_memory_tool_results"

    app_name: Mapped[str] = mapped_column(String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    result_id: Mapped[str] = mapped_column(String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    content: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(PreciseTimestamp, default=func.now(), onupdate=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class _SqlStore:
    def __init__(self, config: AdvancedMemoryConfig, paths: AdvancedMemoryPaths, storage: SqlStorage) -> None:
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

    async def _refresh_session_scope(self, db: Any, session_id: str) -> None:
        expiry = self._expiry(self._config.session_ttl_seconds)
        if expiry is None:
            return
        tables = (
            (SqlSessionMemory, (self._app_name, self._user_id, session_id)),
            (SqlTranscript, (self._app_name, self._user_id, session_id)),
            (SqlTranscriptSeen, (self._app_name, self._user_id, session_id)),
            (SqlToolResult, (self._app_name, self._user_id, session_id)),
        )
        for model, key in tables:
            rows = await self._storage.query(
                db,
                SqlKey(key=key, storage_cls=model),
                SqlCondition(filters=[
                    getattr(model, "app_name") == self._app_name,
                    getattr(model, "user_id") == self._user_id,
                    getattr(model, "session_id") == session_id,
                    getattr(model, "expires_at").is_(None) | (getattr(model, "expires_at") > self._now()),
                ]),
            )
            for row in rows:
                row.expires_at = expiry

    async def delete_session(self, session_id: str) -> None:
        """Delete all Advanced Memory rows for one session."""
        models = (
            SqlSessionMemory,
            SqlTranscript,
            SqlTranscriptSeen,
            SqlToolResult,
        )
        filters = {
            SqlSessionMemory: [
                SqlSessionMemory.app_name == self._app_name,
                SqlSessionMemory.user_id == self._user_id,
                SqlSessionMemory.session_id == session_id,
            ],
            SqlTranscript: [
                SqlTranscript.app_name == self._app_name,
                SqlTranscript.user_id == self._user_id,
                SqlTranscript.session_id == session_id,
            ],
            SqlTranscriptSeen: [
                SqlTranscriptSeen.app_name == self._app_name,
                SqlTranscriptSeen.user_id == self._user_id,
                SqlTranscriptSeen.session_id == session_id,
            ],
            SqlToolResult: [
                SqlToolResult.app_name == self._app_name,
                SqlToolResult.user_id == self._user_id,
                SqlToolResult.session_id == session_id,
            ],
        }
        async with self._storage.create_db_session() as db:
            for model in models:
                await self._storage.delete(
                    db,
                    SqlKey(key=tuple(), storage_cls=model),
                    SqlCondition(filters=filters[model]),
                )
            await self._storage.commit(db)


class SqlLongTermMemoryStore(_SqlStore):
    async def initialize(self) -> None:
        await super().initialize()
        async with self._storage.create_db_session() as db:
            key = SqlKey(key=(self._app_name, self._user_id), storage_cls=SqlMemoryIndex)
            row = await self._storage.get(db, key)
            if row is None:
                await self._storage.add(db, SqlMemoryIndex(
                    app_name=self._app_name,
                    user_id=self._user_id,
                    content="",
                    expires_at=self._expiry(self._config.memory_ttl_seconds),
                ))
                await self._storage.commit(db)

    async def read_index(self) -> str:
        async with self._storage.create_db_session() as db:
            row = await self._storage.get(
                db, SqlKey(key=(self._app_name, self._user_id), storage_cls=SqlMemoryIndex)
            )
            if row is None or self._expired(row.expires_at):
                return ""
            await self._refresh_memory_scope(db)
            await self._storage.commit(db)
            content = row.content
        lines, used_bytes = [], 0
        for line in content.splitlines(keepends=True)[:self._config.memory_index_max_lines]:
            size = len(line.encode(self._config.encoding))
            if used_bytes + size > self._config.memory_index_max_bytes:
                break
            lines.append(line)
            used_bytes += size
        return "".join(lines)

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


class SqlSessionMemoryStore(_SqlStore):
    async def read(self, session_id: str) -> str | None:
        async with self._storage.create_db_session() as db:
            row = await self._storage.get(
                db, SqlKey(key=(self._app_name, self._user_id, session_id), storage_cls=SqlSessionMemory)
            )
            if row is None or self._expired(row.expires_at):
                return None
            await self._refresh_session_scope(db, session_id)
            await self._storage.commit(db)
            return row.content

    async def write(self, session_id: str, document: SessionMemoryDocument) -> Path:
        async with self._storage.create_db_session() as db:
            key = (self._app_name, self._user_id, session_id)
            row = await self._storage.get(db, SqlKey(key=key, storage_cls=SqlSessionMemory))
            if row is None:
                row = SqlSessionMemory(app_name=key[0], user_id=key[1], session_id=key[2])
                await self._storage.add(db, row)
            row.content = document.to_markdown()
            row.updated_at = self._now()
            row.expires_at = self._expiry(self._config.session_ttl_seconds)
            await self._refresh_session_scope(db, session_id)
            await self._storage.commit(db)
        return Path(f"advanced-memory://sql/{self._app_name}/{self._user_id}/{session_id}/summary")


class SqlToolResultStore(_SqlStore):
    async def write(self, session_id: str, result_id: str, serialized_result: str) -> Path:
        async with self._storage.create_db_session() as db:
            key = (self._app_name, self._user_id, session_id, result_id)
            row = await self._storage.get(db, SqlKey(key=key, storage_cls=SqlToolResult))
            if row is None:
                row = SqlToolResult(
                    app_name=key[0], user_id=key[1], session_id=key[2], result_id=key[3],
                )
                await self._storage.add(db, row)
            row.content = serialized_result
            row.updated_at = self._now()
            row.expires_at = self._expiry(self._config.session_ttl_seconds)
            await self._refresh_session_scope(db, session_id)
            await self._storage.commit(db)
        return Path(f"advanced-memory://sql/{self._app_name}/{self._user_id}/{session_id}/tool/{result_id}")

    async def read(self, session_id: str, result_id: str) -> str | None:
        async with self._storage.create_db_session() as db:
            row = await self._storage.get(
                db,
                SqlKey(key=(self._app_name, self._user_id, session_id, result_id), storage_cls=SqlToolResult),
            )
            if row is None or self._expired(row.expires_at):
                return None
            await self._refresh_session_scope(db, session_id)
            await self._storage.commit(db)
            return row.content


class SqlTranscriptStore(_SqlStore):
    def _dedupe_id(self, session_id: str, unique_key: str, value: str) -> str:
        raw = "\0".join((self._app_name, self._user_id, session_id, unique_key, value))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def append(self, session_id: str, record: Mapping[str, Any]) -> Path:
        payload = dict(record)
        payload.setdefault("recorded_at", self._now().replace(tzinfo=timezone.utc).isoformat())
        async with self._storage.create_db_session() as db:
            await self._storage.add(db, SqlTranscript(
                app_name=self._app_name,
                user_id=self._user_id,
                session_id=session_id,
                record_id=uuid.uuid4().hex,
                payload=json.dumps(payload, ensure_ascii=False),
                expires_at=self._expiry(self._config.session_ttl_seconds),
            ))
            await self._refresh_session_scope(db, session_id)
            await self._storage.commit(db)
        return Path(f"advanced-memory://sql/{self._app_name}/{self._user_id}/{session_id}/transcript")

    async def append_unique(
        self,
        session_id: str,
        record: Mapping[str, Any],
        *,
        unique_key: str,
    ) -> tuple[Path, bool]:
        payload = dict(record)
        value = payload.get(unique_key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"Transcript unique key {unique_key!r} must be a non-empty string")
        async with self._storage.create_db_session() as db:
            dedupe_id = self._dedupe_id(session_id, unique_key, value)
            seen_key = (self._app_name, self._user_id, session_id, unique_key, value)
            seen = await self._storage.get(
                db,
                SqlKey(key=(dedupe_id,), storage_cls=SqlTranscriptSeen),
            )
            if seen is not None and not self._expired(seen.expires_at):
                await self._refresh_session_scope(db, session_id)
                await self._storage.commit(db)
                return Path(f"advanced-memory://sql/{self._app_name}/{self._user_id}/{session_id}/transcript"), False
            if seen is not None:
                await self._storage.delete(
                    db,
                    SqlKey(key=(dedupe_id,), storage_cls=SqlTranscriptSeen),
                    SqlCondition(filters=[
                        SqlTranscriptSeen.dedupe_id == dedupe_id,
                    ]),
                )
            payload.setdefault("recorded_at", self._now().replace(tzinfo=timezone.utc).isoformat())
            await self._storage.add(db, SqlTranscriptSeen(
                dedupe_id=dedupe_id,
                app_name=seen_key[0],
                user_id=seen_key[1],
                session_id=seen_key[2],
                unique_key=seen_key[3],
                unique_value=seen_key[4],
                expires_at=self._expiry(self._config.session_ttl_seconds),
            ))
            await self._storage.add(db, SqlTranscript(
                app_name=self._app_name,
                user_id=self._user_id,
                session_id=session_id,
                record_id=uuid.uuid4().hex,
                payload=json.dumps(payload, ensure_ascii=False),
                expires_at=self._expiry(self._config.session_ttl_seconds),
            ))
            await self._refresh_session_scope(db, session_id)
            await self._storage.commit(db)
        return Path(f"advanced-memory://sql/{self._app_name}/{self._user_id}/{session_id}/transcript"), True

    async def read_all(self, session_id: str) -> list[dict[str, Any]]:
        async with self._storage.create_db_session() as db:
            rows = await self._storage.query(
                db,
                SqlKey(key=(self._app_name, self._user_id, session_id), storage_cls=SqlTranscript),
                SqlCondition(filters=[
                    SqlTranscript.app_name == self._app_name,
                    SqlTranscript.user_id == self._user_id,
                    SqlTranscript.session_id == session_id,
                    SqlTranscript.expires_at.is_(None) | (SqlTranscript.expires_at > self._now()),
                ], order_func=SqlTranscript.recorded_at.asc),
            )
            await self._refresh_session_scope(db, session_id)
            await self._storage.commit(db)
            return [json.loads(row.payload) for row in rows]


class SqlAdvancedMemoryCleanup:
    """Periodically remove expired Advanced Memory SQL rows."""

    _models = (
        SqlMemoryIndex,
        SqlMemoryTopic,
        SqlSessionMemory,
        SqlTranscript,
        SqlTranscriptSeen,
        SqlToolResult,
    )

    def __init__(self, config: AdvancedMemoryConfig, storage: SqlStorage) -> None:
        self._config = config
        self._storage = storage
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None

    async def start(self) -> None:
        if self._task is not None or (
            self._config.memory_ttl_seconds is None
            and self._config.session_ttl_seconds is None
        ):
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
    "SqlSessionMemoryStore",
    "SqlToolResultStore",
    "SqlTranscriptStore",
]
