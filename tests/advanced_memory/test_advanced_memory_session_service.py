"""Tests for the standalone Advanced Memory SessionService."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from trpc_agent_sdk.advanced_memory import AdvancedMemoryConfig
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import AdvancedMemorySessionService
from trpc_agent_sdk.sessions import SessionServiceConfig
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


def _event(event_id: str, text: str) -> Event:
    """Create a deterministic event for persistence tests."""
    return Event(
        id=event_id,
        invocation_id=f"invocation-{event_id}",
        author="user",
        content=Content(parts=[Part.from_text(text=text)]),
    )


def _config(root_dir: Path) -> AdvancedMemoryConfig:
    """Disable model-driven background work for storage-only tests."""
    return AdvancedMemoryConfig(
        root_dir=root_dir,
        session_memory_enabled=False,
        history_snip_enabled=False,
        microcompact_enabled=False,
        autocompact_enabled=False,
    )


async def test_session_service_persists_and_restores_events(tmp_path: Path) -> None:
    """Ensure a new service instance can restore a complete transcript."""
    first = AdvancedMemorySessionService(config=_config(tmp_path))
    session = await first.create_session(
        app_name="demo-app",
        user_id="demo-user",
        session_id="demo-session",
    )
    await first.append_event(session, _event("event-1", "hello"))

    second = AdvancedMemorySessionService(config=_config(tmp_path))
    restored = await second.get_session(
        app_name="demo-app",
        user_id="demo-user",
        session_id="demo-session",
    )

    assert restored is not None
    assert [event.id for event in restored.events] == ["event-1"]


async def test_session_id_collision_between_users_is_rejected(tmp_path: Path) -> None:
    """Prevent different users from silently sharing one session directory."""
    service = AdvancedMemorySessionService(config=_config(tmp_path))
    await service.create_session(
        app_name="demo-app",
        user_id="user-a",
        session_id="shared-session",
    )

    try:
        await service.create_session(
            app_name="demo-app",
            user_id="user-b",
            session_id="shared-session",
        )
    except ValueError as exc:
        assert "already used" in str(exc)
    else:
        raise AssertionError("Expected a cross-user session ID collision to fail")


async def test_delete_session_removes_persistent_session_data(tmp_path: Path) -> None:
    """Ensure deleting a session removes its metadata and transcript directory."""
    service = AdvancedMemorySessionService(config=_config(tmp_path))
    session = await service.create_session(
        app_name="demo-app",
        user_id="demo-user",
        session_id="delete-me",
    )
    await service.append_event(session, _event("event-1", "hello"))

    await service.delete_session(
        app_name="demo-app",
        user_id="demo-user",
        session_id=session.id,
    )

    assert await service.get_session(
        app_name="demo-app",
        user_id="demo-user",
        session_id=session.id,
    ) is None
    assert not service.runtime.paths.session_dir(session.id).exists()


async def test_ttl_cleanup_removes_expired_persistent_sessions(tmp_path: Path) -> None:
    """Ensure configured session TTL removes idle session directories."""
    session_config = SessionServiceConfig(ttl=SessionServiceConfig.create_ttl_config(
        ttl_seconds=1,
        cleanup_interval_seconds=0.05,
    ))
    service = AdvancedMemorySessionService(
        config=_config(tmp_path),
        session_config=session_config,
    )
    session = await service.create_session(
        app_name="demo-app",
        user_id="demo-user",
        session_id="expires",
    )

    await asyncio.sleep(1.1)

    assert await service.get_session(
        app_name="demo-app",
        user_id="demo-user",
        session_id=session.id,
    ) is None
    await service.close()


async def test_runner_binds_standalone_session_service(tmp_path: Path) -> None:
    """Ensure Runner installs Advanced callbacks without a memory service."""
    service = AdvancedMemorySessionService(config=_config(tmp_path))
    agent = SimpleNamespace(name="test-agent", tools=[], before_model_callback=None)

    runner = Runner(
        app_name="demo-app",
        agent=agent,
        session_service=service,
    )

    assert runner.session_service.delegate is service
    assert service.integration is not None


def test_session_service_can_cross_event_loops(tmp_path: Path) -> None:
    """Ensure deferred Runner work can share the service's file lock."""
    service = AdvancedMemorySessionService(config=_config(tmp_path))

    async def create() -> None:
        await service.create_session(
            app_name="demo-app",
            user_id="demo-user",
            session_id="demo-session",
        )

    async def append() -> None:
        session = await service.get_session(
            app_name="demo-app",
            user_id="demo-user",
            session_id="demo-session",
        )
        assert session is not None
        await service.append_event(session, _event("event-1", "hello"))

    asyncio.run(create())
    asyncio.run(append())


def test_transcript_decorator_can_cross_event_loops(tmp_path: Path) -> None:
    """Ensure the wrapped service is safe for deferred-worker event loops."""
    service = AdvancedMemorySessionService(config=_config(tmp_path))
    runner = Runner(
        app_name="demo-app",
        agent=SimpleNamespace(
            name="test-agent",
            tools=[],
            before_model_callback=None,
            get_subagents=lambda: [],
        ),
        session_service=service,
    )

    async def create() -> None:
        await runner.session_service.create_session(
            app_name="demo-app",
            user_id="demo-user",
            session_id="wrapped-session",
        )

    async def append() -> None:
        session = await runner.session_service.get_session(
            app_name="demo-app",
            user_id="demo-user",
            session_id="wrapped-session",
        )
        assert session is not None
        await runner.session_service.append_event(session, _event("event-1", "hello"))

    asyncio.run(create())
    asyncio.run(append())
    asyncio.run(runner.close())
