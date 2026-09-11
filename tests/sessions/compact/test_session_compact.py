"""Tests for SessionService-owned Session Compact."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from trpc_agent_sdk.abc import CompactSummarizerABC
from trpc_agent_sdk.abc import CompactTrigger
from trpc_agent_sdk.context import new_agent_context
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.sessions import SessionServiceConfig
from trpc_agent_sdk.sessions.compact import AdvancedAutoCompactSummarizer
from trpc_agent_sdk.sessions.compact import AdvancedAutoCompactSummarizerConfig
from trpc_agent_sdk.sessions.compact import AdvancedAutoCompactSummarizerManager
from trpc_agent_sdk.sessions.compact import AdvancedAutoCompactSummarizerRuntime
from trpc_agent_sdk.sessions.compact import AutoCompactSummarizerConfig
from trpc_agent_sdk.sessions.compact import SessionMemoryExtractor
from trpc_agent_sdk.sessions.compact.advanced import SessionMemoryExtractorConfig
from trpc_agent_sdk.sessions.compact.advanced import ToolResultBudgetConfig
from trpc_agent_sdk.sessions.compact.advanced._tool_result_budget import ToolResultBudget
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import Part


class _MemoryModel:
    name = "memory-model"

    async def generate_async(self, request, *, stream, ctx):
        del request, stream, ctx
        yield LlmResponse(
            content=Content(
                role="model",
                parts=[Part.from_text(text="# Session Title\nTest session\n\n# Current State\nUpdated")],
            ),
        )


class _SummaryModel:
    name = "summary-model"

    async def generate_async(self, request, *, stream, ctx):
        del request, stream, ctx
        yield LlmResponse(content=Content(
            role="model",
            parts=[
                Part.from_text(
                    text="<analysis>covered</analysis>"
                    "<summary># Session Title\nCompact summary</summary>")
            ],
        ))


def _event(event_id: str, content: Content) -> Event:
    return Event(
        id=event_id,
        invocation_id="invocation",
        author="agent",
        content=content,
    )


def test_compact_runtime_has_no_external_storage() -> None:
    runtime = AdvancedAutoCompactSummarizerRuntime(AdvancedAutoCompactSummarizerConfig())

    assert not hasattr(runtime, "transcripts")
    assert not hasattr(runtime, "tool_results")
    assert not hasattr(runtime, "paths")


@pytest.mark.asyncio
async def test_disabled_auto_compact_returns_without_recursion() -> None:
    config = AdvancedAutoCompactSummarizerConfig(
        auto_compact=AutoCompactSummarizerConfig(enabled=False),
    )
    summarizer = AdvancedAutoCompactSummarizer(config)
    session = SimpleNamespace(id="session", app_name="app", user_id="user")
    ctx = SimpleNamespace(session=session, session_id=session.id)

    result = await summarizer.apply(LlmRequest(), ctx=ctx)

    assert result.compacted is False
    assert result.blocked is False


@pytest.mark.asyncio
async def test_session_service_accepts_a_configured_compact_manager() -> None:
    summarizer = AdvancedAutoCompactSummarizer(AdvancedAutoCompactSummarizerConfig())
    manager = AdvancedAutoCompactSummarizerManager(summarizer)
    service = InMemorySessionService(
        session_config=SessionServiceConfig(store_historical_events=True),
        summarizer_manager=manager,
    )

    assert service.summarizer_manager is manager
    await service.close()


@pytest.mark.asyncio
async def test_advanced_summarizer_implements_compact_abc_and_timing() -> None:
    summarizer = AdvancedAutoCompactSummarizer(AdvancedAutoCompactSummarizerConfig(
        session_memory=SessionMemoryExtractorConfig(enabled=False),
    ))
    before_model_manager = AdvancedAutoCompactSummarizerManager(summarizer)
    after_turn_manager = AdvancedAutoCompactSummarizerManager(
        summarizer,
        compact_trigger=CompactTrigger.AFTER_TURN,
    )

    assert isinstance(summarizer, CompactSummarizerABC)
    assert before_model_manager.compact_trigger == CompactTrigger.BEFORE_MODEL
    assert after_turn_manager.compact_trigger == CompactTrigger.AFTER_TURN

    session = SimpleNamespace(events=[])
    ctx = SimpleNamespace(session=session)
    summarizer.should_summarize = AsyncMock(return_value=True)
    summarizer.create_session_summary = AsyncMock(return_value="summary")
    await before_model_manager.create_session_summary(session, ctx=ctx)
    summarizer.should_summarize.assert_not_awaited()

    await after_turn_manager.create_session_summary(session, ctx=ctx)
    summarizer.should_summarize.assert_awaited_once_with(session)
    summarizer.create_session_summary.assert_awaited_once_with(
        session,
        ctx=ctx,
        store_historical_events=True,
    )


@pytest.mark.asyncio
async def test_advanced_end_of_turn_compaction_moves_old_events_to_history() -> None:
    service = InMemorySessionService(
        session_config=SessionServiceConfig(store_historical_events=True),
    )
    session = await service.create_session(
        app_name="app",
        user_id="user",
        session_id="session",
    )
    for index in range(3):
        await service.append_event(
            session,
            _event(
                f"event-{index}",
                Content(role="user", parts=[Part.from_text(text=f"{index}:" + "x" * 2_000)]),
            ),
        )
    original_ids = [event.id for event in session.events]
    config = AdvancedAutoCompactSummarizerConfig(
        session_memory=SessionMemoryExtractorConfig(enabled=False),
        auto_compact=AutoCompactSummarizerConfig(
            trigger_chars=500,
            target_chars=250,
            blocking_chars=10_000,
            keep_recent_contents=1,
        ),
    )
    summarizer = AdvancedAutoCompactSummarizer(config, model=_SummaryModel())
    ctx = SimpleNamespace(
        session=session,
        session_id=session.id,
        session_service=service,
        agent_context=new_agent_context(),
        agent=SimpleNamespace(model=_SummaryModel()),
    )

    assert await summarizer.should_summarize(session) is True
    summary = await summarizer.create_session_summary(
        session,
        ctx=ctx,
        store_historical_events=True,
    )

    assert summary is not None and "Compact summary" in summary
    assert len(session.events) == 2
    assert session.events[0].is_summary_event()
    assert session.events[1].id == original_ids[-1]
    assert [event.id for event in session.historical_events] == original_ids[:-1]
    await service.close()


@pytest.mark.asyncio
async def test_session_memory_is_written_to_session_state() -> None:
    service = InMemorySessionService(session_config=SessionServiceConfig(store_historical_events=True), )
    session = await service.create_session(
        app_name="app",
        user_id="user",
        session_id="session",
    )
    await service.append_event(
        session,
        _event("event-1", Content(parts=[Part.from_text(text="hello")])),
    )
    config = AdvancedAutoCompactSummarizerConfig(
        session_memory=SessionMemoryExtractorConfig(
            initial_chars=1,
            update_chars=1,
        ),
    )
    extractor = SessionMemoryExtractor(
        AdvancedAutoCompactSummarizerRuntime(config),
        model=_MemoryModel(),
    )

    result = await extractor.extract_if_needed(
        SimpleNamespace(
            session=session,
            session_service=service,
            agent=SimpleNamespace(model=_MemoryModel(), generate_content_config=None),
            override_messages=None,
        ),
        force=True,
    )

    assert result.extracted is True
    assert "_trpc_agent:summary" in session.state
    stored = await service.get_session(
        app_name="app",
        user_id="user",
        session_id="session",
    )
    assert stored is not None
    assert "_trpc_agent:summary" in stored.state
    await service.close()


@pytest.mark.asyncio
async def test_tool_result_budget_keeps_the_session_event_id() -> None:
    service = InMemorySessionService()
    session = await service.create_session(
        app_name="app",
        user_id="user",
        session_id="session",
    )
    content = Content(parts=[
        Part(function_response=FunctionResponse(
            id="tool-call-1",
            name="demo",
            response={"output": "x" * 500},
        ))
    ])
    await service.append_event(session, _event("event-tool", content))
    request = LlmRequest(model="test", contents=[content.model_copy(deep=True)])
    config = AdvancedAutoCompactSummarizerConfig(
        tool_result_budget=ToolResultBudgetConfig(
            max_chars=100,
            preview_chars=20,
        ),
    )
    budget = ToolResultBudget(
        AdvancedAutoCompactSummarizerRuntime(config),
    )

    await budget.apply(
        request,
        ctx=SimpleNamespace(session=session),
    )

    replacement = request.contents[0].parts[0].function_response.response
    assert replacement["session_event_id"] == "event-tool"
    assert "path" not in replacement
    await service.close()
