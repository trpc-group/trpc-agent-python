"""Unit tests for trpc_agent_sdk.server.openclaw.session_memory._claw_memory_service."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.types import Content, Part, SearchMemoryResponse

from trpc_agent_sdk.server.openclaw.session_memory._claw_memory_service import ClawMemoryService
from trpc_agent_sdk.server.openclaw.storage import HISTORY_KEY, LONG_TERM_MEMORY_KEY


def _make_storage_manager():
    sm = MagicMock()
    sm.write_long_term = AsyncMock()
    sm.append_history = AsyncMock()
    sm.read_long_term = AsyncMock(return_value="")
    return sm


def _make_session(session_id="s1") -> Session:
    return Session(id=session_id, app_name="app", user_id="user", save_key="app/user/s1")


def _make_memory_event(text="long term memory content") -> Event:
    return Event(
        invocation_id="mem",
        author="system",
        content=Content(parts=[Part.from_text(text=text)]),
        timestamp=time.time(),
    )


class TestClawMemoryServiceInit:

    def test_stores_storage_manager(self):
        sm = _make_storage_manager()
        svc = ClawMemoryService(storage_manager=sm, enabled=True)
        assert svc.storage_manager is sm

    def test_enabled_flag(self):
        sm = _make_storage_manager()
        svc = ClawMemoryService(storage_manager=sm, enabled=True)
        assert svc.enabled is True

    def test_disabled_by_default(self):
        sm = _make_storage_manager()
        svc = ClawMemoryService(storage_manager=sm)
        assert svc.enabled is False


class TestStoreSession:

    @pytest.fixture
    def service(self):
        sm = _make_storage_manager()
        return ClawMemoryService(storage_manager=sm, enabled=True), sm

    async def test_with_long_term_memory(self, service):
        svc, sm = service
        session = _make_session()
        memory_event = _make_memory_event("my memory")
        agent_ctx = AgentContext()
        agent_ctx.with_metadata(LONG_TERM_MEMORY_KEY, memory_event)
        agent_ctx.with_metadata(HISTORY_KEY, "")

        await svc.store_session(session, agent_context=agent_ctx)

        sm.write_long_term.assert_awaited_once()
        call_args = sm.write_long_term.call_args
        assert "my memory" in call_args[0][1]

    async def test_with_history_entry(self, service):
        svc, sm = service
        session = _make_session()
        agent_ctx = AgentContext()
        agent_ctx.with_metadata(LONG_TERM_MEMORY_KEY, "")
        agent_ctx.with_metadata(HISTORY_KEY, "history entry text")

        await svc.store_session(session, agent_context=agent_ctx)

        sm.append_history.assert_awaited_once()
        call_args = sm.append_history.call_args
        assert "history entry text" in str(call_args)

    async def test_without_agent_context_raises(self, service):
        svc, sm = service
        session = _make_session()
        with pytest.raises(ValueError, match="Agent context is required"):
            await svc.store_session(session, agent_context=None)

    async def test_no_memory_no_history_no_writes(self, service):
        svc, sm = service
        session = _make_session()
        agent_ctx = AgentContext()
        agent_ctx.with_metadata(LONG_TERM_MEMORY_KEY, "")
        agent_ctx.with_metadata(HISTORY_KEY, "")

        await svc.store_session(session, agent_context=agent_ctx)

        sm.write_long_term.assert_not_awaited()
        sm.append_history.assert_not_awaited()


class TestSearchMemory:

    async def test_with_existing_memory(self):
        sm = _make_storage_manager()
        sm.read_long_term = AsyncMock(return_value="Stored long-term memory content")
        svc = ClawMemoryService(storage_manager=sm, enabled=True)

        result = await svc.search_memory(key="app/user/s1", query="test")

        assert isinstance(result, SearchMemoryResponse)
        assert len(result.memories) == 1
        assert result.memories[0].content.parts[0].text == "Stored long-term memory content"

    async def test_without_memory(self):
        sm = _make_storage_manager()
        sm.read_long_term = AsyncMock(return_value="")
        svc = ClawMemoryService(storage_manager=sm, enabled=True)

        result = await svc.search_memory(key="app/user/s1", query="test")

        assert isinstance(result, SearchMemoryResponse)
        assert len(result.memories) == 0
