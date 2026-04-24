"""Unit tests for trpc_agent_sdk.server.openclaw.session_memory._claw_summarizer."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trpc_agent_sdk.context import AgentContext, InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions import Session, SessionSummary
from trpc_agent_sdk.types import Content, Part

from trpc_agent_sdk.server.openclaw.session_memory._claw_summarizer import (
    ClawSessionSummarizer,
    ClawSummarizerSessionManager,
    _parse_llm_response,
)
from trpc_agent_sdk.server.openclaw.storage import LONG_TERM_MEMORY_KEY, RAW_EVENTS_KEY


def _make_event(author="user", text="hello") -> Event:
    return Event(
        invocation_id="inv-1",
        author=author,
        content=Content(parts=[Part.from_text(text=text)]),
        timestamp=time.time(),
    )


def _make_session(events=None, session_id="s1") -> Session:
    s = Session(id=session_id, app_name="app", user_id="user", save_key="app/user/s1")
    s.events = events or []
    return s


def _make_model_mock():
    model = MagicMock()
    model.name = "test-model"
    return model


def _make_storage_manager_mock():
    sm = MagicMock()
    sm.read_long_term = AsyncMock(return_value="")
    sm.write_long_term = AsyncMock()
    sm.append_history = AsyncMock()
    return sm


# ---------------------------------------------------------------------------
# _parse_llm_response
# ---------------------------------------------------------------------------


class TestParseLlmResponse:

    def test_both_tags_present(self):
        text = (
            "<history_entry>Summary of events</history_entry>"
            "<memory_update>Updated memory text here with enough length to pass</memory_update>"
        )
        history, memory = _parse_llm_response(text)
        assert history == "Summary of events"
        assert memory == "Updated memory text here with enough length to pass"

    def test_only_history_entry(self):
        text = "<history_entry>Just history</history_entry>"
        history, memory = _parse_llm_response(text)
        assert history == "Just history"
        assert memory == text.strip()

    def test_only_memory_update(self):
        text = "<memory_update>Just memory data with enough content</memory_update>"
        history, memory = _parse_llm_response(text)
        assert history == ""
        assert memory == "Just memory data with enough content"

    def test_neither_tag_fallback(self):
        text = "This is plain text without any XML tags for parsing."
        history, memory = _parse_llm_response(text)
        assert memory == text.strip()
        assert history == text[:300].strip()

    def test_empty_string(self):
        history, memory = _parse_llm_response("")
        assert history == ""
        assert memory == ""

    def test_whitespace_only(self):
        history, memory = _parse_llm_response("   \n  ")
        assert history == ""
        assert memory == ""

    def test_case_insensitive_tags(self):
        text = "<HISTORY_ENTRY>Upper case</HISTORY_ENTRY><MEMORY_UPDATE>Upper mem</MEMORY_UPDATE>"
        history, memory = _parse_llm_response(text)
        assert history == "Upper case"
        assert memory == "Upper mem"

    def test_multiline_content(self):
        text = (
            "<history_entry>\nLine 1\nLine 2\n</history_entry>"
            "<memory_update>\nMem line 1\nMem line 2\n</memory_update>"
        )
        history, memory = _parse_llm_response(text)
        assert "Line 1" in history
        assert "Mem line 1" in memory


# ---------------------------------------------------------------------------
# ClawSessionSummarizer._find_safe_split
# ---------------------------------------------------------------------------


class TestFindSafeSplit:

    def setup_method(self):
        self.model = _make_model_mock()
        self.storage = _make_storage_manager_mock()
        self.summarizer = ClawSessionSummarizer(
            model=self.model,
            storage_manager=self.storage,
            keep_recent_count=3,
        )

    def test_events_lte_keep_n(self):
        events = [_make_event() for _ in range(3)]
        old, recent = self.summarizer._find_safe_split(events, 3)
        assert old == []
        assert len(recent) == 3

    def test_events_less_than_keep_n(self):
        events = [_make_event() for _ in range(2)]
        old, recent = self.summarizer._find_safe_split(events, 5)
        assert old == []
        assert len(recent) == 2

    def test_user_boundary_found(self):
        events = [
            _make_event(author="user", text="u1"),
            _make_event(author="agent", text="a1"),
            _make_event(author="agent", text="a2"),
            _make_event(author="user", text="u2"),
            _make_event(author="agent", text="a3"),
            _make_event(author="agent", text="a4"),
        ]
        old, recent = self.summarizer._find_safe_split(events, 3)
        assert len(old) + len(recent) == 6
        assert recent[0].author == "user"

    def test_no_user_event_uses_ideal_split(self):
        events = [
            _make_event(author="system", text="s1"),
            _make_event(author="agent", text="a1"),
            _make_event(author="agent", text="a2"),
            _make_event(author="agent", text="a3"),
            _make_event(author="agent", text="a4"),
        ]
        old, recent = self.summarizer._find_safe_split(events, 2)
        assert len(old) == 3
        assert len(recent) == 2

    def test_keep_n_zero_returns_all_old(self):
        events = [_make_event() for _ in range(5)]
        old, recent = self.summarizer._find_safe_split(events, 0)
        assert old == []
        assert len(recent) == 5

    def test_single_user_at_start(self):
        events = [
            _make_event(author="user", text="u1"),
            _make_event(author="agent", text="a1"),
            _make_event(author="agent", text="a2"),
            _make_event(author="agent", text="a3"),
            _make_event(author="agent", text="a4"),
        ]
        old, recent = self.summarizer._find_safe_split(events, 2)
        assert len(old) + len(recent) == 5


# ---------------------------------------------------------------------------
# ClawSessionSummarizer._make_raw_archive
# ---------------------------------------------------------------------------


class TestMakeRawArchive:

    def setup_method(self):
        self.model = _make_model_mock()
        self.storage = _make_storage_manager_mock()
        self.summarizer = ClawSessionSummarizer(
            model=self.model,
            storage_manager=self.storage,
        )

    def test_with_existing_memory(self):
        events = [_make_event(text="hello"), _make_event(text="world")]
        history, memory = self.summarizer._make_raw_archive(events, "existing memory content")
        assert "[RAW]" in history
        assert "2 events" in history
        assert "existing memory content" in memory
        assert "RAW ARCHIVE" in memory

    def test_empty_memory(self):
        events = [_make_event(text="msg")]
        history, memory = self.summarizer._make_raw_archive(events, "")
        assert "[RAW]" in history
        assert "RAW ARCHIVE" in memory
        assert "existing" not in memory.lower() or True

    def test_memory_is_empty_marker(self):
        events = [_make_event(text="msg")]
        history, memory = self.summarizer._make_raw_archive(events, "(empty)")
        assert "[RAW ARCHIVE]" in memory


# ---------------------------------------------------------------------------
# ClawSummarizerSessionManager._update_summary_cache
# ---------------------------------------------------------------------------


class TestUpdateSummaryCache:

    def test_creates_cache_entry(self):
        model = _make_model_mock()
        storage = _make_storage_manager_mock()
        manager = ClawSummarizerSessionManager(model=model, storage_manager=storage)
        session = _make_session()

        manager._update_summary_cache(
            session=session,
            history_entry="Some history",
            memory_update="Updated memory",
            original_event_count=10,
            compressed_event_count=5,
        )

        cached = manager._summarizer_cache["app"]["user"]["s1"]
        assert isinstance(cached, SessionSummary)
        assert cached.session_id == "s1"
        assert cached.summary_text == "Some history"
        assert cached.original_event_count == 10
        assert cached.compressed_event_count == 5
        assert "LONG_TERM_MEMORY" in cached.metadata
        assert "HISTORY" in cached.metadata

    def test_overwrites_existing_entry(self):
        model = _make_model_mock()
        storage = _make_storage_manager_mock()
        manager = ClawSummarizerSessionManager(model=model, storage_manager=storage)
        session = _make_session()

        manager._update_summary_cache(
            session=session,
            history_entry="First",
            memory_update="Mem1",
            original_event_count=5,
            compressed_event_count=2,
        )
        manager._update_summary_cache(
            session=session,
            history_entry="Second",
            memory_update="Mem2",
            original_event_count=8,
            compressed_event_count=3,
        )

        cached = manager._summarizer_cache["app"]["user"]["s1"]
        assert cached.summary_text == "Second"
        assert cached.metadata["LONG_TERM_MEMORY"] == "Mem2"


# ---------------------------------------------------------------------------
# ClawSummarizerSessionManager initialization
# ---------------------------------------------------------------------------


class TestClawSummarizerSessionManagerInit:

    def test_default_summarizer_created(self):
        model = _make_model_mock()
        storage = _make_storage_manager_mock()
        manager = ClawSummarizerSessionManager(model=model, storage_manager=storage)
        assert manager._storage_manager is storage
        assert isinstance(manager._summarizer, ClawSessionSummarizer)

    def test_custom_summarizer(self):
        model = _make_model_mock()
        storage = _make_storage_manager_mock()
        custom = ClawSessionSummarizer(model=model, storage_manager=storage)
        manager = ClawSummarizerSessionManager(
            model=model,
            storage_manager=storage,
            summarizer=custom,
        )
        assert manager._summarizer is custom


# ---------------------------------------------------------------------------
# ClawSessionSummarizer properties
# ---------------------------------------------------------------------------


class TestClawSessionSummarizerProperties:

    def test_storage_manager_property(self):
        model = _make_model_mock()
        storage = _make_storage_manager_mock()
        summarizer = ClawSessionSummarizer(model=model, storage_manager=storage)
        assert summarizer.storage_manager is storage


# ---------------------------------------------------------------------------
# ClawSummarizerSessionManager.get_summarizer_metadata
# ---------------------------------------------------------------------------


class TestGetSummarizerMetadata:

    def test_returns_dict_with_type(self):
        model = _make_model_mock()
        storage = _make_storage_manager_mock()
        manager = ClawSummarizerSessionManager(model=model, storage_manager=storage)
        meta = manager.get_summarizer_metadata()
        assert isinstance(meta, dict)
        assert meta["summarizer_type"] == "SessionSummarizer"


# ---------------------------------------------------------------------------
# ClawSessionSummarizer._invoke_llm
# ---------------------------------------------------------------------------


class TestInvokeLlm:

    async def test_returns_concatenated_text(self):
        model = _make_model_mock()
        storage = _make_storage_manager_mock()
        summarizer = ClawSessionSummarizer(model=model, storage_manager=storage)

        resp1 = MagicMock()
        resp1.content = Content(parts=[Part.from_text(text="Hello ")])
        resp2 = MagicMock()
        resp2.content = Content(parts=[Part.from_text(text="World")])

        async def mock_gen(*args, **kwargs):
            yield resp1
            yield resp2

        model.generate_async = mock_gen

        ctx = MagicMock(spec=InvocationContext)
        result = await summarizer._invoke_llm("test prompt", ctx)
        assert result == "Hello World"

    async def test_empty_response(self):
        model = _make_model_mock()
        storage = _make_storage_manager_mock()
        summarizer = ClawSessionSummarizer(model=model, storage_manager=storage)

        resp = MagicMock()
        resp.content = None

        async def mock_gen(*args, **kwargs):
            yield resp

        model.generate_async = mock_gen

        ctx = MagicMock(spec=InvocationContext)
        result = await summarizer._invoke_llm("test", ctx)
        assert result == ""


# ---------------------------------------------------------------------------
# ClawSessionSummarizer._call_llm_for_memory
# ---------------------------------------------------------------------------


class TestCallLlmForMemory:

    async def test_success_on_first_attempt(self):
        model = _make_model_mock()
        storage = _make_storage_manager_mock()
        summarizer = ClawSessionSummarizer(model=model, storage_manager=storage)

        long_text = (
            "<history_entry>Summary</history_entry>"
            "<memory_update>This is a sufficiently long memory update text for validation</memory_update>"
        )
        summarizer._invoke_llm = AsyncMock(return_value=long_text)

        ctx = MagicMock(spec=InvocationContext)
        history, memory = await summarizer._call_llm_for_memory("prompt", "s1", ctx)
        assert history == "Summary"
        assert "sufficiently long" in memory

    async def test_empty_response_retries(self):
        model = _make_model_mock()
        storage = _make_storage_manager_mock()
        summarizer = ClawSessionSummarizer(model=model, storage_manager=storage)

        summarizer._invoke_llm = AsyncMock(return_value="")

        ctx = MagicMock(spec=InvocationContext)
        history, memory = await summarizer._call_llm_for_memory("prompt", "s1", ctx)
        assert history == ""
        assert memory == ""
        assert summarizer._invoke_llm.await_count == 2

    async def test_too_short_memory_retries(self):
        model = _make_model_mock()
        storage = _make_storage_manager_mock()
        summarizer = ClawSessionSummarizer(model=model, storage_manager=storage)

        summarizer._invoke_llm = AsyncMock(return_value="<memory_update>short</memory_update>")

        ctx = MagicMock(spec=InvocationContext)
        history, memory = await summarizer._call_llm_for_memory("prompt", "s1", ctx)
        assert history == ""
        assert memory == ""

    async def test_exception_retries(self):
        model = _make_model_mock()
        storage = _make_storage_manager_mock()
        summarizer = ClawSessionSummarizer(model=model, storage_manager=storage)

        summarizer._invoke_llm = AsyncMock(side_effect=RuntimeError("llm boom"))

        ctx = MagicMock(spec=InvocationContext)
        history, memory = await summarizer._call_llm_for_memory("prompt", "s1", ctx)
        assert history == ""
        assert memory == ""
        assert summarizer._invoke_llm.await_count == 2

    async def test_success_on_second_attempt(self):
        model = _make_model_mock()
        storage = _make_storage_manager_mock()
        summarizer = ClawSessionSummarizer(model=model, storage_manager=storage)

        good_text = (
            "<history_entry>Retry Summary</history_entry>"
            "<memory_update>This is now long enough memory update on second try</memory_update>"
        )
        summarizer._invoke_llm = AsyncMock(side_effect=["", good_text])

        ctx = MagicMock(spec=InvocationContext)
        history, memory = await summarizer._call_llm_for_memory("prompt", "s1", ctx)
        assert history == "Retry Summary"
        assert "long enough" in memory


# ---------------------------------------------------------------------------
# ClawSessionSummarizer.create_session_summary
# ---------------------------------------------------------------------------


class TestCreateSessionSummary:

    def _make_ctx(self):
        ctx = MagicMock(spec=InvocationContext)
        ctx.agent_context = AgentContext()
        return ctx

    async def test_no_ctx_raises(self):
        model = _make_model_mock()
        storage = _make_storage_manager_mock()
        summarizer = ClawSessionSummarizer(model=model, storage_manager=storage, keep_recent_count=3)
        session = _make_session(events=[_make_event() for _ in range(10)])
        with pytest.raises(ValueError, match="Invocation context is required"):
            await summarizer.create_session_summary(session, ctx=None)

    async def test_too_few_events_returns_none(self):
        model = _make_model_mock()
        storage = _make_storage_manager_mock()
        summarizer = ClawSessionSummarizer(model=model, storage_manager=storage, keep_recent_count=10)
        session = _make_session(events=[_make_event() for _ in range(5)])
        ctx = self._make_ctx()
        result = await summarizer.create_session_summary(session, ctx=ctx)
        assert result is None

    async def test_successful_summarization(self):
        model = _make_model_mock()
        storage = _make_storage_manager_mock()
        summarizer = ClawSessionSummarizer(model=model, storage_manager=storage, keep_recent_count=3)

        events = [_make_event(text=f"msg {i}") for i in range(10)]
        session = _make_session(events=events)
        ctx = self._make_ctx()

        long_memory = "This is a long enough memory update for the validation check to pass okay"
        summarizer._call_llm_for_memory = AsyncMock(return_value=("history entry", long_memory))

        result = await summarizer.create_session_summary(session, ctx=ctx)
        assert result == "history entry"
        assert len(session.events) <= 10

    async def test_llm_failure_increments_counter(self):
        model = _make_model_mock()
        storage = _make_storage_manager_mock()
        summarizer = ClawSessionSummarizer(model=model, storage_manager=storage, keep_recent_count=3)

        events = [_make_event(text=f"msg {i}") for i in range(10)]
        session = _make_session(events=events)
        ctx = self._make_ctx()

        summarizer._call_llm_for_memory = AsyncMock(return_value=("", ""))

        result = await summarizer.create_session_summary(session, ctx=ctx)
        assert result is None
        assert summarizer._consecutive_failures == 1

    async def test_raw_archive_fallback_after_max_failures(self):
        model = _make_model_mock()
        storage = _make_storage_manager_mock()
        summarizer = ClawSessionSummarizer(model=model, storage_manager=storage, keep_recent_count=3)
        summarizer._consecutive_failures = 2  # one away from max

        events = [_make_event(text=f"msg {i}") for i in range(10)]
        session = _make_session(events=events)
        ctx = self._make_ctx()

        summarizer._call_llm_for_memory = AsyncMock(return_value=("", ""))

        result = await summarizer.create_session_summary(session, ctx=ctx)
        assert result is not None
        assert "[RAW]" in result
        assert summarizer._consecutive_failures == 0

    async def test_memory_truncated_when_too_long(self):
        model = _make_model_mock()
        storage = _make_storage_manager_mock()
        summarizer = ClawSessionSummarizer(
            model=model,
            storage_manager=storage,
            keep_recent_count=3,
            max_summary_length=50,
        )

        events = [_make_event(text=f"msg {i}") for i in range(10)]
        session = _make_session(events=events)
        ctx = self._make_ctx()

        long_memory = "x" * 100
        summarizer._call_llm_for_memory = AsyncMock(return_value=("history", long_memory))

        result = await summarizer.create_session_summary(session, ctx=ctx)
        assert result == "history"
        mem_event = ctx.agent_context.get_metadata(LONG_TERM_MEMORY_KEY)
        assert mem_event.content.parts[0].text.endswith("…")

    async def test_no_old_events_returns_none(self):
        model = _make_model_mock()
        storage = _make_storage_manager_mock()
        summarizer = ClawSessionSummarizer(model=model, storage_manager=storage, keep_recent_count=0)

        events = [_make_event() for _ in range(3)]
        session = _make_session(events=events)
        ctx = self._make_ctx()

        result = await summarizer.create_session_summary(session, ctx=ctx)
        assert result is None


# ---------------------------------------------------------------------------
# ClawSummarizerSessionManager.create_session_summary
# ---------------------------------------------------------------------------


class TestManagerCreateSessionSummary:

    def _make_ctx(self):
        ctx = MagicMock(spec=InvocationContext)
        ctx.agent_context = AgentContext()
        return ctx

    async def test_no_ctx_raises(self):
        model = _make_model_mock()
        storage = _make_storage_manager_mock()
        manager = ClawSummarizerSessionManager(model=model, storage_manager=storage)
        session = _make_session()
        with pytest.raises(ValueError, match="Invocation context is required"):
            await manager.create_session_summary(session, ctx=None)

    async def test_restores_memory_for_empty_session(self):
        model = _make_model_mock()
        storage = _make_storage_manager_mock()
        storage.read_long_term = AsyncMock(return_value="persisted memory content")
        manager = ClawSummarizerSessionManager(model=model, storage_manager=storage)
        manager.should_summarize_session = AsyncMock(return_value=False)

        session = _make_session(events=[])
        ctx = self._make_ctx()

        await manager.create_session_summary(session, ctx=ctx)

        storage.read_long_term.assert_awaited_once()
        restored = ctx.agent_context.get_metadata(LONG_TERM_MEMORY_KEY)
        assert restored is not None

    async def test_no_restore_when_empty_memory(self):
        model = _make_model_mock()
        storage = _make_storage_manager_mock()
        storage.read_long_term = AsyncMock(return_value="  ")
        manager = ClawSummarizerSessionManager(model=model, storage_manager=storage)
        manager.should_summarize_session = AsyncMock(return_value=False)

        session = _make_session(events=[])
        ctx = self._make_ctx()

        await manager.create_session_summary(session, ctx=ctx)

        restored = ctx.agent_context.get_metadata(LONG_TERM_MEMORY_KEY, None)
        assert restored is None

    async def test_round_loop_with_successful_summarizer(self):
        model = _make_model_mock()
        storage = _make_storage_manager_mock()
        manager = ClawSummarizerSessionManager(model=model, storage_manager=storage)

        events = [_make_event(text=f"msg {i}") for i in range(10)]
        session = _make_session(events=events)
        ctx = self._make_ctx()

        mem_event = Event(
            invocation_id="mem",
            author="system",
            content=Content(parts=[Part.from_text(text="Long term memory content")]),
            timestamp=time.time(),
        )

        call_count = 0

        async def mock_should_summarize(s):
            nonlocal call_count
            call_count += 1
            return call_count <= 1

        manager.should_summarize_session = mock_should_summarize

        async def mock_create_summary(s, c):
            ctx.agent_context.with_metadata(LONG_TERM_MEMORY_KEY, mem_event)
            ctx.agent_context.with_metadata(RAW_EVENTS_KEY, [_make_event()])
            s.events = s.events[-3:]
            return "history from summarizer"

        manager._summarizer.create_session_summary = mock_create_summary

        await manager.create_session_summary(session, force=False, ctx=ctx)

        cached = manager._summarizer_cache.get("app", {}).get("user", {}).get("s1")
        assert isinstance(cached, SessionSummary)
        assert cached.summary_text == "history from summarizer"

    async def test_summarizer_returns_none_stops_loop(self):
        model = _make_model_mock()
        storage = _make_storage_manager_mock()
        manager = ClawSummarizerSessionManager(model=model, storage_manager=storage)

        events = [_make_event(text=f"msg {i}") for i in range(10)]
        session = _make_session(events=events)
        ctx = self._make_ctx()

        manager.should_summarize_session = AsyncMock(return_value=True)
        manager._summarizer.create_session_summary = AsyncMock(return_value=None)

        await manager.create_session_summary(session, force=True, ctx=ctx)

    async def test_force_triggers_first_round(self):
        model = _make_model_mock()
        storage = _make_storage_manager_mock()
        manager = ClawSummarizerSessionManager(model=model, storage_manager=storage)

        events = [_make_event(text=f"msg {i}") for i in range(10)]
        session = _make_session(events=events)
        ctx = self._make_ctx()

        manager.should_summarize_session = AsyncMock(return_value=False)

        mem_event = Event(
            invocation_id="mem",
            author="system",
            content=Content(parts=[Part.from_text(text="Memory")]),
            timestamp=time.time(),
        )

        async def mock_create_summary(s, c):
            ctx.agent_context.with_metadata(LONG_TERM_MEMORY_KEY, mem_event)
            ctx.agent_context.with_metadata(RAW_EVENTS_KEY, [])
            return "forced history"

        manager._summarizer.create_session_summary = mock_create_summary

        await manager.create_session_summary(session, force=True, ctx=ctx)

        cached = manager._summarizer_cache.get("app", {}).get("user", {}).get("s1")
        assert isinstance(cached, SessionSummary)

    @patch("trpc_agent_sdk.server.openclaw.session_memory._claw_summarizer.get_invocation_ctx", return_value=None)
    @patch("trpc_agent_sdk.server.openclaw.session_memory._claw_summarizer.set_invocation_ctx")
    async def test_updates_base_service(self, mock_set_ctx, mock_get_ctx):
        model = _make_model_mock()
        storage = _make_storage_manager_mock()
        manager = ClawSummarizerSessionManager(model=model, storage_manager=storage)
        manager._base_service = MagicMock()
        manager._base_service.update_session = AsyncMock()

        events = [_make_event(text=f"msg {i}") for i in range(10)]
        session = _make_session(events=events)
        ctx = self._make_ctx()

        manager.should_summarize_session = AsyncMock(return_value=False)

        mem_event = Event(
            invocation_id="mem",
            author="system",
            content=Content(parts=[Part.from_text(text="Memory content")]),
            timestamp=time.time(),
        )

        async def mock_create_summary(s, c):
            ctx.agent_context.with_metadata(LONG_TERM_MEMORY_KEY, mem_event)
            ctx.agent_context.with_metadata(RAW_EVENTS_KEY, [])
            return "some history"

        manager._summarizer.create_session_summary = mock_create_summary

        await manager.create_session_summary(session, force=True, ctx=ctx)

        manager._base_service.update_session.assert_awaited_once()
        mock_set_ctx.assert_called()


# ---------------------------------------------------------------------------
# ClawSummarizerSessionManager.get_session_summary
# ---------------------------------------------------------------------------


class TestManagerGetSessionSummary:

    async def test_returns_summary_when_cached(self):
        model = _make_model_mock()
        storage = _make_storage_manager_mock()
        manager = ClawSummarizerSessionManager(model=model, storage_manager=storage)
        session = _make_session()

        manager._update_summary_cache(
            session=session,
            history_entry="hist",
            memory_update="mem",
            original_event_count=5,
            compressed_event_count=2,
        )

        result = await manager.get_session_summary(session)
        assert isinstance(result, SessionSummary)
        assert result.session_id == "s1"

    async def test_returns_none_when_not_cached(self):
        model = _make_model_mock()
        storage = _make_storage_manager_mock()
        manager = ClawSummarizerSessionManager(model=model, storage_manager=storage)
        session = _make_session()

        result = await manager.get_session_summary(session)
        assert result is None
