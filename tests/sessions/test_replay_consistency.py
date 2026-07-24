# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Replay consistency tests for Session / Memory / Summary backends.

Run in lightweight mode (InMemory vs SQLite) by default.  Set the
``TRPC_REPLAY_REDIS_URL`` environment variable to include the Redis backend
in integration mode; if Redis is unavailable the test skips it automatically.

The test produces ``session_memory_summary_diff_report.json`` in the workspace
root containing every detected difference with backend, case, field path and
values.
"""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from trpc_agent_sdk.sessions._types import SessionServiceConfig

from .replay_cases import get_all_cases_with_faults
from .replay_consistency_framework import BackendBundle
from .replay_consistency_framework import BackendFactory
from .replay_consistency_framework import in_memory_backend_factory
from .replay_consistency_framework import ReplayHarness
from .replay_consistency_framework import save_report
from .replay_consistency_framework import sqlite_backend_factory


# ---------------------------------------------------------------------------
# Redis mock for optional integration mode
# ---------------------------------------------------------------------------


class _MockRedisStorage:
    """In-memory Redis storage mock used when a real Redis is not available."""

    def __init__(self):
        self._store: dict[str, Any] = {}
        self._hash_store: dict[str, dict[str, Any]] = {}

    @asynccontextmanager
    async def create_db_session(self):
        yield MagicMock()

    async def execute_command(self, session, command):
        method = command.method
        args = command.args

        if method == "set":
            self._store[args[0]] = args[1]
            return True
        if method == "get":
            return self._store.get(args[0])
        if method == "keys":
            prefix = args[0].replace("*", "")
            return [k for k in self._store.keys() if k.startswith(prefix)]
        if method == "hset":
            key = args[0]
            pairs = args[1:]
            if key not in self._hash_store:
                self._hash_store[key] = {}
            for i in range(0, len(pairs), 2):
                self._hash_store[key][pairs[i]] = pairs[i + 1]
            return True
        if method == "hgetall":
            return self._hash_store.get(args[0], {})
        if method == "rpush":
            key = args[0]
            self._store[key] = [a for a in args[1:]]
            return len(args) - 1
        if method == "lrange":
            key = args[0]
            return self._store.get(key, [])
        return None

    async def delete(self, session, key):
        self._store.pop(key, None)

    async def expire(self, session, expire_obj):
        pass

    async def query(self, session, pattern, condition):
        prefix = pattern.replace("*", "")
        return [(k, v) for k, v in self._store.items() if k.startswith(prefix)]

    async def close(self):
        pass


def redis_backend_factory() -> BackendBundle:
    """Factory for the Redis backend pair (mock storage, no external server)."""
    from trpc_agent_sdk.memory import RedisMemoryService
    from trpc_agent_sdk.sessions import RedisSessionService
    from .replay_consistency_framework import _make_summarizer_manager

    summarizer_manager = _make_summarizer_manager()
    config = SessionServiceConfig(store_historical_events=True)
    config.clean_ttl_config()
    with patch("trpc_agent_sdk.sessions._redis_session_service.RedisStorage"):
        session_service = RedisSessionService(
            db_url="redis://localhost:6379",
            summarizer_manager=summarizer_manager,
            session_config=config,
        )
    session_service._redis_storage = _MockRedisStorage()

    with patch("trpc_agent_sdk.memory._redis_memory_service.RedisStorage"):
        memory_service = RedisMemoryService(
            db_url="redis://localhost:6379",
            enabled=True,
        )
    memory_service._redis_storage = _MockRedisStorage()

    return BackendBundle(
        name="redis_mock",
        session_service=session_service,
        memory_service=memory_service,
        summarizer_manager=summarizer_manager,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def harness() -> ReplayHarness:
    """Build a harness comparing InMemory to SQLite and optionally Redis."""
    factories: dict[str, BackendFactory] = {
        "in_memory": in_memory_backend_factory,
        "sqlite": sqlite_backend_factory,
    }

    redis_url = os.environ.get("TRPC_REPLAY_REDIS_URL")
    if redis_url:
        try:
            import redis as redis_lib
            client = redis_lib.from_url(redis_url, socket_connect_timeout=2)
            client.ping()
            factories["redis"] = redis_backend_factory
        except Exception:
            pytest.skip("TRPC_REPLAY_REDIS_URL set but Redis is not reachable")
    elif os.environ.get("TRPC_REPLAY_INCLUDE_REDIS_MOCK"):
        factories["redis_mock"] = redis_backend_factory

    return ReplayHarness(factories, reference_backend="in_memory")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_consistency_report(harness: ReplayHarness):
    """Run all replay cases and assert acceptance criteria."""
    cases, faults = get_all_cases_with_faults()

    start = time.monotonic()
    report = await harness.run_cases(cases, faults)
    elapsed = time.monotonic() - start

    # Acceptance criteria
    summary = report["summary"]
    assert summary["false_positive_rate"] <= 0.05, (
        f"False positive rate {summary['false_positive_rate']} exceeds 5%"
    )
    assert summary["injected_fault_detection_rate"] == 1.0, (
        f"Injected fault detection rate {summary['injected_fault_detection_rate']} is not 100%"
    )
    assert summary["summary_fault_detection_rate"] == 1.0, (
        f"Summary fault detection rate {summary['summary_fault_detection_rate']} is not 100%"
    )
    assert elapsed <= 30.0, f"Lightweight mode took {elapsed:.2f}s, expected <= 30s"

    # Write report to workspace root
    workspace = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    report_path = os.path.join(workspace, "session_memory_summary_diff_report.json")
    save_report(report, report_path)


@pytest.mark.asyncio
async def test_replay_consistency_summary_details(harness: ReplayHarness):
    """Verify that summary-specific faults produce field-level diffs."""
    cases, faults = get_all_cases_with_faults()
    report = await harness.run_cases(cases, faults)

    summary_faults = {"injected_drop_summary", "injected_wrong_summary_session",
                      "injected_summary_loss", "injected_summary_override_error"}
    for case_report in report["cases"]:
        if case_report["injected_fault"] in summary_faults:
            assert not case_report["consistent"], (
                f"Summary fault {case_report['injected_fault']} was not detected"
            )
            paths = [d["path"] for d in case_report["differences"]]
            assert any("summaries" in p for p in paths), (
                f"No summary path diff for {case_report['case_name']}"
            )
