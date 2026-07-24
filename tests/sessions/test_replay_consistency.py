# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Replay consistency harness for Session, Memory, and Summary backends."""

from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pytest

from trpc_agent_sdk.abc import MemoryServiceConfig
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.memory import InMemoryMemoryService, SqlMemoryService
from trpc_agent_sdk.sessions import InMemorySessionService, Session, SessionServiceConfig
from trpc_agent_sdk.sessions import SessionSummarizer, SqlSessionService, SummarizerSessionManager
from trpc_agent_sdk.types import Content, EventActions, FunctionCall, FunctionResponse, Part

APP_NAME = "replay-app"
USER_ID = "replay-user"
REPORT_PATH = Path("session_memory_summary_diff_report.json")
CASES_PATH = Path(__file__).with_name("replay_cases") / "issue_89_replay_cases.jsonl"
SUMMARY_PREFIX = "Previous conversation summary: "


class _FakeSummaryModel:
    name = "deterministic-summary-model"


class _DeterministicSummarizer(SessionSummarizer):
    """A real SessionSummarizer subclass with deterministic text generation."""

    def __init__(self) -> None:
        super().__init__(
            model=_FakeSummaryModel(),
            check_summarizer_functions=[lambda session: bool(session.events)],
            keep_recent_count=2,
        )
        self._revision_by_session: dict[str, int] = {}

    async def _compress_session_to_summary(
        self,
        events: list[Event],
        session_id: str,
        ctx=None,
    ) -> str:
        revision = self._revision_by_session.get(session_id, 0) + 1
        self._revision_by_session[session_id] = revision
        covered = "|".join(_event_text(event) for event in events if _event_text(event))
        return f"{session_id} summary revision v{revision}: {covered}"


@dataclass
class _Backend:
    name: str
    session_service: Any
    memory_service: Any
    db_url: str | None = None


@dataclass
class _ReplayContext:
    backend: _Backend
    sessions: dict[str, Session]
    memory_searches: list[dict[str, Any]] = field(default_factory=list)
    summary_generations: dict[str, int] = field(default_factory=dict)


def _load_replay_cases() -> list[dict[str, Any]]:
    with CASES_PATH.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _memory_config() -> MemoryServiceConfig:
    config = MemoryServiceConfig(enabled=True)
    config.clean_ttl_config()
    return config


def _session_config() -> SessionServiceConfig:
    config = SessionServiceConfig(store_historical_events=True)
    config.clean_ttl_config()
    return config


def _summarizer_manager() -> SummarizerSessionManager:
    return SummarizerSessionManager(
        model=_FakeSummaryModel(),
        summarizer=_DeterministicSummarizer(),
        auto_summarize=True,
    )


async def _build_inmemory_backend() -> _Backend:
    return _Backend(
        name="inmemory",
        session_service=InMemorySessionService(
            session_config=_session_config(),
            summarizer_manager=_summarizer_manager(),
        ),
        memory_service=InMemoryMemoryService(memory_service_config=_memory_config()),
    )


async def _build_sqlite_backend(tmp_path: Path) -> _Backend:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite:///{(tmp_path / 'replay-consistency.db').as_posix()}"
    session_service = SqlSessionService(
        db_url=db_url,
        session_config=_session_config(),
        summarizer_manager=_summarizer_manager(),
        is_async=False,
    )
    memory_service = SqlMemoryService(
        db_url=db_url,
        is_async=False,
        memory_service_config=_memory_config(),
    )
    await session_service._sql_storage.create_sql_engine()
    await memory_service._sql_storage.create_sql_engine()
    return _Backend(
        name="sqlite",
        session_service=session_service,
        memory_service=memory_service,
        db_url=db_url,
    )


async def _close_backend(backend: _Backend) -> None:
    await backend.memory_service.close()
    await backend.session_service.close()


async def _reopen_sqlite_session_backend(db_url: str) -> _Backend:
    session_service = SqlSessionService(
        db_url=db_url,
        session_config=_session_config(),
        summarizer_manager=_summarizer_manager(),
        is_async=False,
    )
    await session_service._sql_storage.create_sql_engine()
    return _Backend(name="sqlite-cold", session_service=session_service, memory_service=None, db_url=db_url)


async def _ensure_session(ctx: _ReplayContext, session_id: str) -> Session:
    if session_id not in ctx.sessions:
        await ctx.backend.session_service.create_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=session_id,
        )
        ctx.sessions[session_id] = await _refresh_session(ctx, session_id)
    return ctx.sessions[session_id]


async def _refresh_session(ctx: _ReplayContext, session_id: str) -> Session:
    session = await ctx.backend.session_service.get_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id,
    )
    assert session is not None
    return session


async def _append_event(ctx: _ReplayContext, session_id: str, event: Event) -> None:
    session = await _ensure_session(ctx, session_id)
    await ctx.backend.session_service.append_event(session, event)
    ctx.sessions[session_id] = await _refresh_session(ctx, session_id)


def _text_event(
    *,
    author: str,
    text: str,
    invocation_id: str,
    event_id: str | None = None,
    state_delta: dict[str, Any] | None = None,
    timestamp: float | None = None,
) -> Event:
    return Event(
        id=event_id or "",
        invocation_id=invocation_id,
        author=author,
        content=Content(parts=[Part.from_text(text=text)]),
        actions=EventActions(state_delta=state_delta or {}),
        timestamp=timestamp or _replay_timestamp(0),
    )


def _tool_call_event(operation: dict[str, Any], invocation_id: str, timestamp: float) -> Event:
    return Event(
        invocation_id=invocation_id,
        author=operation["author"],
        timestamp=timestamp,
        content=Content(
            parts=[
                Part(
                    function_call=FunctionCall(
                        id=operation["call_id"],
                        name=operation["name"],
                        args=operation["args"],
                    )
                )
            ]
        ),
    )


def _tool_response_event(operation: dict[str, Any], invocation_id: str, timestamp: float) -> Event:
    return Event(
        invocation_id=invocation_id,
        author=operation["author"],
        timestamp=timestamp,
        content=Content(
            parts=[
                Part(
                    function_response=FunctionResponse(
                        id=operation["call_id"],
                        name=operation["name"],
                        response=operation["response"],
                    )
                )
            ]
        ),
    )


def _replay_timestamp(index: int) -> float:
    return 2_000_000_000.0 + index


async def _run_case(case: dict[str, Any], backend: _Backend) -> dict[str, Any]:
    primary_session_id = f"{case['case_id']}-session"
    ctx = _ReplayContext(backend=backend, sessions={})
    await _ensure_session(ctx, primary_session_id)

    for index, operation in enumerate(case["operations"]):
        invocation_id = f"{case['case_id']}-inv-{index}"
        op = operation["op"]
        if op == "event":
            event = _text_event(
                event_id=operation.get("event_id"),
                invocation_id=invocation_id,
                author=operation["author"],
                text=operation["text"],
                timestamp=_replay_timestamp(index),
            )
            await _append_event(ctx, primary_session_id, event)
        elif op == "tool_call":
            await _append_event(
                ctx,
                primary_session_id,
                _tool_call_event(operation, invocation_id, _replay_timestamp(index)),
            )
        elif op == "tool_response":
            await _append_event(
                ctx,
                primary_session_id,
                _tool_response_event(operation, invocation_id, _replay_timestamp(index)),
            )
        elif op == "state":
            event = _text_event(
                invocation_id=invocation_id,
                author="system",
                text=f"state {operation['key']} updated",
                state_delta={operation["key"]: operation["value"]},
                timestamp=_replay_timestamp(index),
            )
            await _append_event(ctx, primary_session_id, event)
        elif op == "memory_store":
            await ctx.backend.memory_service.store_session(ctx.sessions[primary_session_id])
        elif op == "memory_search":
            response = await ctx.backend.memory_service.search_memory(
                f"{APP_NAME}/{USER_ID}",
                operation["query"],
                limit=10,
            )
            results = [_normalize_memory_entry(memory.model_dump(mode="json")) for memory in response.memories]
            ctx.memory_searches.append(
                {
                    "query": operation["query"],
                    "results": sorted(results, key=lambda item: json.dumps(item, sort_keys=True)),
                }
            )
        elif op == "summary":
            await ctx.backend.session_service.create_session_summary(ctx.sessions[primary_session_id])
            ctx.sessions[primary_session_id] = await _refresh_session(ctx, primary_session_id)
            ctx.summary_generations[primary_session_id] = ctx.summary_generations.get(primary_session_id, 0) + 1
        elif op == "duplicate_update":
            await ctx.backend.session_service.update_session(ctx.sessions[primary_session_id])
            ctx.sessions[primary_session_id] = await _refresh_session(ctx, primary_session_id)
        elif op == "other_session_event":
            session_id = operation["session_id"]
            event = _text_event(
                invocation_id=invocation_id,
                author=operation["author"],
                text=operation["text"],
                timestamp=_replay_timestamp(index),
            )
            await _append_event(ctx, session_id, event)
        elif op == "other_session_summary":
            session_id = operation["session_id"]
            await ctx.backend.session_service.create_session_summary(ctx.sessions[session_id])
            ctx.sessions[session_id] = await _refresh_session(ctx, session_id)
            ctx.summary_generations[session_id] = ctx.summary_generations.get(session_id, 0) + 1
        else:
            raise AssertionError(f"unknown replay operation: {op}")

    return await _snapshot(case["case_id"], ctx)


async def _snapshot(case_id: str, ctx: _ReplayContext) -> dict[str, Any]:
    sessions = {}
    for session_id in sorted(ctx.sessions):
        reloaded = await ctx.backend.session_service.get_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=session_id,
        )
        assert reloaded is not None
        runtime_summary = await ctx.backend.session_service.get_session_summary(reloaded)
        sessions[session_id] = _normalize_session(
            reloaded,
            runtime_summary=runtime_summary,
            observed_generation_ordinal=ctx.summary_generations.get(session_id, 0),
        )
    return {
        "case_id": case_id,
        "sessions": sessions,
        "memory": {
            "searches": sorted(ctx.memory_searches, key=lambda item: item["query"]),
        },
    }


async def _cold_sqlite_snapshot(case_id: str, warm_backend: _Backend, warm_snapshot: dict[str, Any]) -> dict[str, Any]:
    assert warm_backend.db_url is not None
    cold_backend = await _reopen_sqlite_session_backend(warm_backend.db_url)
    try:
        sessions = {}
        for session_id, warm_session in warm_snapshot["sessions"].items():
            reloaded = await cold_backend.session_service.get_session(
                app_name=APP_NAME,
                user_id=USER_ID,
                session_id=session_id,
            )
            assert reloaded is not None
            sessions[session_id] = _normalize_session(
                reloaded,
                runtime_summary=None,
                observed_generation_ordinal=warm_session["summary"]["observed_generation_ordinal"],
            )
        return {
            "case_id": case_id,
            "sessions": sessions,
            "memory": warm_snapshot["memory"],
        }
    finally:
        await cold_backend.session_service.close()


def _normalize_session(
    session: Session,
    *,
    runtime_summary: str | None,
    observed_generation_ordinal: int,
) -> dict[str, Any]:
    active_events = [_normalize_event(event, index) for index, event in enumerate(session.events)]
    historical_events = [_normalize_event(event, index) for index, event in enumerate(session.historical_events)]
    active_summary_anchors = [event for event in session.events if event.is_summary_event()]
    historical_summary_anchors = [event for event in session.historical_events if event.is_summary_event()]
    all_summary_anchors = active_summary_anchors + historical_summary_anchors
    current_anchor = active_summary_anchors[0] if active_summary_anchors else None
    current_text = _summary_text(current_anchor) if current_anchor else None
    return {
        "session_id": session.id,
        "state": _stable_json(session.state),
        "events": active_events,
        "historical_events": historical_events,
        "summary": {
            "runtime_text": runtime_summary,
            "persisted_text": current_text,
            "current_revision": _summary_revision(current_text),
            "observed_generation_ordinal": observed_generation_ordinal,
            "persisted_version": {
                "status": "unsupported",
                "reason": "SDK exposes observable summary revision state, not a dedicated persisted version field.",
            },
            "anchor_id": current_anchor.id if current_anchor else None,
            "anchor_timestamp": current_anchor.timestamp if current_anchor else None,
            "anchor_count": len(all_summary_anchors),
            "active_anchor_count": len(active_summary_anchors),
            "historical_anchor_count": len(historical_summary_anchors),
            "coverage": {
                "historical_event_count": len(session.historical_events),
                "active_event_count": len(session.events),
                "has_summary_anchor": current_anchor is not None,
                "has_recent_events_after_summary": bool(current_anchor and len(session.events) > 1),
            },
            "session_owner": session.id if current_anchor else None,
        },
    }


def _normalize_event(event: Event, index: int) -> dict[str, Any]:
    return {
        "event_index": index,
        "author": event.author,
        "invocation_id": event.invocation_id,
        "parts": [_normalize_part(part) for part in (event.content.parts if event.content else [])],
        "state_delta": _stable_json(event.actions.state_delta if event.actions else {}),
        "is_summary": event.is_summary_event(),
        "version": event.version,
    }


def _normalize_part(part: Part) -> dict[str, Any]:
    if part.text is not None:
        return {"text": part.text}
    if part.function_call is not None:
        return {
            "function_call": {
                "id": part.function_call.id,
                "name": part.function_call.name,
                "args": _stable_json(part.function_call.args),
            }
        }
    if part.function_response is not None:
        return {
            "function_response": {
                "id": part.function_response.id,
                "name": part.function_response.name,
                "response": _stable_json(part.function_response.response),
            }
        }
    return {}


def _normalize_memory_entry(entry: dict[str, Any]) -> dict[str, Any]:
    parts = entry.get("content", {}).get("parts", [])
    return {
        "author": entry.get("author"),
        "parts": [_stable_json(part) for part in parts],
    }


def _stable_json(value: Any) -> Any:
    return json.loads(json.dumps(value or {}, sort_keys=True, default=str))


def _event_text(event: Event) -> str:
    if not event.content or not event.content.parts:
        return ""
    return "".join(part.text or "" for part in event.content.parts)


def _summary_text(event: Event | None) -> str | None:
    text = _event_text(event) if event else ""
    if text.startswith(SUMMARY_PREFIX):
        return text[len(SUMMARY_PREFIX):]
    return text or None


def _summary_revision(summary_text: str | None) -> str | None:
    if not summary_text:
        return None
    match = re.search(r"\bv\d+\b", summary_text)
    return match.group(0) if match else None


def _compare_snapshots(
    case_id: str,
    expected: dict[str, Any],
    actual: dict[str, Any],
    *,
    expected_backend: str,
    actual_backend: str,
) -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = []
    _compare_value(case_id, expected, actual, "", diffs, expected_backend, actual_backend)
    return diffs


def _compare_value(
    case_id: str,
    expected: Any,
    actual: Any,
    path: str,
    diffs: list[dict[str, Any]],
    expected_backend: str,
    actual_backend: str,
) -> None:
    if _is_allowed_normalized_difference(path):
        return
    if expected == actual:
        return
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            _compare_value(
                case_id,
                expected.get(key),
                actual.get(key),
                f"{path}.{key}" if path else key,
                diffs,
                expected_backend,
                actual_backend,
            )
        return
    if isinstance(expected, list) and isinstance(actual, list):
        for index in range(max(len(expected), len(actual))):
            left = expected[index] if index < len(expected) else None
            right = actual[index] if index < len(actual) else None
            _compare_value(
                case_id,
                left,
                right,
                f"{path}[{index}]",
                diffs,
                expected_backend,
                actual_backend,
            )
        return
    diffs.append(
        {
            "case_id": case_id,
            "expected_backend": expected_backend,
            "actual_backend": actual_backend,
            "section": path.split(".", 1)[0],
            "session_id": _extract_session_id(path),
            "event_index": _extract_event_index(path),
            "summary_id": _extract_summary_id(path, actual),
            "field_path": path,
            "expected": expected,
            "actual": actual,
            "allowed": False,
            "reason": "normalized SDK-observable replay state differs",
        }
    )


def _is_allowed_normalized_difference(path: str) -> bool:
    return path.endswith("summary.anchor_id") or path.endswith("summary.anchor_timestamp")


def _extract_session_id(path: str) -> str | None:
    match = re.search(r"sessions\.([^.[]+)", path)
    return match.group(1) if match else None


def _extract_event_index(path: str) -> int | None:
    match = re.search(r"events\[(\d+)\]", path)
    return int(match.group(1)) if match else None


def _extract_summary_id(path: str, actual: Any) -> str | None:
    return str(actual) if path.endswith("summary.anchor_id") and actual is not None else None


def _allowed_diffs(case_id: str) -> list[dict[str, Any]]:
    return [
        {
            "case_id": case_id,
            "field_path": "sessions.*.events.*.id",
            "allowed": True,
            "reason": "event ids are SDK-generated unless fixed by the replay case",
        },
        {
            "case_id": case_id,
            "field_path": "sessions.*.events.*.timestamp",
            "allowed": True,
            "reason": "event timestamps are normalized away as non-business fields",
        },
        {
            "case_id": case_id,
            "field_path": "sessions.*.summary.anchor_timestamp",
            "allowed": True,
            "reason": "summary timestamps are compared by validity and order, not cross-backend equality",
        },
    ]


def _write_report(comparisons: list[dict[str, Any]]) -> None:
    report = {
        "generated_at": "deterministic-test-run",
        "comparisons": comparisons,
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _assert_summary_timestamps(snapshot: dict[str, Any]) -> None:
    for session in snapshot["sessions"].values():
        summary = session["summary"]
        if not summary["anchor_timestamp"]:
            continue
        assert isinstance(summary["anchor_timestamp"], float)
        assert summary["anchor_timestamp"] > 0


def _assert_sqlite_cold_summary(warm: dict[str, Any], cold: dict[str, Any]) -> None:
    for session_id, warm_session in warm["sessions"].items():
        warm_summary = warm_session["summary"]
        cold_summary = cold["sessions"][session_id]["summary"]
        assert cold_summary["runtime_text"] is None
        assert cold_summary["persisted_text"] == warm_summary["persisted_text"]
        assert cold_summary["anchor_id"] == warm_summary["anchor_id"]
        assert cold_summary["anchor_timestamp"] == warm_summary["anchor_timestamp"]


Mutation = tuple[str, Callable[[dict[str, Any]], None]]


def _mutations() -> list[Mutation]:
    return [
        ("event_missing", lambda snapshot: snapshot["sessions"][next(iter(snapshot["sessions"]))]["events"].pop(0)),
        (
            "event_duplicate",
            lambda snapshot: snapshot["sessions"][next(iter(snapshot["sessions"]))]["events"].append(
                copy.deepcopy(snapshot["sessions"][next(iter(snapshot["sessions"]))]["events"][0])
            ),
        ),
        ("event_order", _swap_first_two_events),
        ("event_content", _mutate_first_event_text),
        ("state_missing", lambda snapshot: snapshot["sessions"][next(iter(snapshot["sessions"]))]["state"].clear()),
        (
            "state_dirty",
            lambda snapshot: snapshot["sessions"][next(iter(snapshot["sessions"]))]["state"].update({"dirty": True}),
        ),
        ("memory_missing", lambda snapshot: snapshot["memory"]["searches"].clear()),
        ("memory_value", _mutate_memory_result),
        ("summary_lost", _mutate_summary_lost),
        ("summary_overwrite_wrong", _mutate_summary_overwrite),
        ("summary_wrong_session", _mutate_summary_wrong_session),
    ]


def _first_session(snapshot: dict[str, Any]) -> dict[str, Any]:
    return snapshot["sessions"][next(iter(snapshot["sessions"]))]


def _swap_first_two_events(snapshot: dict[str, Any]) -> None:
    events = _first_session(snapshot)["events"]
    events[0], events[1] = events[1], events[0]


def _mutate_first_event_text(snapshot: dict[str, Any]) -> None:
    first_part = _first_session(snapshot)["events"][0]["parts"][0]
    first_part["text"] = "mutated text"


def _mutate_memory_result(snapshot: dict[str, Any]) -> None:
    searches = snapshot["memory"]["searches"]
    if searches and searches[0]["results"]:
        searches[0]["results"][0]["author"] = "wrong-author"
    else:
        searches.append({"query": "missing", "results": [{"author": "wrong-author", "parts": []}]})


def _mutate_summary_lost(snapshot: dict[str, Any]) -> None:
    summary = _first_session(snapshot)["summary"]
    summary["persisted_text"] = None
    summary["current_revision"] = None
    summary["anchor_id"] = None
    summary["anchor_count"] = 0


def _mutate_summary_overwrite(snapshot: dict[str, Any]) -> None:
    summary = _first_session(snapshot)["summary"]
    summary["persisted_text"] = "stale summary revision v1"
    summary["current_revision"] = "v1"


def _mutate_summary_wrong_session(snapshot: dict[str, Any]) -> None:
    _first_session(snapshot)["summary"]["session_owner"] = "wrong-session"


@pytest.mark.asyncio
async def test_replay_cases_compare_inmemory_and_sqlite_without_false_positives(tmp_path: Path):
    cases = _load_replay_cases()
    assert len(cases) == 10

    comparisons = []
    false_positive_count = 0
    for case in cases:
        inmemory = await _build_inmemory_backend()
        sqlite = await _build_sqlite_backend(tmp_path / case["case_id"])
        try:
            inmemory_snapshot = await _run_case(case, inmemory)
            sqlite_snapshot = await _run_case(case, sqlite)
            cold_snapshot = await _cold_sqlite_snapshot(case["case_id"], sqlite, sqlite_snapshot)
            diffs = _compare_snapshots(
                case["case_id"],
                inmemory_snapshot,
                sqlite_snapshot,
                expected_backend="inmemory",
                actual_backend="sqlite",
            )
            false_positive_count += 1 if diffs else 0
            comparisons.append(
                {
                    "case_id": case["case_id"],
                    "expected_backend": "inmemory",
                    "actual_backend": "sqlite",
                    "diffs": diffs,
                    "allowed_diffs": _allowed_diffs(case["case_id"]),
                }
            )
            _assert_summary_timestamps(inmemory_snapshot)
            _assert_summary_timestamps(sqlite_snapshot)
            _assert_sqlite_cold_summary(sqlite_snapshot, cold_snapshot)
        finally:
            await _close_backend(inmemory)
            await _close_backend(sqlite)

    _write_report(comparisons)
    assert false_positive_count / len(cases) <= 0.05
    assert all(not comparison["diffs"] for comparison in comparisons)


@pytest.mark.asyncio
async def test_injected_inconsistencies_are_detected(tmp_path: Path):
    cases = {item["case_id"]: item for item in _load_replay_cases()}
    baselines = {}
    for case_id in ["summary_update", "state_overwrite", "memory_write_read"]:
        backend = await _build_sqlite_backend(tmp_path / case_id)
        try:
            baselines[case_id] = await _run_case(cases[case_id], backend)
        finally:
            await _close_backend(backend)

    missed = []
    for mutation_name, mutate in _mutations():
        if mutation_name.startswith("state"):
            case_id = "state_overwrite"
        elif mutation_name.startswith("memory"):
            case_id = "memory_write_read"
        else:
            case_id = "summary_update"
        baseline = baselines[case_id]
        mutated = copy.deepcopy(baseline)
        mutate(mutated)
        diffs = _compare_snapshots(
            case_id,
            baseline,
            mutated,
            expected_backend="baseline",
            actual_backend=mutation_name,
        )
        if not diffs:
            missed.append(mutation_name)
    assert missed == []


@pytest.mark.asyncio
async def test_summary_loss_overwrite_and_wrong_session_are_detected(tmp_path: Path):
    case = next(item for item in _load_replay_cases() if item["case_id"] == "summary_update")
    backend = await _build_sqlite_backend(tmp_path)
    try:
        baseline = await _run_case(case, backend)
    finally:
        await _close_backend(backend)

    for mutation_name, mutate in [
        ("summary_lost", _mutate_summary_lost),
        ("summary_overwrite_wrong", _mutate_summary_overwrite),
        ("summary_wrong_session", _mutate_summary_wrong_session),
    ]:
        mutated = copy.deepcopy(baseline)
        mutate(mutated)
        diffs = _compare_snapshots(
            case["case_id"],
            baseline,
            mutated,
            expected_backend="baseline",
            actual_backend=mutation_name,
        )
        assert diffs, mutation_name


def test_diff_report_shape():
    assert REPORT_PATH.exists()
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert "comparisons" in report
    assert len(report["comparisons"]) == 10
    for comparison in report["comparisons"]:
        assert {"case_id", "expected_backend", "actual_backend", "diffs", "allowed_diffs"} <= comparison.keys()
        for diff in comparison["diffs"]:
            assert {"session_id", "event_index", "summary_id", "field_path", "expected", "actual"} <= diff.keys()


def test_redis_integration_is_env_gated():
    if not os.getenv("TRPC_AGENT_REPLAY_REDIS_URL"):
        pytest.skip("Set TRPC_AGENT_REPLAY_REDIS_URL to enable Redis replay consistency integration.")
    assert os.getenv("TRPC_AGENT_REPLAY_REDIS_URL")
