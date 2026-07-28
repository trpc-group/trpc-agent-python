# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.sessions._summarizer_manager.

Covers:
- SummarizerSessionManager: init, set_session_service, set_summarizer,
  create_session_summary, get_session_summary, should_summarize_session,
  get_summarizer_metadata
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions._session import Session
from trpc_agent_sdk.sessions._session_summarizer import SessionSummarizer, SessionSummary
from trpc_agent_sdk.sessions._session_summarizer import SESSION_SUMMARY_METADATA_KEY
from trpc_agent_sdk.sessions._summarizer_manager import SummarizerSessionManager
from trpc_agent_sdk.types import Content, Part


def _make_session(events=None, conversation_count=0) -> Session:
    s = Session(id="s1", app_name="app", user_id="user", save_key="app/user")
    s.events = events or []
    s.conversation_count = conversation_count
    return s


def _make_event(text="hello") -> Event:
    return Event(
        invocation_id="inv-1",
        author="agent",
        content=Content(parts=[Part.from_text(text=text)]),
    )


def _make_model():
    model = MagicMock()
    model.name = "test-model"
    return model


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

class TestSummarizerSessionManagerInit:
    def test_init_with_model(self):
        model = _make_model()
        manager = SummarizerSessionManager(model=model)
        assert manager._summarizer is not None
        assert manager._base_service is None
        assert manager._auto_summarize is True

    def test_init_with_custom_summarizer(self):
        model = _make_model()
        summarizer = SessionSummarizer(model=model)
        manager = SummarizerSessionManager(model=model, summarizer=summarizer)
        assert manager._summarizer is summarizer

    def test_init_auto_summarize_false(self):
        model = _make_model()
        manager = SummarizerSessionManager(model=model, auto_summarize=False)
        assert manager._auto_summarize is False


# ---------------------------------------------------------------------------
# set_session_service / set_summarizer
# ---------------------------------------------------------------------------

class TestSetSessionService:
    def test_set_service(self):
        model = _make_model()
        manager = SummarizerSessionManager(model=model)
        mock_service = MagicMock()
        manager.set_session_service(mock_service)
        assert manager._base_service is mock_service

    def test_set_service_no_overwrite(self):
        model = _make_model()
        manager = SummarizerSessionManager(model=model)
        svc1 = MagicMock()
        svc2 = MagicMock()
        manager.set_session_service(svc1)
        manager.set_session_service(svc2)
        assert manager._base_service is svc1

    def test_set_service_force(self):
        model = _make_model()
        manager = SummarizerSessionManager(model=model)
        svc1 = MagicMock()
        svc2 = MagicMock()
        manager.set_session_service(svc1)
        manager.set_session_service(svc2, force=True)
        assert manager._base_service is svc2


class TestSetSummarizer:
    def test_set_summarizer(self):
        model = _make_model()
        manager = SummarizerSessionManager(model=model)
        new_summarizer = SessionSummarizer(model=model)
        original = manager._summarizer
        manager.set_summarizer(new_summarizer)
        assert manager._summarizer is original  # won't overwrite

    def test_set_summarizer_force(self):
        model = _make_model()
        manager = SummarizerSessionManager(model=model)
        new_summarizer = SessionSummarizer(model=model)
        manager.set_summarizer(new_summarizer, force=True)
        assert manager._summarizer is new_summarizer

    def test_set_summarizer_when_none(self):
        model = _make_model()
        manager = SummarizerSessionManager(model=model)
        manager._summarizer = None
        new_summarizer = SessionSummarizer(model=model)
        manager.set_summarizer(new_summarizer)
        assert manager._summarizer is new_summarizer


# ---------------------------------------------------------------------------
# create_session_summary
# ---------------------------------------------------------------------------

class TestCreateSessionSummary:
    async def test_summary_when_should_summarize(self):
        model = _make_model()
        manager = SummarizerSessionManager(model=model)
        manager._summarizer.should_summarize = AsyncMock(return_value=True)
        manager._summarizer.create_session_summary = AsyncMock(return_value="summary text")
        mock_service = AsyncMock()
        manager.set_session_service(mock_service)

        session = _make_session(events=[_make_event()])
        await manager.create_session_summary(session)

        manager._summarizer.create_session_summary.assert_called_once()
        mock_service.update_session.assert_called_once()

    async def test_no_summary_when_should_not_summarize(self):
        model = _make_model()
        manager = SummarizerSessionManager(model=model)
        manager._summarizer.should_summarize = AsyncMock(return_value=False)
        manager._summarizer.create_session_summary = AsyncMock()

        session = _make_session(events=[_make_event()])
        await manager.create_session_summary(session)

        manager._summarizer.create_session_summary.assert_not_called()

    async def test_force_summary(self):
        model = _make_model()
        manager = SummarizerSessionManager(model=model)
        manager._summarizer.should_summarize = AsyncMock(return_value=False)
        manager._summarizer.create_session_summary = AsyncMock(return_value="forced summary")
        mock_service = AsyncMock()
        manager.set_session_service(mock_service)

        session = _make_session(events=[_make_event()])
        await manager.create_session_summary(session, force=True)

        manager._summarizer.create_session_summary.assert_called_once()

    async def test_no_base_service(self):
        model = _make_model()
        manager = SummarizerSessionManager(model=model)
        manager._summarizer.should_summarize = AsyncMock(return_value=True)
        manager._summarizer.create_session_summary = AsyncMock(return_value="text")

        session = _make_session(events=[_make_event()])
        await manager.create_session_summary(session)

    async def test_summary_text_none_still_updates_session(self):
        model = _make_model()
        manager = SummarizerSessionManager(model=model)
        manager._summarizer.should_summarize = AsyncMock(return_value=True)
        manager._summarizer.create_session_summary = AsyncMock(return_value=None)
        mock_service = AsyncMock()
        manager.set_session_service(mock_service)

        session = _make_session(events=[_make_event()])
        await manager.create_session_summary(session)

        mock_service.update_session.assert_called_once()
        assert "s1" not in manager._summarizer_cache.get("app", {}).get("user", {})


# ---------------------------------------------------------------------------
# get_session_summary
# ---------------------------------------------------------------------------

class TestGetSessionSummary:
    async def test_get_cached_summary(self):
        model = _make_model()
        manager = SummarizerSessionManager(model=model)
        summary = SessionSummary(
            session_id="s1",
            summary_text="cached summary",
            original_event_count=10,
            compressed_event_count=3,
            summary_timestamp=time.time(),
        )
        manager._summarizer_cache = {"app": {"user": {"s1": summary}}}
        session = _make_session()
        result = await manager.get_session_summary(session)
        assert result is summary

    async def test_get_no_cache(self):
        model = _make_model()
        manager = SummarizerSessionManager(model=model)
        session = _make_session()
        result = await manager.get_session_summary(session)
        assert result is None

    async def test_get_rebuilds_cache_from_persisted_summary_event(self):
        """Recover full version metadata after cache loss.

        验证缓存丢失后可从持久化摘要事件恢复版本、覆盖链和实际更新时间。
        """
        model = _make_model()
        manager = SummarizerSessionManager(model=model)
        summary_event = Event(
            id="summary-2",
            invocation_id="summary",
            author="system",
            version=2,
            timestamp=123.0,
            content=Content(parts=[Part.from_text(text="Previous conversation summary: persisted")]),
            custom_metadata={
                SESSION_SUMMARY_METADATA_KEY: {
                    "session_id": "s1",
                    "summary_text": "persisted",
                    "version": 2,
                    "replaces_summary_id": "summary-1",
                    "original_event_count": 8,
                    "compressed_event_count": 3,
                    "summary_timestamp": 456.0,
                }
            },
        )
        summary_event.set_summary_event(True)
        session = _make_session(events=[summary_event])

        result = await manager.get_session_summary(session)

        assert result.summary_id == "summary-2"
        assert result.session_id == "s1"
        assert result.summary_text == "persisted"
        assert result.version == 2
        assert result.replaces_summary_id == "summary-1"
        assert result.summary_timestamp == 456.0
        assert manager._summarizer_cache["app"]["user"]["s1"] is result

    async def test_get_rejects_summary_owned_by_another_session(self):
        """Reject a persisted summary whose explicit owner is another session.

        验证显式归属于其他会话的持久化摘要不会被当前会话错误加载。
        """
        model = _make_model()
        manager = SummarizerSessionManager(model=model)
        summary_event = Event(
            id="summary-wrong-owner",
            invocation_id="summary",
            author="system",
            content=Content(parts=[Part.from_text(text="Previous conversation summary: wrong")]),
            custom_metadata={
                SESSION_SUMMARY_METADATA_KEY: {
                    "session_id": "another-session",
                    "summary_text": "wrong",
                    "version": 1,
                    "original_event_count": 4,
                    "compressed_event_count": 2,
                    "summary_timestamp": 123.0,
                }
            },
        )
        summary_event.set_summary_event(True)
        session = _make_session(events=[summary_event])

        assert await manager.get_session_summary(session) is None

    async def test_get_no_summarizer(self):
        model = _make_model()
        manager = SummarizerSessionManager(model=model)
        manager._summarizer = None
        session = _make_session()
        result = await manager.get_session_summary(session)
        assert result is None

    async def test_get_missing_app(self):
        model = _make_model()
        manager = SummarizerSessionManager(model=model)
        manager._summarizer_cache = {"other_app": {}}
        session = _make_session()
        result = await manager.get_session_summary(session)
        assert result is None

    async def test_get_missing_user(self):
        model = _make_model()
        manager = SummarizerSessionManager(model=model)
        manager._summarizer_cache = {"app": {"other_user": {}}}
        session = _make_session()
        result = await manager.get_session_summary(session)
        assert result is None

    async def test_get_missing_session(self):
        model = _make_model()
        manager = SummarizerSessionManager(model=model)
        manager._summarizer_cache = {"app": {"user": {"other_session": MagicMock()}}}
        session = _make_session()
        result = await manager.get_session_summary(session)
        assert result is None


# ---------------------------------------------------------------------------
# should_summarize_session
# ---------------------------------------------------------------------------

class TestShouldSummarizeSession:
    async def test_auto_summarize_disabled(self):
        model = _make_model()
        manager = SummarizerSessionManager(model=model, auto_summarize=False)
        session = _make_session(events=[_make_event()])
        assert await manager.should_summarize_session(session) is False

    async def test_no_summarizer(self):
        model = _make_model()
        manager = SummarizerSessionManager(model=model)
        manager._summarizer = None
        session = _make_session(events=[_make_event()])
        assert await manager.should_summarize_session(session) is False

    async def test_delegates_to_summarizer(self):
        model = _make_model()
        manager = SummarizerSessionManager(model=model)
        manager._summarizer.should_summarize = AsyncMock(return_value=True)
        session = _make_session(events=[_make_event()])
        assert await manager.should_summarize_session(session) is True


# ---------------------------------------------------------------------------
# get_summarizer_metadata
# ---------------------------------------------------------------------------

class TestGetSummarizerMetadata:
    def test_with_summarizer(self):
        model = _make_model()
        manager = SummarizerSessionManager(model=model)
        metadata = manager.get_summarizer_metadata()
        assert "model_name" in metadata

    def test_without_summarizer(self):
        model = _make_model()
        manager = SummarizerSessionManager(model=model)
        manager._summarizer = None
        metadata = manager.get_summarizer_metadata()
        assert metadata == {}
