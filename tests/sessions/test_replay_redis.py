# Tencent is pleased to support the open source community by making
# tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Redis-backed replay consistency tests (env-var gated).

Set REDIS_URL to enable: e.g. REDIS_URL=redis://localhost:6379

When Redis is not available, all tests skip gracefully.
"""

from __future__ import annotations

import os
import pathlib

import pytest

from trpc_agent_sdk.sessions._in_memory_session_service import (
    InMemorySessionService,
)
from trpc_agent_sdk.sessions._sql_session_service import (
    SqlSessionService,
)
from trpc_agent_sdk.sessions._types import SessionServiceConfig
from trpc_agent_sdk.memory._in_memory_memory_service import (
    InMemoryMemoryService,
)
from trpc_agent_sdk.memory._sql_memory_service import (
    SqlMemoryService,
)

from tests.sessions.replay_consistency.cases import _replay_cases
from tests.sessions.replay_consistency.normalizer import normalize_snapshot
from tests.sessions.replay_consistency.comparator import recursive_diff


# ---------------------------------------------------------------------------
# Redis availability check
# ---------------------------------------------------------------------------

def _redis_available() -> bool:
    """Check if Redis is configured and reachable."""
    redis_url = os.environ.get("REDIS_URL", "")
    if not redis_url:
        return False
    try:
        import redis as redis_client  # noqa: F401
        r = redis_client.from_url(redis_url)
        r.ping()
        r.close()
        return True
    except Exception:
        return False


REDIS_SKIP_REASON = (
    "REDIS_URL not set or Redis not reachable. "
    "Set REDIS_URL=redis://localhost:6379 to enable."
)


# ---------------------------------------------------------------------------
# Backend helpers
# ---------------------------------------------------------------------------

def _make_inmemory_backend():
    cfg = SessionServiceConfig()
    return InMemorySessionService(session_config=cfg), InMemoryMemoryService()


def _make_sqlite_backend(db_path: str):
    cfg = SessionServiceConfig()
    return (
        SqlSessionService(sqlite_db_path=db_path, session_config=cfg),
        SqlMemoryService(sqlite_db_path=db_path),
    )


def _make_redis_backend():
    if not _redis_available():
        pytest.skip(REDIS_SKIP_REASON)
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    from trpc_agent_sdk.sessions._redis_session_service import (
        RedisSessionService,
    )
    from trpc_agent_sdk.memory._redis_memory_service import (
        RedisMemoryService,
    )
    cfg = SessionServiceConfig()
    return (
        RedisSessionService(redis_url=redis_url, session_config=cfg, is_async=False),
        RedisMemoryService(redis_url=redis_url, is_async=False),
    )


async def _run_case(sess_svc, mem_svc, case):
    """Execute a single replay case against given backends."""
    session = await sess_svc.create_session(
        app_name=case.app_name,
        user_id=case.user_id,
        session_id=case.session_id,
        state=case.initial_state,
    )

    from trpc_agent_sdk.events import Event
    from trpc_agent_sdk.types import Content, EventActions, Part

    for i, es in enumerate(case.events):
        actions = (
            EventActions(state_delta=es.state_delta) if es.state_delta
            else EventActions()
        )
        content = Content(
            parts=[Part.from_text(text=es.text)] if es.text else []
        )
        event = Event(
            invocation_id=es.invocation_id or f"inv-{case.session_id}-{i}",
            author=es.author,
            content=content,
            actions=actions,
        )
        await sess_svc.append_event(session, event)

        for ss in case.summary_steps:
            if ss.after_event_index == i + 1:
                try:
                    await sess_svc.create_session_summary(
                        session=session, filter_key=ss.filter_key,
                    )
                except Exception:
                    pass

    for mw in case.memory_writes:
        try:
            await mem_svc.add_memory(
                app_name=case.app_name,
                user_id=case.user_id,
                memory=mw.memory,
                topics=mw.topics,
            )
        except Exception:
            pass

    all_memories: list[dict] = []
    for mq in case.memory_queries:
        try:
            entries = await mem_svc.search_memories(
                app_name=case.app_name,
                user_id=case.user_id,
                query=mq.query,
                max_results=mq.limit,
            )
            for entry in entries:
                if hasattr(entry, "memory") and entry.memory:
                    all_memories.append({
                        "content": entry.memory.memory,
                        "topics": getattr(entry.memory, "topics", []),
                    })
        except Exception:
            pass

    session = await sess_svc.get_session(
        app_name=case.app_name,
        user_id=case.user_id,
        session_id=case.session_id,
    )
    return normalize_snapshot(session, all_memories)


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestReplayRedis:
    """Redis vs InMemory/SQLite replay consistency tests."""

    async def test_redis_vs_inmemory_all_10_cases(self, tmp_path: pathlib.Path):
        """Redis vs InMemory: all 10 cases must have 0 unallowed diffs."""
        if not _redis_available():
            pytest.skip(REDIS_SKIP_REASON)

        cases = _replay_cases()
        im_sess, im_mem = _make_inmemory_backend()
        rd_sess, rd_mem = _make_redis_backend()

        for case in cases:
            snap_im = await _run_case(im_sess, im_mem, case)
            snap_rd = await _run_case(rd_sess, rd_mem, case)

            diffs = recursive_diff(
                snap_im, snap_rd,
                case_name=f"{case.name} [inmemory vs redis]",
            )
            unallowed = [d for d in diffs if not d.allowed]
            if unallowed:
                for d in unallowed:
                    print(f"UNALLOWED DIFF [{case.name}]: {d.path}: {d.left} != {d.right}")
            assert len(unallowed) == 0, (
                f"Case '{case.name}' has {len(unallowed)} unallowed diffs "
                f"between InMemory and Redis"
            )

    async def test_redis_vs_sqlite_all_10_cases(self, tmp_path: pathlib.Path):
        """Redis vs SQLite: all 10 cases must have 0 unallowed diffs."""
        if not _redis_available():
            pytest.skip(REDIS_SKIP_REASON)

        db_path = str(tmp_path / "replay_redis_test.db")
        sql_sess, sql_mem = _make_sqlite_backend(db_path)
        rd_sess, rd_mem = _make_redis_backend()

        cases = _replay_cases()
        for case in cases:
            snap_sql = await _run_case(sql_sess, sql_mem, case)
            snap_rd = await _run_case(rd_sess, rd_mem, case)

            diffs = recursive_diff(
                snap_sql, snap_rd,
                case_name=f"{case.name} [sqlite vs redis]",
            )
            unallowed = [d for d in diffs if not d.allowed]
            assert len(unallowed) == 0, (
                f"Case '{case.name}' has {len(unallowed)} unallowed diffs "
                f"between SQLite and Redis"
            )

    async def test_three_backend_full_comparison(self, tmp_path: pathlib.Path):
        """InMemory vs SQLite vs Redis full matrix."""
        if not _redis_available():
            pytest.skip(REDIS_SKIP_REASON)

        db_path = str(tmp_path / "replay_three.db")
        im_sess, im_mem = _make_inmemory_backend()
        sql_sess, sql_mem = _make_sqlite_backend(db_path)
        rd_sess, rd_mem = _make_redis_backend()

        pairs = [
            ("inmemory", im_sess, im_mem),
            ("sqlite", sql_sess, sql_mem),
            ("redis", rd_sess, rd_mem),
        ]

        case = _replay_cases()[0]  # Use first case for quick check.
        snapshots = []
        for name, sess, mem in pairs:
            snap = await _run_case(sess, mem, case)
            snapshots.append((name, snap))

        for i in range(len(snapshots)):
            for j in range(i + 1, len(snapshots)):
                name_a, snap_a = snapshots[i]
                name_b, snap_b = snapshots[j]
                diffs = recursive_diff(snap_a, snap_b)
                unallowed = [d for d in diffs if not d.allowed]
                assert len(unallowed) == 0, (
                    f"Case '{case.name}' unallowed diffs between "
                    f"{name_a} and {name_b}: {len(unallowed)}"
                )
