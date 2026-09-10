"""Session-state persistence tests for Redis/SQL Advanced Memory."""

from pathlib import Path
from types import SimpleNamespace

from trpc_agent_sdk.sessions.compact import AdvancedCompactConfig
from trpc_agent_sdk.sessions.compact import AdvancedMemoryRuntime
from trpc_agent_sdk.sessions.compact import AutoCompact
from trpc_agent_sdk.sessions.compact import SessionMemoryDocument
from trpc_agent_sdk.sessions.compact import SessionMemoryExtractor
from trpc_agent_sdk.sessions.compact._formats import SESSION_MEMORY_STATE_KEY
from trpc_agent_sdk.sessions.compact._formats import parse_session_memory_state
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.sessions import SqlSessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


class _Generator:

    def __init__(self) -> None:
        self.inputs = []

    async def generate(self, extraction_input, ctx) -> SessionMemoryDocument:
        del ctx
        self.inputs.append(extraction_input)
        return SessionMemoryDocument(
            session_title="State-backed session",
            current_state=f"Processed {extraction_input.last_event_id}",
        )


class _LegacyGenerator:

    async def generate(self, history, ctx) -> str:
        del history, ctx
        return "legacy"


def _event(event_id: str, text: str) -> Event:
    return Event(
        id=event_id,
        invocation_id="invocation",
        author="agent",
        content=Content(role="model", parts=[Part.from_text(text=text)]),
    )


async def test_sql_session_memory_is_persisted_in_session_state(tmp_path: Path, ) -> None:
    database = tmp_path / "state-memory.db"
    runtime = AdvancedMemoryRuntime.create(
        AdvancedCompactConfig(
            storage_backend="sql",
            sql_url=f"sqlite:///{database}",
            sql_is_async=False,
            session_memory_initial_chars=1,
            session_memory_update_chars=1,
        ))
    service = SqlSessionService(db_url=f"sqlite:///{database}", is_async=False)
    session = await service.create_session(
        app_name="app",
        user_id="user",
        session_id="session",
    )
    await service.append_event(session, _event("event-1", "x" * 2_000))
    generator = _Generator()
    extractor = SessionMemoryExtractor(
        runtime,
        generator,
        session_service=service,
    )
    ctx = SimpleNamespace(
        session=session,
        agent=SimpleNamespace(model="test-model"),
    )

    result = await extractor.extract_if_needed(session, ctx, force=True)

    loaded = await service.get_session(
        app_name="app",
        user_id="user",
        session_id="session",
    )
    assert result.extracted is True
    assert loaded is not None
    parsed = parse_session_memory_state(loaded.state[SESSION_MEMORY_STATE_KEY])
    assert parsed is not None
    document, checkpoint, _ = parsed
    assert document.current_state == "Processed event-1"
    assert checkpoint["last_event_id"] == "event-1"
    assert len(loaded.events) == 1
    assert runtime.for_session(loaded).session_memory is None
    assert await runtime.for_session(loaded).transcripts.read_all(loaded.id) == []
    await service.close()
    await runtime.close()


async def test_autocompact_generates_state_memory_only_when_invoked(tmp_path: Path, ) -> None:
    database = tmp_path / "autocompact-state.db"
    runtime = AdvancedMemoryRuntime.create(
        AdvancedCompactConfig(
            storage_backend="sql",
            sql_url=f"sqlite:///{database}",
            sql_is_async=False,
            autocompact_target_chars=20_000,
            session_memory_initial_chars=1,
            session_memory_update_chars=1,
        ))
    service = SqlSessionService(db_url=f"sqlite:///{database}", is_async=False)
    session = await service.create_session(
        app_name="app",
        user_id="user",
        session_id="session",
    )
    for index in range(3):
        await service.append_event(
            session,
            _event(f"event-{index}", f"message-{index}-" + "x" * 3_000),
        )
    generator = _Generator()
    extractor = SessionMemoryExtractor(
        runtime,
        generator,
        session_service=service,
    )
    compressor = AutoCompact(runtime, _LegacyGenerator())
    compressor.attach_session_memory_extractor(extractor)
    ctx = SimpleNamespace(
        session=session,
        session_service=service,
        agent=SimpleNamespace(model="test-model"),
    )
    request = LlmRequest(
        model="test-model",
        contents=[event.content.model_copy(deep=True) for event in session.events],
    )

    result = await compressor.apply(
        request,
        session_id=session.id,
        ctx=ctx,
        force=True,
    )

    assert result.compacted is True
    assert result.source == "session-memory"
    assert generator.inputs
    assert SESSION_MEMORY_STATE_KEY in session.state
    assert session.events[0].is_summary_event()
    assert [event.id for event in session.historical_events] == [
        "event-0",
        "event-1",
        "event-2",
    ]
    records = await runtime.for_session(session).transcripts.read_all(session.id)
    assert [record["kind"] for record in records] == ["autocompact-success"]
    assert all(record["kind"] != "event" for record in records)
    await service.close()
    await runtime.close()
