"""Tests for request compression over an unchanged SessionService."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from trpc_agent_sdk.evaluation._eval_session_service import EvalSessionService
from trpc_agent_sdk.sessions.compact import AdvancedCompactConfig
from trpc_agent_sdk.sessions.compact import BaseSessionCompactConfig
from trpc_agent_sdk.sessions.compact import AdvancedMemoryRuntime
from trpc_agent_sdk.sessions.compact import BaseSessionCompactManager
from trpc_agent_sdk.sessions.compact import AutoCompactCallback
from trpc_agent_sdk.sessions.compact import HistorySnipCallback
from trpc_agent_sdk.sessions.compact import MicrocompactCallback
from trpc_agent_sdk.sessions.compact import SESSION_MEMORY_STATE_KEY
from trpc_agent_sdk.sessions.compact import SessionMemoryDocument
from trpc_agent_sdk.sessions.compact import ToolResultBudget
from trpc_agent_sdk.sessions.compact import ToolResultBudgetCallback
from trpc_agent_sdk.sessions.compact import setup_advanced_session_compact
from trpc_agent_sdk.sessions.compact import setup_context_compression
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.sessions import SessionServiceConfig
from trpc_agent_sdk.sessions import SqlSessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import Part


class FakeSummaryGenerator:
    """Return a deterministic autocompact summary."""

    async def generate(self, history: str, ctx) -> str:
        del history, ctx
        return "summary"


class FakeSessionMemoryGenerator:
    """Return deterministic structured Session Memory."""

    async def generate(self, extraction_input, ctx) -> SessionMemoryDocument:
        del ctx
        return SessionMemoryDocument(
            session_title="Post-turn memory",
            current_state=f"Processed {extraction_input.last_event_id}",
        )


class DummySummarizerManager:
    """Provide the BaseSessionService attachment protocol."""

    def set_session_service(self, service) -> None:
        self.service = service


def _runtime(tmp_path: Path) -> AdvancedMemoryRuntime:
    return AdvancedMemoryRuntime.create(
        AdvancedCompactConfig(
            root_dir=tmp_path,
            tool_result_max_chars=200,
            tool_results_per_message_max_chars=5_000,
            tool_result_preview_chars=40,
        ))


def _session_service() -> InMemorySessionService:
    return InMemorySessionService(
        session_config=SessionServiceConfig(store_historical_events=True),
    )


async def test_session_service_accepts_base_compact_manager(tmp_path: Path) -> None:
    """Inject the Advanced manager through the common manager contract."""
    agent = SimpleNamespace(before_model_callback=None)
    service = InMemorySessionService(
        session_config=SessionServiceConfig(store_historical_events=True),
    )
    manager = setup_advanced_session_compact(
        agent,
        service,
        AdvancedCompactConfig(root_dir=tmp_path),
        session_memory_generator=FakeSessionMemoryGenerator(),
    )

    assert isinstance(service.session_compact_manager, BaseSessionCompactManager)
    assert service.session_compact_manager is manager
    await service.close()


def test_advanced_config_implements_compact_config_contract() -> None:
    """Concrete strategies must be selectable through the config base class."""
    assert issubclass(AdvancedCompactConfig, BaseSessionCompactConfig)


async def test_advanced_setup_infers_sql_backend_from_session_service(
    tmp_path: Path,
) -> None:
    """Use the SessionService as the single source of backend settings."""
    database_url = f"sqlite:///{tmp_path / 'compact.db'}"
    service = SqlSessionService(
        db_url=database_url,
        is_async=False,
        session_config=SessionServiceConfig(store_historical_events=True),
    )
    manager = setup_advanced_session_compact(
        SimpleNamespace(before_model_callback=None),
        service,
        AdvancedCompactConfig(root_dir=tmp_path),
        session_memory_generator=FakeSessionMemoryGenerator(),
    )

    assert manager.runtime.config.storage_backend == "sql"
    assert manager.runtime.config.sql_url == database_url
    assert manager.runtime.config.sql_is_async is False
    await service.close()


@pytest.mark.asyncio
async def test_runner_auto_installs_compact_from_session_config(tmp_path: Path) -> None:
    """Let Runner create the manager from the declarative SessionService config."""
    from trpc_agent_sdk.runners import Runner

    agent = SimpleNamespace(
        name="compact-agent",
        tools=[],
        before_model_callback=None,
        get_subagents=lambda: [],
    )
    service = InMemorySessionService(
        session_config=SessionServiceConfig(store_historical_events=True),
        session_compact_config=AdvancedCompactConfig(root_dir=tmp_path),
    )

    runner = Runner(
        app_name="compact-test",
        agent=agent,
        session_service=service,
        enable_post_turn_processing=False,
    )

    assert service.session_compact_manager is not None
    assert service.session_compact_manager.runtime.config.root_dir == tmp_path.resolve()
    await runner.close()


def _tool_event(output: str) -> Event:
    return Event(
        id="event-1",
        invocation_id="invocation-1",
        author="user",
        content=Content(parts=[
            Part(function_response=FunctionResponse(
                id="result-1",
                name="demo_tool",
                response={"output": output},
            ))
        ]),
    )


async def test_setup_attaches_manager_to_original_service(tmp_path: Path) -> None:
    """Install only the four request callbacks over the original service."""
    runtime = _runtime(tmp_path)
    delegate = _session_service()
    agent = SimpleNamespace(before_model_callback=None)

    service = setup_context_compression(
        agent,
        delegate,
        runtime,
        FakeSummaryGenerator(),
    )

    assert service is delegate
    assert service.session_compact_manager is not None
    assert service.session_compact_manager.runtime is runtime
    assert [type(callback) for callback in agent.before_model_callback] == [
        ToolResultBudgetCallback,
        HistorySnipCallback,
        MicrocompactCallback,
        AutoCompactCallback,
    ]


async def test_setup_rejects_original_session_summarizer(tmp_path: Path) -> None:
    """Prevent two independent mechanisms from writing summary Events."""
    delegate = InMemorySessionService(
        summarizer_manager=DummySummarizerManager(),
        session_config=SessionServiceConfig(store_historical_events=True),
    )
    agent = SimpleNamespace(before_model_callback=None)

    with pytest.raises(ValueError, match="mutually exclusive"):
        setup_context_compression(
            agent,
            delegate,
            _runtime(tmp_path),
            FakeSummaryGenerator(),
        )
    await delegate.close()


async def test_manager_keeps_events_in_original_service_only(tmp_path: Path) -> None:
    """Read and append Events without a second Event transcript."""
    runtime = _runtime(tmp_path)
    delegate = _session_service()
    session = await delegate.create_session(
        app_name="demo-app",
        user_id="demo-user",
        session_id="legacy-session",
    )
    old_event = Event(
        id="old-event",
        invocation_id="invocation-1",
        author="user",
        content=Content(parts=[Part.from_text(text="old event")]),
    )
    await delegate.append_event(session, old_event)

    agent = SimpleNamespace(before_model_callback=None)
    service = setup_context_compression(agent, delegate, runtime, FakeSummaryGenerator())
    loaded = await service.get_session(
        app_name="demo-app",
        user_id="demo-user",
        session_id=session.id,
    )
    assert loaded is not None
    await service.append_event(loaded, _tool_event("x" * 500))

    stored = await delegate.get_session(
        app_name="demo-app",
        user_id="demo-user",
        session_id=session.id,
    )
    assert stored is not None
    assert [event.id for event in stored.events] == ["old-event", "event-1"]
    assert await runtime.for_session(stored).transcripts.read_all(stored.id) == []


async def test_request_replacement_does_not_rewrite_stored_event(tmp_path: Path) -> None:
    """Replace a request copy while retaining the complete persisted result."""
    runtime = _runtime(tmp_path)
    delegate = _session_service()
    agent = SimpleNamespace(before_model_callback=None)
    service = setup_context_compression(agent, delegate, runtime, FakeSummaryGenerator())
    session = await service.create_session(
        app_name="demo-app",
        user_id="demo-user",
        session_id="budget-session",
    )
    await service.append_event(session, _tool_event("x" * 500))
    request = LlmRequest(
        model="test-model",
        contents=[session.events[0].content.model_copy(deep=True)],
    )

    result = await ToolResultBudget(runtime.for_session(session)).apply(
        request,
        session_id=session.id,
    )

    stored = await delegate.get_session(
        app_name="demo-app",
        user_id="demo-user",
        session_id=session.id,
    )
    assert result.replaced_count == 1
    assert "persisted_output" in request.contents[0].parts[0].function_response.response
    assert stored is not None
    assert stored.events[0].content.parts[0].function_response.response == {
        "output": "x" * 500
    }
    records = await runtime.for_session(stored).transcripts.read_all(stored.id)
    assert all(record.get("kind") != "event" for record in records)


async def test_setup_is_idempotent_and_validates_runtime_first(tmp_path: Path) -> None:
    """Reuse one manager and reject a different runtime without changing callbacks."""
    runtime = _runtime(tmp_path / "one")
    agent = SimpleNamespace(before_model_callback=None)
    service = setup_context_compression(
        agent,
        _session_service(),
        runtime,
        FakeSummaryGenerator(),
    )
    repeated = setup_context_compression(agent, service, runtime, FakeSummaryGenerator())
    assert repeated is service
    assert len(agent.before_model_callback) == 4

    clean_agent = SimpleNamespace(before_model_callback=None)
    with pytest.raises(ValueError, match="another runtime"):
        setup_context_compression(
            clean_agent,
            service,
            _runtime(tmp_path / "two"),
            FakeSummaryGenerator(),
        )
    assert clean_agent.before_model_callback is None


async def test_compact_manager_is_mutually_exclusive_with_native_summarizer(tmp_path: Path) -> None:
    """Prevent adding the native summarizer after compact setup."""
    service = _session_service()
    setup_context_compression(
        SimpleNamespace(before_model_callback=None),
        service,
        _runtime(tmp_path),
        FakeSummaryGenerator(),
    )

    with pytest.raises(ValueError, match="mutually exclusive"):
        service.set_summarizer_manager(DummySummarizerManager())


async def test_original_service_delete_cleans_compact_side_data(tmp_path: Path) -> None:
    """Run compact cleanup through the original SessionService lifecycle."""
    runtime = _runtime(tmp_path)
    service = _session_service()
    setup_context_compression(
        SimpleNamespace(before_model_callback=None),
        service,
        runtime,
        FakeSummaryGenerator(),
    )
    session = await service.create_session(
        app_name="demo-app",
        user_id="demo-user",
        session_id="delete-me",
    )
    scoped = runtime.for_session(session)
    await scoped.transcripts.append(session.id, {"kind": "test-record"})

    await service.delete_session(
        app_name=session.app_name,
        user_id=session.user_id,
        session_id=session.id,
    )

    assert await scoped.transcripts.read_all(session.id) == []


async def test_eval_session_service_forwards_compact_manager(tmp_path: Path) -> None:
    """Keep evaluation wrappers on the inner service's compact lifecycle."""
    inner = _session_service()
    service = EvalSessionService(inner)
    runtime = _runtime(tmp_path)

    configured = setup_context_compression(
        SimpleNamespace(before_model_callback=None),
        service,
        runtime,
        FakeSummaryGenerator(),
    )

    assert configured is service
    assert service.session_compact_manager is inner.session_compact_manager
    assert service.session_compact_manager.runtime is runtime


async def test_sql_delegate_keeps_its_existing_event_storage(tmp_path: Path) -> None:
    """Ensure manager composition works with the SQL SessionService."""
    runtime = _runtime(tmp_path / "advanced")
    delegate = SqlSessionService(
        db_url=f"sqlite:///{tmp_path / 'sessions.db'}",
        is_async=False,
    )
    session = await delegate.create_session(
        app_name="demo-app",
        user_id="demo-user",
        session_id="sql-session",
    )
    event = Event(
        id="sql-event",
        invocation_id="invocation-1",
        author="user",
        content=Content(parts=[Part.from_text(text="stored by SQL")]),
    )
    await delegate.append_event(session, event)

    service = setup_context_compression(
        SimpleNamespace(before_model_callback=None),
        delegate,
        runtime,
        FakeSummaryGenerator(),
    )
    loaded = await service.get_session(
        app_name="demo-app",
        user_id="demo-user",
        session_id=session.id,
    )

    assert loaded is not None
    assert [item.id for item in loaded.events] == ["sql-event"]
    assert await runtime.for_session(loaded).transcripts.read_all(loaded.id) == []
    await service.close()
    await runtime.close()


async def test_post_turn_hook_updates_session_memory_state(tmp_path: Path) -> None:
    """Ensure the existing Runner summary hook updates Session Memory."""
    database = tmp_path / "post-turn.db"
    runtime = AdvancedMemoryRuntime.create(
        AdvancedCompactConfig(
            storage_backend="sql",
            sql_url=f"sqlite:///{database}",
            sql_is_async=False,
            session_memory_initial_chars=1,
            session_memory_update_chars=1,
        ),
    )
    delegate = SqlSessionService(
        db_url=f"sqlite:///{database}",
        is_async=False,
    )
    agent = SimpleNamespace(before_model_callback=None)
    service = setup_context_compression(
        agent,
        delegate,
        runtime,
        FakeSummaryGenerator(),
        session_memory_generator=FakeSessionMemoryGenerator(),
    )
    session = await service.create_session(
        app_name="demo-app",
        user_id="demo-user",
        session_id="post-turn",
    )
    await service.append_event(session, _tool_event("post-turn content"))
    ctx = SimpleNamespace(
        session=session,
        session_service=service,
        agent=SimpleNamespace(model="fake-model"),
    )

    await service.create_session_summary(session, ctx=ctx)

    assert SESSION_MEMORY_STATE_KEY in session.state
    loaded = await service.get_session(
        app_name=session.app_name,
        user_id=session.user_id,
        session_id=session.id,
    )
    assert loaded is not None
    assert SESSION_MEMORY_STATE_KEY in loaded.state
    summary = await service.get_session_summary(loaded)
    assert summary is not None
    assert "Post-turn memory" in summary
    await service.close()
    await runtime.close()
