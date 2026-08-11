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

from .runtime import AdvancedMemoryRuntime
from .coordination import CrossLoopLock
from .session_memory import SessionMemoryExtractor
from .transcript import build_event_transcript_record
from .transcript import find_last_event_id


class TranscriptSessionService(SessionServiceABC):
    """Decorate a legacy SessionService and append persisted Events."""

    def __init__(
        self,
        delegate: SessionServiceABC,
        memory_runtime: AdvancedMemoryRuntime,
        session_memory_extractor: SessionMemoryExtractor | None = None,
    ) -> None:
        """Store the legacy service and optional Advanced Memory runtime."""
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

    async def _ensure_initialized(self) -> None:
        """Initialize memory directories before the first transcript write."""
        if self._initialized or not self._memory_runtime.config.enabled:
            return
        async with self._initialize_lock:
            if self._initialized:
                return
            self._initialized = await self._memory_runtime.initialize()

    def _session_lock(self, session_id: str) -> CrossLoopLock:
        """Return an independent asynchronous write lock per session."""
        lock = self._session_locks.get(session_id)
        if lock is None:
            lock = CrossLoopLock()
            self._session_locks[session_id] = lock
        return lock

    async def _load_parent_if_needed(self, session_id: str) -> None:
        """Restore the parent-chain tail before the first session write."""
        if session_id in self._loaded_parent_sessions:
            return
        records = await self._memory_runtime.transcripts.read_all(session_id)
        self._last_event_ids[session_id] = find_last_event_id(records)
        self._loaded_parent_sessions.add(session_id)

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
        """Delete only the legacy session and retain transcript records."""
        async with self._session_lock(session_id):
            await self._delegate.delete_session(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
            )
        self._session_locks.pop(session_id, None)
        self._loaded_parent_sessions.discard(session_id)
        self._last_event_ids.pop(session_id, None)

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

        await self._ensure_initialized()
        async with self._session_lock(session.id):
            await self._load_parent_if_needed(session.id)
            record = build_event_transcript_record(
                session,
                persisted_event,
                parent_event_id=self._last_event_ids.get(session.id),
            )
            _, appended = await self._memory_runtime.transcripts.append_unique(
                session.id,
                record,
                unique_key="event_id",
            )
            if appended:
                self._last_event_ids[session.id] = record["event_id"]
        return persisted_event

    async def update_session(self, session: SessionABC) -> None:
        """Delegate session updates to the underlying service."""
        await self._delegate.update_session(session)

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
