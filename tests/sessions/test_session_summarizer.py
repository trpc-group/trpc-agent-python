# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.sessions._session_summarizer.

Covers:
- SessionSummary: get_compression_ratio, to_dict
- SessionSummarizer: should_summarize, _has_important_content,
  _extract_conversation_text, _create_summarization_prompt,
  create_session_summary, create_session_summary_by_events, get_summary_metadata
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions._session import Session
from trpc_agent_sdk.sessions._session_summarizer import (
    DEFAULT_SUMMARIZER_PROMPT,
    SessionSummarizer,
    SessionSummary,
)
from trpc_agent_sdk.types import Content, EventActions, FunctionCall, FunctionResponse, Part

_DEFAULT_ACTIONS = EventActions()


def _make_session(events=None) -> Session:
    s = Session(id="s1", app_name="app", user_id="user", save_key="app/user")
    s.events = events or []
    return s


def _make_event(author="agent", text="hello", partial=False, branch=None, skip_summarization=False) -> Event:
    actions = EventActions(skip_summarization=True) if skip_summarization else EventActions()
    return Event(
        invocation_id="inv-1",
        author=author,
        content=Content(parts=[Part.from_text(text=text)]),
        partial=partial,
        branch=branch,
        actions=actions,
    )


def _make_model_mock():
    model = MagicMock()
    model.name = "test-model"
    return model


# ---------------------------------------------------------------------------
# SessionSummary
# ---------------------------------------------------------------------------

class TestSessionSummary:
    def test_get_compression_ratio(self):
        summary = SessionSummary(
            session_id="s1",
            summary_text="summary",
            original_event_count=100,
            compressed_event_count=10,
            summary_timestamp=time.time(),
        )
        assert summary.get_compression_ratio() == 90.0

    def test_get_compression_ratio_zero_original(self):
        summary = SessionSummary(
            session_id="s1",
            summary_text="summary",
            original_event_count=0,
            compressed_event_count=0,
            summary_timestamp=time.time(),
        )
        assert summary.get_compression_ratio() == 0.0

    def test_get_compression_ratio_no_compression(self):
        summary = SessionSummary(
            session_id="s1",
            summary_text="summary",
            original_event_count=10,
            compressed_event_count=10,
            summary_timestamp=time.time(),
        )
        assert summary.get_compression_ratio() == 0.0


# ---------------------------------------------------------------------------
# SessionSummarizer — should_summarize
# ---------------------------------------------------------------------------

class TestShouldSummarize:
    async def test_empty_events(self):
        model = _make_model_mock()
        summarizer = SessionSummarizer(model=model)
        session = _make_session(events=[])
        assert await summarizer.should_summarize(session) is False

    async def test_default_checker_below_threshold(self):
        model = _make_model_mock()
        summarizer = SessionSummarizer(model=model)
        session = _make_session(events=[_make_event()])
        session.conversation_count = 5
        assert await summarizer.should_summarize(session) is False

    async def test_custom_checker_passes(self):
        model = _make_model_mock()
        checker = lambda s: True
        summarizer = SessionSummarizer(model=model, check_summarizer_functions=[checker])
        session = _make_session(events=[_make_event()])
        assert await summarizer.should_summarize(session) is True

    async def test_custom_checker_fails(self):
        model = _make_model_mock()
        checker = lambda s: False
        summarizer = SessionSummarizer(model=model, check_summarizer_functions=[checker])
        session = _make_session(events=[_make_event()])
        assert await summarizer.should_summarize(session) is False

    async def test_multiple_checkers_all_must_pass(self):
        model = _make_model_mock()
        c1 = lambda s: True
        c2 = lambda s: False
        summarizer = SessionSummarizer(model=model, check_summarizer_functions=[c1, c2])
        session = _make_session(events=[_make_event()])
        assert await summarizer.should_summarize(session) is False


# ---------------------------------------------------------------------------
# SessionSummarizer — _has_important_content
# ---------------------------------------------------------------------------

class TestHasImportantContent:
    def test_no_events(self):
        model = _make_model_mock()
        summarizer = SessionSummarizer(model=model)
        assert summarizer._has_important_content([]) is False

    def test_event_with_long_text(self):
        model = _make_model_mock()
        summarizer = SessionSummarizer(model=model)
        events = [_make_event(text="This is a meaningful conversation")]
        assert summarizer._has_important_content(events) is True

    def test_event_with_short_text(self):
        model = _make_model_mock()
        summarizer = SessionSummarizer(model=model)
        events = [_make_event(text="hi")]
        assert summarizer._has_important_content(events) is False

    def test_event_without_content(self):
        model = _make_model_mock()
        summarizer = SessionSummarizer(model=model)
        event = Event(invocation_id="inv-1", author="agent", actions=_DEFAULT_ACTIONS)
        assert summarizer._has_important_content([event]) is False

    def test_event_with_empty_parts(self):
        model = _make_model_mock()
        summarizer = SessionSummarizer(model=model)
        event = Event(invocation_id="inv-1", author="agent", content=Content(parts=[]), actions=_DEFAULT_ACTIONS)
        assert summarizer._has_important_content([event]) is False


# ---------------------------------------------------------------------------
# SessionSummarizer — _extract_conversation_text
# ---------------------------------------------------------------------------

class TestExtractConversationText:
    def test_basic_extraction(self):
        model = _make_model_mock()
        summarizer = SessionSummarizer(model=model)
        events = [
            _make_event(author="user", text="What is AI?"),
            _make_event(author="agent", text="AI is artificial intelligence."),
        ]
        text = summarizer._extract_conversation_text(events)
        assert "user: What is AI?" in text
        assert "agent: AI is artificial intelligence." in text

    def test_skip_summarization_events(self):
        model = _make_model_mock()
        summarizer = SessionSummarizer(model=model)
        events = [
            _make_event(author="user", text="Question"),
            _make_event(author="agent", text="Skipped", skip_summarization=True),
            _make_event(author="agent", text="Included"),
        ]
        text = summarizer._extract_conversation_text(events)
        assert "Skipped" not in text
        assert "Included" in text

    def test_empty_events(self):
        model = _make_model_mock()
        summarizer = SessionSummarizer(model=model)
        text = summarizer._extract_conversation_text([])
        assert text == ""

    def test_event_without_content(self):
        model = _make_model_mock()
        summarizer = SessionSummarizer(model=model)
        event = Event(invocation_id="inv-1", author="agent", actions=_DEFAULT_ACTIONS)
        text = summarizer._extract_conversation_text([event])
        assert text == ""

    def test_function_call_extraction(self):
        model = _make_model_mock()
        summarizer = SessionSummarizer(model=model)
        fc = FunctionCall(name="search", args={"query": "test"})
        event = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(parts=[Part(function_call=fc)]),
            actions=_DEFAULT_ACTIONS,
        )
        text = summarizer._extract_conversation_text([event])
        assert "tool_call" in text
        assert "search" in text

    def test_function_response_extraction(self):
        model = _make_model_mock()
        summarizer = SessionSummarizer(model=model)
        fr = FunctionResponse(name="search", response={"result": "found"})
        event = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(parts=[Part(function_response=fr)]),
            actions=_DEFAULT_ACTIONS,
        )
        text = summarizer._extract_conversation_text([event])
        assert "tool_response" in text
        assert "search" in text

    def test_partial_events_merged(self):
        model = _make_model_mock()
        summarizer = SessionSummarizer(model=model)
        events = [
            _make_event(author="agent", text="part1", partial=True, branch="main"),
            _make_event(author="agent", text="part2", partial=True, branch="main"),
        ]
        text = summarizer._extract_conversation_text(events)
        assert "part1" in text
        assert "part2" in text

    def test_whitespace_only_text_skipped(self):
        model = _make_model_mock()
        summarizer = SessionSummarizer(model=model)
        events = [_make_event(text="   ")]
        text = summarizer._extract_conversation_text(events)
        assert text == ""


# ---------------------------------------------------------------------------
# SessionSummarizer — _create_summarization_prompt
# ---------------------------------------------------------------------------

class TestCreateSummarizationPrompt:
    def test_default_prompt(self):
        model = _make_model_mock()
        summarizer = SessionSummarizer(model=model)
        prompt = summarizer._create_summarization_prompt("Hello conversation")
        assert "Hello conversation" in prompt
        assert "Summary:" in prompt

    def test_custom_prompt(self):
        model = _make_model_mock()
        custom = "Summarize: {conversation_text}"
        summarizer = SessionSummarizer(model=model, summarizer_prompt=custom)
        prompt = summarizer._create_summarization_prompt("test content")
        assert prompt == "Summarize: test content"


# ---------------------------------------------------------------------------
# SessionSummarizer — _generate_summary
# ---------------------------------------------------------------------------

class TestGenerateSummary:
    async def test_generate_summary_success(self):
        model = _make_model_mock()
        llm_response = MagicMock()
        llm_response.content = Content(parts=[Part.from_text(text="This is the summary.")])

        async def mock_generate(request, stream=False, ctx=None):
            yield llm_response

        model.generate_async = mock_generate
        summarizer = SessionSummarizer(model=model)
        result = await summarizer._generate_summary("conversation text")
        assert result == "This is the summary."

    async def test_generate_summary_truncated(self):
        model = _make_model_mock()
        long_text = "A" * 2000
        llm_response = MagicMock()
        llm_response.content = Content(parts=[Part.from_text(text=long_text)])

        async def mock_generate(request, stream=False, ctx=None):
            yield llm_response

        model.generate_async = mock_generate
        summarizer = SessionSummarizer(model=model, max_summary_length=100)
        result = await summarizer._generate_summary("conversation text")
        assert len(result) <= 104  # 100 + "..."
        assert result.endswith("...")

    async def test_generate_summary_error(self):
        model = _make_model_mock()

        async def mock_generate(request, stream=False, ctx=None):
            raise RuntimeError("LLM error")
            yield  # pragma: no cover

        model.generate_async = mock_generate
        summarizer = SessionSummarizer(model=model)
        result = await summarizer._generate_summary("text")
        assert result == ""


# ---------------------------------------------------------------------------
# SessionSummarizer — create_session_summary_by_events
# ---------------------------------------------------------------------------

class TestCreateSessionSummaryByEvents:
    async def test_summary_with_keep_recent(self):
        model = _make_model_mock()
        llm_response = MagicMock()
        llm_response.content = Content(parts=[Part.from_text(text="summary text")])

        async def mock_generate(request, stream=False, ctx=None):
            yield llm_response

        model.generate_async = mock_generate
        summarizer = SessionSummarizer(model=model)
        events = [_make_event(text=f"msg{i}") for i in range(10)]
        summary_text, result_events = await summarizer.create_session_summary_by_events(
            events, "s1", keep_recent_count=3)
        assert summary_text is not None
        assert len(result_events) == 11  # preserve all original events + 1 summary
        visible_events = [event for event in result_events if event.is_model_visible()]
        assert len(visible_events) == 4  # 1 summary + 3 recent
        assert any(event.is_summary_event() for event in result_events)

    async def test_summary_without_keep_recent(self):
        model = _make_model_mock()
        llm_response = MagicMock()
        llm_response.content = Content(parts=[Part.from_text(text="summary text")])

        async def mock_generate(request, stream=False, ctx=None):
            yield llm_response

        model.generate_async = mock_generate
        summarizer = SessionSummarizer(model=model)
        events = [_make_event(text=f"msg{i}") for i in range(5)]
        summary_text, result_events = await summarizer.create_session_summary_by_events(
            events, "s1", keep_recent_count=None)
        assert summary_text is not None
        assert len(result_events) == 6  # preserve all original events + 1 summary
        visible_events = [event for event in result_events if event.is_model_visible()]
        assert len(visible_events) == 1  # only summary event remains model-visible

    async def test_summary_no_events(self):
        model = _make_model_mock()
        summarizer = SessionSummarizer(model=model)
        summary_text, result_events = await summarizer.create_session_summary_by_events([], "s1")
        assert summary_text is None
        assert result_events == []

    async def test_summary_error_returns_none(self):
        model = _make_model_mock()

        async def mock_generate(request, stream=False, ctx=None):
            raise RuntimeError("error")
            yield  # pragma: no cover

        model.generate_async = mock_generate
        summarizer = SessionSummarizer(model=model)
        events = [_make_event(text=f"msg{i}") for i in range(5)]
        summary_text, result_events = await summarizer.create_session_summary_by_events(events, "s1")
        assert summary_text is None


# ---------------------------------------------------------------------------
# SessionSummarizer — create_session_summary
# ---------------------------------------------------------------------------

class TestCreateSessionSummary:
    async def test_summary_updates_session_events(self):
        model = _make_model_mock()
        llm_response = MagicMock()
        llm_response.content = Content(parts=[Part.from_text(text="session summary")])

        async def mock_generate(request, stream=False, ctx=None):
            yield llm_response

        model.generate_async = mock_generate
        summarizer = SessionSummarizer(model=model, keep_recent_count=2)
        session = _make_session(events=[_make_event(text=f"msg{i}") for i in range(10)])
        result = await summarizer.create_session_summary(session)
        assert result is not None
        assert len(session.events) == 11  # preserve all original events + 1 summary
        visible_events = [event for event in session.events if event.is_model_visible()]
        assert len(visible_events) == 3  # 1 summary + 2 recent
        assert any(event.is_summary_event() for event in session.events)

    async def test_summary_no_update_on_failure(self):
        model = _make_model_mock()

        async def mock_generate(request, stream=False, ctx=None):
            raise RuntimeError("fail")
            yield  # pragma: no cover

        model.generate_async = mock_generate
        summarizer = SessionSummarizer(model=model, keep_recent_count=2)
        events = [_make_event(text=f"msg{i}") for i in range(10)]
        session = _make_session(events=events)
        result = await summarizer.create_session_summary(session)
        assert result is None
        assert len(session.events) == 10


# ---------------------------------------------------------------------------
# SessionSummarizer — get_summary_metadata
# ---------------------------------------------------------------------------

class TestGetSummaryMetadata:
    def test_metadata(self):
        model = _make_model_mock()
        summarizer = SessionSummarizer(model=model, max_summary_length=500, keep_recent_count=5)
        metadata = summarizer.get_summary_metadata()
        assert metadata["model_name"] == "test-model"
        assert metadata["max_summary_length"] == 500
        assert metadata["keep_recent_count"] == 5
        assert metadata["model_available"] is True


# ---------------------------------------------------------------------------
# SessionSummarizer — _compress_session_to_summary
# ---------------------------------------------------------------------------

class TestCompressSessionToSummary:
    async def test_no_events(self):
        model = _make_model_mock()
        summarizer = SessionSummarizer(model=model)
        result = await summarizer._compress_session_to_summary([], "s1")
        assert result is None

    async def test_no_model(self):
        summarizer = SessionSummarizer(model=None)
        result = await summarizer._compress_session_to_summary([_make_event()], "s1")
        assert result is None

    async def test_no_conversation_text(self):
        model = _make_model_mock()
        summarizer = SessionSummarizer(model=model)
        event = Event(invocation_id="inv-1", author="agent", actions=_DEFAULT_ACTIONS)
        result = await summarizer._compress_session_to_summary([event], "s1")
        assert result is None

    async def test_exception_handling(self):
        model = _make_model_mock()

        async def mock_generate(request, stream=False, ctx=None):
            raise RuntimeError("LLM error")
            yield  # pragma: no cover

        model.generate_async = mock_generate
        summarizer = SessionSummarizer(model=model)
        events = [_make_event(text="meaningful content here")]
        result = await summarizer._compress_session_to_summary(events, "s1")
        assert result is None or result == ""
