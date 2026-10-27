# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Hy-Memory integration for cross-session agent memory."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import AsyncIterator
from typing import Literal
from typing import Optional

from pydantic import ConfigDict
from pydantic import Field
from typing_extensions import override

from trpc_agent_sdk.abc import MemoryServiceABC as BaseMemoryService
from trpc_agent_sdk.abc import MemoryServiceConfig
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import MemoryEntry
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.types import SearchMemoryResponse
from trpc_agent_sdk.types import Ttl

_HY_MEMORY_OPTIONS_KEY = "hy_memory_options"
_VALID_SCENES = frozenset({"chat", "working", "coding"})
_STORE_LOCK_RETRY_INTERVAL_SECONDS = 0.01


class HyMemoryServiceConfig(MemoryServiceConfig):
    """Configuration for :class:`HyMemoryService`.

    Hy-Memory reads its model and storage settings from environment variables
    by default. ``client_config`` can instead be passed to
    ``HyMemoryClient.from_config``.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    ttl: Ttl = Field(
        default_factory=lambda: Ttl(
            enable=False,
            ttl_seconds=0,
            cleanup_interval_seconds=0,
        ),
        description=("Framework TTL is disabled because retention is managed "
                     "by Hy-Memory."),
    )
    mode: Literal["lite", "pro", "ultra"] = Field(
        default="pro",
        description="Hy-Memory processing mode.",
    )
    scene: Optional[Literal["chat", "working", "coding"]] = Field(
        default="chat",
        description=("Memory scene. Defaults to chat; set None for Hy-Memory routing "
                     "and use agentic search routing for matching recall."),
    )
    source: str = Field(
        default="trpc-agent",
        min_length=1,
        description="Application source persisted with memories.",
    )
    agent_id: Optional[str] = Field(
        default=None,
        description=("Stable Hy-Memory agent ID. The framework app name is "
                     "used when omitted."),
    )
    workspace_id: Optional[str] = Field(
        default=None,
        description="Default working/coding workspace scope.",
    )
    branch: Optional[str] = Field(
        default=None,
        description="Default coding branch scope; requires workspace_id.",
    )
    search_mode: Literal["default", "agentic"] = Field(
        default="default",
        description="Hy-Memory search routing mode.",
    )
    min_score: float = Field(default=0.4, ge=0, le=1)
    schema_min_score: float = Field(default=0.4, ge=0, le=1)
    schema_limit: int = Field(default=10, ge=0)
    intention_limit: int = Field(default=0, ge=0)
    include_sources: Optional[list[str]] = None
    exclude_sources: Optional[list[str]] = None
    client_config: Optional[dict[str, Any]] = Field(
        default=None,
        description=("Configuration passed to HyMemoryClient.from_config; "
                     "environment configuration is used when omitted."),
    )


def set_hy_memory_options(
    agent_context: Optional[AgentContext],
    options: dict[str, Any],
) -> None:
    """Set per-invocation Hy-Memory options on an agent context."""
    if agent_context is not None:
        agent_context.with_metadata(_HY_MEMORY_OPTIONS_KEY, options.copy())


def get_hy_memory_options(agent_context: Optional[AgentContext], ) -> dict[str, Any]:
    """Get per-invocation Hy-Memory options from an agent context."""
    if agent_context is None:
        return {}
    options = agent_context.get_metadata(_HY_MEMORY_OPTIONS_KEY, {})
    return options.copy() if isinstance(options, dict) else {}


class HyMemoryService(BaseMemoryService):
    """Store and retrieve long-term memory through the Hy-Memory Python SDK.

    The Runner supplies the complete session after each turn. This adapter
    tracks accepted event IDs and submits only new events during the current
    process lifetime. Tool calls and tool responses are preserved so
    Hy-Memory's working and coding pipelines can process execution traces.
    """

    def __init__(
        self,
        config: HyMemoryServiceConfig,
        client: Optional[Any] = None,
    ) -> None:
        super().__init__(memory_service_config=config)
        self._config = config
        self._owns_client = client is None
        self._client = client or self._create_client(config)
        self._stored_event_ids: dict[tuple[str, str], set[str]] = {}
        self._store_locks: dict[tuple[str, str], threading.Lock] = {}
        self._store_state_lock = threading.Lock()

        if config.ttl.need_ttl_expire():
            logger.warning(
                "HyMemoryService does not apply framework TTL settings; "
                "configure retention in Hy-Memory.", )

    @staticmethod
    def _create_client(config: HyMemoryServiceConfig) -> Any:
        try:
            from hy_memory import HyMemoryClient
        except ImportError as exc:
            raise ImportError("HyMemoryService requires the optional 'hy-memory' package. "
                              "Install it with: pip install 'trpc-agent-py[hy-memory]'") from exc

        if config.client_config is not None:
            return HyMemoryClient.from_config(config.client_config)
        return HyMemoryClient(mode=config.mode)

    @override
    async def store_session(
        self,
        session: Session,
        agent_context: Optional[AgentContext] = None,
    ) -> None:
        """Store new session events while preserving tool-call traces."""
        session_key = (session.save_key, session.id)
        async with self._store_lock(session_key):
            with self._store_state_lock:
                stored_ids = self._stored_event_ids.setdefault(session_key, set())
            pending = [event for event in session.events if event.id and event.id not in stored_ids]
            messages = self._events_to_messages(pending)
            if not messages:
                stored_ids.update(event.id for event in pending)
                return

            options = self._effective_options(agent_context)
            agent_id = options.get("agent_id") or self._config.agent_id or session.app_name
            scene = self._normalize_scene(options.get("scene", self._config.scene))
            workspace_id = options.get("workspace_id", self._config.workspace_id)
            branch = options.get("branch", self._config.branch)
            if branch and not workspace_id:
                logger.warning("Ignoring Hy-Memory branch because workspace_id is not set.", )
                branch = None

            event_ids = [event.id for event in pending]
            meta = {
                "app_name": session.app_name,
                "event_ids": event_ids,
            }
            custom_meta = options.get("meta")
            if isinstance(custom_meta, dict):
                meta.update(custom_meta)

            try:
                result = await self._client.async_add(
                    messages,
                    user_id=session.user_id,
                    agent_id=agent_id,
                    session_id=session.id,
                    source=options.get("source", self._config.source),
                    scene=scene,
                    workspace_id=workspace_id,
                    branch=branch,
                    request_id=self._request_id(session, event_ids),
                    meta=meta,
                )
                if isinstance(result, dict) and result.get("success") is False:
                    logger.warning(
                        "Hy-Memory rejected session write. save_key=%s, "
                        "session_id=%s, result=%s",
                        session.save_key,
                        session.id,
                        result,
                    )
                    return
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning(
                    "Failed to store session in Hy-Memory. save_key=%s, "
                    "session_id=%s, err=%s",
                    session.save_key,
                    session.id,
                    exc,
                )
                return

            stored_ids.update(event_ids)

    @override
    async def search_memory(
        self,
        key: str,
        query: str,
        limit: int = 10,
        agent_context: Optional[AgentContext] = None,
    ) -> SearchMemoryResponse:
        """Search chat, working, or coding memories."""
        response = SearchMemoryResponse()
        if limit <= 0:
            return response

        app_name, user_id = self._identity_from_key(key)
        options = self._effective_options(agent_context)
        agent_id = options.get("agent_id") or self._config.agent_id or app_name
        scene = self._normalize_scene(options.get("scene", self._config.scene))
        workspace_id = options.get("workspace_id", self._config.workspace_id)
        branch = options.get("branch", self._config.branch)
        if branch and not workspace_id:
            branch = None

        try:
            result = await self._client.async_search(
                query,
                scene=scene,
                search_mode=options.get("search_mode", self._config.search_mode),
                user_ids=[user_id],
                agent_ids=[agent_id] if agent_id else None,
                include_sources=options.get(
                    "include_sources",
                    self._config.include_sources,
                ),
                exclude_sources=options.get(
                    "exclude_sources",
                    self._config.exclude_sources,
                ),
                limit=limit,
                min_score=options.get("min_score", self._config.min_score),
                schema_limit=options.get(
                    "schema_limit",
                    self._config.schema_limit,
                ),
                schema_min_score=options.get(
                    "schema_min_score",
                    self._config.schema_min_score,
                ),
                intention_limit=options.get(
                    "intention_limit",
                    self._config.intention_limit,
                ),
                workspace_id=workspace_id,
                branch=branch,
            )
            for item, channel in self._iter_search_items(result):
                entry = self._to_memory_entry(item, channel)
                if entry is not None:
                    response.memories.append(entry)
                    if len(response.memories) >= limit:
                        break
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                "Failed to search Hy-Memory. key=%s, query=%s, err=%s",
                key,
                query,
                exc,
            )
        return response

    @override
    async def close(self) -> None:
        """Close the owned Hy-Memory client."""
        if not self._owns_client:
            return
        close = getattr(self._client, "close", None)
        if close is None:
            return
        result = await asyncio.to_thread(close)
        if inspect.isawaitable(result):
            await result

    def _effective_options(
        self,
        agent_context: Optional[AgentContext],
    ) -> dict[str, Any]:
        options = get_hy_memory_options(agent_context)
        return options

    @asynccontextmanager
    async def _store_lock(
        self,
        session_key: tuple[str, str],
    ) -> AsyncIterator[None]:
        """Serialize a session across threads without blocking event loops."""
        with self._store_state_lock:
            lock = self._store_locks.setdefault(session_key, threading.Lock())
        while not lock.acquire(blocking=False):
            await asyncio.sleep(_STORE_LOCK_RETRY_INTERVAL_SECONDS)
        try:
            yield
        finally:
            lock.release()

    @staticmethod
    def _normalize_scene(scene: Any) -> Optional[str]:
        if scene is None or scene == "":
            return None
        normalized = str(scene).strip().lower()
        if normalized not in _VALID_SCENES:
            raise ValueError("Hy-Memory scene must be one of: chat, working, coding", )
        return normalized

    @staticmethod
    def _identity_from_key(key: str) -> tuple[str, str]:
        app_name, separator, user_id = key.partition("/")
        if not separator or not app_name or not user_id:
            raise ValueError("Hy-Memory search key must use '<app_name>/<user_id>' format", )
        return app_name, user_id

    @staticmethod
    def _request_id(session: Session, event_ids: list[str]) -> str:
        raw = "\0".join([session.app_name, session.user_id, session.id, *event_ids], )
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return f"trpc-agent-{digest}"

    @classmethod
    def _events_to_messages(
        cls,
        events: list[Event],
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for event in events:
            text = cls._event_visible_text(event)
            function_responses = event.get_function_responses()
            for index, function_response in enumerate(function_responses):
                response = function_response.response
                if not isinstance(response, str):
                    response = json.dumps(
                        response,
                        ensure_ascii=False,
                        default=str,
                    )
                messages.append({
                    "role": "tool",
                    "tool_call_id": function_response.id or f"{event.id}-response-{index}",
                    "name": function_response.name or "tool",
                    "content": response,
                })

            function_calls = event.get_function_calls()
            if not text and not function_calls:
                continue
            role = "user" if event.author == "user" else "assistant"
            message: dict[str, Any] = {
                "role": role,
                "content": text,
            }
            if function_calls:
                message["role"] = "assistant"
                message["tool_calls"] = []
                for index, function_call in enumerate(function_calls):
                    arguments = function_call.args or {}
                    message["tool_calls"].append({
                        "id": function_call.id or f"{event.id}-call-{index}",
                        "type": "function",
                        "function": {
                            "name": function_call.name or "tool",
                            "arguments": json.dumps(
                                arguments,
                                ensure_ascii=False,
                                default=str,
                            ),
                        },
                    })
            messages.append(message)
        return messages

    @staticmethod
    def _event_visible_text(event: Event) -> str:
        """Return model-visible text without reasoning/thought parts."""
        if not event.content or not event.content.parts:
            return ""
        return "".join(part.text for part in event.content.parts if part.text and not part.thought)

    @classmethod
    def _iter_search_items(
        cls,
        result: Any,
    ):
        if not isinstance(result, dict):
            return

        memories = result.get("memories", {})
        if isinstance(memories, dict):
            for channel in ("normal", "schema", "proactive", "profile"):
                items = memories.get(channel, [])
                if isinstance(items, list):
                    for item in items:
                        yield item, channel
        elif isinstance(memories, list):
            for item in memories:
                yield item, "memory"

        for channel in ("facts", "experiences"):
            items = result.get(channel, [])
            if isinstance(items, list):
                for item in items:
                    yield item, channel

        chain_block = result.get("chain_block")
        if isinstance(chain_block, str) and chain_block.strip():
            yield {
                "content": chain_block.strip(),
                "author": "hy-memory",
            }, "chain"

    @classmethod
    def _to_memory_entry(
        cls,
        item: Any,
        channel: str,
    ) -> Optional[MemoryEntry]:
        if isinstance(item, str):
            text = item.strip()
            metadata: dict[str, Any] = {}
        elif isinstance(item, dict):
            metadata = item
            text = cls._item_text(item)
        else:
            return None
        if not text:
            return None

        if channel == "chain":
            text = f"Code evolution context:\n{text}"
        author = (metadata.get("author") or metadata.get("role") or metadata.get("source") or f"hy-memory:{channel}")
        timestamp = cls._item_timestamp(metadata)
        return MemoryEntry(
            content=Content(
                role="user",
                parts=[Part.from_text(text=text)],
            ),
            author=str(author),
            timestamp=timestamp,
        )

    @staticmethod
    def _item_text(item: dict[str, Any]) -> str:
        statement = item.get("statement")
        if isinstance(statement, str) and statement.strip():
            parts = [statement.strip()]
            details = item.get("details")
            if isinstance(details, str) and details.strip():
                parts.append(f"Details: {details.strip()}")
            chain_summary = item.get("fact_chain_summary")
            if isinstance(chain_summary, str) and chain_summary.strip():
                parts.append(f"Evolution: {chain_summary.strip()}")
            return "\n".join(parts)

        task = item.get("task")
        solution = item.get("solution")
        if (isinstance(task, str) and task.strip() or isinstance(solution, str) and solution.strip()):
            parts = []
            if isinstance(task, str) and task.strip():
                parts.append(f"Task: {task.strip()}")
            if isinstance(solution, str) and solution.strip():
                parts.append(f"Solution: {solution.strip()}")
            boundary_envs = item.get("boundary_envs")
            if boundary_envs:
                value = (boundary_envs if isinstance(boundary_envs, str) else json.dumps(
                    boundary_envs,
                    ensure_ascii=False,
                    default=str,
                ))
                parts.append(f"Applicable conditions: {value}")
            return "\n".join(parts)

        for key in (
                "content",
                "memory",
                "text",
                "fact",
                "experience",
                "summary",
        ):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, (dict, list)) and value:
                return json.dumps(value, ensure_ascii=False, default=str)
        return ""

    @staticmethod
    def _item_timestamp(item: dict[str, Any]) -> Optional[str]:
        for key in (
                "updated_at",
                "created_at",
                "gmt_modified",
                "gmt_created",
                "memory_at",
                "timestamp",
        ):
            value = item.get(key)
            if value is None:
                continue
            if isinstance(value, (int, float)):
                return datetime.fromtimestamp(
                    value,
                    tz=timezone.utc,
                ).isoformat()
            return str(value)
        return None


__all__ = [
    "HyMemoryService",
    "HyMemoryServiceConfig",
    "get_hy_memory_options",
    "set_hy_memory_options",
]
