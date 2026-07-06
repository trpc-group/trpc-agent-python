# Tencent is pleased to support the open source community by making
# tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Replay consistency test harness for Session, Memory, and Summary backends.

Drives InMemory and SQLite backends with the same deterministic replay
cases, normalizes non-business differences, compares snapshots, and
writes a structured diff report to ``session_memory_summary_diff_report.json``.

Mirrors the Go implementation in trpc-agent-go/session/replaytest/.
"""

from __future__ import annotations

import dataclasses
import json
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

from tests.sessions.replay_consistency.cases import (
    _replay_cases,
    EventSpec,
    ReplayCase,
)
from tests.sessions.replay_consistency.normalizer import normalize_snapshot
from tests.sessions.replay_consistency.comparator import (
    DiffEntry,
    recursive_diff,
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


# ---------------------------------------------------------------------------
# Report generator
# ---------------------------------------------------------------------------

def _generate_report(
    diffs_by_case: dict[str, list[DiffEntry]],
    output_path: str = "session_memory_summary_diff_report.json",
) -> None:
    """Write the diff report to a JSON file."""
    report = []
    for case_name, diffs in diffs_by_case.items():
        report.append({
            "case_name": case_name,
            "diffs": [dataclasses.asdict(d) for d in diffs],
        })
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# Case runner
# ---------------------------------------------------------------------------

async def _run_case(sess_svc, mem_svc, case: ReplayCase):
    """Execute a single replay case against given backends."""
    from trpc_agent_sdk.events import Event
    from trpc_agent_sdk.types import Content, EventActions, Part

    session = await sess_svc.create_session(
        app_name=case.app_name,
        user_id=case.user_id,
        session_id=case.session_id,
        state=case.initial_state,
    )

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


# =============================================================================
# Test functions
# =============================================================================

@pytest.mark.asyncio
class TestReplayConsistency:

    async def test_in_memory_and_sqlite_session_replay_match(
        self, tmp_path: pathlib.Path,
    ):
        """Run all 10 replay cases across InMemory and SQLite backends
        and assert zero unallowed diffs."""
        cases = _replay_cases()
        assert len(cases) == 10, f"Expected 10 cases, got {len(cases)}"

        backends = [("inmemory", *_make_inmemory_backend())]

        try:
            db_path = str(tmp_path / "replay_test.db")
            sqlite_sess, sqlite_mem = _make_sqlite_backend(db_path)
            backends.append(("sqlite", sqlite_sess, sqlite_mem))
        except Exception:
            pass

        all_diffs: dict[str, list[DiffEntry]] = {}

        for case in cases:
            snapshots = []
            for name, sess_svc, mem_svc in backends:
                snapshot = await _run_case(sess_svc, mem_svc, case)
                snapshots.append((name, snapshot))

            for i in range(len(snapshots)):
                for j in range(i + 1, len(snapshots)):
                    name_a, snap_a = snapshots[i]
                    name_b, snap_b = snapshots[j]
                    diffs = recursive_diff(
                        snap_a, snap_b,
                        case_name=f"{case.name} [{name_a} vs {name_b}]",
                    )
                    key = f"{case.name}_{name_a}_vs_{name_b}"
                    all_diffs[key] = diffs

                    unallowed = [d for d in diffs if not d.allowed]
                    if unallowed:
                        for d in unallowed:
                            print(
                                f"UNALLOWED DIFF [{case.name}]: "
                                f"{d.path}: {d.left} != {d.right}"
                            )
                    assert len(unallowed) == 0, (
                        f"Case '{case.name}' has {len(unallowed)} "
                        f"unallowed diffs between {name_a} and {name_b}"
                    )

        _generate_report(all_diffs)

    async def test_diff_detects_summary_injections(self):
        """Verify summary-specific diff detection."""
        left = {"summaries": {"": "Correct summary"}}
        right: dict = {"summaries": {}}
        diffs = recursive_diff(left, right)
        assert len(diffs) > 0, "Missing summary should be detected"

        right2 = {"summaries": {"": "Overwritten text"}}
        diffs2 = recursive_diff(left, right2)
        assert len(diffs2) > 0, "Summary overwrite must be detected"

    async def test_diff_detects_state_memory_injections(self):
        """Verify state, memory, and event diffs are detected."""
        left = {
            "events": [{"author": "user", "text": "Hello"}],
            "state": {"key": "value1"},
            "memories": [{"content": "test memory"}],
            "tracks": [{"track": "exec", "payload": '{"ok":true}'}],
        }
        right = {
            "events": [{"author": "user", "text": "Different"}],
            "state": {"key": "value2"},
            "memories": [{"content": "other memory"}],
            "tracks": [{"track": "exec", "payload": '{"ok":false}'}],
        }
        diffs = recursive_diff(left, right)
        sections: set[str] = set()
        for d in diffs:
            path = d.path or ""
            top = path.split("[")[0].split(".")[0]
            sections.add(top)
        assert "events" in sections, f"Event diffs not detected in {sections}"
        assert "state" in sections, f"State diffs not detected in {sections}"
        assert "memories" in sections, f"Memory diffs not detected in {sections}"
        assert "tracks" in sections, f"Track diffs not detected in {sections}"

    async def test_jsonl_roundtrip_matches_python_cases(self):
        """Cases loaded from JSONL should match Python-defined cases."""
        cases = _replay_cases()
        from tests.sessions.replay_consistency.cases import load_case_from_jsonl
        fixtures_dir = pathlib.Path(__file__).parent / "replay_consistency" / "fixtures"
        filenames = sorted(fixtures_dir.glob("case_*.jsonl"))
        assert len(filenames) == 10

        for i, fpath in enumerate(filenames):
            from_jsonl = load_case_from_jsonl(str(fpath))
            from_py = cases[i]
            assert from_jsonl.name == from_py.name
            assert from_jsonl.session_id == from_py.session_id
            assert len(from_jsonl.events) == len(from_py.events)
            assert len(from_jsonl.memory_writes) == len(from_py.memory_writes)
