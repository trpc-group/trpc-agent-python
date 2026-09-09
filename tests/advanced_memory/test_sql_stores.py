"""SQLite tests for the Advanced Memory SQL backend."""

from __future__ import annotations

from pathlib import Path

from trpc_agent_sdk.advanced_memory import (
    AdvancedMemoryConfig,
    AdvancedMemoryRuntime,
    MemoryDocument,
    MemoryIndexEntry,
    MemoryType,
    SessionMemoryDocument,
)


def _runtime(tmp_path: Path) -> AdvancedMemoryRuntime:
    return AdvancedMemoryRuntime.create(
        AdvancedMemoryConfig(
            storage_backend="sql",
            sql_url=f"sqlite:///{tmp_path / 'advanced-memory.db'}",
            sql_is_async=False,
            memory_ttl_seconds=120,
            session_ttl_seconds=60,
        )
    )


async def test_sql_stores_round_trip_and_deduplicate(tmp_path: Path) -> None:
    root = _runtime(tmp_path)
    scoped = root.for_scope("app", "user")
    await scoped.initialize()

    await scoped.long_term_memory.write_index([
        MemoryIndexEntry(name="Profile", filename="profile.md", summary="Profile"),
    ])
    await scoped.long_term_memory.write_topic(
        "profile",
        MemoryDocument(
            name="Profile",
            description="Profile",
            memory_type=MemoryType.USER,
            content="A user profile",
        ),
    )
    await scoped.session_memory.write("session", SessionMemoryDocument(session_title="Test"))
    await scoped.tool_results.write("session", "result", '{"ok": true}')
    await scoped.transcripts.append("session", {"event_id": "one"})
    _, first = await scoped.transcripts.append_unique(
        "session",
        {"event_id": "two"},
        unique_key="event_id",
    )
    _, second = await scoped.transcripts.append_unique(
        "session",
        {"event_id": "two"},
        unique_key="event_id",
    )

    assert first is True
    assert second is False
    assert "profile.md" in await scoped.long_term_memory.read_index()
    assert await scoped.long_term_memory.read_topic("profile")
    assert await scoped.session_memory.read("session")
    assert await scoped.tool_results.read("session", "result") == '{"ok": true}'
    assert len(await scoped.transcripts.read_all("session")) == 2

    await root.close()


async def test_sql_stores_isolate_users(tmp_path: Path) -> None:
    root = _runtime(tmp_path)
    first = root.for_scope("app", "first")
    second = root.for_scope("app", "second")
    await first.initialize()
    await second.initialize()

    await first.long_term_memory.write_index([
        MemoryIndexEntry(name="First", filename="first.md", summary="First"),
    ])

    assert "first.md" in await first.long_term_memory.read_index()
    assert "first.md" not in await second.long_term_memory.read_index()

    await root.close()
