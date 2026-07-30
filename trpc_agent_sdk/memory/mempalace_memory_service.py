# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""MemPalace-based memory service for local-first semantic memory."""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from datetime import datetime
from typing import Any
from typing import Optional
from typing_extensions import override

from mempalace.config import MempalaceConfig  # type: ignore[import-not-found]
from mempalace.config import sanitize_content  # type: ignore[import-not-found]
from mempalace.config import sanitize_name  # type: ignore[import-not-found]
from mempalace.palace import get_collection  # type: ignore[import-not-found]
from mempalace.searcher import search_memories  # type: ignore[import-not-found]

from trpc_agent_sdk.abc import MemoryEntry
from trpc_agent_sdk.abc import MemoryServiceABC as BaseMemoryService
from trpc_agent_sdk.abc import MemoryServiceConfig
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.types import SearchMemoryResponse

from ._utils import format_timestamp
from ._utils import event_to_text

_MEMPALACE_KEY_METADATA = "mempalace_metadata"
_DEFAULT_ROOM = "conversations"
_DEFAULT_WING = "trpc_agent"
_DEFAULT_ADDED_BY = "trpc_agent"
_EVENT_TEXT_PREFIX_RE = re.compile(r"^\[([^\]]+)\]\s+([^:\n]+):\n")

__all__ = [
    "MempalaceMemoryService",
    "get_mempalace_filters",
    "set_mempalace_filters",
]


def set_mempalace_filters(agent_context: AgentContext, filters: dict[str, Any]) -> None:
    """Set MemPalace wing/room filters into agent_context."""
    if agent_context:
        agent_context.with_metadata(_MEMPALACE_KEY_METADATA, filters)


def get_mempalace_filters(agent_context: Optional[AgentContext] = None) -> dict[str, Any]:
    """Get MemPalace wing/room filters from agent_context."""
    filters: dict[str, Any] = {}
    if agent_context:
        filters.update(agent_context.get_metadata(_MEMPALACE_KEY_METADATA, {}))
    return filters


def _slugify_name(value: str, default: str) -> str:
    """Convert arbitrary framework keys into MemPalace-safe wing/room names."""
    value = (value or "").strip().lower()
    value = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff .'-]+", "_", value)
    value = re.sub(r"[_\s-]+", "_", value).strip("_. -'")
    return value[:128] or default


class MempalaceMemoryService(BaseMemoryService):
    """MemPalace-backed memory service.

    This implementation stores framework events as verbatim MemPalace drawers and
    searches them with MemPalace semantic search. MemPalace is an optional
    dependency; install it with ``pip install mempalace`` or the project extra.
    """

    def __init__(
        self,
        memory_service_config: Optional[MemoryServiceConfig] = None,
        config: Optional[MempalaceConfig] = None,
        wing: Optional[str] = None,
        room: str = _DEFAULT_ROOM,
        added_by: str = _DEFAULT_ADDED_BY,
        **kwargs: Any,
    ) -> None:
        super().__init__(memory_service_config=memory_service_config)
        self._config = config or MempalaceConfig()
        self._wing = wing
        self._room = room
        self._added_by = added_by
        self._pending_tasks: set[asyncio.Task[None]] = set()
        self._scheduled_drawer_ids: set[str] = set()
        self._stored_drawer_ids: set[str] = set()
        self.__cleanup_task: Optional[asyncio.Task] = None
        self.__cleanup_stop_event: Optional[asyncio.Event] = None
        self._start_cleanup_task()

    @override
    async def store_session(self, session: Session, agent_context: Optional[AgentContext] = None) -> None:
        """Store session events as verbatim MemPalace drawers."""
        filters = get_mempalace_filters(agent_context)
        wing = self._resolve_wing(session.save_key, filters)
        room = self._resolve_room(filters)

        events_to_store: list[tuple[Event, str, str]] = []
        for event in session.events:
            text = self._event_to_text(event)
            if not text:
                continue
            drawer_id = self._drawer_id(wing, room, event.id, text)
            if drawer_id in self._stored_drawer_ids or drawer_id in self._scheduled_drawer_ids:
                continue
            self._scheduled_drawer_ids.add(drawer_id)
            events_to_store.append((event, text, drawer_id))
        if not events_to_store:
            return

        task = asyncio.create_task(self._store_events_background(session, events_to_store, wing, room))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    @override
    async def search_memory(
        self,
        key: str,
        query: str,
        limit: int = 10,
        agent_context: Optional[AgentContext] = None,
    ) -> SearchMemoryResponse:
        """Search MemPalace by primary framework key plus optional filters."""
        response = SearchMemoryResponse()
        filters = get_mempalace_filters(agent_context)
        wing = self._resolve_wing(key, filters)
        room = filters.get("room", None)

        search_result = await self._search(query, wing, room, limit)
        for item in search_result.get("results", []):
            memory_text = item.get("text")
            if not memory_text:
                continue
            metadata = item.get("metadata") or item
            response.memories.append(
                MemoryEntry(
                    content=Content(parts=[Part.from_text(text=memory_text)], role="user"),
                    author=self._memory_author(metadata, memory_text),
                    timestamp=self._memory_timestamp(metadata, memory_text),
                ))
        return response

    @override
    async def close(self) -> None:
        """Stop cleanup task and wait for pending background writes."""
        self._stop_cleanup_task()
        await self._wait_pending_writes()

    async def _wait_pending_writes(self) -> None:
        """Wait for pending background writes."""
        if self._pending_tasks:
            await asyncio.gather(*self._pending_tasks, return_exceptions=True)

    async def delete_memory(self, wing: str, room: Optional[str] = None) -> int:
        """Delete MemPalace drawers by wing, optionally limited to a room.

        Args:
            wing: Wing to delete. For this service, this is usually the
                slugified save_key, i.e. ``{app}/{user}``.
            room: Optional room under the wing. If omitted, the whole wing is
                deleted.

        Returns:
            Number of matching drawers found before deletion.
        """
        await self._wait_pending_writes()
        try:
            deleted_count = await asyncio.to_thread(self._delete_memory, wing, room)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Failed to delete MemPalace memory. wing=%s, room=%s, err=%s", wing, room, exc)
            return 0

        # Deleting from storage invalidates the in-process dedupe cache. Keep it
        # conservative so deleted events can be written again later if needed.
        self._stored_drawer_ids.clear()
        self._scheduled_drawer_ids.clear()
        return deleted_count

    # ------------------------------------------------------------------
    # TTL eviction
    # ------------------------------------------------------------------

    def _start_cleanup_task(self) -> None:
        """Start the background TTL cleanup task if TTL is configured."""
        if not self._memory_service_config.ttl.need_ttl_expire():
            logger.debug("MemPalace memory cleanup task disabled (ttl is disabled)")
            return

        if self.__cleanup_task is not None:
            logger.debug("MemPalace memory cleanup task is already running")
            return

        self.__cleanup_stop_event = asyncio.Event()
        self.__cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.debug("MemPalace memory cleanup task created")

    def _stop_cleanup_task(self) -> None:
        """Stop the background TTL cleanup task."""
        if self.__cleanup_task is None:
            return

        if self.__cleanup_stop_event is not None:
            self.__cleanup_stop_event.set()

        if not self.__cleanup_task.done():
            self.__cleanup_task.cancel()

        self.__cleanup_task = None
        self.__cleanup_stop_event = None
        logger.debug("MemPalace memory cleanup task stopped")

    async def _cleanup_loop(self) -> None:
        """Periodic background loop that evicts expired memories."""
        logger.debug("MemPalace memory cleanup task started with interval: %ss",
                     self._memory_service_config.ttl.cleanup_interval_seconds)
        try:
            while not self.__cleanup_stop_event.is_set():
                try:
                    await asyncio.wait_for(
                        self.__cleanup_stop_event.wait(),
                        timeout=self._memory_service_config.ttl.cleanup_interval_seconds,
                    )
                    break
                except asyncio.TimeoutError:
                    try:
                        await self._cleanup_expired_memories()
                        logger.debug("MemPalace memory cleanup cycle completed")
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.error("Error during MemPalace memory cleanup: %s", exc, exc_info=True)
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("MemPalace memory cleanup loop encountered error: %s", exc, exc_info=True)
        finally:
            logger.debug("MemPalace memory cleanup task stopped")

    async def _cleanup_expired_memories(self) -> None:
        """Delete MemPalace drawers whose event timestamp has expired."""
        await self._wait_pending_writes()
        deleted_ids = await asyncio.to_thread(self._cleanup_expired_memories_sync)
        if deleted_ids:
            self._stored_drawer_ids.difference_update(deleted_ids)
            logger.info("MemPalace cleanup: deleted %s expired memories", len(deleted_ids))

    async def _store_events_background(
        self,
        session: Session,
        events_to_store: list[tuple[Event, str, str]],
        wing: str,
        room: str,
    ) -> None:
        """Store MemPalace drawers without blocking the caller."""
        drawer_ids = {drawer_id for _, _, drawer_id in events_to_store}
        stored_drawer_ids: set[str] = set()
        try:
            stored_drawer_ids = await asyncio.to_thread(self._store_events, session, events_to_store, wing, room)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Failed to store session in MemPalace. save_key=%s, session_id=%s, err=%s", session.save_key,
                           session.id, exc)
        finally:
            self._scheduled_drawer_ids.difference_update(drawer_ids)
            self._stored_drawer_ids.update(stored_drawer_ids)

    def _store_events(
        self,
        session: Session,
        events_to_store: list[tuple[Event, str, str]],
        wing: str,
        room: str,
    ) -> set[str]:
        """Synchronous MemPalace drawer upsert."""

        collection_name = self._config.collection_name
        col = get_collection(self._config.palace_path, collection_name=collection_name, create=True)

        stored_drawer_ids: set[str] = set()
        safe_wing = sanitize_name(wing, "wing")
        safe_room = sanitize_name(room, "room")
        for event, text, drawer_id in events_to_store:
            content = sanitize_content(text)
            source_file = f"{session.save_key}/{session.id}/{event.id}"
            metadata = {
                "wing": safe_wing,
                "room": safe_room,
                "source_file": source_file,
                "session_id": session.id,
                "event_id": event.id,
                "invocation_id": event.invocation_id,
                "author": event.author,
                "timestamp": format_timestamp(event.timestamp),
                "added_by": self._added_by,
                "filed_at": datetime.now().isoformat(),
                "chunk_index": 0,
            }
            try:
                existing = col.get(ids=[drawer_id])
                if existing and existing.get("ids"):
                    stored_drawer_ids.add(drawer_id)
                    continue
                col.upsert(ids=[drawer_id], documents=[content], metadatas=[metadata])
                stored_drawer_ids.add(drawer_id)
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning("Failed to store MemPalace drawer. drawer_id=%s, err=%s", drawer_id, exc)
        return stored_drawer_ids

    async def _search(self, query: str, wing: str, room: Optional[str], limit: int) -> dict[str, list[dict[str, Any]]]:
        """Synchronous MemPalace semantic search."""
        try:
            return await asyncio.to_thread(
                search_memories,
                query=query,
                palace_path=self._config.palace_path,
                wing=wing,
                room=room,
                n_results=limit,
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Failed to search MemPalace. query=%s, wing=%s, room=%s, err=%s", query, wing, room, exc)
            return {"results": []}

    def _delete_memory(self, wing: str, room: Optional[str] = None) -> int:
        """Synchronously delete MemPalace drawers by wing/room."""
        safe_wing = sanitize_name(_slugify_name(wing, _DEFAULT_WING), "wing")
        if room is None:
            where: dict[str, Any] = {"wing": safe_wing}
        else:
            safe_room = sanitize_name(_slugify_name(room, _DEFAULT_ROOM), "room")
            where = {"$and": [{"wing": safe_wing}, {"room": safe_room}]}

        col = get_collection(self._config.palace_path, collection_name=self._config.collection_name, create=False)
        existing = col.get(where=where)
        ids = existing.get("ids", []) if existing else []
        if ids:
            col.delete(where=where)
        return len(ids)

    def _cleanup_expired_memories_sync(self) -> set[str]:
        """Synchronously delete expired MemPalace drawers written by this service."""
        now = time.time()
        ttl_seconds = self._memory_service_config.ttl.ttl_seconds
        col = get_collection(self._config.palace_path, collection_name=self._config.collection_name, create=False)

        expired_ids: list[str] = []
        offset = 0
        batch_size = 500
        while True:
            batch = col.get(
                where={"added_by": self._added_by},
                include=["metadatas"],
                limit=batch_size,
                offset=offset,
            )
            ids = batch.get("ids", []) if batch else []
            metadatas = batch.get("metadatas", []) if batch else []
            if not ids:
                break

            for drawer_id, metadata in zip(ids, metadatas):
                metadata = metadata or {}
                ts = self._parse_memory_timestamp(metadata.get("timestamp"))
                if ts is not None and ts < now - ttl_seconds:
                    expired_ids.append(drawer_id)

            if len(ids) < batch_size:
                break
            offset += len(ids)

        for index in range(0, len(expired_ids), batch_size):
            col.delete(ids=expired_ids[index:index + batch_size])

        return set(expired_ids)

    def _resolve_wing(self, key: str, filters: dict[str, Any]) -> str:
        return _slugify_name(filters.get("wing", self._wing or key), _DEFAULT_WING)

    def _resolve_room(self, filters: dict[str, Any]) -> str:
        return _slugify_name(filters.get("room", self._room), _DEFAULT_ROOM)

    @staticmethod
    def _drawer_id(wing: str, room: str, event_id: str, content: str) -> str:
        digest = hashlib.sha256(f"{wing}|{room}|{event_id}|{content}".encode()).hexdigest()[:24]
        return f"drawer_{wing}_{room}_{digest}"

    @staticmethod
    def _memory_author(metadata: dict[str, Any], memory_text: str = "") -> str:
        """Return a framework-friendly author for a retrieved memory."""
        author = metadata.get("author")
        if isinstance(author, str) and author.strip():
            return author.strip()
        role = metadata.get("role")
        if isinstance(role, str) and role.strip():
            return role.strip()
        match = _EVENT_TEXT_PREFIX_RE.match(memory_text)
        if match:
            return match.group(2).strip()
        return "user"

    @staticmethod
    def _memory_timestamp(metadata: dict[str, Any], memory_text: str = "") -> Optional[str]:
        """Return the original event timestamp when available."""
        timestamp = metadata.get("timestamp")
        if isinstance(timestamp, str) and timestamp.strip():
            return timestamp.strip()
        match = _EVENT_TEXT_PREFIX_RE.match(memory_text)
        if match:
            return match.group(1).strip()
        filed_at = metadata.get("filed_at") or metadata.get("created_at")
        if isinstance(filed_at, str) and filed_at.strip():
            return filed_at.strip()
        return None

    @staticmethod
    def _parse_memory_timestamp(timestamp: Any) -> Optional[float]:
        """Parse an ISO memory timestamp back to a Unix timestamp."""
        if not isinstance(timestamp, str) or not timestamp.strip():
            return None
        try:
            return datetime.fromisoformat(timestamp.strip()).timestamp()
        except ValueError:
            return None

    @classmethod
    def _event_to_text(cls, event: Event) -> str:
        """Extract verbatim text-like content from an event."""
        if not event.content or not event.content.parts:
            return ""
        text = event_to_text(event)
        if not text:
            return ""
        timestamp = format_timestamp(event.timestamp)
        return f"[{timestamp}] {event.author}:\n{text}"
