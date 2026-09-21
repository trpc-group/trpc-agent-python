# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for CodeAct implementation persistence backends."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import MetaData

from trpc_agent_sdk.codeact import (
    CodeActImplementation,
    FileCodeActImplementationStore,
    RedisCodeActImplementationStore,
    SqlCodeActImplementationStore,
)
from trpc_agent_sdk.storage import FileStorage, SqlStorage


def _implementation(result: int) -> CodeActImplementation:
    return CodeActImplementation.create(
        tool_name="add_numbers",
        contract_hash="contract",
        capability_hash="capabilities",
        code_cells=[f"return_result({{'result': {result}}})"],
    )


async def _assert_version_lifecycle(store: Any) -> None:
    first = _implementation(1)
    second = _implementation(2)
    assert await store.save_candidate(first) == first
    assert await store.save_candidate(second) == second
    assert {
        implementation.version
        for implementation in await store.list_implementations(
            first.tool_name,
            first.contract_hash,
        )
    } == {first.version, second.version}

    await store.approve(
        first.tool_name,
        first.contract_hash,
        second.version,
        approved_by="reviewer",
    )
    assert (
        await store.get_approved(first.tool_name, first.contract_hash)
        == second
    )

    await store.approve(
        first.tool_name,
        first.contract_hash,
        first.version,
        approved_by="rollback",
    )
    assert (
        await store.get_approved(first.tool_name, first.contract_hash)
        == first
    )


@pytest.mark.asyncio
async def test_file_store_uses_shared_file_storage(tmp_path):
    storage = FileStorage(tmp_path)
    store = FileCodeActImplementationStore(storage=storage)
    await _assert_version_lifecycle(store)

    reloaded = FileCodeActImplementationStore(storage=storage)
    approved = await reloaded.get_approved("add_numbers", "contract")
    assert approved is not None
    assert approved.version == _implementation(1).version


@pytest.mark.asyncio
async def test_file_storage_rejects_path_escape(tmp_path):
    storage = FileStorage(tmp_path)
    with pytest.raises(ValueError, match="escapes root"):
        await storage.write_text(Path("..") / "outside.json", "{}")


@pytest.mark.asyncio
async def test_sql_store_uses_sql_storage(tmp_path):
    store = SqlCodeActImplementationStore(
        db_url=f"sqlite:///{tmp_path / 'codeact.db'}",
    )
    try:
        await _assert_version_lifecycle(store)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_sql_store_can_share_existing_storage(tmp_path):
    storage = SqlStorage(
        is_async=False,
        db_url=f"sqlite:///{tmp_path / 'shared.db'}",
        metadata=MetaData(),
    )
    store = SqlCodeActImplementationStore(storage=storage)
    try:
        await _assert_version_lifecycle(store)
    finally:
        await store.close()
        await storage.close()


class _FakeRedisContext(AbstractAsyncContextManager[object]):
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback


class _FakeRedisStorage:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}

    def create_db_session(self) -> _FakeRedisContext:
        return _FakeRedisContext()

    async def execute_command(self, connection, command):
        del connection
        method = command.method.lower()
        if method == "set":
            key, value = command.args
            if command.kwargs.get("nx") and key in self.values:
                return None
            self.values[key] = value
            return True
        if method == "get":
            return self.values.get(command.args[0])
        if method == "sadd":
            key, *values = command.args
            target = self.sets.setdefault(key, set())
            before = len(target)
            target.update(values)
            return len(target) - before
        if method == "smembers":
            return self.sets.get(command.args[0], set())
        raise AssertionError(f"Unexpected Redis command: {method}")


@pytest.mark.asyncio
async def test_redis_store_uses_redis_storage():
    storage = _FakeRedisStorage()
    store = RedisCodeActImplementationStore(storage=storage)
    await _assert_version_lifecycle(store)
