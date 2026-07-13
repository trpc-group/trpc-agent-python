# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Persistent backend restart and rebuild replay tests."""

from __future__ import annotations

import asyncio
from collections import defaultdict

import pytest

from .adapters import SQLiteReplayAdapter
from .canonicalize import canonicalize_snapshot
from .cases import ReplayCase
from .cases import standard_cases
from .diff import compare_snapshots


def _run(coro):
    return asyncio.run(coro)


@pytest.mark.replay_lightweight
@pytest.mark.parametrize(
    ("case_id", "split_after_kind"),
    [
        ("summary_create_update", "summarize"),
        ("single_turn_text", "store_memory"),
    ],
)
def test_sqlite_destroy_rebuild_continue_and_readback(tmp_path, case_id, split_after_kind):
    case = _case_by_id(case_id)
    expected = canonicalize_snapshot(_run(_run_full_sqlite(case, tmp_path / "full")))
    actual = canonicalize_snapshot(_run(_run_restarted_sqlite(case, tmp_path / "restart", split_after_kind)))

    diffs = compare_snapshots(expected, actual, case_id=case.case_id, backend_pair=("sqlite_full", "sqlite_rebuilt"))

    assert diffs == []


async def _run_full_sqlite(case: ReplayCase, tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    adapter = SQLiteReplayAdapter(case=case, workdir=tmp_path)
    await adapter.setup()
    try:
        return await adapter.replay()
    finally:
        await adapter.close()


async def _run_restarted_sqlite(case: ReplayCase, tmp_path, split_after_kind: str):
    tmp_path.mkdir(parents=True, exist_ok=True)
    split_index = _split_index(case, split_after_kind)
    first = SQLiteReplayAdapter(case=case, workdir=tmp_path)
    await first.setup()
    try:
        for op in case.operations[:split_index]:
            await first.apply(op)
        state = _adapter_state(first)
    finally:
        await first.close()

    second = SQLiteReplayAdapter(case=case, workdir=tmp_path)
    await second.setup()
    try:
        _restore_adapter_state(second, state)
        await _refresh_known_sessions(second)
        for op in case.operations[split_index:]:
            await second.apply(op)
        state = _adapter_state(second)
    finally:
        await second.close()

    third = SQLiteReplayAdapter(case=case, workdir=tmp_path)
    await third.setup()
    try:
        _restore_adapter_state(third, state)
        await _refresh_known_sessions(third)
        return await third.snapshot()
    finally:
        await third.close()


def _split_index(case: ReplayCase, split_after_kind: str) -> int:
    for idx, op in enumerate(case.operations, start=1):
        if op.kind == split_after_kind:
            return idx
    raise AssertionError(f"{case.case_id} has no operation kind {split_after_kind}")


def _adapter_state(adapter: SQLiteReplayAdapter) -> dict:
    return {
        "sessions": dict(adapter.sessions),
        "event_id_map": dict(adapter.event_id_map),
        "actual_to_client_event_id": dict(adapter.actual_to_client_event_id),
        "summary_records": {key: list(value) for key, value in adapter.summary_records.items()},
        "memory_probes": list(adapter.memory_probes),
        "timestamp": adapter._timestamp,
    }


def _restore_adapter_state(adapter: SQLiteReplayAdapter, state: dict) -> None:
    adapter.sessions = dict(state["sessions"])
    adapter.event_id_map = dict(state["event_id_map"])
    adapter.actual_to_client_event_id = dict(state["actual_to_client_event_id"])
    adapter.summary_records = defaultdict(list, {key: list(value) for key, value in state["summary_records"].items()})
    adapter.memory_probes = list(state["memory_probes"])
    adapter._timestamp = state["timestamp"]


async def _refresh_known_sessions(adapter: SQLiteReplayAdapter) -> None:
    for key, session in list(adapter.sessions.items()):
        refreshed = await adapter.session_service.get_session(
            app_name=session.app_name,
            user_id=session.user_id,
            session_id=session.id,
        )
        assert refreshed is not None
        adapter.sessions[key] = refreshed


def _case_by_id(case_id: str) -> ReplayCase:
    return next(case for case in standard_cases() if case.case_id == case_id)
