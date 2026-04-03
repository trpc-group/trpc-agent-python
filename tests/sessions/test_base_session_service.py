# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.sessions._base_session_service.

Covers:
- BaseSessionService: init, append_event, state updates, temp state trimming,
  filter_events, summarizer delegation, close
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trpc_agent_sdk.abc import ListSessionsResponse
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions._base_session_service import BaseSessionService
from trpc_agent_sdk.sessions._session import Session
from trpc_agent_sdk.sessions._summarizer_manager import SummarizerSessionManager
from trpc_agent_sdk.sessions._types import SessionServiceConfig
from trpc_agent_sdk.types import Content, EventActions, Part, State


class ConcreteSessionService(BaseSessionService):
    """Minimal concrete subclass for testing BaseSessionService logic."""

    async def create_session(self, **kwargs):
        raise NotImplementedError

    async def get_session(self, **kwargs):
        raise NotImplementedError

    async def list_sessions(self, **kwargs):
        return ListSessionsResponse()

    async def delete_session(self, **kwargs):
        pass


def _make_session(**kwargs) -> Session:
    defaults = dict(id="s1", app_name="app", user_id="user", save_key="app/user")
    defaults.update(kwargs)
    return Session(**defaults)


def _make_event(author: str = "agent", text: str = "hello", state_delta: dict | None = None,
                partial: bool = False) -> Event:
    actions = EventActions(state_delta=state_delta) if state_delta else EventActions()
    return Event(
        invocation_id="inv-1",
        author=author,
        content=Content(parts=[Part.from_text(text=text)]),
        actions=actions,
        partial=partial,
    )


class TestBaseSessionServiceInit:
    """Test initialization of BaseSessionService."""

    def test_default_init(self):
        svc = ConcreteSessionService()
        assert svc._summarizer_manager is None
        assert svc._session_config is not None
        assert svc._session_config.ttl.enable is False

    def test_init_with_config(self):
        config = SessionServiceConfig(max_events=10, event_ttl_seconds=30.0)
        svc = ConcreteSessionService(session_config=config)
        assert svc._session_config.max_events == 10
        assert svc._session_config.event_ttl_seconds == 30.0

    def test_init_with_summarizer(self):
        mock_model = MagicMock()
        mock_model.name = "test-model"
        manager = SummarizerSessionManager(model=mock_model)
        svc = ConcreteSessionService(summarizer_manager=manager)
        assert svc.summarizer_manager is manager

    def test_init_sets_service_on_summarizer(self):
        mock_model = MagicMock()
        mock_model.name = "test-model"
        manager = SummarizerSessionManager(model=mock_model)
        svc = ConcreteSessionService(summarizer_manager=manager)
        assert manager._base_service is svc


class TestBaseSessionServiceAppendEvent:
    """Test append_event method."""

    async def test_append_event_basic(self):
        svc = ConcreteSessionService()
        session = _make_session()
        event = _make_event()
        result = await svc.append_event(session, event)
        assert result is event
        assert len(session.events) == 1
        assert session.events[0] is event

    async def test_append_event_partial_skipped(self):
        svc = ConcreteSessionService()
        session = _make_session()
        event = _make_event(partial=True)
        result = await svc.append_event(session, event)
        assert result is event
        assert len(session.events) == 0

    async def test_append_event_updates_session_state(self):
        svc = ConcreteSessionService()
        session = _make_session()
        event = _make_event(state_delta={"my_key": "my_value"})
        await svc.append_event(session, event)
        assert session.state["my_key"] == "my_value"

    async def test_append_event_skips_temp_state(self):
        svc = ConcreteSessionService()
        session = _make_session()
        event = _make_event(state_delta={f"{State.TEMP_PREFIX}temp_key": "temp_value", "regular": "value"})
        await svc.append_event(session, event)
        assert f"{State.TEMP_PREFIX}temp_key" not in session.state
        assert session.state.get("regular") == "value"

    async def test_append_event_no_actions(self):
        svc = ConcreteSessionService()
        session = _make_session()
        event = Event(invocation_id="inv-1", author="agent",
                      content=Content(parts=[Part.from_text(text="test")]),
                      actions=EventActions())
        await svc.append_event(session, event)
        assert len(session.events) == 1

    async def test_append_event_empty_state_delta(self):
        svc = ConcreteSessionService()
        session = _make_session()
        event = _make_event(state_delta={})
        await svc.append_event(session, event)
        assert len(session.events) == 1


class TestBaseSessionServiceTrimTempDeltaState:
    """Test _trim_temp_delta_state method."""

    def test_trim_temp_keys(self):
        svc = ConcreteSessionService()
        event = _make_event(state_delta={
            f"{State.TEMP_PREFIX}t1": "val1",
            "normal_key": "val2",
        })
        result = svc._trim_temp_delta_state(event)
        assert f"{State.TEMP_PREFIX}t1" not in result.actions.state_delta
        assert result.actions.state_delta["normal_key"] == "val2"

    def test_trim_no_state_delta(self):
        svc = ConcreteSessionService()
        event = _make_event()
        result = svc._trim_temp_delta_state(event)
        assert result is event


class TestBaseSessionServiceFilterEvents:
    """Test filter_events method."""

    def test_filter_by_num_recent_events(self):
        config = SessionServiceConfig(num_recent_events=3)
        svc = ConcreteSessionService(session_config=config)
        session = _make_session()
        for i in range(10):
            session.events.append(_make_event(text=f"msg{i}"))
        svc.filter_events(session)
        assert len(session.events) == 3

    def test_filter_by_event_ttl(self):
        config = SessionServiceConfig(event_ttl_seconds=5.0)
        svc = ConcreteSessionService(session_config=config)
        session = _make_session()

        old_event = _make_event(text="old")
        old_event.timestamp = time.time() - 100
        session.events.append(old_event)

        new_event = _make_event(text="new")
        new_event.timestamp = time.time()
        session.events.append(new_event)

        svc.filter_events(session)
        assert len(session.events) == 1
        assert session.events[0].get_text() == "new"

    def test_filter_no_config(self):
        svc = ConcreteSessionService()
        session = _make_session()
        for i in range(5):
            session.events.append(_make_event(text=f"msg{i}"))
        svc.filter_events(session)
        assert len(session.events) == 5

    def test_filter_ttl_removes_all_old(self):
        config = SessionServiceConfig(event_ttl_seconds=1.0)
        svc = ConcreteSessionService(session_config=config)
        session = _make_session()
        for i in range(5):
            e = _make_event(text=f"old{i}")
            e.timestamp = time.time() - 100
            session.events.append(e)
        svc.filter_events(session)
        assert len(session.events) == 0


class TestBaseSessionServiceSetSummarizerManager:
    """Test set_summarizer_manager method."""

    def test_set_when_none(self):
        svc = ConcreteSessionService()
        mock_model = MagicMock()
        mock_model.name = "test"
        manager = SummarizerSessionManager(model=mock_model)
        svc.set_summarizer_manager(manager)
        assert svc.summarizer_manager is manager

    def test_set_does_not_overwrite(self):
        mock_model = MagicMock()
        mock_model.name = "test"
        manager1 = SummarizerSessionManager(model=mock_model)
        manager2 = SummarizerSessionManager(model=mock_model)
        svc = ConcreteSessionService(summarizer_manager=manager1)
        svc.set_summarizer_manager(manager2)
        assert svc.summarizer_manager is manager1

    def test_set_force_overwrites(self):
        mock_model = MagicMock()
        mock_model.name = "test"
        manager1 = SummarizerSessionManager(model=mock_model)
        manager2 = SummarizerSessionManager(model=mock_model)
        svc = ConcreteSessionService(summarizer_manager=manager1)
        svc.set_summarizer_manager(manager2, force=True)
        assert svc.summarizer_manager is manager2


class TestBaseSessionServiceSummarization:
    """Test create_session_summary and get_session_summary."""

    async def test_create_session_summary_with_manager(self):
        mock_model = MagicMock()
        mock_model.name = "test"
        manager = SummarizerSessionManager(model=mock_model)
        manager.create_session_summary = AsyncMock()
        svc = ConcreteSessionService(summarizer_manager=manager)
        session = _make_session()
        await svc.create_session_summary(session)
        manager.create_session_summary.assert_called_once()

    async def test_create_session_summary_no_manager(self):
        svc = ConcreteSessionService()
        session = _make_session()
        await svc.create_session_summary(session)

    async def test_get_session_summary_with_manager(self):
        mock_model = MagicMock()
        mock_model.name = "test"
        manager = SummarizerSessionManager(model=mock_model)
        mock_summary = MagicMock()
        mock_summary.summary_text = "This is a summary"
        manager.get_session_summary = AsyncMock(return_value=mock_summary)
        svc = ConcreteSessionService(summarizer_manager=manager)
        session = _make_session()
        result = await svc.get_session_summary(session)
        assert result == "This is a summary"

    async def test_get_session_summary_no_manager(self):
        svc = ConcreteSessionService()
        session = _make_session()
        result = await svc.get_session_summary(session)
        assert result is None

    async def test_get_session_summary_returns_none(self):
        mock_model = MagicMock()
        mock_model.name = "test"
        manager = SummarizerSessionManager(model=mock_model)
        manager.get_session_summary = AsyncMock(return_value=None)
        svc = ConcreteSessionService(summarizer_manager=manager)
        session = _make_session()
        result = await svc.get_session_summary(session)
        assert result is None


class TestBaseSessionServiceUpdateAndClose:
    """Test update_session and close."""

    async def test_update_session_default_noop(self):
        svc = ConcreteSessionService()
        session = _make_session()
        await svc.update_session(session)

    async def test_close(self):
        svc = ConcreteSessionService()
        await svc.close()
