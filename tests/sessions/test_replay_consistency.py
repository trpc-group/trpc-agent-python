# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Replay consistency tests for InMemory and SQLite session and memory services.

Covers:
- Standard session, memory, and summary replay tracks
- Canonical comparison and field-level diff reports
"""

from __future__ import annotations

import json
import os
import re
import time
from contextlib import asynccontextmanager
from copy import deepcopy
from fnmatch import fnmatch
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest

from trpc_agent_sdk.abc import MemoryServiceConfig
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.memory._in_memory_memory_service import InMemoryMemoryService
from trpc_agent_sdk.memory._sql_memory_service import SqlMemoryService
from trpc_agent_sdk.memory._redis_memory_service import RedisMemoryService
from trpc_agent_sdk.sessions._in_memory_session_service import InMemorySessionService
from trpc_agent_sdk.sessions._redis_session_service import RedisSessionService
from trpc_agent_sdk.sessions._session import Session
from trpc_agent_sdk.sessions._session_summarizer import SessionSummarizer
from trpc_agent_sdk.sessions._sql_session_service import SqlSessionService
from trpc_agent_sdk.sessions._types import SessionServiceConfig
from trpc_agent_sdk.types import Content, EventActions, FunctionCall, FunctionResponse, Part

_APP_NAME = "replay-app"
_USER_ID = "replay-user"
_SESSION_ID = "replay-session"
_REPORT_PATH = Path(__file__).with_name("session_memory_summary_diff_report.json")
_DESIGN_PATH = Path(__file__).parents[2] / "docs" / "replay_consistency_design.md"


class _MockRedisStorage:
    """Minimal Redis storage double used by the replay harness."""

    def __init__(self):
        self._strings: dict[str, Any] = {}
        self._hashes: dict[str, dict[str, Any]] = {}
        self._lists: dict[str, list[Any]] = {}

    @asynccontextmanager
    async def create_db_session(self):
        yield self

    def _keys(self, pattern: str) -> list[str]:
        keys = self._strings.keys() | self._hashes.keys() | self._lists.keys()
        return sorted(key for key in keys if fnmatch(key, pattern))

    async def execute_command(self, _session: Any, command: Any) -> Any:
        method = command.method.lower()
        args = command.args
        if method == "set":
            self._strings[args[0]] = args[1]
            return True
        if method == "get":
            return self._strings.get(args[0])
        if method == "keys":
            return self._keys(args[0])
        if method == "hset":
            values = self._hashes.setdefault(args[0], {})
            values.update(dict(zip(args[1::2], args[2::2])))
            return True
        if method == "hgetall":
            return dict(self._hashes.get(args[0], {}))
        if method == "rpush":
            self._lists.setdefault(args[0], []).extend(args[1:])
            return len(self._lists[args[0]])
        if method == "type":
            key = args[0]
            return "string" if key in self._strings else "hash" if key in self._hashes else "list"
        if method == "lrange":
            return list(self._lists.get(args[0], []))
        raise ValueError(f"Unsupported mock Redis command: {method}")

    async def delete(self, _session: Any, key: str) -> None:
        self._strings.pop(key, None)
        self._hashes.pop(key, None)
        self._lists.pop(key, None)

    async def query(self, _session: Any, pattern: str, conditions: Any) -> list[tuple[str, Any]]:
        keys = self._keys(pattern)
        if conditions.limit > 0:
            keys = keys[:conditions.limit]
        return [(key, list(self._lists[key])) for key in keys if key in self._lists]

    async def expire(self, _session: Any, _command: Any) -> None:
        return None

    async def close(self) -> None:
        return None


class _ReplaySummaryModel:
    """Deterministic model double that still exercises SessionSummarizer."""

    name = "replay-summary-model"

    def __init__(self, text: str):
        self._text = text

    async def generate_async(self, _request: Any, stream: bool = False, ctx: Any = None):
        del stream, ctx
        response = type("ReplaySummaryResponse", (), {})()
        response.content = Content(parts=[Part.from_text(text=self._text)])
        yield response


def _load_cases() -> list[dict[str, Any]]:
    cases_dir = Path(__file__).with_name("replay_cases")
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(cases_dir.glob("*.jsonl"))]


def _replay_identity() -> tuple[str, str, str]:
    persistent_backend = os.getenv("REPLAY_REDIS_URL") or os.getenv("REPLAY_SQL_URL")
    if not persistent_backend or os.getenv("REPLAY_LIGHTWEIGHT") == "1":
        return _APP_NAME, _USER_ID, _SESSION_ID
    suffix = uuid4().hex
    return f"{_APP_NAME}-{suffix}", f"{_USER_ID}-{suffix}", f"{_SESSION_ID}-{suffix}"


def _expected_comparison_backends() -> list[str]:
    if os.getenv("REPLAY_LIGHTWEIGHT") == "1":
        return []
    backends = ["sql", "redis_mock"]
    if os.getenv("REPLAY_REDIS_URL"):
        backends.append("redis")
    return backends


def _make_session_config() -> SessionServiceConfig:
    config = SessionServiceConfig(store_historical_events=True)
    config.clean_ttl_config()
    return config


def _make_memory_config() -> MemoryServiceConfig:
    config = MemoryServiceConfig(enabled=True)
    config.clean_ttl_config()
    return config


def _make_event(operation: dict[str, Any], index: int, timestamp: float) -> Event:
    part: Part
    event_type = operation.get("type", "text")
    if event_type == "function_call":
        part = Part(function_call=FunctionCall(name=operation["name"], args=operation.get("args", {})))
    elif event_type == "function_response":
        part = Part(function_response=FunctionResponse(name=operation["name"], response=operation.get("response", {})))
    else:
        part = Part.from_text(text=operation["text"])
    return Event(
        id=f"event-{index}",
        invocation_id=f"invocation-{index}",
        author=operation["author"],
        content=Content(parts=[part]),
        actions=EventActions(state_delta=operation.get("state_delta", {})),
        timestamp=timestamp,
    )


async def _create_backends() -> dict[str, tuple[Any, Any]]:
    session_config = _make_session_config()
    memory_config = _make_memory_config()
    in_memory = (
        InMemorySessionService(session_config=session_config),
        InMemoryMemoryService(memory_service_config=memory_config, enabled=True),
    )
    backends = {"in_memory": in_memory}
    if os.getenv("REPLAY_LIGHTWEIGHT") != "1":
        sql_url = os.getenv("REPLAY_SQL_URL", "sqlite:///:memory:")
        sql = (
            SqlSessionService(db_url=sql_url, session_config=session_config, is_async=False),
            SqlMemoryService(db_url=sql_url, memory_service_config=memory_config, is_async=False),
        )
        await sql[0]._sql_storage.create_sql_engine()
        await sql[1]._sql_storage.create_sql_engine()
        backends["sql"] = sql
        # Constructors create RedisStorage immediately. Return one shared mock
        # so Session and Memory exercise the same Redis key space.
        mock_storage = _MockRedisStorage()
        with (
                patch(
                    "trpc_agent_sdk.sessions._redis_session_service.RedisStorage",
                    return_value=mock_storage,
                ),
                patch(
                    "trpc_agent_sdk.memory._redis_memory_service.RedisStorage",
                    return_value=mock_storage,
                ),
        ):
            redis_mock = (
                RedisSessionService(db_url="redis://mock", session_config=session_config, is_async=False),
                RedisMemoryService(db_url="redis://mock",
                                   memory_service_config=memory_config,
                                   enabled=True,
                                   is_async=False),
            )
        backends["redis_mock"] = redis_mock

        redis_url = os.getenv("REPLAY_REDIS_URL")
        if redis_url:
            backends["redis"] = (
                RedisSessionService(db_url=redis_url, session_config=session_config, is_async=False),
                RedisMemoryService(db_url=redis_url, memory_service_config=memory_config, enabled=True, is_async=False),
            )
    return backends


async def _close_backends(backends: dict[str, tuple[Any, Any]]) -> None:
    for session_service, memory_service in backends.values():
        await session_service.close()
        await memory_service.close()


async def _create_summary(
    session: Session,
    session_service: Any,
    operation: dict[str, Any],
    version: int,
    updated_at: float,
) -> None:
    before = list(session.events)
    summarizer = SessionSummarizer(
        model=_ReplaySummaryModel(operation["text"]),
        check_summarizer_functions=[lambda _session: True],
        keep_recent_count=operation.get("keep_recent", 1),
        start_by_user_turn=False,
    )
    if not await summarizer.should_summarize(session):
        raise AssertionError("Replay summary did not reach its deterministic trigger")
    summary_text = await summarizer.create_session_summary(
        session,
        store_historical_events=operation.get("store_historical", False),
    )
    if not summary_text:
        raise AssertionError("Replay summary generation returned no content")

    summary = next(event for event in session.events if event.is_summary_event())
    retained_objects = {id(event) for event in session.events}
    summary.custom_metadata = {
        "summary_replaces_event_ids": [event.id for event in before if id(event) not in retained_objects],
        "summary_session_id": session.id,
        "summary_updated_at": updated_at,
        "summary_version": version,
    }
    summary.timestamp = session.events[1].timestamp - 0.001 if len(session.events) > 1 else updated_at
    await session_service.update_session(session)


def _event_snapshot(event: Event) -> dict[str, Any]:
    calls = [{"name": call.name, "args": _normalize(call.args)} for call in event.get_function_calls()]
    responses = [{
        "name": response.name,
        "response": _normalize(response.response)
    } for response in event.get_function_responses()]
    return {
        "author": event.author,
        "text": _normalize_summary_text(event.get_text()) if event.is_summary_event() else event.get_text(),
        "function_calls": calls,
        "function_responses": responses,
        "state_delta": _normalize(event.actions.state_delta if event.actions else {}),
        "is_summary": event.is_summary_event(),
        "summary_metadata": _summary_metadata_snapshot(event.custom_metadata) if event.is_summary_event() else {},
    }


def _summary_metadata_snapshot(metadata: dict[str, Any] | None) -> dict[str, Any]:
    normalized = _normalize(metadata or {})
    replaced_ids = normalized.get("summary_replaces_event_ids")
    if isinstance(replaced_ids, list):
        normalized["summary_replaces_event_ids"] = [
            "<generated-event-id>" if re.fullmatch(r"[0-9a-f-]{36}", event_id) else event_id
            for event_id in replaced_ids
        ]
    return normalized


def _normalize_summary_text(text: str) -> str:
    """Compare summary content semantically while keeping its metadata strict."""
    return re.sub(r"[\s，。,.!！?？]+", " ", text).strip().lower()


def _memory_snapshot(response: Any) -> list[dict[str, Any]]:
    entries = [{"author": memory.author, "content": _normalize(memory.content)} for memory in response.memories]
    return sorted(entries, key=lambda entry: json.dumps(entry, sort_keys=True, ensure_ascii=False))


def _stored_summary_version(session: Session) -> int:
    versions = [
        event.custom_metadata.get("summary_version", 0) for event in session.events + session.historical_events
        if event.is_summary_event() and event.custom_metadata
    ]
    return max(versions, default=0)


def _snapshot(session: Session, memory_results: list[Any]) -> dict[str, Any]:
    return {
        "session_id": session.id,
        "state": _normalize(session.state),
        "events": [_event_snapshot(event) for event in session.events],
        "historical_events": [_event_snapshot(event) for event in session.historical_events],
        "memory": [_memory_snapshot(result) for result in memory_results],
        "summary_version": _stored_summary_version(session),
    }


async def _replay_case(case: dict[str, Any]) -> dict[str, dict[str, Any]]:
    backends = await _create_backends()
    snapshots: dict[str, dict[str, Any]] = {}
    app_name, user_id, session_id = _replay_identity()
    try:
        base_timestamp = time.time()
        for name, (session_service, memory_service) in backends.items():
            session = await session_service.create_session(app_name=app_name, user_id=user_id, session_id=session_id)
            memory_results = []
            summary_version = 0
            event_index = 0
            timestamp = base_timestamp
            for operation in case["operations"]:
                timestamp += 2.0
                if operation["op"] == "append":
                    event_index += 1
                    await session_service.append_event(session, _make_event(operation, event_index, timestamp))
                elif operation["op"] == "store_memory":
                    await memory_service.store_session(session)
                elif operation["op"] == "search_memory":
                    memory_results.append(await memory_service.search_memory(session.save_key, operation["query"]))
                elif operation["op"] == "summary":
                    summary_version += 1
                    await _create_summary(session, session_service, operation, summary_version, timestamp)
                elif operation["op"] == "summary_retry":
                    summary_version += 1
                    original_update = session_service.update_session
                    with patch.object(
                            session_service,
                            "update_session",
                            side_effect=RuntimeError("injected summary persistence failure"),
                    ):
                        with pytest.raises(RuntimeError, match="injected summary persistence failure"):
                            await _create_summary(session, session_service, operation, summary_version, timestamp)
                    await original_update(session)
                elif operation["op"] == "store_memory_retry":
                    original_store = memory_service.store_session
                    with patch.object(
                            memory_service,
                            "store_session",
                            side_effect=RuntimeError("injected memory persistence failure"),
                    ):
                        with pytest.raises(RuntimeError, match="injected memory persistence failure"):
                            await memory_service.store_session(session)
                    await original_store(session)
                else:
                    raise ValueError(f"Unsupported replay operation: {operation['op']}")
            stored = await session_service.get_session(app_name=app_name, user_id=user_id, session_id=session_id)
            assert stored is not None
            snapshots[name] = _snapshot(stored, memory_results)
    finally:
        await _close_backends(backends)
    return snapshots


def _normalize(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return {key: _normalize(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, set):
        return sorted(_normalize(item) for item in value)
    return value


def _diff(expected: Any, actual: Any, allowed_diff: set[str], path: str = "") -> list[dict[str, Any]]:
    if isinstance(expected, dict) and isinstance(actual, dict):
        diffs = []
        for key in sorted(set(expected) | set(actual)):
            child = f"{path}.{key}" if path else key
            diffs.extend(_diff(expected.get(key), actual.get(key), allowed_diff, child))
        return diffs
    if isinstance(expected, list) and isinstance(actual, list):
        diffs = []
        for index in range(max(len(expected), len(actual))):
            child = f"{path}[{index}]"
            left = expected[index] if index < len(expected) else None
            right = actual[index] if index < len(actual) else None
            diffs.extend(_diff(left, right, allowed_diff, child))
        return diffs
    if expected != actual:
        return [{"path": path, "expected": expected, "actual": actual, "allowed": path in allowed_diff}]
    return []


def _add_diff_context(diffs: list[dict[str, Any]], session_id: str) -> None:
    for diff in diffs:
        diff["session_id"] = session_id
        event_match = re.match(r"(?:events|historical_events)\[(\d+)]", diff["path"])
        if event_match:
            diff["event_index"] = int(event_match.group(1))


def _inject_case_difference(case_id: str, snapshot: dict[str, Any]) -> tuple[dict[str, Any], str]:
    injected = deepcopy(snapshot)
    if case_id == "01_single_turn":
        injected["events"][1]["text"] = "missing response"
        expected_path = "events[1].text"
    elif case_id == "02_multi_turn":
        injected["events"][1], injected["events"][2] = injected["events"][2], injected["events"][1]
        expected_path = "events[1].author"
    elif case_id == "03_tool_call":
        injected["events"][1]["function_responses"] = []
        expected_path = "events[1].function_responses[0]"
    elif case_id == "04_session_state":
        injected["state"]["mode"] = "wrong"
        expected_path = "state.mode"
    elif case_id == "05_scoped_state":
        injected["state"]["session_key"] = "wrong"
        expected_path = "state.session_key"
    elif case_id == "06_memory":
        index = len(injected["memory"][0])
        injected["memory"][0].append(deepcopy(injected["memory"][0][0]))
        expected_path = f"memory[0][{index}]"
    elif case_id == "07_summary_create_update":
        injected["events"][0]["summary_metadata"]["summary_version"] = 0
        expected_path = "events[0].summary_metadata.summary_version"
    elif case_id == "08_summary_truncation":
        index = len(injected["historical_events"]) - 1
        injected["historical_events"].pop()
        expected_path = f"historical_events[{index}]"
    elif case_id == "09_duplicate_store":
        index = len(injected["memory"][0])
        injected["memory"][0].append(deepcopy(injected["memory"][0][0]))
        expected_path = f"memory[0][{index}]"
    elif case_id == "10_failure_recovery":
        injected["session_id"] = "wrong-session"
        expected_path = "session_id"
    else:
        raise ValueError(f"Unsupported injected replay case: {case_id}")
    return injected, expected_path


def _write_report(results: list[dict[str, Any]]) -> None:
    _REPORT_PATH.write_text(json.dumps({"cases": results}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _compare_snapshot(
    backend: str,
    baseline: dict[str, Any],
    actual: dict[str, Any],
    allowed_diff: set[str],
    allowed_diff_reason: str | None = None,
) -> dict[str, Any]:
    diffs = _diff(baseline, actual, allowed_diff)
    _add_diff_context(diffs, baseline["session_id"])
    for diff in diffs:
        diff["target"] = "memory" if diff["path"].startswith("memory") else "session"
        if diff["allowed"] and allowed_diff_reason:
            diff["allowed_reason"] = allowed_diff_reason
    has_unallowed_diff = any(not diff["allowed"] for diff in diffs)
    return {
        "backend": backend,
        "session_id": baseline["session_id"],
        "status": "different" if has_unallowed_diff else "match",
        "diffs": diffs,
    }


async def _compare_case(case: dict[str, Any]) -> dict[str, Any]:
    snapshots = await _replay_case(case)
    comparisons = []
    for backend, snapshot in snapshots.items():
        if backend == "in_memory":
            continue
        comparisons.append(
            _compare_snapshot(
                backend,
                snapshots["in_memory"],
                snapshot,
                set(case.get("allowed_diff", [])),
                case.get("allowed_diff_reason"),
            ))
    status = "match" if all(comparison["status"] == "match" for comparison in comparisons) else "different"
    return {
        "case_id": case["id"],
        "baseline": "in_memory",
        "status": status,
        "comparisons": comparisons,
    }


# ---------------------------------------------------------------------------
# Replay cases
# ---------------------------------------------------------------------------


class TestReplayCases:

    async def test_single_turn_matches_default_backends(self):
        result = await _compare_case(_load_cases()[0])
        assert result["status"] == "match"
        assert [item["backend"] for item in result["comparisons"]] == _expected_comparison_backends()

    async def test_all_public_cases_match_and_write_report(self):
        results = [await _compare_case(case) for case in _load_cases()]
        _write_report(results)
        expected = {case["id"]: "match" for case in _load_cases()}
        assert {result["case_id"]: result["status"] for result in results} == expected
        report = json.loads(_REPORT_PATH.read_text(encoding="utf-8"))
        assert len(report["cases"]) == 10

    async def test_all_public_cases_detect_injected_difference(self):
        for case in _load_cases():
            snapshots = await _replay_case(case)
            source = snapshots.get("sql", snapshots["in_memory"])
            injected, expected_path = _inject_case_difference(case["id"], source)
            diffs = _diff(source, injected, set())
            assert expected_path in {diff["path"] for diff in diffs if not diff["allowed"]}, case["id"]

    async def test_normal_cases_have_no_false_positives(self):
        results = [await _compare_case(case) for case in _load_cases()]
        false_positives = sum(result["status"] != "match" for result in results)
        assert false_positives == 0

    async def test_lightweight_mode_runs_without_a_persistent_backend(self, monkeypatch):
        monkeypatch.setenv("REPLAY_LIGHTWEIGHT", "1")
        started_at = time.perf_counter()
        results = [await _compare_case(case) for case in _load_cases()]
        assert all(result["status"] == "match" and result["comparisons"] == [] for result in results)
        assert time.perf_counter() - started_at < 30


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


class TestReplayComparison:

    def test_design_note_contains_150_to_300_chinese_characters(self):
        design = _DESIGN_PATH.read_text(encoding="utf-8")
        assert 150 <= len(re.findall(r"[\u4e00-\u9fff]", design)) <= 300

    def test_real_redis_replay_uses_isolated_identity(self, monkeypatch):
        monkeypatch.delenv("REPLAY_LIGHTWEIGHT", raising=False)
        monkeypatch.setenv("REPLAY_REDIS_URL", "redis://localhost:6379/0")
        app_name, user_id, session_id = _replay_identity()
        assert app_name.startswith(f"{_APP_NAME}-")
        assert user_id.startswith(f"{_USER_ID}-")
        assert session_id.startswith(f"{_SESSION_ID}-")

    def test_external_sql_replays_use_isolated_identities(self, monkeypatch):
        monkeypatch.delenv("REPLAY_LIGHTWEIGHT", raising=False)
        monkeypatch.delenv("REPLAY_REDIS_URL", raising=False)
        monkeypatch.setenv("REPLAY_SQL_URL", "sqlite:///replay.db")
        first = _replay_identity()
        second = _replay_identity()
        assert first != second
        assert first[0].startswith(f"{_APP_NAME}-")
        assert first[1].startswith(f"{_USER_ID}-")
        assert first[2].startswith(f"{_SESSION_ID}-")

    @pytest.mark.skipif(not os.getenv("REPLAY_REDIS_URL"), reason="REPLAY_REDIS_URL is not configured")
    async def test_real_redis_replays_use_distinct_identities(self):
        case = _load_cases()[0]
        first = await _replay_case(case)
        second = await _replay_case(case)
        first_ids = {snapshot["session_id"] for snapshot in first.values()}
        second_ids = {snapshot["session_id"] for snapshot in second.values()}
        assert len(first_ids) == 1
        assert len(second_ids) == 1
        assert first_ids != second_ids

    def test_diff_reports_event_index_and_field(self):
        diffs = _diff({"events": [{"author": "user"}]}, {"events": [{"author": "agent"}]}, set())
        assert diffs == [{"path": "events[0].author", "expected": "user", "actual": "agent", "allowed": False}]

    def test_summary_text_normalization_keeps_metadata_comparison_strict(self):
        assert _normalize_summary_text(" User likes tea。\n") == _normalize_summary_text("user likes tea!")
        diffs = _diff(
            {"events": [{
                "summary_metadata": {
                    "summary_version": 2
                }
            }]},
            {"events": [{
                "summary_metadata": {
                    "summary_version": 1
                }
            }]},
            set(),
        )
        assert diffs[0]["path"] == "events[0].summary_metadata.summary_version"

    def test_normalization_preserves_business_field_differences(self):
        expected = _normalize({"state": {"mode": "normal"}})
        actual = _normalize({"state": {"mode": "wrong"}})
        assert _diff(expected, actual, set()) == [{
            "path": "state.mode",
            "expected": "normal",
            "actual": "wrong",
            "allowed": False,
        }]

    def test_allowed_difference_is_retained_in_report_data(self):
        diffs = _diff({"state": {"backend": "a"}}, {"state": {"backend": "b"}}, {"state.backend"})
        assert diffs == [{"path": "state.backend", "expected": "a", "actual": "b", "allowed": True}]

    def test_allowed_difference_includes_its_reason_in_the_report(self):
        comparison = _compare_snapshot(
            "sql",
            {"session_id": "replay-session", "state": {"backend": "a"}},
            {"session_id": "replay-session", "state": {"backend": "b"}},
            {"state.backend"},
            "Persistent backend stores this field differently.",
        )
        assert comparison["status"] == "match"
        assert comparison["diffs"][0]["allowed_reason"] == "Persistent backend stores this field differently."


class TestReplaySummaryMismatches:

    async def test_summary_runs_production_compression_and_persists_metadata(self):
        service = InMemorySessionService(session_config=_make_session_config())
        session = await service.create_session(app_name=_APP_NAME, user_id=_USER_ID, session_id=_SESSION_ID)
        for index in range(4):
            await service.append_event(
                session,
                _make_event({
                    "author": "user" if index % 2 == 0 else "agent",
                    "text": f"message {index}"
                }, index, index),
            )

        await _create_summary(
            session,
            service,
            {
                "text": "deterministic summary",
                "keep_recent": 2,
                "store_historical": True
            },
            version=1,
            updated_at=10.0,
        )
        stored = await service.get_session(app_name=_APP_NAME, user_id=_USER_ID, session_id=_SESSION_ID)
        assert stored is not None
        summary = stored.events[0]
        assert summary.get_text() == "Previous conversation summary: deterministic summary"
        assert len(stored.events) == 3
        assert summary.custom_metadata == {
            "summary_replaces_event_ids": ["event-0", "event-1"],
            "summary_session_id": _SESSION_ID,
            "summary_updated_at": 10.0,
            "summary_version": 1,
        }
        await service.close()

    async def test_report_detects_all_required_summary_corruptions(self):
        case = next(case for case in _load_cases() if case["id"] == "07_summary_create_update")
        snapshots = await _replay_case(case)
        snapshot = snapshots.get("sql", snapshots["in_memory"])
        corruptions = []

        missing = deepcopy(snapshot)
        missing["events"].pop(0)
        corruptions.append((missing, "events[0]"))

        replaced = deepcopy(snapshot)
        replaced["events"][0]["text"] = "old summary"
        corruptions.append((replaced, "events[0].text"))

        wrong_session = deepcopy(snapshot)
        wrong_session["events"][0]["summary_metadata"]["summary_session_id"] = "wrong-session"
        corruptions.append((wrong_session, "events[0].summary_metadata.summary_session_id"))

        for corrupted, expected_path in corruptions:
            comparison = _compare_snapshot("fault", snapshot, corrupted, set())
            matching = [diff for diff in comparison["diffs"] if diff["path"].startswith(expected_path)]
            assert comparison["status"] == "different"
            assert matching
            assert all(diff["session_id"] == snapshot["session_id"] and diff["event_index"] == 0 and "expected" in diff
                       and "actual" in diff for diff in matching)

    async def test_report_detects_partial_recovery_corruption(self):
        case = next(case for case in _load_cases() if case["id"] == "10_failure_recovery")
        snapshots = await _replay_case(case)
        snapshot = snapshots.get("sql", snapshots["in_memory"])

        corruptions = []
        duplicate_event = deepcopy(snapshot)
        event_index = len(duplicate_event["events"])
        duplicate_event["events"].append(deepcopy(duplicate_event["events"][-1]))
        corruptions.append((duplicate_event, f"events[{event_index}]"))

        dirty_state = deepcopy(snapshot)
        dirty_state["state"]["recovery_status"] = "partial"
        corruptions.append((dirty_state, "state.recovery_status"))

        duplicate_memory = deepcopy(snapshot)
        memory_index = len(duplicate_memory["memory"][0])
        duplicate_memory["memory"][0].append(deepcopy(duplicate_memory["memory"][0][0]))
        corruptions.append((duplicate_memory, f"memory[0][{memory_index}]"))

        for corrupted, expected_path in corruptions:
            comparison = _compare_snapshot("fault", snapshot, corrupted, set())
            assert comparison["status"] == "different"
            assert expected_path in {diff["path"] for diff in comparison["diffs"]}

    async def test_failure_recovery_finishes_with_clean_invariants(self):
        case = next(case for case in _load_cases() if case["id"] == "10_failure_recovery")
        snapshots = await _replay_case(case)
        for snapshot in snapshots.values():
            assert snapshot["state"]["recovery_status"] == "complete"
            assert snapshot["summary_version"] == 1
            assert len(snapshot["events"]) == 3
            assert snapshot["events"][0]["is_summary"] is True
            assert snapshot["events"][0]["summary_metadata"]["summary_session_id"] == snapshot["session_id"]
            memories = snapshot["memory"][0]
            assert len({json.dumps(memory, sort_keys=True) for memory in memories}) == len(memories)
