# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Storage manager for trpc_claw."""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from sqlalchemy import MetaData
from sqlalchemy import Text
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from trpc_agent_sdk.context import get_invocation_ctx
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.storage import BaseStorage
from trpc_agent_sdk.storage import RedisCommand
from trpc_agent_sdk.storage import RedisStorage
from trpc_agent_sdk.storage import SqlKey
from trpc_agent_sdk.storage import SqlStorage

from ._aiofile_storage import AioFileStorage
from ._aiofile_storage import FileData
from ._constants import RAW_EVENTS_KEY
from ._constants import RECORD_METADATA
from ._constants import RECORD_RAW_EVENT
from ._utils import get_agent_context
from ._utils import make_memory_key


def build_session_memory_kv_model(md: MetaData):
    """Build a SQLAlchemy KV model bound to the target metadata."""

    class SessionMemoryBase(DeclarativeBase):
        __abstract__ = True
        metadata = md

    class SessionMemoryKV(SessionMemoryBase):
        __tablename__ = "session_memory_kv"
        key: Mapped[str] = mapped_column(Text, primary_key=True)
        value: Mapped[str] = mapped_column(Text, nullable=False)

    return SessionMemoryKV


class StorageManager:
    """Storage manager."""

    def __init__(self, storage: BaseStorage):
        if not hasattr(storage, "create_db_session"):
            raise TypeError("Storage must implement create_db_session() in addition to BaseStorage methods")
        self.storage = storage
        self._sql_kv_cls = None
        if isinstance(storage, SqlStorage):
            metadata = getattr(storage, "_SqlStorage__metadata", None)
            if metadata is None:
                raise ValueError("SqlStorage metadata is unavailable")
            self._sql_kv_cls = build_session_memory_kv_model(metadata)

    async def write_long_term(self, memory_key: str, content: str) -> None:
        """Overwrite ``MEMORY.md`` for *memory_key* with *content*.

        Args:
            memory_key: The memory key.
            content: The content to write.
        """
        await self._set_value(self._memory_content_key(memory_key), content)
        logger.debug("MEMORY.md updated for session %s (%d chars)", memory_key, len(content))

    async def append_history(self, memory_key: str, history_entry: str) -> None:
        """Append *history_entry* to ``HISTORY.md`` for *memory_key*.

        Args:
            memory_key: The memory key.
            history_entry: The history entry to append.
        """
        if not history_entry.strip():
            return
        key = self._history_content_key(memory_key)
        append_text = history_entry.rstrip() + "\n\n"
        previous = await self._get_value(key)
        current = str(previous) if previous is not None else ""

        await self._set_value(key, current + append_text)
        logger.debug("HISTORY.md appended for session %s", memory_key)

    async def read_long_term(self, memory_key: str) -> str:
        """Return the contents of ``MEMORY.md`` for *memory_key*, or ``""``.

        Args:
            memory_key: The memory key.

        Returns:
            str: The contents of ``MEMORY.md`` for *memory_key*, or ``""``.
        """
        value = await self._get_value(self._memory_content_key(memory_key))
        if value is None:
            return ""
        return str(value)

    async def save_session(self, path: Path, session: Session) -> None:
        """Write *session* to *path* in JSONL format (overwrites existing file).

        Args:
            path: The path to write the session to.
            session: The session to write.
        """
        storage_key = self._session_storage_key(path)
        invocation_ctx = get_invocation_ctx()
        if invocation_ctx:
            agent_context = invocation_ctx.agent_context
        else:
            agent_context = get_agent_context()
        meta: dict[str, Any] = {
            "_type": RECORD_METADATA,
            "app_name": session.app_name,
            "user_id": session.user_id,
            "id": session.id,
            "state": session.state,
            "conversation_count": session.conversation_count,
            "last_update_time": session.last_update_time,
            "save_key": session.save_key,
            "saved_at": datetime.now().isoformat(),
            # Keep model-visible recent window recoverable from full raw archive.
            "recent_event_ids": [e.id for e in session.events if getattr(e, "id", None)],
            "recent_event_count": len(session.events),
        }
        lines: list[str] = [json.dumps(meta, ensure_ascii=False)]
        raw_events = agent_context.get_metadata(RAW_EVENTS_KEY, []) if agent_context else []
        for ev in raw_events:
            lines.append(json.dumps({"_type": RECORD_RAW_EVENT, "data": self._dump_event(ev)}, ensure_ascii=False))
        await self._set_value(storage_key, "\n".join(lines) + "\n")

    async def load_session(
        self,
        path: Path,
        app_name: str,
        user_id: str,
        session_id: str,
    ) -> Session | None:
        """Read and reconstruct a :class:`Session` from *path*.  Returns *None* on failure.

        Args:
            path: The path to read the session from.
            app_name: The app name.
            user_id: The user ID.
            session_id: The session ID.

        Returns:
            Session | None: The loaded session, or None if the session is not valid.
        """
        raw = await self._get_value(self._session_storage_key(path))
        if raw is None:
            logger.debug("Session file %s not found", path)
            return None
        agent_context = get_agent_context()
        try:
            metadata: dict[str, Any] = {}
            raw_events: list[Event] = []
            for raw_line in str(raw).splitlines():
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                record = json.loads(raw_line)
                rtype = record.get("_type")
                if rtype == RECORD_METADATA:
                    metadata = record
                elif rtype == RECORD_RAW_EVENT:
                    ev = self._load_event(record.get("data", {}))
                    if ev:
                        raw_events.append(ev)
            recent_events = self._extract_recent_events(raw_events, metadata)
            agent_context.with_metadata(RAW_EVENTS_KEY, raw_events)
            return Session(
                id=session_id,
                app_name=app_name,
                user_id=user_id,
                state=metadata.get("state", {}),
                events=recent_events,
                conversation_count=metadata.get("conversation_count", 0),
                last_update_time=metadata.get("last_update_time", time.time()),
                save_key=metadata.get("save_key") or make_memory_key(app_name, user_id, session_id),
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Failed to load Session from {}: {}", path, exc)
            return None

    async def _set_value(self, key: str, value: Any) -> None:
        async with self.storage.create_db_session() as db:
            if isinstance(self.storage, SqlStorage):
                if self._sql_kv_cls is None:
                    raise ValueError("Sql KV model is not initialized")
                sql_key = SqlKey(key=(key, ), storage_cls=self._sql_kv_cls)
                existing = await self.storage.get(db, sql_key)
                if existing is not None:
                    existing.value = self._serialize_value(value)
                else:
                    await self.storage.add(db, self._sql_kv_cls(key=key, value=self._serialize_value(value)))
            else:
                payload = self._build_add_payload(key, value)
                await self.storage.add(db, payload)
            await self.storage.commit(db)

    async def _get_value(self, key: str) -> Any:
        async with self.storage.create_db_session() as db:
            get_key = self._build_get_key(key)
            raw = await self.storage.get(db, get_key)
            return self._extract_get_value(key, raw)

    def _build_add_payload(self, key: str, value: Any) -> Any:
        if isinstance(self.storage, AioFileStorage):
            return FileData(key=key, value=value)
        if isinstance(self.storage, RedisStorage):
            return RedisCommand(method="set", args=(key, self._serialize_value(value)))
        return {"key": key, "value": value}

    def _build_get_key(self, key: str) -> Any:
        if isinstance(self.storage, RedisStorage):
            return RedisCommand(method="get", args=(key, ))
        if isinstance(self.storage, SqlStorage):
            if self._sql_kv_cls is None:
                raise ValueError("Sql KV model is not initialized")
            return SqlKey(key=(key, ), storage_cls=self._sql_kv_cls)
        return key

    def _extract_get_value(self, key: str, raw: Any) -> Any:
        if isinstance(self.storage, SqlStorage):
            if raw is None:
                return None
            if self._is_text_storage_key(key):
                return raw.value
            return self._deserialize_value(raw.value)
        if isinstance(self.storage, RedisStorage):
            if self._is_text_storage_key(key):
                if raw is None:
                    return None
                if isinstance(raw, bytes):
                    return raw.decode("utf-8", errors="replace")
                return str(raw)
            return self._deserialize_value(raw)
        return raw

    @staticmethod
    def _serialize_value(value: Any) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False, default=str)

    @staticmethod
    def _deserialize_value(raw: Any) -> Any:
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        if not isinstance(raw, str):
            return raw
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    @staticmethod
    def _memory_content_key(memory_key: str) -> str:
        return f"memory:{quote(memory_key, safe='')}"

    @staticmethod
    def _history_content_key(memory_key: str) -> str:
        return f"history:{quote(memory_key, safe='')}"

    @staticmethod
    def _session_storage_key(path: Path) -> str:
        return f"session:{quote(str(path), safe='')}"

    @staticmethod
    def _is_text_storage_key(key: str) -> bool:
        return key.startswith(("memory:", "history:", "session:"))

    @staticmethod
    def _extract_recent_events(raw_events: list[Event], metadata: dict[str, Any]) -> list[Event]:
        """Recover model-visible recent events from full raw archive."""
        if not raw_events:
            return []
        recent_ids = metadata.get("recent_event_ids") or []
        if isinstance(recent_ids, list) and recent_ids:
            id_set = {str(i) for i in recent_ids}
            selected = [ev for ev in raw_events if str(getattr(ev, "id", "")) in id_set]
            if selected:
                return selected
        count = int(metadata.get("recent_event_count") or 0)
        if count > 0:
            return raw_events[-count:]
        return []

    @classmethod
    def _dump_event(cls, event: Event) -> dict[str, Any]:
        """Dump an event to a dictionary.

        Args:
            event: The event to dump.

        Returns:
            dict[str, Any]: The dumped event.
        """
        data = event.model_dump(mode="json", exclude_none=True)
        return cls._sanitize_event_for_storage(data)

    @classmethod
    def _sanitize_event_for_storage(cls, event_data: dict[str, Any]) -> dict[str, Any]:
        """Align with nanobot memory behavior for image attachments.

        nanobot stores image user content as lightweight placeholders in session
        history (e.g. ``[image]``) instead of persisting full base64 payloads.
        Do the same here before writing JSONL records to avoid ballooning files.

        Args:
            event_data: The event data to sanitize.

        Returns:
            dict[str, Any]: The sanitized event data.
        """
        author = str(event_data.get("author", "")).lower()
        if author != "user":
            return event_data

        content = event_data.get("content")
        if not isinstance(content, dict):
            return event_data

        parts = content.get("parts")
        if not isinstance(parts, list):
            return event_data

        sanitized_parts: list[dict[str, Any]] = []
        changed = False
        for part in parts:
            if not isinstance(part, dict):
                sanitized_parts.append(part)
                continue

            inline = part.get("inline_data") or part.get("inlineData")
            mime_type = ""
            if isinstance(inline, dict):
                mime_type = str(inline.get("mime_type") or inline.get("mimeType") or "")

            if mime_type.startswith("image/"):
                sanitized_parts.append({"text": "[image]"})
                changed = True
                continue

            sanitized_parts.append(part)

        if not changed:
            return event_data

        content["parts"] = sanitized_parts
        event_data["content"] = content
        return event_data

    @classmethod
    def _load_event(cls, data: dict[str, Any]) -> Event | None:
        """Load an event from a dictionary.

        Args:
            data: The event data to load.

        Returns:
            Event | None: The loaded event, or None if the event is not valid.
        """
        try:
            return Event.model_validate(data)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Failed to deserialize event: {}", exc)
            return None
