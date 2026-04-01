# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Mem0-based memory service for multi-session memory storage/search."""

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from typing import Callable
from typing import Optional
from typing import Set
from typing import Union
from typing_extensions import override

import httpx
from mem0 import AsyncMemory
from mem0 import AsyncMemoryClient

from trpc_agent_sdk.abc import MemoryServiceABC as BaseMemoryService
from trpc_agent_sdk.abc import MemoryServiceConfig
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.events import Event as EventCls
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import MemoryEntry
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.types import SearchMemoryResponse

_MEM0_KEY_METADATA = "metadata"


@dataclass
class Mem0Kwargs:
    user_id: str
    agent_id: Optional[str] = None
    run_id: Optional[str] = None,
    session_id: Optional[str] = None,
    filters: Optional[dict[str, Any]] = None,


def set_mem0_filters(agent_context: AgentContext, filters: dict[str, Any]) -> None:
    """Set mem0 metadata/filters into agent_context."""
    if agent_context:
        agent_context.with_metadata(_MEM0_KEY_METADATA, filters)


def get_mem0_filters(agent_context: Optional[AgentContext] = None) -> dict[str, Any]:
    """Get mem0 metadata/filters from agent_context."""
    filters: dict[str, Any] = {}
    if agent_context:
        filters.update(agent_context.get_metadata(_MEM0_KEY_METADATA, {}))
    return filters


class Mem0MemoryService(BaseMemoryService):
    """Mem0-based memory service with two-level key strategy.

    Two-level key:
    - level 1 (primary): session.save_key -> mapped to mem0 user_id
    - level 2 (sub-key): session.id -> stored in metadata["session_id"]

    This enables:
    - query all sessions by session.save_key
    - query one specific session by adding metadata filter session_id

    When TTL is configured, a background cleanup task periodically iterates over
    all known user_ids, retrieves their memories via get_all(), and deletes entries
    whose stored event.timestamp is older than ttl_seconds.
    """

    def __init__(
        self,
        memory_service_config: MemoryServiceConfig,
        mem0_client: Union[AsyncMemoryClient, AsyncMemory],
        infer: bool = True,
        async_mode: bool = False,
    ) -> None:
        super().__init__(memory_service_config=memory_service_config)
        self._mem0 = mem0_client
        self._infer = infer
        # only for AsyncMemoryClient, when async_mode is True, the platform will wait for the indexing to complete before returning
        self._async_mode = async_mode
        self._known_user_ids: Set[tuple[str, str]] = set()

        self.__cleanup_task: Optional[asyncio.Task] = None
        self.__cleanup_stop_event: Optional[asyncio.Event] = None
        self._is_remote_mem0 = isinstance(self._mem0, AsyncMemoryClient)

        self._start_cleanup_task()

    def parse_mem0_kwargs(self, metadata: dict[str, Any], save_key: str) -> Mem0Kwargs:
        """Parse mem0 kwargs from save_key."""
        agent_id = None
        session_id = metadata.pop("session_id", None)
        run_id = session_id
        keys = save_key.split("/")
        if len(keys) >= 2:
            agent_id = keys[0]
            user_id = "".join(keys[1:])
        else:
            user_id = save_key
        if self._is_remote_mem0:
            return Mem0Kwargs(user_id=user_id, agent_id=agent_id, run_id=session_id, filters=metadata or {})
        return Mem0Kwargs(user_id=user_id,
                          agent_id=agent_id,
                          run_id=run_id,
                          session_id=session_id,
                          filters=metadata or {})

    async def __mem0_store_session(self, messages: list[dict[str, str]], mem0_kwargs: Mem0Kwargs) -> None:
        """Add messages to Mem0."""
        if self._is_remote_mem0:
            return await self._mem0.add(messages,
                                        user_id=mem0_kwargs.user_id,
                                        agent_id=mem0_kwargs.agent_id,
                                        run_id=mem0_kwargs.run_id,
                                        metadata=mem0_kwargs.filters,
                                        infer=self._infer,
                                        async_mode=self._async_mode)
        if not self._infer:
            # infer=False: upsert event-by-event to avoid duplicates.
            await self._mem0.delete_all(user_id=mem0_kwargs.user_id,
                                        agent_id=mem0_kwargs.agent_id,
                                        run_id=mem0_kwargs.run_id)
        if self._infer and not self._is_remote_mem0:
            mem0_kwargs.run_id = None
            mem0_kwargs.agent_id = None
        return await self._mem0.add(messages,
                                    user_id=mem0_kwargs.user_id,
                                    agent_id=mem0_kwargs.agent_id,
                                    run_id=mem0_kwargs.run_id,
                                    metadata=mem0_kwargs.filters,
                                    infer=self._infer)

    @override
    async def store_session(self, session: Session, agent_context: Optional[AgentContext] = None) -> None:
        """Store session into Mem0 using two-level key.

        level-1 key: session.save_key -> user_id
        level-2 key: session.id -> metadata["session_id"]
        """
        valid_events = [event for event in session.events if event.content and event.content.parts]
        if not valid_events:
            return

        session_id = session.id
        mem0_metadata = get_mem0_filters(agent_context)
        mem0_metadata["session_id"] = session_id
        mem0_kwargs = self.parse_mem0_kwargs(mem0_metadata, session.save_key)

        self._known_user_ids.add((mem0_kwargs.agent_id, mem0_kwargs.user_id))
        # infer=True is not "each message is stored as is" mode
        # Mem0 official documentation: infer=True will do information extraction + conflict
        # resolution (latest truth wins), and may do deduplication/update, instead of storing each message as is.
        # So "some rounds are not stored" is expected behavior.
        user_messages = []
        assistant_messages = []
        for event in valid_events:
            text = self._event_to_text(event)
            if not text:
                continue
            role = self._event_to_role(event)
            if role == "user":
                user_messages.append({"role": role, "content": text})
            else:
                assistant_messages.append({"role": role, "content": text})
        try:
            if user_messages:
                mem0_kwargs.filters["real_role"] = "user"
                await self.__mem0_store_session(user_messages, mem0_kwargs)
            if assistant_messages:
                mem0_kwargs.filters["real_role"] = "assistant"
                await self.__mem0_store_session(assistant_messages, mem0_kwargs)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("Failed to store session in Mem0. save_key=%s, "
                           "session_id=%s, err=%s", session.save_key, session.id, e)

    async def __mem0_search_memory(self, query: str, mem0_kwargs: Mem0Kwargs, limit: int) -> SearchMemoryResponse:
        """Search memory by primary key(session.save_key) + optional metadata filters."""
        if self._is_remote_mem0:
            api_filters: list[dict[str, Any]] = [{"user_id": mem0_kwargs.user_id}]
            if mem0_kwargs.agent_id:
                api_filters.append({"agent_id": mem0_kwargs.agent_id})
            if mem0_kwargs.filters:
                for key, value in mem0_kwargs.filters.items():
                    api_filters.append({key: value})
            filters = {"AND": [{"OR": api_filters}, {"run_id": "*"}]}
            search_fn = lambda: self._mem0.search(
                query=query, user_id=mem0_kwargs.user_id, filters=filters, top_k=limit)
            return await self._retry_transport("search", search_fn)
        kwargs: dict[str, Any] = {"user_id": mem0_kwargs.user_id, "limit": limit}
        if mem0_kwargs.agent_id:
            kwargs["agent_id"] = mem0_kwargs.agent_id
        if mem0_kwargs.filters:
            kwargs["filters"] = mem0_kwargs.filters or None
        if self._infer:
            kwargs["run_id"] = None
            kwargs["agent_id"] = None
        return await self._mem0.search(query=query, **kwargs)

    @override
    async def search_memory(
        self,
        key: str,
        query: str,
        limit: int = 10,
        agent_context: Optional[AgentContext] = None,
    ) -> SearchMemoryResponse:
        """Search memory by primary key(session.save_key) + optional metadata filters."""
        response = SearchMemoryResponse()
        metadata = get_mem0_filters(agent_context)
        mem0_kwargs = self.parse_mem0_kwargs(metadata, key)
        try:
            search_result: dict = await self.__mem0_search_memory(query, mem0_kwargs, limit)
            results: list[dict[str, Any]] = search_result.get("results", [])
            for item in results:
                memory_text = item.get("memory")
                if not memory_text:
                    continue
                # Timestamp is stored as formatted ISO string in event.timestamp metadata key.
                created_at = item.get("created_at", datetime.now().isoformat())
                updated_at = item.get("updated_at", None) or created_at
                if self._is_remote_mem0:
                    role = item.get("metadata", {}).get("real_role", "user")
                else:
                    role = item.get("role") or item.get("real_role")
                entry = MemoryEntry(
                    content=Content(parts=[Part.from_text(text=memory_text)], role=role),
                    author=role,
                    timestamp=updated_at,
                )
                response.memories.append(entry)
        except Exception as e:  # pylint: disable=broad-except
            resp_body = ""
            if isinstance(e, httpx.HTTPStatusError):
                try:
                    resp_body = f", response_body={e.response.text!r}"
                except Exception:  # pylint: disable=broad-except
                    pass
            logger.warning("Failed to search memory in Mem0. key=%s, query=%s, err=%s%s", key, query, e, resp_body)
        return response

    @override
    async def close(self) -> None:
        """Stop cleanup task and close underlying async client if present."""
        self._stop_cleanup_task()
        if hasattr(self._mem0, "async_client"):
            try:
                await self._mem0.async_client.aclose()
            except Exception:  # pylint: disable=broad-except
                pass

    async def _retry_transport(self, op_name: str, call: Callable[..., Any], max_attempts: int = 5) -> Any:
        """Retry mem0 API calls for transient network/TLS transport failures."""
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return await call()
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt == max_attempts:
                    break
                wait_s = min(2 * attempt, 8)
                logger.warning("[net-retry] %s attempt=%s/%s failed: %s; sleep %ss", op_name, attempt, max_attempts,
                               exc, wait_s)
                await asyncio.sleep(wait_s)
        logger.warning("[net-retry] %s exhausted retries: %s", op_name, last_exc)
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _event_to_text(event: EventCls) -> str:
        """Extract text from event content parts."""
        if not event.content or not event.content.parts:
            return ""
        parts = [part.text for part in event.content.parts if part.text]
        return " ".join(parts).strip()

    @staticmethod
    def _event_to_role(event: EventCls) -> str:
        """Map framework event author to Mem0 role."""
        return "user" if event.author == "user" else "assistant"

    @staticmethod
    def _parse_event_timestamp(ts_str: str) -> Optional[float]:
        """Parse a stored event.timestamp ISO string back to a Unix float.

        Returns None when the value cannot be parsed.
        """
        try:
            return datetime.fromisoformat(ts_str).timestamp()
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _extract_memories_from_result(result: Any) -> list[dict[str, Any]]:
        """Normalise the return value of get_all() / search() to a plain list."""
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("results", [])
        return []

    # ------------------------------------------------------------------
    # TTL eviction
    # ------------------------------------------------------------------

    def _start_cleanup_task(self) -> None:
        """Start the background TTL cleanup task if TTL is configured."""
        if not self._memory_service_config.ttl.need_ttl_expire():
            logger.debug("Mem0 memory cleanup task disabled (ttl is disabled)")
            return

        if self.__cleanup_task is not None:
            logger.debug("Mem0 memory cleanup task is already running")
            return

        self.__cleanup_stop_event = asyncio.Event()
        self.__cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.debug("Mem0 memory cleanup task created")

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
        logger.debug("Mem0 memory cleanup task stopped")

    async def _cleanup_loop(self) -> None:
        """Periodic background loop that evicts expired memories."""
        logger.debug("Mem0 memory cleanup task started with interval: %ss",
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
                        logger.debug("Mem0 memory cleanup cycle completed")
                    except Exception as e:  # pylint: disable=broad-except
                        logger.error("Error during Mem0 memory cleanup: %s", e, exc_info=True)
        except Exception as e:  # pylint: disable=broad-except
            logger.error("Mem0 memory cleanup loop encountered error: %s", e, exc_info=True)
        finally:
            logger.debug("Mem0 memory cleanup task stopped")

    async def _cleanup_expired_memories(self) -> None:
        """Iterate over all known user_ids and delete expired memories.

        A memory is considered expired when the ISO timestamp stored under the
        ``event.timestamp`` metadata key represents a point in time older than
        ``ttl_seconds`` ago.

        Both AsyncMemoryClient and AsyncMemory expose get_all(user_id) and
        delete(memory_id) with the same signature, so no branching is needed here.
        """
        if not self._known_user_ids:
            return

        now = time.time()
        ttl_seconds = self._memory_service_config.ttl.ttl_seconds

        for ids in list(self._known_user_ids):
            agent_id, user_id = ids
            try:
                if not self._is_remote_mem0:
                    if self._infer:
                        agent_id = None
                    result = await self._mem0.get_all(user_id=user_id, agent_id=agent_id)
                else:
                    # AsyncMemoryClient(v2) requires filters and role-scoped data
                    # may live under user_id or agent_id, so use OR instead of AND.
                    filters = {
                        "AND": [{
                            "OR": [
                                {
                                    "user_id": user_id
                                },
                                {
                                    "agent_id": agent_id
                                },
                            ]
                        }, {
                            "run_id": "*"
                        }]
                    }
                    result = await self._retry_transport(
                        "get_all",
                        lambda: self._mem0.get_all(filters=filters),
                    )

                memories = self._extract_memories_from_result(result)
                deleted_count = 0
                for memory in memories:
                    created_at = memory.get("created_at", datetime.now().isoformat())
                    ts_str = memory.get("updated_at", None) or created_at
                    ts = self._parse_event_timestamp(ts_str)
                    if ts is None:
                        continue
                    if ts < now - ttl_seconds:
                        memory_id = memory.get("id")
                        if memory_id:
                            await self._mem0.delete(memory_id=memory_id)
                            deleted_count += 1

                if deleted_count:
                    logger.info("Mem0 cleanup: deleted %s expired memories for user_id=%s", deleted_count, user_id)
            except Exception as e:  # pylint: disable=broad-except
                resp_body = ""
                if isinstance(e, httpx.HTTPStatusError):
                    try:
                        resp_body = f", response_body={e.response.text!r}"
                    except Exception:  # pylint: disable=broad-except
                        pass
                logger.warning("Mem0 cleanup failed for user_id=%s, err=%s%s", user_id, e, resp_body)
