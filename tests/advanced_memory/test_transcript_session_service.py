"""Unit tests for TranscriptSessionService automatic recording."""

from __future__ import annotations

from pathlib import Path

from trpc_agent_sdk.advanced_memory import AdvancedMemoryConfig
from trpc_agent_sdk.advanced_memory import AdvancedMemoryRuntime
from trpc_agent_sdk.advanced_memory import TranscriptSessionService
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


def _event(event_id: str, text: str, *, partial: bool = False) -> Event:
    """Create a fixed Event for transcript tests."""
    return Event(
        id=event_id,
        invocation_id="invocation-1",
        author="agent",
        content=Content(parts=[Part.from_text(text=text)]),
        partial=partial,
    )


async def _session(service: TranscriptSessionService):
    """Create a test session through the decorated service."""
    return await service.create_session(
        app_name="demo-app",
        user_id="demo-user",
        session_id="demo-session",
    )


async def test_append_event_writes_versioned_parent_chain(tmp_path: Path) -> None:
    """Ensure persisted Events produce an ordered parent-linked transcript."""
    runtime = AdvancedMemoryRuntime.create(AdvancedMemoryConfig(enabled=True, root_dir=tmp_path))
    service = TranscriptSessionService(InMemorySessionService(), runtime)
    session = await _session(service)

    await service.append_event(session, _event("event-1", "hello"))
    await service.append_event(session, _event("event-2", "world"))

    records = await runtime.transcripts.read_all(session.id)
    assert [record["event_id"] for record in records] == ["event-1", "event-2"]
    assert records[0]["parent_event_id"] is None
    assert records[1]["parent_event_id"] == "event-1"
    assert records[0]["schema_version"] == 1
    assert records[0]["session"] == {
        "id": "demo-session",
        "app_name": "demo-app",
        "user_id": "demo-user",
    }
    assert records[0]["event"]["invocationId"] == "invocation-1"


async def test_duplicate_event_id_is_not_written_twice(tmp_path: Path) -> None:
    """Ensure duplicate Event IDs are not written twice."""
    runtime = AdvancedMemoryRuntime.create(AdvancedMemoryConfig(enabled=True, root_dir=tmp_path))
    service = TranscriptSessionService(InMemorySessionService(), runtime)
    session = await _session(service)
    duplicate = _event("event-1", "hello")

    await service.append_event(session, duplicate)
    await service.append_event(session, duplicate.model_copy(deep=True))

    records = await runtime.transcripts.read_all(session.id)
    assert [record["event_id"] for record in records] == ["event-1"]


async def test_old_duplicate_does_not_rewind_parent_chain(tmp_path: Path) -> None:
    """Ensure replaying an old Event does not rewind the parent chain."""
    runtime = AdvancedMemoryRuntime.create(AdvancedMemoryConfig(enabled=True, root_dir=tmp_path))
    service = TranscriptSessionService(InMemorySessionService(), runtime)
    session = await _session(service)
    await service.append_event(session, _event("event-1", "first"))
    await service.append_event(session, _event("event-2", "second"))
    await service.append_event(session, _event("event-1", "first"))
    await service.append_event(session, _event("event-3", "third"))

    records = await runtime.transcripts.read_all(session.id)

    assert [record["event_id"] for record in records] == ["event-1", "event-2", "event-3"]
    assert records[-1]["parent_event_id"] == "event-2"


async def test_new_wrapper_restores_parent_from_existing_transcript(tmp_path: Path) -> None:
    """Ensure a rebuilt wrapper restores the parent-chain tail from disk."""
    runtime = AdvancedMemoryRuntime.create(AdvancedMemoryConfig(enabled=True, root_dir=tmp_path))
    delegate = InMemorySessionService()
    first_service = TranscriptSessionService(delegate, runtime)
    session = await _session(first_service)
    await first_service.append_event(session, _event("event-1", "first"))

    second_runtime = AdvancedMemoryRuntime.create(AdvancedMemoryConfig(enabled=True, root_dir=tmp_path))
    second_service = TranscriptSessionService(delegate, second_runtime)
    await second_service.append_event(session, _event("event-2", "second"))

    records = await second_runtime.transcripts.read_all(session.id)
    assert records[-1]["parent_event_id"] == "event-1"


async def test_disabled_runtime_preserves_old_service_without_disk_writes(tmp_path: Path) -> None:
    """Ensure disabled mode preserves the legacy service without disk writes."""
    runtime = AdvancedMemoryRuntime.create(AdvancedMemoryConfig(enabled=False, root_dir=tmp_path))
    service = TranscriptSessionService(InMemorySessionService(), runtime)
    session = await _session(service)

    persisted_event = await service.append_event(session, _event("event-1", "hello"))

    assert persisted_event.id == "event-1"
    assert [event.id for event in session.events] == ["event-1"]
    assert not (tmp_path / "MEMORY").exists()
    assert not (tmp_path / "SESSION").exists()


async def test_partial_event_is_not_written_to_transcript(tmp_path: Path) -> None:
    """Ensure streaming partial Events enter neither session nor transcript."""
    runtime = AdvancedMemoryRuntime.create(AdvancedMemoryConfig(enabled=True, root_dir=tmp_path))
    service = TranscriptSessionService(InMemorySessionService(), runtime)
    session = await _session(service)

    await service.append_event(session, _event("partial-1", "chunk", partial=True))

    assert session.events == []
    assert await runtime.transcripts.read_all(session.id) == []
