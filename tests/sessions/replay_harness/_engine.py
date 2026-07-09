#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Replay engine for executing replay cases against session and memory backends."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel
from pydantic import Field

from trpc_agent_sdk.abc import MemoryServiceABC
from trpc_agent_sdk.abc import SessionServiceABC
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.sessions._session_summarizer import SessionSummary
from trpc_agent_sdk.sessions._summarizer_manager import SummarizerSessionManager
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import EventActions
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import Part

from ..replay_cases._base import ReplayCase
from ..replay_cases._base import ReplayOp


class BackendResult(BaseModel):
    """Raw result collected from replaying a case against one backend."""

    events: list[dict] = Field(default_factory=list)
    """Serialized event dicts from ``Event.model_dump()``."""

    state: dict = Field(default_factory=dict)
    """Session state after replay."""

    summaries: list[dict] = Field(default_factory=list)
    """Serialized ``SessionSummary`` dicts."""

    memory_entries: list[dict] = Field(default_factory=list)
    """Serialized ``MemoryEntry`` dicts from search results."""

    errors: list[str] = Field(default_factory=list)
    """Errors encountered during replay."""


class ReplayEngine:
    """Executes a ``ReplayCase`` against a pair of services."""

    def __init__(
        self,
        session_service: SessionServiceABC,
        memory_service: Optional[MemoryServiceABC] = None,
    ):
        self._session_service = session_service
        self._memory_service = memory_service

    async def run_case(self, case: ReplayCase) -> BackendResult:
        """Execute all operations in *case* and return the raw result."""
        result = BackendResult()

        setup = case.session_setup
        app_name = setup.get("app_name", "replay_test")
        user_id = setup.get("user_id", "test_user")
        session_id = setup.get("session_id", case.case_id)
        initial_state = setup.get("state", {}) or {}

        session = await self._session_service.create_session(
            app_name=app_name,
            user_id=user_id,
            state=initial_state.copy(),
            session_id=session_id,
        )

        last_event: Optional[Event] = None

        for op in case.operations:
            try:
                await self._execute_op(op, session, result, last_event)
                if op.op == "append_event" and last_event is None:
                    pass
            except Exception as exc:
                result.errors.append(f"{op.op}: {exc}")

            if op.op == "append_event":
                last_event = self._build_event(op)
            elif op.op == "read_back":
                refreshed = await self._session_service.get_session(
                    app_name=app_name,
                    user_id=user_id,
                    session_id=session_id,
                )
                if refreshed is not None:
                    session = refreshed

        events = getattr(session, "events", [])
        result.events = [e.model_dump(mode="json") for e in events]
        result.state = dict(session.state) if hasattr(session, "state") else {}

        result.summaries = self._collect_summaries(session)
        result.memory_entries = self._collect_memory_entries(session)

        return result

    async def _execute_op(
        self,
        op: ReplayOp,
        session: Session,
        result: BackendResult,
        last_event: Optional[Event],
    ) -> None:
        """Dispatch a single replay operation."""
        op_type = op.op

        if op_type == "append_event":
            event = self._build_event(op)
            await self._session_service.append_event(session, event)
            return

        if op_type == "update_state":
            event = self._build_event(op)
            await self._session_service.append_event(session, event)
            return

        if op_type == "inject_summary":
            self._inject_summary(session, op)
            return

        if op_type == "store_memory":
            if self._memory_service and self._memory_service.enabled:
                await self._memory_service.store_session(session)
            return

        if op_type == "search_memory":
            if self._memory_service and self._memory_service.enabled:
                query = op.query or ""
                response = await self._memory_service.search_memory(
                    key=session.id,
                    query=query,
                    limit=10,
                )
                entries = response.memories if response else []
                result.memory_entries = [e.model_dump(mode="json") for e in entries]
            return

        if op_type == "duplicate_append":
            if last_event is not None:
                dup = self._copy_event(last_event)
                try:
                    await self._session_service.append_event(session, dup)
                except Exception:
                    pass
            return

        if op_type == "delete_session":
            app = getattr(session, "app_name", "replay_test")
            uid = getattr(session, "user_id", "test_user")
            await self._session_service.delete_session(app_name=app, user_id=uid, session_id=session.id)
            return

        if op_type == "read_back":
            return

    # ── event construction ─────────────────────────────────────────────

    @staticmethod
    def _build_event(op: ReplayOp) -> Event:
        """Build an ``Event`` from a replay operation dict."""
        parts: list[Part] = []

        if op.text:
            parts.append(Part.from_text(text=op.text))

        if op.function_call:
            fc = FunctionCall(
                id="call-replay",
                name=op.function_call.get("name", ""),
                args=op.function_call.get("args", {}),
            )
            parts.append(Part(function_call=fc))

        if op.function_response:
            fr = FunctionResponse(
                id="resp-replay",
                name=op.function_response.get("name", ""),
                response=op.function_response.get("response", {}),
            )
            parts.append(Part(function_response=fr))

        content = Content(parts=parts, role="user") if parts else Content()

        actions = EventActions()
        if op.state_delta:
            actions.state_delta = op.state_delta

        return Event(
            invocation_id="replay-inv",
            author=op.author or "user",
            content=content,
            actions=actions,
            partial=op.partial,
        )

    @staticmethod
    def _copy_event(event: Event) -> Event:
        """Create a structural copy of *event* with a fresh ID."""
        data = event.model_dump()
        data.pop("id", None)
        return Event(**data)

    # ── summary injection ──────────────────────────────────────────────

    def _inject_summary(self, session: Session, op: ReplayOp) -> None:
        """Bypass the LLM and inject a ``SessionSummary`` into the cache."""
        manager: Optional[SummarizerSessionManager] = getattr(
            self._session_service, "_summarizer_manager", None)
        if manager is None:
            return

        cache = getattr(manager, "_summarizer_cache", None)
        if cache is None:
            return

        import time

        summary = SessionSummary(
            session_id=op.session_id or session.id,
            summary_text=op.summary_text,
            original_event_count=op.original_event_count,
            compressed_event_count=op.compressed_event_count,
            summary_timestamp=time.time(),
        )

        app_name = session.app_name
        user_id = session.user_id
        cache.setdefault(app_name, {})
        cache[app_name].setdefault(user_id, {})
        cache[app_name][user_id][session.id] = summary

        summary_event = Event(
            invocation_id="summary",
            author="system",
            content=Content(
                parts=[Part.from_text(text=f"Previous conversation summary: {op.summary_text}")],
                role="user",
            ),
            timestamp=time.time(),
        )
        summary_event.set_summary_event(True)
        session.events.insert(0, summary_event)

    # ── result collection ──────────────────────────────────────────────

    def _collect_summaries(self, session: Session) -> list[dict]:
        manager: Optional[SummarizerSessionManager] = getattr(
            self._session_service, "_summarizer_manager", None)
        if manager is None:
            return []
        cache = getattr(manager, "_summarizer_cache", None)
        if cache is None:
            return []
        entries = cache.get(session.app_name, {}).get(session.user_id, {})
        return [s.model_dump(mode="json") for s in entries.values()]

    def _collect_memory_entries(self, session: Session) -> list[dict]:
        return []  # collected inline during search_memory op
