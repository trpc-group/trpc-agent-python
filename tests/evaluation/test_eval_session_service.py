# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for EvalSessionService."""

from unittest.mock import AsyncMock, MagicMock

import trpc_agent_sdk.runners  # noqa: F401

from trpc_agent_sdk.evaluation._eval_session_service import EvalSessionService
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.types import Content


def _make_session():
    """Create a mock session with required attributes."""
    session = MagicMock()
    session.events = []
    return session


class TestEvalSessionServiceCreateSession:
    """Test suite for EvalSessionService.create_session."""

    async def test_no_context_messages(self):
        """Test create_session without context_messages delegates directly."""
        inner = AsyncMock()
        session = _make_session()
        inner.create_session.return_value = session
        svc = EvalSessionService(inner, context_messages=None)
        result = await svc.create_session(app_name="a", user_id="u")
        assert result is session
        inner.create_session.assert_called_once()
        inner.update_session.assert_not_called()

    async def test_with_context_messages(self):
        """Test create_session with context_messages prepends events."""
        inner = AsyncMock()
        session = _make_session()
        inner.create_session.return_value = session
        content = MagicMock()
        content.role = "user"
        svc = EvalSessionService(inner, context_messages=[content])
        result = await svc.create_session(app_name="a", user_id="u")
        assert result is session
        assert len(session.events) == 1
        assert session.events[0].author == "user"
        inner.update_session.assert_called_once_with(session)

    async def test_context_messages_consumed_once(self):
        """Test context_messages are consumed after first create_session."""
        inner = AsyncMock()
        session1 = _make_session()
        session2 = _make_session()
        inner.create_session.side_effect = [session1, session2]
        content = MagicMock()
        content.role = "assistant"
        svc = EvalSessionService(inner, context_messages=[content])
        await svc.create_session(app_name="a", user_id="u")
        assert len(session1.events) == 1
        await svc.create_session(app_name="a", user_id="u")
        assert len(session2.events) == 0

    async def test_multiple_context_messages_reversed(self):
        """Test multiple context messages are prepended in correct order."""
        inner = AsyncMock()
        session = _make_session()
        inner.create_session.return_value = session
        c1 = MagicMock()
        c1.role = "user"
        c2 = MagicMock()
        c2.role = "assistant"
        svc = EvalSessionService(inner, context_messages=[c1, c2])
        await svc.create_session(app_name="a", user_id="u")
        assert len(session.events) == 2
        assert session.events[0].author == "user"
        assert session.events[1].author == "assistant"


class TestEvalSessionServiceDelegates:
    """Test suite for EvalSessionService delegate methods."""

    async def test_get_session_delegates(self):
        """Test get_session delegates to inner."""
        inner = AsyncMock()
        svc = EvalSessionService(inner)
        await svc.get_session(app_name="a", user_id="u", session_id="s1")
        inner.get_session.assert_called_once()

    async def test_list_sessions_delegates(self):
        """Test list_sessions delegates to inner."""
        inner = AsyncMock()
        svc = EvalSessionService(inner)
        await svc.list_sessions(app_name="a", user_id="u")
        inner.list_sessions.assert_called_once()

    async def test_delete_session_delegates(self):
        """Test delete_session delegates to inner."""
        inner = AsyncMock()
        svc = EvalSessionService(inner)
        await svc.delete_session(app_name="a", user_id="u", session_id="s1")
        inner.delete_session.assert_called_once()

    async def test_append_event_delegates(self):
        """Test append_event delegates to inner."""
        inner = AsyncMock()
        svc = EvalSessionService(inner)
        session = _make_session()
        event = Event(author="user")
        await svc.append_event(session, event)
        inner.append_event.assert_called_once()

    async def test_update_session_delegates(self):
        """Test update_session delegates to inner."""
        inner = AsyncMock()
        svc = EvalSessionService(inner)
        session = _make_session()
        await svc.update_session(session)
        inner.update_session.assert_called_once()

    async def test_close_delegates(self):
        """Test close delegates to inner."""
        inner = AsyncMock()
        svc = EvalSessionService(inner)
        await svc.close()
        inner.close.assert_called_once()

    async def test_create_session_summary_delegates(self):
        """Test create_session_summary delegates to inner."""
        inner = AsyncMock()
        svc = EvalSessionService(inner)
        session = _make_session()
        await svc.create_session_summary(session)
        inner.create_session_summary.assert_called_once()

    async def test_get_session_summary_delegates(self):
        """Test get_session_summary delegates to inner."""
        inner = AsyncMock()
        svc = EvalSessionService(inner)
        session = _make_session()
        await svc.get_session_summary(session)
        inner.get_session_summary.assert_called_once()
