# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Reusable Session/Memory/Summary replay consistency harness."""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import math
import os
import re
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any
from typing import AsyncIterator
from typing import Callable
from typing import Optional

from trpc_agent_sdk.abc import MemoryServiceConfig
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.memory import InMemoryMemoryService
from trpc_agent_sdk.memory import RedisMemoryService
from trpc_agent_sdk.memory import SqlMemoryService
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.sessions import RedisSessionService
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.sessions import SessionServiceConfig
from trpc_agent_sdk.sessions import SessionSummarizer
from trpc_agent_sdk.sessions import SqlSessionService
from trpc_agent_sdk.sessions import SummarizerSessionManager
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import EventActions
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import Part

from .replay_cases import REPLAY_CASES
from .replay_cases import ReplayCase
from .replay_cases import ReplayOperation

APP_NAME = "replay-consistency"
# A stable future timestamp avoids accidentally exercising SQL's stale-writer
# recovery branch while still making ordinary event timestamps comparable.
BASE_TIMESTAMP = 4102444800.0


class DeterministicSummaryModel:
    """A no-network model that returns the summary selected by the trace."""

    name = "deterministic-replay-summary"

    def __init__(self) -> None:
        self.next_summary = ""
        self.fail_next = False

    async def generate_async(self, _request: Any, stream: bool = False, ctx: Any = None):
        del stream, ctx
        if self.fail_next:
            self.fail_next = False
            return
        yield LlmResponse(content=Content(parts=[Part.from_text(text=self.next_summary)]))


class _InProcessRedisStorage:
    """Minimal RedisStorage-compatible backend for dependency-free replay tests."""

    def __init__(self) -> None:
        self._strings: dict[str, Any] = {}
        self._hashes: dict[str, dict[str, Any]] = {}
        self._lists: dict[str, list[Any]] = {}

    @asynccontextmanager
    async def create_db_session(self) -> AsyncIterator["_InProcessRedisStorage"]:
        yield self

    async def execute_command(self, _session: Any, command: Any) -> Any:
        method = command.method.lower()
        args = command.args
        if method == "set":
            self._strings[args[0]] = args[1]
            return True
        if method == "get":
            return self._strings.get(args[0])
        if method == "keys":
            keys = set(self._strings) | set(self._hashes) | set(self._lists)
            return sorted(key for key in keys if fnmatchcase(key, args[0]))
        if method == "hset":
            values = self._hashes.setdefault(args[0], {})
            for index in range(1, len(args), 2):
                values[args[index]] = args[index + 1]
            return True
        if method == "hgetall":
            return copy.deepcopy(self._hashes.get(args[0], {}))
        if method == "rpush":
            self._lists.setdefault(args[0], []).extend(args[1:])
            return len(self._lists[args[0]])
        raise ValueError(f"Unsupported in-process Redis command: {method}")

    async def query(self, _session: Any, pattern: str, conditions: Any) -> list[tuple[str, Any]]:
        keys = sorted(key for key in self._lists if fnmatchcase(key, pattern))
        if conditions.limit > 0:
            keys = keys[:conditions.limit]
        return [(key, list(self._lists[key])) for key in keys]

    async def delete(self, _session: Any, key: str) -> None:
        self._strings.pop(key, None)
        self._hashes.pop(key, None)
        self._lists.pop(key, None)

    async def expire(self, _session: Any, _expire: Any) -> None:
        return None

    async def close(self) -> None:
        return None


@dataclass
class BackendBundle:
    """Session, Memory, and Summary services operated as one backend."""

    name: str
    session_service: Any
    memory_service: Any
    summary_manager: SummarizerSessionManager
    summary_model: DeterministicSummaryModel
    summary_versions: dict[str, int]

    async def close(self) -> None:
        try:
            await self.memory_service.close()
        finally:
            await self.session_service.close()


@dataclass(frozen=True)
class AllowedDiff:
    """A narrowly-scoped, documented backend difference."""

    path: str
    reason: str


@dataclass
class DiffEntry:
    """One field-level difference with replay location metadata."""

    case_id: str
    left_backend: str
    right_backend: str
    session_id: str
    path: str
    left_value: Any
    right_value: Any
    allowed: bool
    reason: Optional[str] = None
    event_index: Optional[int] = None
    summary_id: Optional[str] = None


ALLOWED_DIFFS: tuple[AllowedDiff, ...] = (
    AllowedDiff("$.session.last_update_time", "Persistent backends use their storage commit clock."),
    AllowedDiff("$.summary.summary_timestamp", "Summary metadata is stamped independently by each manager."),
)


def _session_config() -> SessionServiceConfig:
    config = SessionServiceConfig(store_historical_events=True)
    config.clean_ttl_config()
    return config


def _memory_config() -> MemoryServiceConfig:
    config = MemoryServiceConfig(enabled=True)
    config.clean_ttl_config()
    return config


def _summary_components() -> tuple[DeterministicSummaryModel, SummarizerSessionManager]:
    model = DeterministicSummaryModel()
    summarizer = SessionSummarizer(
        model=model,  # type: ignore[arg-type]
        check_summarizer_functions=[lambda _session: True],
        keep_recent_count=2,
        start_by_user_turn=True,
    )
    manager = SummarizerSessionManager(model=model, summarizer=summarizer,
                                       auto_summarize=True)  # type: ignore[arg-type]
    return model, manager


async def create_in_memory_backend() -> BackendBundle:
    """Create the dependency-free lightweight backend."""

    model, manager = _summary_components()
    session_service = InMemorySessionService(summarizer_manager=manager, session_config=_session_config())
    memory_service = InMemoryMemoryService(memory_service_config=_memory_config(), enabled=True)
    return BackendBundle("in_memory", session_service, memory_service, manager, model, {})


async def create_sqlite_backend() -> BackendBundle:
    """Create an isolated SQLite persistence backend."""

    return await create_sql_backend("sqlite:///:memory:", name="sqlite")


async def create_sql_backend(db_url: str, name: str = "sql") -> BackendBundle:
    """Create a SQL persistence backend from a sync SQLAlchemy URL."""

    model, manager = _summary_components()
    session_service = SqlSessionService(
        db_url=db_url,
        summarizer_manager=manager,
        session_config=_session_config(),
        is_async=False,
    )
    memory_service = SqlMemoryService(
        db_url=db_url,
        enabled=True,
        memory_service_config=_memory_config(),
        is_async=False,
    )
    await session_service._sql_storage.create_sql_engine()  # pylint: disable=protected-access
    await memory_service._sql_storage.create_sql_engine()  # pylint: disable=protected-access
    return BackendBundle(name, session_service, memory_service, manager, model, {})


async def create_redis_backend(redis_url: str) -> BackendBundle:
    """Create an optional Redis integration backend."""

    model, manager = _summary_components()
    session_service = RedisSessionService(
        db_url=redis_url,
        summarizer_manager=manager,
        session_config=_session_config(),
        is_async=False,
    )
    memory_service = RedisMemoryService(
        db_url=redis_url,
        enabled=True,
        memory_service_config=_memory_config(),
        is_async=False,
    )
    return BackendBundle("redis", session_service, memory_service, manager, model, {})


async def create_mock_redis_backend() -> BackendBundle:
    """Create Redis services backed by an in-process RedisStorage substitute."""

    model, manager = _summary_components()
    session_service = RedisSessionService(
        db_url="redis://replay-mock",
        summarizer_manager=manager,
        session_config=_session_config(),
        is_async=False,
    )
    memory_service = RedisMemoryService(
        db_url="redis://replay-mock",
        enabled=True,
        memory_service_config=_memory_config(),
        is_async=False,
    )
    storage = _InProcessRedisStorage()
    session_service._redis_storage = storage  # pylint: disable=protected-access
    memory_service._redis_storage = storage  # pylint: disable=protected-access
    return BackendBundle("redis_mock", session_service, memory_service, manager, model, {})


def _identity(case: ReplayCase) -> tuple[str, str]:
    return f"user-{case.case_id}", f"session-{case.case_id}"


def _app_name(case: ReplayCase) -> str:
    return f"{APP_NAME}-{case.case_id}"


def _event_from_operation(case: ReplayCase, operation: ReplayOperation, sequence: int) -> Event:
    payload = operation.payload
    event_type = payload["event_type"]
    event_id = payload.get("event_id", f"{case.case_id}-event-{sequence:02d}")
    content = None
    if event_type == "text":
        content = Content(parts=[Part.from_text(text=payload["text"])])
    elif event_type == "function_call":
        function_call = FunctionCall(id=payload["call_id"], name=payload["name"], args=payload["args"])
        content = Content(parts=[Part(function_call=function_call)])
    elif event_type == "function_response":
        function_response = FunctionResponse(
            id=payload["call_id"],
            name=payload["name"],
            response=payload["response"],
        )
        content = Content(parts=[Part(function_response=function_response)])
    elif event_type != "state":
        raise ValueError(f"Unsupported replay event type: {event_type}")

    return Event(
        id=event_id,
        invocation_id=f"{case.case_id}-invocation-{sequence:02d}",
        author=payload["author"],
        content=content,
        actions=EventActions(state_delta=copy.deepcopy(payload.get("state_delta", {}))),
        timestamp=BASE_TIMESTAMP + sequence,
        partial=payload.get("partial", False),
        turn_complete=not payload.get("partial", False),
    )


async def execute_case(bundle: BackendBundle, case: ReplayCase) -> dict[str, Any]:
    """Replay one case and return its normalized backend snapshot."""

    user_id, session_id = _identity(case)
    app_name = _app_name(case)
    await bundle.session_service.delete_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    bundle.summary_versions[session_id] = 0
    session = await bundle.session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        state={"case_id": case.case_id},
    )

    sequence = 0
    checks: dict[str, bool] = {}
    summary_history: list[dict[str, Any]] = []
    previous_summary_timestamp: Optional[float] = None
    for operation in case.operations:
        if operation.kind == "event":
            sequence += 1
            event = _event_from_operation(case, operation, sequence)
            before_state = copy.deepcopy(session.state)
            attempted_state_delta = copy.deepcopy(event.actions.state_delta)
            await bundle.session_service.append_event(session, event)
            if event.partial:
                session = await _get_session(bundle, app_name, user_id, session_id)
                check_prefix = f"partial_event_{sequence}"
                checks[f"{check_prefix}_not_persisted"] = all(item.id != event.id for item in session.events)
                checks[f"{check_prefix}_state_clean"] = all(
                    session.state.get(key) == before_state.get(key) for key in attempted_state_delta)
        elif operation.kind == "store_memory":
            session = await _get_session(bundle, app_name, user_id, session_id)
            await bundle.memory_service.store_session(session)
        elif operation.kind == "summarize":
            session = await _get_session(bundle, app_name, user_id, session_id)
            bundle.summary_model.next_summary = operation.payload["summary_text"]
            await bundle.session_service.create_session_summary(session)
            public_summary = await bundle.session_service.get_session_summary(session)
            stored_summary = await bundle.summary_manager.get_session_summary(session)
            if stored_summary is None or public_summary is None:
                checks[f"summary_{len(summary_history) + 1}_saved"] = False
            else:
                version = bundle.summary_versions[session_id] + 1
                bundle.summary_versions[session_id] = version
                summary_history.append(
                    _summary_revision_snapshot(
                        stored_summary,
                        public_summary,
                        version,
                        previous_summary_timestamp,
                    ))
                previous_summary_timestamp = stored_summary.summary_timestamp
                checks[f"summary_{version}_saved"] = True
                checks[f"summary_{version}_public_read"] = (
                    _normalize_summary_text(public_summary) == _normalize_summary_text(stored_summary.summary_text))
            # update_session implementations may retain the supplied object.
            # Re-read before subsequent writes to preserve the service boundary.
            session = await _get_session(bundle, app_name, user_id, session_id)
        elif operation.kind == "summarize_failure":
            session = await _get_session(bundle, app_name, user_id, session_id)
            before_projection = _recovery_projection(session)
            before_summary = await bundle.session_service.get_session_summary(session)
            bundle.summary_model.fail_next = True
            await bundle.session_service.create_session_summary(session)
            session = await _get_session(bundle, app_name, user_id, session_id)
            after_summary = await bundle.session_service.get_session_summary(session)
            checks["failed_summary_preserves_session"] = _recovery_projection(session) == before_projection
            checks["failed_summary_preserves_summary"] = after_summary == before_summary
        else:
            raise ValueError(f"Unsupported replay operation: {operation.kind}")

    session = await _get_session(bundle, app_name, user_id, session_id)
    memory: dict[str, list[dict[str, Any]]] = {}
    for query in case.memory_queries:
        response = await bundle.memory_service.search_memory(session.save_key, query)
        memory[query] = sorted(
            (_memory_entry_snapshot(entry) for entry in response.memories),
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
        )

    public_summary = await bundle.session_service.get_session_summary(session)
    summary = await bundle.summary_manager.get_session_summary(session)
    return {
        "session": _session_snapshot(session),
        "memory": memory,
        "summary": _summary_snapshot(summary, public_summary, bundle.summary_versions[session_id]),
        "summary_history": summary_history,
        "checks": checks,
    }


async def _get_session(bundle: BackendBundle, app_name: str, user_id: str, session_id: str) -> Session:
    session = await bundle.session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    if session is None:
        raise AssertionError(f"{bundle.name} lost session {session_id}")
    return session


def _session_snapshot(session: Session) -> dict[str, Any]:
    return {
        "id": session.id,
        "app_name": session.app_name,
        "user_id": session.user_id,
        "save_key": session.save_key,
        "state": _json_value(session.state),
        "events": [_event_snapshot(event) for event in session.events],
        "historical_events": [_event_snapshot(event) for event in session.historical_events],
        "conversation_count": session.conversation_count,
        "last_update_time": session.last_update_time,
    }


def _event_snapshot(event: Event) -> dict[str, Any]:
    is_summary = event.is_summary_event()
    return {
        "id": "<generated-summary-id>" if is_summary else event.id,
        "invocation_id": event.invocation_id,
        "author": event.author,
        "content": _json_value(event.content.model_dump(exclude_none=True, mode="json") if event.content else None),
        "actions": _json_value(event.actions.model_dump(exclude_none=True, mode="json")),
        "timestamp": "<generated-summary-time>" if is_summary else event.timestamp,
        "partial": event.partial,
        "turn_complete": event.turn_complete,
        "visible": event.visible,
        "model_flags": event.model_flags,
        "version": event.version,
        "is_summary": is_summary,
    }


def _memory_entry_snapshot(entry: Any) -> dict[str, Any]:
    return {
        "author": entry.author,
        "content": _json_value(entry.content.model_dump(exclude_none=True, mode="json")),
        "timestamp": entry.timestamp,
    }


def _normalize_summary_text(summary_text: str) -> str:
    return " ".join(summary_text.split())


def _summary_snapshot(summary: Any, public_summary: Optional[str], version: int) -> Optional[dict[str, Any]]:
    if summary is None:
        return None
    return {
        "summary_id": f"{summary.session_id}:summary",
        "session_id": summary.session_id,
        "summary_text": _normalize_summary_text(public_summary or ""),
        "metadata_summary_text": _normalize_summary_text(summary.summary_text),
        "original_event_count": summary.original_event_count,
        "compressed_event_count": summary.compressed_event_count,
        "summary_timestamp": summary.summary_timestamp,
        "version": version,
    }


def _summary_revision_snapshot(
    summary: Any,
    public_summary: str,
    version: int,
    previous_timestamp: Optional[float],
) -> dict[str, Any]:
    timestamp = summary.summary_timestamp
    return {
        "summary_id": f"{summary.session_id}:summary",
        "session_id": summary.session_id,
        "summary_text": _normalize_summary_text(public_summary),
        "metadata_summary_text": _normalize_summary_text(summary.summary_text),
        "original_event_count": summary.original_event_count,
        "compressed_event_count": summary.compressed_event_count,
        "timestamp_valid": isinstance(timestamp, (int, float)) and math.isfinite(timestamp) and timestamp > 0,
        "updated_after_previous": previous_timestamp is None or timestamp > previous_timestamp,
        "version": version,
    }


def _recovery_projection(session: Session) -> dict[str, Any]:
    """Return business fields that must survive a failed summary write."""

    snapshot = _session_snapshot(session)
    snapshot.pop("last_update_time")
    return snapshot


def _json_value(value: Any) -> Any:
    """Convert model values to stable JSON primitives without stringifying structures."""

    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def compare_snapshots(
    case_id: str,
    session_id: str,
    left_backend: str,
    right_backend: str,
    left: Any,
    right: Any,
) -> list[DiffEntry]:
    """Recursively compare snapshots while retaining list order and field paths."""

    differences: list[DiffEntry] = []

    def walk(left_value: Any, right_value: Any, path: str) -> None:
        if isinstance(left_value, dict) and isinstance(right_value, dict):
            for key in sorted(set(left_value) | set(right_value)):
                child_path = f"{path}.{key}"
                if key not in left_value:
                    add(child_path, "<missing>", right_value[key])
                elif key not in right_value:
                    add(child_path, left_value[key], "<missing>")
                else:
                    walk(left_value[key], right_value[key], child_path)
            return
        if isinstance(left_value, list) and isinstance(right_value, list):
            for index in range(max(len(left_value), len(right_value))):
                child_path = f"{path}[{index}]"
                if index >= len(left_value):
                    add(child_path, "<missing>", right_value[index])
                elif index >= len(right_value):
                    add(child_path, left_value[index], "<missing>")
                else:
                    walk(left_value[index], right_value[index], child_path)
            return
        if left_value != right_value:
            add(path, left_value, right_value)

    def add(path: str, left_value: Any, right_value: Any) -> None:
        reason = next((item.reason for item in ALLOWED_DIFFS if item.path == path), None)
        event_match = re.search(r"\.(?:historical_)?events\[(\d+)\]", path)
        summary_id = None
        if path.startswith("$.summary"):
            left_summary = left if isinstance(left, dict) else {}
            right_summary = right if isinstance(right, dict) else {}
            summary_id = ((left_summary.get("summary") or {}).get("summary_id")
                          or (right_summary.get("summary") or {}).get("summary_id"))
        differences.append(
            DiffEntry(
                case_id=case_id,
                left_backend=left_backend,
                right_backend=right_backend,
                session_id=session_id,
                path=path,
                left_value=_json_value(left_value),
                right_value=_json_value(right_value),
                allowed=reason is not None,
                reason=reason,
                event_index=int(event_match.group(1)) if event_match else None,
                summary_id=summary_id,
            ))

    walk(left, right, "$")
    return differences


def _delete_first_event(snapshot: dict[str, Any]) -> None:
    del snapshot["session"]["events"][0]


def _swap_first_events(snapshot: dict[str, Any]) -> None:
    events = snapshot["session"]["events"]
    events[0], events[1] = events[1], events[0]


def _corrupt_tool_response(snapshot: dict[str, Any]) -> None:
    event = next(event for event in snapshot["session"]["events"]
                 if event["content"] and event["content"]["parts"][0].get("function_response"))
    event["content"]["parts"][0]["function_response"]["response"]["result"] = "corrupted"


def _corrupt_state(snapshot: dict[str, Any]) -> None:
    snapshot["session"]["state"]["phase"] = "corrupted"


def _corrupt_scoped_state(snapshot: dict[str, Any]) -> None:
    snapshot["session"]["state"]["user:language"] = "corrupted"


def _corrupt_memory(snapshot: dict[str, Any]) -> None:
    entry = next(iter(snapshot["memory"].values()))[0]
    entry["content"]["parts"][0]["text"] = "corrupted memory"


def _drop_summary(snapshot: dict[str, Any]) -> None:
    snapshot["summary"] = None


def _restore_old_summary(snapshot: dict[str, Any]) -> None:
    snapshot["summary"]["summary_text"] = "summary update version one"
    snapshot["summary"]["version"] = 1


def _move_summary(snapshot: dict[str, Any]) -> None:
    snapshot["summary"]["session_id"] = "session-wrong-owner"


def _duplicate_event(snapshot: dict[str, Any]) -> None:
    snapshot["session"]["events"].append(copy.deepcopy(snapshot["session"]["events"][-1]))


def _check_failures(
    case_id: str,
    session_id: str,
    backend_name: str,
    snapshot: dict[str, Any],
) -> list[DiffEntry]:
    """Turn failed replay invariants into ordinary field-level differences."""

    return [
        DiffEntry(
            case_id=case_id,
            left_backend="expected",
            right_backend=backend_name,
            session_id=session_id,
            path=f"$.checks.{check_name}",
            left_value=True,
            right_value=passed,
            allowed=False,
        ) for check_name, passed in snapshot["checks"].items() if not passed
    ]


INJECTED_FAULTS: tuple[tuple[str, str, Callable[[dict[str, Any]], None]], ...] = (
    ("missing_event", "single_turn", _delete_first_event),
    ("event_order", "multi_turn", _swap_first_events),
    ("tool_response", "tool_call", _corrupt_tool_response),
    ("state_overwrite", "state_overwrite", _corrupt_state),
    ("state_scope", "scoped_state", _corrupt_scoped_state),
    ("memory_content", "memory_roundtrip", _corrupt_memory),
    ("summary_missing", "summary_create", _drop_summary),
    ("summary_overwrite", "summary_update", _restore_old_summary),
    ("summary_session_owner", "summary_truncation", _move_summary),
    ("duplicate_retry", "partial_retry", _duplicate_event),
)


async def run_replay_matrix(backends: list[BackendBundle]) -> dict[str, Any]:
    """Run normal comparisons and the public ten-fault detection matrix."""

    if not backends:
        raise ValueError("At least one replay backend is required")
    started = time.perf_counter()
    snapshots: dict[str, dict[str, dict[str, Any]]] = {backend.name: {} for backend in backends}
    try:
        for case in REPLAY_CASES:
            for backend in backends:
                snapshots[backend.name][case.case_id] = await execute_case(backend, case)

        baseline = backends[0]
        normal_results = []
        for case in REPLAY_CASES:
            case_diffs: list[DiffEntry] = []
            _, session_id = _identity(case)
            for backend in backends:
                case_diffs.extend(
                    _check_failures(
                        case.case_id,
                        session_id,
                        backend.name,
                        snapshots[backend.name][case.case_id],
                    ))
            for backend in backends[1:]:
                case_diffs.extend(
                    compare_snapshots(
                        case.case_id,
                        session_id,
                        baseline.name,
                        backend.name,
                        snapshots[baseline.name][case.case_id],
                        snapshots[backend.name][case.case_id],
                    ))
            normal_results.append({
                "case_id": case.case_id,
                "passed": not any(not diff.allowed for diff in case_diffs),
                "differences": [asdict(diff) for diff in case_diffs],
            })

        injected_results = []
        for fault_id, case_id, inject in INJECTED_FAULTS:
            expected = snapshots[baseline.name][case_id]
            corrupted = copy.deepcopy(expected)
            inject(corrupted)
            session_id = expected["session"]["id"]
            differences = compare_snapshots(
                case_id,
                session_id,
                baseline.name,
                f"injected:{fault_id}",
                expected,
                corrupted,
            )
            detected = any(not diff.allowed for diff in differences)
            injected_results.append({
                "fault_id": fault_id,
                "case_id": case_id,
                "detected": detected,
                "differences": [asdict(diff) for diff in differences],
            })

        normal_failures = sum(not result["passed"] for result in normal_results)
        detected_faults = sum(result["detected"] for result in injected_results)
        summary_faults = [result for result in injected_results if result["fault_id"].startswith("summary_")]
        return {
            "schema_version": 1,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "backends": [backend.name for backend in backends],
            "case_count": len(REPLAY_CASES),
            "allowed_diff": [asdict(item) for item in ALLOWED_DIFFS],
            "normal_cases": normal_results,
            "injected_cases": injected_results,
            "metrics": {
                "false_positive_rate": normal_failures / len(normal_results),
                "injected_detection_rate": detected_faults / len(injected_results),
                "summary_fault_detection_rate":
                sum(result["detected"] for result in summary_faults) / len(summary_faults),
                "duration_seconds": round(time.perf_counter() - started, 6),
            },
        }
    finally:
        for backend in reversed(backends):
            await backend.close()


def write_report(report: dict[str, Any], output_path: Path) -> None:
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def _generate_default_report(
    output_path: Path,
    lightweight: bool = False,
    include_integrations: bool = False,
) -> dict[str, Any]:
    backends = [await create_in_memory_backend()]
    if not lightweight:
        backends.append(await create_sqlite_backend())
    if include_integrations:
        sql_url = os.getenv("TRPC_REPLAY_SQL_URL")
        redis_url = os.getenv("TRPC_REPLAY_REDIS_URL")
        backends.append(await create_sql_backend(sql_url, name="sql")
                        if sql_url else await create_sql_backend("sqlite:///:memory:", name="sql_fallback"))
        backends.append(await create_redis_backend(redis_url) if redis_url else await create_mock_redis_backend())
    report = await run_replay_matrix(backends)
    write_report(report, output_path)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("session_memory_summary_diff_report.json"),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--light", action="store_true", help="run only the dependency-free InMemory backend")
    mode.add_argument(
        "--integration",
        action="store_true",
        help="use configured SQL/Redis backends or dependency-free fallbacks",
    )
    args = parser.parse_args()
    report = asyncio.run(
        _generate_default_report(
            args.output,
            lightweight=args.light,
            include_integrations=args.integration,
        ))
    print(json.dumps(report["metrics"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
