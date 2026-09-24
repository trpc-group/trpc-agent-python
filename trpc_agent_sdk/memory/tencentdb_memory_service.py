# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""TencentDB Agent Memory V3 integration."""

from __future__ import annotations

import asyncio
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Optional
from typing_extensions import override

import httpx
from pydantic import ConfigDict
from pydantic import Field

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

from ._utils import event_to_text

_CONVERSATION_ADD_PATH = "/v3/conversation/add"
_CONVERSATION_SEARCH_PATH = "/v3/conversation/search"
_ATOMIC_SEARCH_PATH = "/v3/atomic/search"
_MAX_MESSAGES_PER_REQUEST = 100
_MAX_MESSAGE_UTF16_UNITS = 8192


class TencentDBMemoryServiceConfig(MemoryServiceConfig):
    """Configuration for :class:`TencentDBMemoryService`.

    ``service_id``, ``team_id`` and ``agent_id`` define the stable V3
    isolation boundary. The framework session supplies ``user_id`` and
    ``session_id`` at runtime.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    ttl: Ttl = Field(
        default_factory=lambda: Ttl(
            enable=False,
            ttl_seconds=0,
            cleanup_interval_seconds=0,
        ),
        description=("Framework TTL is disabled because retention is managed by "
                     "TencentDB Agent Memory."),
    )
    endpoint: str = Field(
        default="http://127.0.0.1:8420",
        min_length=1,
        description="TencentDB Agent Memory gateway endpoint.",
    )
    api_key: str = Field(
        default="local",
        description="Bearer token used by the gateway.",
    )
    service_id: str = Field(
        min_length=1,
        description="TencentDB Agent Memory service/instance ID.",
    )
    team_id: str = Field(
        min_length=1,
        description="Team isolation ID.",
    )
    agent_id: str = Field(
        min_length=1,
        description="Agent isolation ID.",
    )
    timeout: float = Field(
        default=10.0,
        gt=0,
        description="HTTP request timeout in seconds.",
    )
    verify_ssl: bool = Field(
        default=True,
        description="Whether to verify the gateway TLS certificate.",
    )


class TencentDBMemoryError(RuntimeError):
    """Raised when the TencentDB Agent Memory gateway rejects a request."""

    def __init__(self, code: int, message: str, request_id: str = "") -> None:
        super().__init__(f"TencentDB Agent Memory request failed: code={code}, "
                         f"message={message}, request_id={request_id}")
        self.code = code
        self.message = message
        self.request_id = request_id


class TencentDBMemoryService(BaseMemoryService):
    """Store and retrieve long-term memory through the TencentDB V3 API.

    The Runner passes the complete session to ``store_session`` after every
    turn. This service tracks successfully accepted event IDs and sends only
    new text events during the current process lifetime. Delivery is
    at-least-once across process restarts because the V3 API does not accept a
    caller-provided idempotency key.

    L0 writes use ``/v3/conversation/add`` with session isolation. Searches
    use ``/v3/atomic/search`` without a session ID so memories can be recalled
    across sessions for the same user.
    """

    def __init__(
        self,
        config: TencentDBMemoryServiceConfig,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        super().__init__(memory_service_config=config)
        self._config = config
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=config.timeout,
            verify=config.verify_ssl,
        )
        self._stored_event_ids: dict[tuple[str, str], set[str]] = {}
        self._store_locks: dict[tuple[str, str], asyncio.Lock] = {}

        if config.ttl.need_ttl_expire():
            logger.warning(
                "TencentDBMemoryService does not apply framework TTL settings; "
                "configure memory retention in TencentDB Agent Memory.", )

    @override
    async def store_session(
        self,
        session: Session,
        agent_context: Optional[AgentContext] = None,
    ) -> None:
        """Send new text events in ``session`` to the V3 conversation API."""
        del agent_context
        session_key = (session.save_key, session.id)
        lock = self._store_locks.setdefault(session_key, asyncio.Lock())

        async with lock:
            stored_ids = self._stored_event_ids.setdefault(session_key, set())
            pending = self._pending_messages(session.events, stored_ids)
            if not pending:
                return

            for start in range(0, len(pending), _MAX_MESSAGES_PER_REQUEST):
                batch = pending[start:start + _MAX_MESSAGES_PER_REQUEST]
                body = {
                    "session_id": session.id,
                    "messages": [message for _, message in batch],
                    **self._isolation_body(session.user_id),
                }
                try:
                    data = await self._post(_CONVERSATION_ADD_PATH, body)
                    self._validate_capture_response(data, len(batch))
                except Exception as exc:  # pylint: disable=broad-except
                    logger.warning(
                        "Failed to store session in TencentDB Agent Memory. "
                        "save_key=%s, session_id=%s, err=%s",
                        session.save_key,
                        session.id,
                        exc,
                    )
                    return
                stored_ids.update(event_id for event_id, _ in batch)

    @override
    async def search_memory(
        self,
        key: str,
        query: str,
        limit: int = 10,
        agent_context: Optional[AgentContext] = None,
    ) -> SearchMemoryResponse:
        """Search V3 L1 atomic memories across the user's sessions."""
        del agent_context
        response = SearchMemoryResponse()
        user_id = self._user_id_from_key(key)
        body = {
            "query": query,
            "limit": limit,
            **self._isolation_body(user_id),
        }
        try:
            # search atomic memories for the user L1
            data = await self._post(_ATOMIC_SEARCH_PATH, body)
            items = data.get("items", [])
            if not items:
                # search conversation memories for the user L0
                data = await self._post(_CONVERSATION_SEARCH_PATH, body)
                items = data.get("messages", [])
            if not isinstance(items, list):
                raise ValueError("TencentDB Agent Memory search response must contain a list")
            for item in items:
                entry = self._to_memory_entry(item)
                if entry is not None:
                    response.memories.append(entry)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                "Failed to search TencentDB Agent Memory. key=%s, query=%s, err=%s",
                key,
                query,
                exc,
            )
        return response

    @override
    async def close(self) -> None:
        """Release the owned HTTP client and local delivery checkpoints."""
        self._stored_event_ids.clear()
        self._store_locks.clear()
        if self._owns_client:
            await self._client.aclose()

    async def _post(
        self,
        path: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "x-tdai-service-id": self._config.service_id,
            "Content-Type": "application/json",
        }

        response = await self._client.post(
            f"{self._config.endpoint.rstrip('/')}{path}",
            json=body,
            headers=headers,
            timeout=self._config.timeout,
        )
        try:
            payload = response.json()
        except ValueError:
            response.raise_for_status()
            raise ValueError("TencentDB Agent Memory response must be valid JSON")
        if not isinstance(payload, dict):
            response.raise_for_status()
            raise ValueError("TencentDB Agent Memory response must be a JSON object")

        code = payload.get("code")
        if response.is_error or code != 0:
            request_id = (response.headers.get("x-qcloud-transaction-id") or payload.get("request_id") or "")
            raise TencentDBMemoryError(
                code=code if isinstance(code, int) else response.status_code,
                message=str(payload.get("message", response.reason_phrase)),
                request_id=str(request_id),
            )

        data = payload.get("data", {})
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ValueError("TencentDB Agent Memory response 'data' must be an object")
        return data

    def _isolation_body(self, user_id: str) -> dict[str, str]:
        return {
            "team_id": self._config.team_id,
            "agent_id": self._config.agent_id,
            "user_id": user_id,
        }

    @classmethod
    def _pending_messages(
        cls,
        events: list[Event],
        stored_ids: set[str],
    ) -> list[tuple[str, dict[str, str]]]:
        pending: list[tuple[str, dict[str, str]]] = []
        for event in events:
            if event.id in stored_ids or event.partial or event.is_error():
                continue
            text = event_to_text(event)
            if not text:
                continue
            text = cls._truncate_utf16(text, _MAX_MESSAGE_UTF16_UNITS)
            pending.append((
                event.id,
                {
                    "role": "user" if event.author == "user" else "assistant",
                    "content": text,
                    "timestamp": cls._format_timestamp(event.timestamp),
                },
            ))
        return pending

    @staticmethod
    def _format_timestamp(timestamp: float) -> str:
        """Format an event timestamp as an RFC 3339 UTC datetime."""
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _truncate_utf16(text: str, max_units: int) -> str:
        encoded = text.encode("utf-16-le")
        max_bytes = max_units * 2
        if len(encoded) <= max_bytes:
            return text

        truncated = encoded[:max_bytes]
        while truncated:
            try:
                return truncated.decode("utf-16-le")
            except UnicodeDecodeError:
                truncated = truncated[:-2]
        return ""

    @staticmethod
    def _validate_capture_response(data: dict[str, Any], expected: int) -> None:
        total_count = data.get("total_count")
        accepted_ids = data.get("accepted_ids")
        if total_count != expected:
            raise ValueError(
                "TencentDB Agent Memory accepted an unexpected number of messages: "
                f"expected={expected}, actual={total_count}", )
        if not isinstance(accepted_ids, list) or len(accepted_ids) != expected:
            raise ValueError(
                "TencentDB Agent Memory returned an incomplete accepted_ids list: "
                f"expected={expected}, actual={len(accepted_ids) if isinstance(accepted_ids, list) else 'missing'}", )

    @staticmethod
    def _user_id_from_key(key: str) -> str:
        _, separator, user_id = key.partition("/")
        resolved = user_id if separator else key
        if not resolved:
            raise ValueError("Memory search key must contain a user ID")
        return resolved

    @staticmethod
    def _to_memory_entry(item: Any) -> Optional[MemoryEntry]:
        if not isinstance(item, dict):
            return None
        memory_text = item.get("content")
        if not isinstance(memory_text, str) or not memory_text:
            return None

        memory_type = item.get("type") or item.get("role")
        author = memory_type if isinstance(memory_type, str) and memory_type else "memory"
        timestamp = item.get("updated_at") or item.get("created_at") or item.get("timestamp")
        if not isinstance(timestamp, str):
            timestamp = None
        return MemoryEntry(
            content=Content(
                parts=[Part.from_text(text=memory_text)],
                role="user",
            ),
            author=author,
            timestamp=timestamp,
        )


__all__ = [
    "TencentDBMemoryError",
    "TencentDBMemoryService",
    "TencentDBMemoryServiceConfig",
]
