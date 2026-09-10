# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Decorate a SessionService to record a complete transcript."""

from __future__ import annotations

from typing import Any
from typing import TYPE_CHECKING

from trpc_agent_sdk.abc import ListSessionsResponse
from trpc_agent_sdk.abc import ResponseABC
from trpc_agent_sdk.abc import SessionABC
from trpc_agent_sdk.abc import SessionServiceABC

if TYPE_CHECKING:
    from trpc_agent_sdk.context import AgentContext
    from trpc_agent_sdk.context import InvocationContext

from ._runtime import AdvancedMemoryRuntime
from ._coordination import CrossLoopLock
from ._session_memory import SessionMemoryExtractor
from ._transcript import build_event_transcript_record
from ._transcript import find_last_event_id


class TranscriptSessionService(SessionServiceABC):
    """Decorate a legacy SessionService and append persisted Events."""

    def __init__(
        self,
        delegate: SessionServiceABC,
        memory_runtime: AdvancedMemoryRuntime,
        session_memory_extractor: SessionMemoryExtractor | None = None,
    ) -> None:
        """Store the legacy service and optional Advanced Memory runtime."""
        if isinstance(delegate, TranscriptSessionService):
            raise ValueError("Transcript session service is already wrapped")
        self._delegate = delegate
        self._memory_runtime = memory_runtime
        self._session_memory_extractor = session_memory_extractor
        self._initialize_lock = CrossLoopLock()
        self._initialized = False
        self._session_locks: dict[str, CrossLoopLock] = {}
        self._loaded_parent_sessions: set[str] = set()
        self._last_event_ids: dict[str, str | None] = {}

    @property
    def delegate(self) -> SessionServiceABC:
        """Return the unchanged underlying SessionService."""
        return self._delegate

    @property
    def memory_runtime(self) -> AdvancedMemoryRuntime:
        """Return the Advanced Memory runtime used by the decorator."""
        return self._memory_runtime

    @property
    def session_config(self) -> Any:
        """Expose the original service configuration."""
        return getattr(self._delegate, "session_config", None)

    @property
    def summarizer_manager(self) -> Any:
        """Expose the original service summarizer, when configured."""
        return getattr(self._delegate, "summarizer_manager", None)

    @property
    def session_memory_extractor(self) -> SessionMemoryExtractor | None:
        """Return the session memory extractor used after each turn."""
        return self._session_memory_extractor

    def attach_session_memory_extractor(
        self,
        extractor: SessionMemoryExtractor,
    ) -> None:
        """Attach a session memory extractor when one is not configured."""
        if self._session_memory_extractor is not None:
            if self._session_memory_extractor is not extractor:
                raise ValueError("Session memory extractor is already configured")
            return
        if extractor.runtime is not self._memory_runtime:
            raise ValueError("Session memory extractor uses another runtime")
        self._session_memory_extractor = extractor

    async def _ensure_initialized(self, session: SessionABC) -> None:
        """Initialize memory directories before the first transcript write."""
        if self._initialized or not self._memory_runtime.config.enabled:
            return
        async with self._initialize_lock:
            if self._initialized:
                return
            self._initialized = await self._memory_runtime.for_session(session).initialize()

    def _session_lock(self, session: SessionABC) -> CrossLoopLock:
        """Return an independent asynchronous write lock per session."""
        key = self._memory_runtime.for_session(session).session_key(session.id)
        lock = self._session_locks.get(key)
        if lock is None:
            lock = CrossLoopLock()
            self._session_locks[key] = lock
        return lock

    async def _load_parent_if_needed(self, session: SessionABC) -> None:
        """Restore the parent-chain tail before the first session write."""
        runtime = self._memory_runtime.for_session(session)
        key = runtime.session_key(session.id)
        if key in self._loaded_parent_sessions:
            return
        records = await runtime.transcripts.read_all(session.id)
        self._last_event_ids[key] = find_last_event_id(records)
        self._loaded_parent_sessions.add(key)

    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: dict[str, Any] | None = None,
        session_id: str | None = None,
        agent_context: AgentContext | None = None,
    ) -> SessionABC:
        """Delegate session creation to the underlying service."""
        return await self._delegate.create_session(
            app_name=app_name,
            user_id=user_id,
            state=state,
            session_id=session_id,
            agent_context=agent_context,
        )

    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        agent_context: AgentContext | None = None,
    ) -> SessionABC | None:
        """Delegate session reads to the underlying service."""
        return await self._delegate.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            agent_context=agent_context,
        )

    async def list_sessions(
        self,
        *,
        app_name: str,
        user_id: str | None = None,
    ) -> ListSessionsResponse:
        """Delegate session listing to the underlying service."""
        return await self._delegate.list_sessions(app_name=app_name, user_id=user_id)

    async def delete_session(self, *, app_name: str, user_id: str, session_id: str) -> None:
        """Delete the framework session and all Advanced Memory session data."""
        runtime = self._memory_runtime.for_scope(app_name, user_id)
        scope_key = runtime.session_key(session_id)
        lock = self._session_locks.setdefault(scope_key, CrossLoopLock())
        async with lock:
            await self._delegate.delete_session(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
            )
            await runtime.delete_session(session_id)
        self._session_locks.pop(scope_key, None)
        self._loaded_parent_sessions.discard(scope_key)
        self._last_event_ids.pop(scope_key, None)

    async def append_event(self, session: SessionABC, event: ResponseABC) -> ResponseABC:
        """Append each persisted non-streaming Event in order."""
        usage_metadata = getattr(event, "usage_metadata", None)
        state = getattr(session, "state", None)
        context_fingerprint = (state.get("advanced_memory_pending_request_context_fingerprint") if isinstance(
            state, dict) else None)
        if usage_metadata is not None and isinstance(context_fingerprint, str):
            metadata = dict(getattr(event, "custom_metadata", None) or {})
            metadata["advanced_memory_request_context_fingerprint"] = context_fingerprint
            event.custom_metadata = metadata
        persisted_event = await self._delegate.append_event(session=session, event=event)
        if not self._memory_runtime.config.enabled or getattr(persisted_event, "partial", False):
            return persisted_event

        await self._ensure_initialized(session)
        runtime = self._memory_runtime.for_session(session)
        key = runtime.session_key(session.id)
        async with self._session_lock(session):
            await self._load_parent_if_needed(session)
            record = build_event_transcript_record(
                session,
                persisted_event,
                parent_event_id=self._last_event_ids.get(key),
            )
            _, appended = await runtime.transcripts.append_unique(
                session.id,
                record,
                unique_key="event_id",
            )
            if appended:
                self._last_event_ids[key] = record["event_id"]
        return persisted_event

    async def update_session(self, session: SessionABC) -> None:
        """Delegate session updates to the underlying service."""
        await self._delegate.update_session(session)

    async def patch_session_state(
        self,
        session: SessionABC,
        state_delta: dict[str, Any],
    ) -> None:
        """Delegate state-only updates without touching persisted Events."""
        await self._delegate.patch_session_state(session, state_delta)

    async def create_session_summary(
        self,
        session: SessionABC,
        ctx: InvocationContext | None = None,
    ) -> None:
        """Preserve legacy summaries, then update session memory as needed."""
        await self._delegate.create_session_summary(session, ctx=ctx)
        if self._session_memory_extractor is not None and ctx is not None:
            await self._session_memory_extractor.extract_if_needed(session, ctx)

    async def get_session_summary(self, session: SessionABC) -> str | None:
        """Delegate session summary reads to the legacy service."""
        return await self._delegate.get_session_summary(session)

    async def close(self) -> None:
        """Close the legacy service while preserving its lifecycle semantics."""
        await self._delegate.close()
