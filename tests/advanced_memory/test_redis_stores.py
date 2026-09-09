"""Tests for Redis Advanced Memory storage and TTL grouping."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from pathlib import Path

import pytest

from trpc_agent_sdk.advanced_memory import AdvancedMemoryConfig
from trpc_agent_sdk.advanced_memory import AdvancedMemoryPaths
from trpc_agent_sdk.advanced_memory import MemoryIndexEntry
from trpc_agent_sdk.advanced_memory import SessionMemoryDocument
from trpc_agent_sdk.advanced_memory._redis_stores import RedisLongTermMemoryStore
from trpc_agent_sdk.advanced_memory._redis_stores import RedisSessionMemoryStore


def _store(store_type: type, **overrides: object):
    config = AdvancedMemoryConfig(
        storage_backend="redis",
        redis_url="redis://localhost:6379/0",
        root_dir=Path("/tmp/advanced-memory-redis-tests"),
        memory_ttl_seconds=120,
        session_ttl_seconds=60,
        **overrides,
    )
    paths = AdvancedMemoryPaths(config).for_scope("app", "user")
    store = store_type(config, paths, MagicMock())

    async def command(method: str, *args: object, **kwargs: object):
        if method == "set" and args and str(args[0]).endswith(":memory:lock"):
            return True
        return []

    store._command = AsyncMock(side_effect=command)
    return store


@pytest.mark.asyncio
async def test_memory_writes_refresh_all_memory_keys() -> None:
    store = _store(RedisLongTermMemoryStore)

    await store.write_index([
        MemoryIndexEntry(name="Profile", filename="profile.md", summary="User profile"),
    ])

    commands = [call.args for call in store._command.await_args_list]
    assert ("set", f"{store._user_base}:memory:index", "- [Profile]（profile.md）:User profile\n") in commands
    assert ("sadd", f"{store._user_base}:memory:keys", f"{store._user_base}:memory:index") in commands
    assert ("expire", f"{store._user_base}:memory:index", 120) in commands
    assert ("expire", f"{store._user_base}:memory:keys", 120) in commands


@pytest.mark.asyncio
async def test_session_writes_refresh_all_session_keys() -> None:
    store = _store(RedisSessionMemoryStore)

    await store.write("session-1", SessionMemoryDocument(session_title="Test session"))

    session_base = store._session_base("session-1")
    commands = [call.args for call in store._command.await_args_list]
    assert any(command[0] == "set" and command[1] == f"{session_base}:summary" for command in commands)
    assert ("sadd", f"{session_base}:keys", f"{session_base}:summary") in commands
    assert ("expire", f"{session_base}:summary", 60) in commands
    assert ("expire", f"{session_base}:keys", 60) in commands


@pytest.mark.asyncio
async def test_ttl_refresh_includes_previously_tracked_keys() -> None:
    store = _store(RedisSessionMemoryStore, session_ttl_delete_transcripts=True)
    session_base = store._session_base("session-1")
    old_key = f"{session_base}:transcript"
    store._command = AsyncMock(side_effect=[
        None,  # SADD
        [old_key.encode()],  # SMEMBERS
        None,  # EXPIRE old key
        None,  # EXPIRE current key
        None,  # EXPIRE registry
    ])

    await store._refresh_session_ttl("session-1", f"{session_base}:summary")

    commands = [call.args for call in store._command.await_args_list]
    assert ("expire", old_key, 60) in commands
    assert ("expire", f"{session_base}:summary", 60) in commands


@pytest.mark.asyncio
async def test_ttl_refresh_preserves_transcript_by_default() -> None:
    store = _store(RedisSessionMemoryStore)
    session_base = store._session_base("session-1")
    old_key = f"{session_base}:transcript"
    old_seen_key = f"{old_key}:seen:event_id"
    store._command = AsyncMock(side_effect=[
        None,  # SADD
        [old_key.encode(), old_seen_key.encode()],  # SMEMBERS
        None,  # EXPIRE current key
        None,  # EXPIRE registry
    ])

    await store._refresh_session_ttl("session-1", f"{session_base}:summary")

    commands = [call.args for call in store._command.await_args_list]
    assert ("expire", old_key, 60) not in commands
    assert ("expire", old_seen_key, 60) not in commands
    assert ("expire", f"{session_base}:summary", 60) in commands


@pytest.mark.asyncio
async def test_memory_write_lock_releases_with_token_check() -> None:
    store = _store(RedisLongTermMemoryStore)

    async with store._memory_write_lock():
        pass

    lock_key = f"{store._user_base}:memory:lock"
    lock_sets = [call for call in store._command.await_args_list if call.args[:2] == ("set", lock_key)]
    releases = [call for call in store._command.await_args_list if call.args and call.args[0] == "eval"]
    assert lock_sets
    assert lock_sets[0].kwargs["nx"] is True
    assert lock_sets[0].kwargs["ex"] == 30
    assert releases
    assert releases[0].args[2] == 1
    assert releases[0].args[3] == lock_key
    assert releases[0].args[4] == lock_sets[0].args[2]
