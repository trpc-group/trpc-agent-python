"""Tests for SessionService-owned Session Compact."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.sessions import SessionServiceConfig
from trpc_agent_sdk.sessions.compact import AdvancedCompactConfig
from trpc_agent_sdk.sessions.compact import AdvancedSessionCompactManager
from trpc_agent_sdk.sessions.compact import SessionCompactRuntime
from trpc_agent_sdk.sessions.compact import SessionMemoryDocument
from trpc_agent_sdk.sessions.compact import SessionMemoryExtractor
from trpc_agent_sdk.sessions.compact import ToolResultBudget
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import Part


class _MemoryGenerator:

    async def generate(self, extraction_input, ctx) -> SessionMemoryDocument:
        del ctx
        return SessionMemoryDocument(
            session_title="Test session",
            current_state=extraction_input.last_event_id,
        )


def _event(event_id: str, content: Content) -> Event:
    return Event(
        id=event_id,
        invocation_id="invocation",
        author="agent",
        content=content,
    )


def test_compact_runtime_has_no_external_storage() -> None:
    runtime = SessionCompactRuntime.create(AdvancedCompactConfig())

    assert not hasattr(runtime, "transcripts")
    assert not hasattr(runtime, "tool_results")
    assert not hasattr(runtime, "paths")


@pytest.mark.asyncio
async def test_session_service_accepts_a_configured_compact_manager() -> None:
    manager = AdvancedSessionCompactManager(config=AdvancedCompactConfig())
    service = InMemorySessionService(
        session_config=SessionServiceConfig(store_historical_events=True),
        session_compact_manager=manager,
    )

    assert service.session_compact_manager is manager
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
    extractor = SessionMemoryExtractor(
        SessionCompactRuntime.create(
            AdvancedCompactConfig(
                session_memory_initial_chars=1,
                session_memory_update_chars=1,
            )),
        _MemoryGenerator(),
        session_service=service,
    )

    result = await extractor.extract_if_needed(
        session,
        SimpleNamespace(session=session, agent=SimpleNamespace(model="test")),
        force=True,
    )

    assert result.extracted is True
    assert "_trpc_agent:summary" in session.state
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
    budget = ToolResultBudget(
        SessionCompactRuntime.create(AdvancedCompactConfig(
            tool_result_max_chars=100,
            tool_result_preview_chars=20,
        )))

    await budget.apply(
        request,
        session_id=session.id,
        ctx=SimpleNamespace(session=session),
    )

    replacement = request.contents[0].parts[0].function_response.response
    assert replacement["session_event_id"] == "event-tool"
    assert "path" not in replacement
    await service.close()
