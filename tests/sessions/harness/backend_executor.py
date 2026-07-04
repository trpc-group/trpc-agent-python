# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Backend executor that replays operations against session/memory services."""

from __future__ import annotations

import time
from typing import Any

from trpc_agent_sdk.abc import MemoryEntry
from trpc_agent_sdk.abc import MemoryServiceABC
from trpc_agent_sdk.abc import MemoryServiceConfig
from trpc_agent_sdk.abc import SessionServiceABC
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.sessions import SessionSummary
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import EventActions
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.types import State

from .replay_loader import Operation
from .snapshot import BackendSnapshot


def _build_event_from_params(params: dict[str, Any]) -> Event:
    """Build an Event object from replay operation parameters.

    Args:
        params: Dictionary with keys: author, content, state_delta,
                invocation_id, partial, etc.

    Returns:
        Constructed Event.
    """
    author = params.get("author", "user")
    content_data = params.get("content", {"parts": [{"text": ""}]})
    parts = []
    for p in content_data.get("parts", []):
        if "function_call" in p:
            from trpc_agent_sdk.types import FunctionCall
            fc_data = p["function_call"]
            parts.append(Part(function_call=FunctionCall(
                id=fc_data.get("id", ""),
                name=fc_data.get("name", ""),
                args=fc_data.get("args", {}),
            )))
        elif "function_response" in p:
            from trpc_agent_sdk.types import FunctionResponse
            fr_data = p["function_response"]
            parts.append(Part(function_response=FunctionResponse(
                id=fr_data.get("id", ""),
                name=fr_data.get("name", ""),
                response=fr_data.get("response", {}),
            )))
        else:
            parts.append(Part.from_text(text=p.get("text", "")))

    content = Content(parts=parts, role=params.get("role", author))

    state_delta = params.get("state_delta", None)
    actions = EventActions(state_delta=state_delta) if state_delta else EventActions()

    return Event(
        invocation_id=params.get("invocation_id", "replay-inv"),
        author=author,
        content=content,
        actions=actions,
        partial=params.get("partial", False),
        timestamp=params.get("timestamp", time.time()),
    )


class BackendExecutor:
    """Executes replay operations against a session service and memory service.

    Maintains an in-memory session cache keyed by session_id so that
    append_event and update_session can reference the correct Session object.
    """

    def __init__(
        self,
        session_service: SessionServiceABC,
        memory_service: MemoryServiceABC | None = None,
        memory_service_config: MemoryServiceConfig | None = None,
    ):
        """Initialize the executor.

        Args:
            session_service: The session service backend to replay against.
            memory_service: Optional memory service backend.
            memory_service_config: Memory service config (used for search_memory key).
        """
        self._session_service = session_service
        self._memory_service = memory_service
        self._memory_config = memory_service_config or MemoryServiceConfig()
        self._sessions: dict[str, Session] = {}

    async def execute(self, operations: list[Operation]) -> BackendSnapshot:
        """Execute all operations and return a snapshot of the final state.

        Args:
            operations: Ordered list of operations to replay.

        Returns:
            BackendSnapshot with the final state of all sessions, memory, and summaries.
        """
        snapshot = BackendSnapshot(backend_name="unknown")
        errors: list[dict[str, Any]] = []

        for idx, op in enumerate(operations):
            try:
                await self._execute_op(op)
            except Exception as e:
                errors.append({
                    "op_index": idx,
                    "op": op.op,
                    "params": op.params,
                    "error": str(e),
                })

        snapshot.backend_name = getattr(
            self._session_service, "__class__", type(self._session_service)
        ).__name__

        for sid, session in self._sessions.items():
            try:
                stored = await self._session_service.get_session(
                    app_name=session.app_name,
                    user_id=session.user_id,
                    session_id=sid,
                )
                if stored is not None:
                    snapshot.sessions[sid] = stored
            except Exception:
                snapshot.sessions[sid] = session

        if self._memory_service:
            for sid, session in self._sessions.items():
                key = session.save_key
                try:
                    response = await self._memory_service.search_memory(
                        key=key, query="", limit=0
                    )
                    snapshot.memory_entries[key] = list(response.memories)
                except Exception:
                    pass

        for sid, session in self._sessions.items():
            try:
                summary_text = await self._session_service.get_session_summary(session)
                if summary_text:
                    snapshot.summaries[sid] = SessionSummary(
                        session_id=sid,
                        summary_text=summary_text,
                        original_event_count=len(session.events),
                        compressed_event_count=len(session.events),
                        summary_timestamp=time.time(),
                    )
            except Exception:
                pass

        snapshot.errors = errors
        return snapshot

    async def _execute_op(self, op: Operation) -> None:
        """Dispatch a single operation to the appropriate handler."""
        handlers = {
            "create_session": self._handle_create_session,
            "append_event": self._handle_append_event,
            "get_session": self._handle_get_session,
            "update_session": self._handle_update_session,
            "delete_session": self._handle_delete_session,
            "store_memory": self._handle_store_memory,
            "search_memory": self._handle_search_memory,
            "create_summary": self._handle_create_summary,
            "get_summary": self._handle_get_summary,
        }
        handler = handlers.get(op.op)
        if handler is None:
            raise ValueError(f"Unknown operation: {op.op}")
        await handler(op.params)

    async def _handle_create_session(self, params: dict[str, Any]) -> None:
        app_name = params.get("app", "test_app")
        user_id = params.get("user", "test_user")
        session_id = params.get("session_id", None)
        state = params.get("state", None)
        session = await self._session_service.create_session(
            app_name=app_name,
            user_id=user_id,
            state=state,
            session_id=session_id,
        )
        self._sessions[session.id] = session

    async def _handle_append_event(self, params: dict[str, Any]) -> None:
        session_id = params["session_id"]
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"Session {session_id} not found in cache")
        event = _build_event_from_params(params.get("event", {}))
        await self._session_service.append_event(session, event)

    async def _handle_get_session(self, params: dict[str, Any]) -> None:
        session_id = params["session_id"]
        session = self._sessions.get(session_id)
        if session is None:
            return
        stored = await self._session_service.get_session(
            app_name=session.app_name,
            user_id=session.user_id,
            session_id=session_id,
        )
        if stored is not None:
            self._sessions[session_id] = stored

    async def _handle_update_session(self, params: dict[str, Any]) -> None:
        session_id = params["session_id"]
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"Session {session_id} not found in cache")
        await self._session_service.update_session(session)

    async def _handle_delete_session(self, params: dict[str, Any]) -> None:
        session_id = params["session_id"]
        session = self._sessions.get(session_id)
        if session is None:
            return
        await self._session_service.delete_session(
            app_name=session.app_name,
            user_id=session.user_id,
            session_id=session_id,
        )
        self._sessions.pop(session_id, None)

    async def _handle_store_memory(self, params: dict[str, Any]) -> None:
        if self._memory_service is None:
            return
        session_id = params["session_id"]
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"Session {session_id} not found in cache")
        await self._memory_service.store_session(session)

    async def _handle_search_memory(self, params: dict[str, Any]) -> None:
        if self._memory_service is None:
            return
        key = params.get("key", "test_app/test_user")
        query = params.get("query", "")
        limit = params.get("limit", 10)
        await self._memory_service.search_memory(key=key, query=query, limit=limit)

    async def _handle_create_summary(self, params: dict[str, Any]) -> None:
        session_id = params["session_id"]
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"Session {session_id} not found in cache")
        await self._session_service.create_session_summary(session)

    async def _handle_get_summary(self, params: dict[str, Any]) -> None:
        session_id = params["session_id"]
        session = self._sessions.get(session_id)
        if session is None:
            return
        await self._session_service.get_session_summary(session)