# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Replay consistency tests for session events, state, memory, and summary.

This harness drives InMemory and SQLite SQL backends with the same
deterministic event stream, then compares normalized snapshots.
"""

from __future__ import annotations

import copy
import json
import os
import re
import uuid
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any, Literal

import pytest

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.memory import BaseMemoryService
from trpc_agent_sdk.memory import InMemoryMemoryService
from trpc_agent_sdk.memory import MemoryServiceConfig
from trpc_agent_sdk.memory import RedisMemoryService
from trpc_agent_sdk.memory import SqlMemoryService
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.sessions import RedisSessionService
from trpc_agent_sdk.sessions import SqlSessionService
from trpc_agent_sdk.sessions import SessionSummarizer
from trpc_agent_sdk.sessions import SummarizerSessionManager
from trpc_agent_sdk.sessions._base_session_service import BaseSessionService
from trpc_agent_sdk.sessions._types import SessionServiceConfig
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import EventActions
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.types import State


EventKind = Literal["text", "function_call", "function_response"]
REPORT_PATH_ENV = "TRPC_AGENT_REPLAY_REPORT_PATH"
SQL_URL_ENV = "TRPC_AGENT_REPLAY_SQL_URL"
REDIS_URL_ENV = "TRPC_AGENT_REPLAY_REDIS_URL"
DEFAULT_REPORT_PATH = Path(__file__).resolve().parents[2] / "session_memory_summary_diff_report.json"


@dataclass(frozen=True)
class EventSpec:
    """Backend-independent event description used by replay cases."""

    author: str
    kind: EventKind
    invocation_id: str
    text: str | None = None
    function_id: str | None = None
    function_name: str | None = None
    function_args: dict[str, Any] | None = None
    function_response: dict[str, Any] | None = None
    state_delta: dict[str, Any] | None = None


@dataclass(frozen=True)
class MemoryQuerySpec:
    """A deterministic memory search after replay storage."""

    query: str
    limit: int = 20


@dataclass(frozen=True)
class SummaryStep:
    """A deterministic summary operation after a replay event is appended."""

    after_event_index: int
    summary_text: str
    keep_recent_count: int
    expected_original_event_count: int
    expected_compressed_event_count: int


@dataclass(frozen=True)
class AllowedDiffRule:
    """A case-local diff allow-list rule with an explicit reason."""

    section: str
    path_pattern: str
    reason: str
    backend_pair: tuple[str, str] | None = None


@dataclass(frozen=True)
class ReplayCase:
    """A deterministic session replay trajectory."""

    name: str
    app_name: str
    user_id: str
    session_id: str
    initial_state: dict[str, Any]
    events: tuple[EventSpec, ...]
    memory_queries: tuple[MemoryQuerySpec, ...] = ()
    summary_steps: tuple[SummaryStep, ...] = ()
    allowed_diffs: tuple[AllowedDiffRule, ...] = ()


@dataclass(frozen=True)
class BackendBundle:
    """Services that make up one replay backend."""

    session_service: BaseSessionService
    memory_service: BaseMemoryService
    summary_model: DeterministicSummaryModel


class DeterministicSummaryModel:
    """Small fake model that returns the next configured summary text."""

    name = "deterministic-summary-model"

    def __init__(self) -> None:
        self.summary_text = ""

    async def generate_async(self, request: Any, stream: bool = False, ctx: Any = None):
        yield LlmResponse(content=Content(parts=[Part.from_text(text=self.summary_text)]))


@dataclass(frozen=True)
class DiffEntry:
    """A single normalized field mismatch between two backend snapshots."""

    section: str
    path: str
    left: Any
    right: Any


def _make_config(*, store_historical_events: bool = False) -> SessionServiceConfig:
    config = SessionServiceConfig()
    config.store_historical_events = store_historical_events
    config.clean_ttl_config()
    return config


def _make_memory_config() -> MemoryServiceConfig:
    config = MemoryServiceConfig(enabled=True)
    config.clean_ttl_config()
    return config


async def _make_backends(*, include_optional: bool = False) -> dict[str, BackendBundle]:
    in_memory_summary_model = DeterministicSummaryModel()
    sqlite_summary_model = DeterministicSummaryModel()
    in_memory_summarizer_manager = _make_summarizer_manager(in_memory_summary_model, keep_recent_count=10)
    sqlite_summarizer_manager = _make_summarizer_manager(sqlite_summary_model, keep_recent_count=10)
    sqlite_service = SqlSessionService(
        db_url="sqlite:///:memory:",
        summarizer_manager=sqlite_summarizer_manager,
        session_config=_make_config(store_historical_events=True),
        is_async=False,
    )
    sqlite_memory_service = SqlMemoryService(
        db_url="sqlite:///:memory:",
        memory_service_config=_make_memory_config(),
        is_async=False,
    )
    await sqlite_service._sql_storage.create_sql_engine()
    await sqlite_memory_service._sql_storage.create_sql_engine()
    backends = {
        "in_memory": BackendBundle(
            session_service=InMemorySessionService(
                summarizer_manager=in_memory_summarizer_manager,
                session_config=_make_config(store_historical_events=True),
            ),
            memory_service=InMemoryMemoryService(memory_service_config=_make_memory_config()),
            summary_model=in_memory_summary_model,
        ),
        "sqlite_sql": BackendBundle(
            session_service=sqlite_service,
            memory_service=sqlite_memory_service,
            summary_model=sqlite_summary_model,
        ),
    }
    if include_optional:
        await _add_optional_sql_backend(backends)
        _add_optional_redis_backend(backends)
    return backends


async def _add_optional_sql_backend(backends: dict[str, BackendBundle]) -> None:
    sql_url = os.environ.get(SQL_URL_ENV)
    if not sql_url:
        return

    summary_model = DeterministicSummaryModel()
    summarizer_manager = _make_summarizer_manager(summary_model, keep_recent_count=10)
    session_service = SqlSessionService(
        db_url=sql_url,
        summarizer_manager=summarizer_manager,
        session_config=_make_config(store_historical_events=True),
        is_async=False,
    )
    memory_service = SqlMemoryService(
        db_url=sql_url,
        memory_service_config=_make_memory_config(),
        is_async=False,
    )
    await session_service._sql_storage.create_sql_engine()
    await memory_service._sql_storage.create_sql_engine()
    backends["sql_integration"] = BackendBundle(
        session_service=session_service,
        memory_service=memory_service,
        summary_model=summary_model,
    )


def _add_optional_redis_backend(backends: dict[str, BackendBundle]) -> None:
    redis_url = os.environ.get(REDIS_URL_ENV)
    if not redis_url:
        return

    summary_model = DeterministicSummaryModel()
    summarizer_manager = _make_summarizer_manager(summary_model, keep_recent_count=10)
    backends["redis_integration"] = BackendBundle(
        session_service=RedisSessionService(
            db_url=redis_url,
            summarizer_manager=summarizer_manager,
            session_config=_make_config(store_historical_events=True),
            is_async=True,
            decode_responses=True,
        ),
        memory_service=RedisMemoryService(
            db_url=redis_url,
            memory_service_config=_make_memory_config(),
            enabled=True,
            is_async=True,
            decode_responses=True,
        ),
        summary_model=summary_model,
    )


def _make_summarizer_manager(
    model: DeterministicSummaryModel,
    *,
    keep_recent_count: int,
) -> SummarizerSessionManager:
    summarizer = SessionSummarizer(
        model=model,
        check_summarizer_functions=[lambda session: False],
        keep_recent_count=keep_recent_count,
    )
    return SummarizerSessionManager(model=model, summarizer=summarizer, auto_summarize=False)


async def _close_backends(backends: dict[str, BackendBundle]) -> None:
    for backend in backends.values():
        await backend.session_service.close()
        await backend.memory_service.close()


def _event_from_spec(spec: EventSpec) -> Event:
    if spec.kind == "text":
        assert spec.text is not None
        parts = [Part.from_text(text=spec.text)]
    elif spec.kind == "function_call":
        assert spec.function_id is not None
        assert spec.function_name is not None
        parts = [
            Part(
                function_call=FunctionCall(
                    id=spec.function_id,
                    name=spec.function_name,
                    args=copy.deepcopy(spec.function_args or {}),
                )
            )
        ]
    else:
        assert spec.function_id is not None
        assert spec.function_name is not None
        parts = [
            Part(
                function_response=FunctionResponse(
                    id=spec.function_id,
                    name=spec.function_name,
                    response=copy.deepcopy(spec.function_response or {}),
                )
            )
        ]

    return Event(
        invocation_id=spec.invocation_id,
        author=spec.author,
        content=Content(parts=parts),
        actions=EventActions(state_delta=copy.deepcopy(spec.state_delta or {})),
    )


async def _run_case(backend: BackendBundle, case: ReplayCase) -> dict[str, Any]:
    session = await backend.session_service.create_session(
        app_name=case.app_name,
        user_id=case.user_id,
        session_id=case.session_id,
        state=copy.deepcopy(case.initial_state),
    )
    summary_steps = {step.after_event_index: step for step in case.summary_steps}
    for event_index, spec in enumerate(case.events):
        await backend.session_service.append_event(session, _event_from_spec(spec))
        if event_index in summary_steps:
            await _run_summary_step(backend, session, summary_steps[event_index])
            reloaded_session = await backend.session_service.get_session(
                app_name=case.app_name,
                user_id=case.user_id,
                session_id=case.session_id,
            )
            assert reloaded_session is not None
            _move_summary_event_to_front(reloaded_session)
            session = reloaded_session

    stored_session = await backend.session_service.get_session(
        app_name=case.app_name,
        user_id=case.user_id,
        session_id=case.session_id,
    )
    assert stored_session is not None
    await backend.memory_service.store_session(stored_session)
    return await _session_snapshot(
        stored_session,
        backend.session_service,
        backend.memory_service,
        case.memory_queries,
    )


async def _load_session(backend: BackendBundle, case: ReplayCase) -> Any:
    session = await backend.session_service.get_session(
        app_name=case.app_name,
        user_id=case.user_id,
        session_id=case.session_id,
    )
    assert session is not None
    return session


async def _snapshot_backend_case(backend: BackendBundle, case: ReplayCase) -> dict[str, Any]:
    session = await _load_session(backend, case)
    return await _session_snapshot(session, backend.session_service, backend.memory_service, case.memory_queries)


async def _inject_duplicate_event(backend: BackendBundle, case: ReplayCase) -> dict[str, Any]:
    session = await _load_session(backend, case)
    assert session.events
    duplicate_event = session.events[-1].model_copy(deep=True)
    duplicate_event.id = f"{duplicate_event.id}-duplicate"
    await backend.session_service.append_event(session, duplicate_event)
    await backend.memory_service.store_session(await _load_session(backend, case))
    return await _snapshot_backend_case(backend, case)


async def _inject_partial_event_loss(backend: BackendBundle, case: ReplayCase) -> dict[str, Any]:
    session = await _load_session(backend, case)
    assert session.events
    session.events = session.events[:-1]
    await backend.session_service.update_session(session)
    await backend.memory_service.store_session(await _load_session(backend, case))
    return await _snapshot_backend_case(backend, case)


async def _inject_state_pollution(backend: BackendBundle, case: ReplayCase) -> dict[str, Any]:
    session = await _load_session(backend, case)
    session.state["stage"] = "polluted"
    await backend.session_service.update_session(session)
    return await _snapshot_backend_case(backend, case)


async def _inject_memory_pollution(backend: BackendBundle, case: ReplayCase) -> dict[str, Any]:
    session = await _load_session(backend, case)
    polluted_session = session.model_copy(deep=True)
    polluted_session.id = f"{session.id}-polluted-memory"
    polluted_session.events = [
        Event(
            invocation_id="polluted-memory",
            author="agent",
            content=Content(parts=[Part.from_text(text="recovery memory polluted by stale backend write")]),
        )
    ]
    await backend.memory_service.store_session(polluted_session)
    return await _snapshot_backend_case(backend, case)


async def _run_summary_step(backend: BackendBundle, session: Any, step: SummaryStep) -> None:
    backend.summary_model.summary_text = step.summary_text
    backend.session_service.set_summarizer_manager(
        _make_summarizer_manager(backend.summary_model, keep_recent_count=step.keep_recent_count),
        force=True,
    )
    assert backend.session_service.summarizer_manager is not None
    await backend.session_service.summarizer_manager.create_session_summary(session, force=True)

    cached_summary = await backend.session_service.summarizer_manager.get_session_summary(session)
    assert cached_summary is not None
    assert cached_summary.summary_text == step.summary_text
    assert cached_summary.original_event_count == step.expected_original_event_count
    assert cached_summary.compressed_event_count == step.expected_compressed_event_count


def _move_summary_event_to_front(session: Any) -> None:
    summary_index = next((idx for idx, event in enumerate(session.events) if event.is_summary_event()), None)
    if summary_index is None or summary_index == 0:
        return

    summary_event = session.events[summary_index]
    session.events = [summary_event] + [
        event
        for idx, event in enumerate(session.events)
        if idx != summary_index
    ]


async def _session_snapshot(
    session: Any,
    session_service: BaseSessionService,
    memory_service: BaseMemoryService,
    memory_queries: tuple[MemoryQuerySpec, ...],
) -> dict[str, Any]:
    active_events = _events_with_summary_first(session.events)
    return {
        "session": {
            "app_name": session.app_name,
            "user_id": session.user_id,
            "id": session.id,
            "conversation_count": session.conversation_count,
        },
        "state": _normalize_json(session.state),
        "events": [_normalize_event(event) for event in active_events],
        "memory": await _memory_snapshot(session.save_key, memory_service, memory_queries),
        "summary": await _summary_snapshot(session, session_service, active_events),
    }


def _normalize_event(event: Event) -> dict[str, Any]:
    data = event.model_dump(exclude_none=True, mode="json")
    for generated_field in ("id", "timestamp"):
        data.pop(generated_field, None)
    if not data.get("long_running_tool_ids"):
        data.pop("long_running_tool_ids", None)
    return _normalize_json(data)


async def _summary_snapshot(
    session: Any,
    session_service: BaseSessionService,
    active_events: list[Event],
) -> dict[str, Any]:
    cached_summary = None
    if session_service.summarizer_manager is not None:
        summary = await session_service.summarizer_manager.get_session_summary(session)
        if summary is not None:
            cached_summary = summary.model_dump(exclude_none=True, mode="json")
            cached_summary.pop("summary_timestamp", None)

    active_summary_event = next((event for event in active_events if event.is_summary_event()), None)
    return _normalize_json({
        "cached_summary": cached_summary,
        "active_summary_event": _normalize_event(active_summary_event) if active_summary_event else None,
        "active_summary_is_first": bool(active_events and active_events[0].is_summary_event()),
        "active_event_texts": [
            event.get_text()
            for event in active_events
            if not event.is_summary_event()
        ],
        "historical_events": [_normalize_event(event) for event in session.historical_events],
    })


def _events_with_summary_first(events: list[Event]) -> list[Event]:
    summary_index = next((idx for idx, event in enumerate(events) if event.is_summary_event()), None)
    if summary_index is None or summary_index == 0:
        return list(events)

    summary_event = events[summary_index]
    return [summary_event] + [
        event
        for idx, event in enumerate(events)
        if idx != summary_index
    ]


async def _memory_snapshot(
    save_key: str,
    memory_service: BaseMemoryService,
    memory_queries: tuple[MemoryQuerySpec, ...],
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for query_spec in memory_queries:
        response = await memory_service.search_memory(
            key=save_key,
            query=query_spec.query,
            limit=query_spec.limit,
        )
        snapshot[query_spec.query] = _normalize_memories(response.memories)
    return snapshot


def _normalize_memories(memories: list[Any]) -> list[dict[str, Any]]:
    normalized = []
    for memory in memories:
        normalized.append({
            "author": memory.author,
            "content": _normalize_json(memory.content.model_dump(exclude_none=True, mode="json")),
        })
    return sorted(normalized, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))


def _normalize_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize_json(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalize_json(item) for item in value]
    if isinstance(value, set):
        return sorted(_normalize_json(item) for item in value)
    return value


def _diff_snapshots(left: dict[str, Any], right: dict[str, Any]) -> list[DiffEntry]:
    diffs: list[DiffEntry] = []
    for section in ("session", "events", "state", "memory", "summary"):
        _collect_diffs(section, left.get(section), right.get(section), section, diffs)
    return diffs


def _collect_diffs(section: str, left: Any, right: Any, path: str, diffs: list[DiffEntry]) -> None:
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            child_path = f"{path}.{key}"
            if key not in left:
                diffs.append(DiffEntry(section, child_path, "<missing>", right[key]))
            elif key not in right:
                diffs.append(DiffEntry(section, child_path, left[key], "<missing>"))
            else:
                _collect_diffs(section, left[key], right[key], child_path, diffs)
        return

    if isinstance(left, list) and isinstance(right, list):
        for idx in range(max(len(left), len(right))):
            child_path = f"{path}[{idx}]"
            if idx >= len(left):
                diffs.append(DiffEntry(section, child_path, "<missing>", right[idx]))
            elif idx >= len(right):
                diffs.append(DiffEntry(section, child_path, left[idx], "<missing>"))
            else:
                _collect_diffs(section, left[idx], right[idx], child_path, diffs)
        return

    if left != right:
        diffs.append(DiffEntry(section, path, left, right))


def _report_entry(
    case_name: str,
    backend_a: str,
    backend_b: str,
    session_id: str,
    diff: DiffEntry,
    allowed_diffs: tuple[AllowedDiffRule, ...] = (),
) -> dict[str, Any]:
    allowed_rule = _matching_allowed_diff(diff, allowed_diffs, backend_a, backend_b)
    return {
        "case": case_name,
        "session_id": session_id,
        "backend_a": backend_a,
        "backend_b": backend_b,
        "section": diff.section,
        "path": diff.path,
        "left": diff.left,
        "right": diff.right,
        "allowed": allowed_rule is not None,
        "reason": allowed_rule.reason if allowed_rule else "",
    }


def _matching_allowed_diff(
    diff: DiffEntry,
    allowed_diffs: tuple[AllowedDiffRule, ...],
    backend_a: str,
    backend_b: str,
) -> AllowedDiffRule | None:
    for rule in allowed_diffs:
        if rule.section != diff.section:
            continue
        if rule.backend_pair and rule.backend_pair not in ((backend_a, backend_b), (backend_b, backend_a)):
            continue
        if _path_matches(diff.path, rule.path_pattern):
            return rule
    return None


def _path_matches(path: str, pattern: str) -> bool:
    escaped_pattern = re.escape(pattern).replace("\\*", ".*")
    return re.fullmatch(escaped_pattern, path) is not None


def _unallowed_report_entries(report: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in report if not entry["allowed"]]


async def _run_replay_matrix(
    backends: dict[str, BackendBundle],
    tmp_path: Path,
    *,
    namespace: str,
) -> list[dict[str, Any]]:
    report: list[dict[str, Any]] = []

    for case in _replay_cases():
        replay_case = _namespaced_case(case, namespace)
        snapshots = {
            backend_name: await _run_case(service, replay_case)
            for backend_name, service in backends.items()
        }
        for backend_a, backend_b in combinations(snapshots, 2):
            diffs = _diff_snapshots(snapshots[backend_a], snapshots[backend_b])
            session_id = snapshots[backend_a]["session"]["id"]
            report.extend(
                _report_entry(case.name, backend_a, backend_b, session_id, diff, case.allowed_diffs)
                for diff in diffs
            )

    _write_report(report, tmp_path)
    return report


def _namespaced_case(case: ReplayCase, namespace: str) -> ReplayCase:
    return ReplayCase(
        name=case.name,
        app_name=f"{case.app_name}_{namespace}",
        user_id=f"{case.user_id}_{namespace}",
        session_id=f"{case.session_id}_{namespace}",
        initial_state=case.initial_state,
        events=case.events,
        memory_queries=case.memory_queries,
        summary_steps=case.summary_steps,
        allowed_diffs=case.allowed_diffs,
    )


def _write_report(report: list[dict[str, Any]], tmp_path: Path) -> Path:
    report_path = Path(os.environ.get(REPORT_PATH_ENV, DEFAULT_REPORT_PATH))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report_path


def _replay_cases() -> tuple[ReplayCase, ...]:
    return (
        ReplayCase(
            name="single_turn_text",
            app_name="replay_app",
            user_id="user-1",
            session_id="single-turn",
            initial_state={"topic": "travel"},
            events=(
                EventSpec(author="user", kind="text", invocation_id="inv-1", text="Plan a one day trip."),
                EventSpec(author="agent", kind="text", invocation_id="inv-1", text="Start with the museum."),
            ),
            memory_queries=(MemoryQuerySpec(query="museum"),),
        ),
        ReplayCase(
            name="multi_turn_state_updates",
            app_name="replay_app",
            user_id="user-1",
            session_id="state-updates",
            initial_state={ 
                "stage": "new",
                f"{State.APP_PREFIX}region": "ap-shanghai",
                f"{State.USER_PREFIX}tier": "gold",
            },
            events=(
                EventSpec(
                    author="user",
                    kind="text",
                    invocation_id="inv-2",
                    text="Remember that I prefer quiet hotels.",
                    state_delta={f"{State.USER_PREFIX}hotel_preference": "quiet"},
                ),
                EventSpec(
                    author="agent",
                    kind="text",
                    invocation_id="inv-2",
                    text="I will keep hotel noise level in mind.",
                    state_delta={"stage": "preference_saved"},
                ),
                EventSpec(
                    author="user",
                    kind="text",
                    invocation_id="inv-3",
                    text="Actually make the trip business focused.",
                    state_delta={"stage": "business", f"{State.APP_PREFIX}region": "ap-guangzhou"},
                ),
            ),
            memory_queries=(MemoryQuerySpec(query="business"),),
        ),
        ReplayCase(
            name="tool_call_roundtrip",
            app_name="replay_app",
            user_id="user-2",
            session_id="tool-call",
            initial_state={},
            events=(
                EventSpec(author="user", kind="text", invocation_id="inv-4", text="What is the weather in Taipei?"),
                EventSpec(
                    author="agent",
                    kind="function_call",
                    invocation_id="inv-4",
                    function_id="call-weather-1",
                    function_name="lookup_weather",
                    function_args={"city": "Taipei", "unit": "celsius"},
                ),
                EventSpec(
                    author="tool",
                    kind="function_response",
                    invocation_id="inv-4",
                    function_id="call-weather-1",
                    function_name="lookup_weather",
                    function_response={"forecast": "sunny", "temperature": 26},
                ),
                EventSpec(
                    author="agent",
                    kind="text",
                    invocation_id="inv-4",
                    text="Taipei is sunny and 26 C.",
                    state_delta={"last_tool": "lookup_weather"},
                ),
            ),
            memory_queries=(MemoryQuerySpec(query="Taipei"),),
        ),
        ReplayCase(
            name="scoped_state_overwrite",
            app_name="replay_app",
            user_id="user-2",
            session_id="scoped-state-overwrite",
            initial_state={
                "phase": "draft",
                f"{State.APP_PREFIX}locale": "zh-CN",
                f"{State.USER_PREFIX}segment": "trial",
            },
            events=(
                EventSpec(
                    author="user",
                    kind="text",
                    invocation_id="inv-18",
                    text="Set my dashboard language to English.",
                    state_delta={
                        "phase": "language_requested",
                        f"{State.APP_PREFIX}locale": "en-US",
                        f"{State.USER_PREFIX}segment": "paid",
                    },
                ),
                EventSpec(
                    author="agent",
                    kind="text",
                    invocation_id="inv-18",
                    text="Dashboard language updated to English.",
                    state_delta={"phase": "language_applied"},
                ),
                EventSpec(
                    author="user",
                    kind="text",
                    invocation_id="inv-19",
                    text="Switch region to Singapore and mark me enterprise.",
                    state_delta={
                        "phase": "enterprise",
                        f"{State.APP_PREFIX}region": "ap-singapore",
                        f"{State.USER_PREFIX}segment": "enterprise",
                    },
                ),
                EventSpec(
                    author="agent",
                    kind="text",
                    invocation_id="inv-19",
                    text="Enterprise settings are active for Singapore.",
                    state_delta={
                        "phase": "complete",
                        f"{State.APP_PREFIX}locale": "en-SG",
                    },
                ),
            ),
            memory_queries=(MemoryQuerySpec(query="enterprise"),),
        ),
        ReplayCase(
            name="memory_multi_author_search",
            app_name="replay_app",
            user_id="user-5",
            session_id="memory-multi-author",
            initial_state={},
            events=(
                EventSpec(author="user", kind="text", invocation_id="inv-20", text="Remember I like green tea."),
                EventSpec(author="agent", kind="text", invocation_id="inv-20", text="Green tea preference saved."),
                EventSpec(author="user", kind="text", invocation_id="inv-21", text="My teammate Alice prefers coffee."),
                EventSpec(author="agent", kind="text", invocation_id="inv-21", text="Alice coffee preference saved."),
                EventSpec(
                    author="tool",
                    kind="text",
                    invocation_id="inv-22",
                    text="Preference audit mentions tea and coffee.",
                ),
            ),
            memory_queries=(
                MemoryQuerySpec(query="tea"),
                MemoryQuerySpec(query="coffee"),
                MemoryQuerySpec(query="preference"),
            ),
        ),
        ReplayCase(
            name="recovery_duplicate_and_partial_write",
            app_name="replay_app",
            user_id="user-6",
            session_id="recovery-write-anomaly",
            initial_state={"stage": "clean"},
            events=(
                EventSpec(
                    author="user",
                    kind="text",
                    invocation_id="inv-23",
                    text="Start recovery validation.",
                    state_delta={"stage": "started"},
                ),
                EventSpec(
                    author="agent",
                    kind="text",
                    invocation_id="inv-23",
                    text="Recovery baseline event recorded.",
                    state_delta={"stage": "baseline"},
                ),
                EventSpec(
                    author="user",
                    kind="text",
                    invocation_id="inv-24",
                    text="Store durable recovery memory.",
                ),
            ),
            memory_queries=(MemoryQuerySpec(query="recovery"),),
        ),
        ReplayCase(
            name="summary_generation",
            app_name="replay_app",
            user_id="user-3",
            session_id="summary-generation",
            initial_state={},
            events=(
                EventSpec(author="user", kind="text", invocation_id="inv-5", text="I need a conference plan."),
                EventSpec(author="agent", kind="text", invocation_id="inv-5", text="Start with venue research."),
                EventSpec(author="user", kind="text", invocation_id="inv-6", text="Keep the budget modest."),
                EventSpec(author="agent", kind="text", invocation_id="inv-6", text="I will prioritize modest costs."),
                EventSpec(author="user", kind="text", invocation_id="inv-7", text="Add a team dinner."),
                EventSpec(author="agent", kind="text", invocation_id="inv-7", text="Dinner is added."),
            ),
            summary_steps=(
                SummaryStep(
                    after_event_index=5,
                    summary_text="Conference plan with modest budget.",
                    keep_recent_count=2,
                    expected_original_event_count=6,
                    expected_compressed_event_count=3,
                ),
            ),
        ),
        ReplayCase(
            name="summary_update_overwrite",
            app_name="replay_app",
            user_id="user-3",
            session_id="summary-update",
            initial_state={},
            events=(
                EventSpec(author="user", kind="text", invocation_id="inv-8", text="Draft a launch checklist."),
                EventSpec(author="agent", kind="text", invocation_id="inv-8", text="Checklist starts with owners."),
                EventSpec(author="user", kind="text", invocation_id="inv-9", text="Include legal review."),
                EventSpec(author="agent", kind="text", invocation_id="inv-9", text="Legal review is included."),
                EventSpec(author="user", kind="text", invocation_id="inv-10", text="Add rollout metrics."),
                EventSpec(author="agent", kind="text", invocation_id="inv-10", text="Metrics are added."),
                EventSpec(author="user", kind="text", invocation_id="inv-11", text="Now add rollback steps."),
                EventSpec(author="agent", kind="text", invocation_id="inv-11", text="Rollback steps are added."),
                EventSpec(author="user", kind="text", invocation_id="inv-12", text="Add executive summary."),
                EventSpec(author="agent", kind="text", invocation_id="inv-12", text="Executive summary is added."),
            ),
            summary_steps=(
                SummaryStep(
                    after_event_index=5,
                    summary_text="Launch checklist includes owners, legal review, and metrics.",
                    keep_recent_count=2,
                    expected_original_event_count=6,
                    expected_compressed_event_count=3,
                ),
                SummaryStep(
                    after_event_index=9,
                    summary_text="Launch checklist includes rollback steps and executive summary.",
                    keep_recent_count=2,
                    expected_original_event_count=7,
                    expected_compressed_event_count=3,
                ),
            ),
        ),
        ReplayCase(
            name="summary_with_truncation",
            app_name="replay_app",
            user_id="user-4",
            session_id="summary-truncation",
            initial_state={},
            events=(
                EventSpec(author="user", kind="text", invocation_id="inv-13", text="Collect onboarding facts."),
                EventSpec(author="agent", kind="text", invocation_id="inv-13", text="Fact one captured."),
                EventSpec(author="user", kind="text", invocation_id="inv-14", text="Add laptop setup."),
                EventSpec(author="agent", kind="text", invocation_id="inv-14", text="Laptop setup captured."),
                EventSpec(author="user", kind="text", invocation_id="inv-15", text="Add buddy assignment."),
                EventSpec(author="agent", kind="text", invocation_id="inv-15", text="Buddy assignment captured."),
            ),
            summary_steps=(
                SummaryStep(
                    after_event_index=5,
                    summary_text="Onboarding facts include setup and assignments.",
                    keep_recent_count=2,
                    expected_original_event_count=6,
                    expected_compressed_event_count=3,
                ),
            ),
        ),
        ReplayCase(
            name="summary_with_tool_events",
            app_name="replay_app",
            user_id="user-4",
            session_id="summary-tool-events",
            initial_state={},
            events=(
                EventSpec(author="user", kind="text", invocation_id="inv-16", text="Check weather before travel."),
                EventSpec(
                    author="agent",
                    kind="function_call",
                    invocation_id="inv-16",
                    function_id="call-weather-2",
                    function_name="lookup_weather",
                    function_args={"city": "Shenzhen"},
                ),
                EventSpec(
                    author="tool",
                    kind="function_response",
                    invocation_id="inv-16",
                    function_id="call-weather-2",
                    function_name="lookup_weather",
                    function_response={"forecast": "rain"},
                ),
                EventSpec(author="agent", kind="text", invocation_id="inv-16", text="Shenzhen is rainy."),
                EventSpec(author="user", kind="text", invocation_id="inv-17", text="Pack umbrella then."),
                EventSpec(author="agent", kind="text", invocation_id="inv-17", text="Umbrella is on the packing list."),
            ),
            summary_steps=(
                SummaryStep(
                    after_event_index=5,
                    summary_text="Weather tool found rain; umbrella was added.",
                    keep_recent_count=2,
                    expected_original_event_count=6,
                    expected_compressed_event_count=3,
                ),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_in_memory_and_sqlite_session_replay_events_state_and_memory_match(tmp_path):
    backends = await _make_backends()

    try:
        report = await _run_replay_matrix(backends, tmp_path, namespace=f"default_{uuid.uuid4().hex[:8]}")
    finally:
        await _close_backends(backends)

    assert _unallowed_report_entries(report) == []


@pytest.mark.asyncio
async def test_configured_integration_session_replay_backends_match(tmp_path):
    if not os.environ.get(SQL_URL_ENV) and not os.environ.get(REDIS_URL_ENV):
        pytest.skip(f"Set {SQL_URL_ENV} or {REDIS_URL_ENV} to run integration replay backends.")

    backends = await _make_backends(include_optional=True)

    try:
        report = await _run_replay_matrix(backends, tmp_path, namespace=f"integration_{uuid.uuid4().hex[:8]}")
    finally:
        await _close_backends(backends)

    assert _unallowed_report_entries(report) == []


@pytest.mark.asyncio
async def test_sqlite_replay_detects_real_duplicate_partial_state_and_memory_recovery_failures():
    backends = await _make_backends()
    namespace = f"recovery_{uuid.uuid4().hex[:8]}"
    case = _namespaced_case(
        next(case for case in _replay_cases() if case.name == "recovery_duplicate_and_partial_write"),
        namespace,
    )

    try:
        baseline = {
            backend_name: await _run_case(service, case)
            for backend_name, service in backends.items()
        }
        assert _diff_snapshots(baseline["in_memory"], baseline["sqlite_sql"]) == []

        duplicate_snapshot = await _inject_duplicate_event(backends["sqlite_sql"], case)
        duplicate_diffs = _diff_snapshots(baseline["in_memory"], duplicate_snapshot)
        assert any(diff.section == "events" and diff.path == "events[3]" for diff in duplicate_diffs)

        partial_backends = await _make_backends()
        try:
            partial_case = _namespaced_case(case, f"{namespace}_partial")
            partial_baseline = {
                backend_name: await _run_case(service, partial_case)
                for backend_name, service in partial_backends.items()
            }
            partial_snapshot = await _inject_partial_event_loss(partial_backends["sqlite_sql"], partial_case)
            partial_diffs = _diff_snapshots(partial_baseline["in_memory"], partial_snapshot)
            assert any(diff.section == "events" and diff.path == "events[2]" for diff in partial_diffs)
        finally:
            await _close_backends(partial_backends)

        state_backends = await _make_backends()
        try:
            state_case = _namespaced_case(case, f"{namespace}_state")
            state_baseline = {
                backend_name: await _run_case(service, state_case)
                for backend_name, service in state_backends.items()
            }
            state_snapshot = await _inject_state_pollution(state_backends["sqlite_sql"], state_case)
            state_diffs = _diff_snapshots(state_baseline["in_memory"], state_snapshot)
            assert DiffEntry("state", "state.stage", "baseline", "polluted") in state_diffs
        finally:
            await _close_backends(state_backends)

        memory_backends = await _make_backends()
        try:
            memory_case = _namespaced_case(case, f"{namespace}_memory")
            memory_baseline = {
                backend_name: await _run_case(service, memory_case)
                for backend_name, service in memory_backends.items()
            }
            memory_snapshot = await _inject_memory_pollution(memory_backends["sqlite_sql"], memory_case)
            memory_diffs = _diff_snapshots(memory_baseline["in_memory"], memory_snapshot)
            assert DiffEntry(
                "memory",
                "memory.recovery[1].content.parts[0].text",
                "Start recovery validation.",
                "recovery memory polluted by stale backend write",
            ) in memory_diffs
        finally:
            await _close_backends(memory_backends)

        report = [
            _report_entry("recovery_duplicate_and_partial_write", "in_memory", "sqlite_sql", case.session_id, diff)
            for diff in duplicate_diffs
        ]
        assert report
        assert all(
            set(entry) == {
                "case",
                "session_id",
                "backend_a",
                "backend_b",
                "section",
                "path",
                "left",
                "right",
                "allowed",
                "reason",
            }
            for entry in report
        )
    finally:
        await _close_backends(backends)


def test_session_replay_diff_reports_event_state_memory_and_summary_paths():
    base_snapshot = {
        "session": {"id": "s1", "conversation_count": 1},
        "events": [
            {"author": "user", "content": {"parts": [{"text": "hello"}]}},
            {"author": "agent", "content": {"parts": [{"text": "world"}]}},
        ],
        "state": {"stage": "draft", "nested": {"count": 1}},
        "memory": {"hello": [{"author": "user", "content": {"parts": [{"text": "hello"}]}}]},
        "summary": {
            "cached_summary": {
                "session_id": "s1",
                "summary_text": "old summary",
                "original_event_count": 4,
                "compressed_event_count": 2,
                "metadata": {},
            },
            "active_summary_event": {
                "author": "system",
                "content": {"parts": [{"text": "Previous conversation summary: old summary"}]},
            },
            "active_summary_is_first": True,
            "active_event_texts": ["recent"],
            "historical_events": [
                {"author": "user", "content": {"parts": [{"text": "old question"}]}},
            ],
        },
    }
    changed_snapshot = copy.deepcopy(base_snapshot)
    changed_snapshot["session"]["conversation_count"] = 2
    changed_snapshot["events"][1]["content"]["parts"][0]["text"] = "WORLD"
    changed_snapshot["state"]["nested"]["count"] = 2
    changed_snapshot["memory"]["hello"][0]["content"]["parts"][0]["text"] = "HELLO"
    changed_snapshot["summary"]["cached_summary"]["summary_text"] = "new summary"

    diffs = _diff_snapshots(base_snapshot, changed_snapshot)

    assert DiffEntry("session", "session.conversation_count", 1, 2) in diffs
    assert DiffEntry("events", "events[1].content.parts[0].text", "world", "WORLD") in diffs
    assert DiffEntry("state", "state.nested.count", 1, 2) in diffs
    assert DiffEntry("memory", "memory.hello[0].content.parts[0].text", "hello", "HELLO") in diffs
    assert DiffEntry("summary", "summary.cached_summary.summary_text", "old summary", "new summary") in diffs


def test_session_replay_allowed_diff_rules_mark_only_explicit_matches():
    allowed_rules = (
        AllowedDiffRule(
            section="events",
            path_pattern="events[*].content.parts[0].text",
            reason="known backend text casing drift",
            backend_pair=("in_memory", "sqlite_sql"),
        ),
    )
    allowed_entry = _report_entry(
        "allowed-case",
        "in_memory",
        "sqlite_sql",
        "session-1",
        DiffEntry("events", "events[3].content.parts[0].text", "hello", "HELLO"),
        allowed_rules,
    )
    unallowed_entry = _report_entry(
        "allowed-case",
        "in_memory",
        "sqlite_sql",
        "session-1",
        DiffEntry("state", "state.stage", "draft", "done"),
        allowed_rules,
    )

    assert allowed_entry["allowed"] is True
    assert allowed_entry["reason"] == "known backend text casing drift"
    assert unallowed_entry["allowed"] is False
    assert unallowed_entry["reason"] == ""
    assert _unallowed_report_entries([allowed_entry, unallowed_entry]) == [unallowed_entry]


def test_session_replay_diff_detects_event_state_and_memory_injections():
    base_snapshot = {
        "session": {"id": "s1", "conversation_count": 2},
        "events": [
            {"author": "user", "content": {"parts": [{"text": "remember quiet hotels"}]}},
            {"author": "agent", "content": {"parts": [{"text": "quiet hotel preference saved"}]}},
        ],
        "state": {"stage": "preference_saved"},
        "memory": {
            "quiet": [
                {"author": "user", "content": {"parts": [{"text": "remember quiet hotels"}]}},
            ],
        },
        "summary": {},
    }

    duplicate_write = copy.deepcopy(base_snapshot)
    duplicate_write["events"].append(copy.deepcopy(base_snapshot["events"][1]))
    assert DiffEntry(
        "events",
        "events[2]",
        "<missing>",
        base_snapshot["events"][1],
    ) in _diff_snapshots(base_snapshot, duplicate_write)

    partial_failure = copy.deepcopy(base_snapshot)
    partial_failure["events"].pop()
    assert DiffEntry(
        "events",
        "events[1]",
        base_snapshot["events"][1],
        "<missing>",
    ) in _diff_snapshots(base_snapshot, partial_failure)

    state_corruption = copy.deepcopy(base_snapshot)
    state_corruption["state"]["stage"] = "stale"
    assert DiffEntry(
        "state",
        "state.stage",
        "preference_saved",
        "stale",
    ) in _diff_snapshots(base_snapshot, state_corruption)

    memory_pollution = copy.deepcopy(base_snapshot)
    memory_pollution["memory"]["quiet"].append(
        {"author": "agent", "content": {"parts": [{"text": "unrelated noisy hotel note"}]}}
    )
    assert DiffEntry(
        "memory",
        "memory.quiet[1]",
        "<missing>",
        memory_pollution["memory"]["quiet"][1],
    ) in _diff_snapshots(base_snapshot, memory_pollution)


def test_session_replay_diff_detects_summary_injections():
    base_snapshot = {
        "events": [],
        "state": {},
        "memory": {},
        "summary": {
            "cached_summary": {
                "session_id": "summary-session",
                "summary_text": "fresh summary",
                "original_event_count": 7,
                "compressed_event_count": 3,
                "metadata": {},
            },
            "active_summary_event": {
                "author": "system",
                "content": {"parts": [{"text": "Previous conversation summary: fresh summary"}]},
            },
            "active_summary_is_first": True,
            "active_event_texts": ["recent question", "recent answer"],
            "historical_events": [
                {"author": "system", "content": {"parts": [{"text": "Previous conversation summary: old summary"}]}},
                {"author": "user", "content": {"parts": [{"text": "old question"}]}},
            ],
        },
    }

    summary_loss = copy.deepcopy(base_snapshot)
    summary_loss["summary"]["active_summary_event"] = None
    assert DiffEntry(
        "summary",
        "summary.active_summary_event",
        base_snapshot["summary"]["active_summary_event"],
        None,
    ) in _diff_snapshots(base_snapshot, summary_loss)

    summary_overwrite = copy.deepcopy(base_snapshot)
    summary_overwrite["summary"]["cached_summary"]["summary_text"] = "old summary"
    assert DiffEntry(
        "summary",
        "summary.cached_summary.summary_text",
        "fresh summary",
        "old summary",
    ) in _diff_snapshots(base_snapshot, summary_overwrite)

    summary_misattribution = copy.deepcopy(base_snapshot)
    summary_misattribution["summary"]["cached_summary"]["session_id"] = "wrong-session"
    assert DiffEntry(
        "summary",
        "summary.cached_summary.session_id",
        "summary-session",
        "wrong-session",
    ) in _diff_snapshots(base_snapshot, summary_misattribution)
