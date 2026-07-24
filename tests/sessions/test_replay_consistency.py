# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Replay consistency tests for Session / Memory / Summary backends.

These tests drive multiple backends with the same replay cases, normalize
backend-specific fields, and verify that injected inconsistencies are reported
with field-level locations.
"""

from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional
from unittest.mock import MagicMock

from trpc_agent_sdk.abc import MemoryServiceConfig
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.memory._in_memory_memory_service import InMemoryMemoryService
from trpc_agent_sdk.memory._sql_memory_service import SqlMemoryService
from trpc_agent_sdk.sessions._in_memory_session_service import InMemorySessionService
from trpc_agent_sdk.sessions._session import Session
from trpc_agent_sdk.sessions._session_summarizer import SessionSummarizer
from trpc_agent_sdk.sessions._summarizer_manager import SummarizerSessionManager
from trpc_agent_sdk.sessions._sql_session_service import SqlSessionService
from trpc_agent_sdk.sessions._types import SessionServiceConfig
from trpc_agent_sdk.types import Content, EventActions, FunctionCall, FunctionResponse, Part

APP_NAME = "replay-app"
USER_ID = "replay-user"
SESSION_ID = "replay-session"
SAVE_KEY = f"{APP_NAME}/{USER_ID}"


@dataclass(frozen=True)
class ReplayStep:
    kind: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class ReplayCase:
    case_id: str
    steps: tuple[ReplayStep, ...]
    memory_query: Optional[str] = None


@dataclass(frozen=True)
class BackendFactory:
    name: str
    create: Callable[[], Any]
    create_memory: Callable[[], Any]


def _session_config(**kwargs: Any) -> SessionServiceConfig:
    config = SessionServiceConfig(**kwargs)
    config.clean_ttl_config()
    return config


def _memory_config() -> MemoryServiceConfig:
    config = MemoryServiceConfig(enabled=True)
    config.clean_ttl_config()
    return config


def _make_model(summary_text: str = "summary text") -> MagicMock:
    model = MagicMock()
    model.name = "replay-summary-model"
    response = MagicMock()
    response.content = Content(parts=[Part.from_text(text=summary_text)])

    async def generate_async(request, stream=False, ctx=None):
        yield response

    model.generate_async = generate_async
    return model


def _make_summarizer_manager(summary_text: str = "stable replay summary") -> SummarizerSessionManager:
    model = _make_model(summary_text)
    summarizer = SessionSummarizer(
        model=model,
        check_summarizer_functions=[lambda session: True],
        keep_recent_count=1,
        start_by_user_turn=True,
    )
    return SummarizerSessionManager(model=model, summarizer=summarizer)


def _make_text_event(author: str, text: str, state_delta: Optional[dict[str, Any]] = None) -> Event:
    return Event(
        invocation_id="replay-invocation",
        author=author,
        content=Content(parts=[Part.from_text(text=text)]),
        actions=EventActions(state_delta=state_delta or {}),
    )


def _make_tool_call_event(tool_name: str, args: dict[str, Any]) -> Event:
    return Event(
        invocation_id="replay-invocation",
        author="agent",
        content=Content(parts=[Part(function_call=FunctionCall(name=tool_name, args=args))]),
    )


def _make_tool_response_event(tool_name: str, response: dict[str, Any]) -> Event:
    return Event(
        invocation_id="replay-invocation",
        author="user",
        content=Content(parts=[Part(function_response=FunctionResponse(name=tool_name, response=response))]),
    )


def _make_summary_event(text: str) -> Event:
    event = _make_text_event("system", text)
    event.set_summary_event(True)
    return event


async def _create_sql_service(config: SessionServiceConfig):
    service = SqlSessionService(db_url="sqlite:///:memory:", session_config=config, is_async=False)
    await service._sql_storage.create_sql_engine()
    return service


async def _create_sql_memory_service():
    service = SqlMemoryService(
        db_url="sqlite:///:memory:",
        memory_service_config=_memory_config(),
        is_async=False,
    )
    await service._sql_storage.create_sql_engine()
    return service


async def _close_service(service: Any) -> None:
    close = getattr(service, "close", None)
    if close:
        await close()


def _backend_factories(config: Optional[SessionServiceConfig] = None) -> tuple[BackendFactory, ...]:
    config = config or _session_config()
    return (
        BackendFactory(
            "in_memory",
            lambda: InMemorySessionService(session_config=copy.deepcopy(config)),
            lambda: InMemoryMemoryService(memory_service_config=_memory_config()),
        ),
        BackendFactory(
            "sqlite",
            lambda: _create_sql_service(copy.deepcopy(config)),
            _create_sql_memory_service,
        ),
    )


async def _create_backend(create: Callable[[], Any]):
    service = create()
    if hasattr(service, "__await__"):
        service = await service
    return service


def _event_to_snapshot(event: Event, index: int) -> dict[str, Any]:
    parts = []
    for part in event.content.parts if event.content and event.content.parts else []:
        if part.text is not None:
            parts.append({"type": "text", "text": part.text})
        elif part.function_call is not None:
            parts.append({
                "type": "function_call",
                "name": part.function_call.name,
                "args": part.function_call.args or {},
            })
        elif part.function_response is not None:
            parts.append({
                "type": "function_response",
                "name": part.function_response.name,
                "response": part.function_response.response or {},
            })

    return {
        "index": index,
        "author": event.author,
        "parts": parts,
        "state_delta": dict(event.actions.state_delta or {}),
        "is_summary": event.is_summary_event(),
        "model_visible": event.is_model_visible(),
    }


def _memory_to_snapshot(memory_response: Any) -> list[dict[str, Any]]:
    memories = []
    for memory in memory_response.memories:
        text = ""
        if memory.content and memory.content.parts:
            text = "".join(part.text or "" for part in memory.content.parts)
        memories.append({
            "author": memory.author,
            "text": text,
        })
    return sorted(memories, key=lambda item: (item["author"] or "", item["text"]))


async def _summary_to_snapshot(service: Any, session: Session) -> Optional[dict[str, Any]]:
    manager = getattr(service, "_summarizer_manager", None)
    if manager is None:
        return None
    summary = await manager.get_session_summary(session)
    if summary is None:
        return None
    return {
        "session_id": summary.session_id,
        "summary_text": summary.summary_text,
        "original_event_count": summary.original_event_count,
        "compressed_event_count": summary.compressed_event_count,
        "metadata": dict(summary.metadata or {}),
    }


async def _session_to_snapshot(service: Any, memory_service: InMemoryMemoryService, session: Session,
                               memory_query: Optional[str]) -> dict[str, Any]:
    stored = await service.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=SESSION_ID)
    assert stored is not None
    memory_response = await memory_service.search_memory(SAVE_KEY, memory_query or "", limit=0)
    return {
        "session_id": stored.id,
        "events": [_event_to_snapshot(event, index) for index, event in enumerate(stored.events)],
        "historical_events": [_event_to_snapshot(event, index) for index, event in enumerate(stored.historical_events)],
        "state": dict(stored.state),
        "memory": _memory_to_snapshot(memory_response),
        "summary": await _summary_to_snapshot(service, stored),
    }


async def _run_case(service: Any, memory_service: Any, case: ReplayCase) -> dict[str, Any]:
    replay_epoch = time.time()

    session = await service.create_session(app_name=APP_NAME, user_id=USER_ID, session_id=SESSION_ID)

    for step_index, step in enumerate(case.steps):
        if step.kind == "text":
            event = _make_text_event(**step.payload)
            event.timestamp = replay_epoch + step_index
            await service.append_event(session, event)
        elif step.kind == "tool_call":
            event = _make_tool_call_event(**step.payload)
            event.timestamp = replay_epoch + step_index
            await service.append_event(session, event)
        elif step.kind == "tool_response":
            event = _make_tool_response_event(**step.payload)
            event.timestamp = replay_epoch + step_index
            await service.append_event(session, event)
        elif step.kind == "summary_event":
            event = _make_summary_event(**step.payload)
            event.timestamp = replay_epoch + step_index
            await service.append_event(session, event)
        elif step.kind == "summarize":
            await service.create_session_summary(session)
            refreshed = await service.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=SESSION_ID)
            assert refreshed is not None
            session = refreshed
        elif step.kind == "store_memory":
            stored = await service.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=SESSION_ID)
            assert stored is not None
            await memory_service.store_session(stored)
        else:
            raise AssertionError(f"Unknown replay step: {step.kind}")

    if case.case_id == "duplicate_or_recovery":
        stored = await service.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=SESSION_ID)
        assert stored is not None
        assert len(stored.events) == 3
        duplicate_events = stored.events[1:]
        assert [event.author for event in duplicate_events] == ["agent", "agent"]
        assert [event.content.parts[0].text for event in duplicate_events] == ["recover answer", "recover answer"]
        assert duplicate_events[0].id != duplicate_events[1].id

    snapshot = await _session_to_snapshot(service, memory_service, session, case.memory_query)

    return snapshot


def _compare_snapshots(case_id: str, backend_a: str, backend_b: str, expected: Any, actual: Any,
                       path: str = "$") -> list[dict[str, Any]]:
    if expected == actual:
        return []

    if isinstance(expected, dict) and isinstance(actual, dict):
        diffs = []
        for key in sorted(set(expected) | set(actual)):
            diffs.extend(
                _compare_snapshots(case_id, backend_a, backend_b, expected.get(key), actual.get(key), f"{path}.{key}")
            )
        return diffs

    if isinstance(expected, list) and isinstance(actual, list):
        diffs = []
        for index in range(max(len(expected), len(actual))):
            left = expected[index] if index < len(expected) else "<missing>"
            right = actual[index] if index < len(actual) else "<missing>"
            diffs.extend(_compare_snapshots(case_id, backend_a, backend_b, left, right, f"{path}[{index}]"))
        return diffs

    return [{
        "case_id": case_id,
        "session_id": SESSION_ID,
        "backend_a": backend_a,
        "backend_b": backend_b,
        "field_path": path,
        "event_index": _event_index_from_path(path),
        "summary_id": SESSION_ID if path.startswith("$.summary") else None,
        "expected": expected,
        "actual": actual,
        "allowed_diff": False,
    }]


def _event_index_from_path(path: str) -> Optional[int]:
    marker = ".events["
    if marker not in path:
        return None
    start = path.index(marker) + len(marker)
    end = path.index("]", start)
    return int(path[start:end])


def _inject_inconsistency(snapshot: dict[str, Any], case_id: str) -> dict[str, Any]:
    broken = copy.deepcopy(snapshot)
    if case_id == "single_turn_conversation":
        broken["events"][1]["parts"][0]["text"] = "wrong answer"
    elif case_id == "multi_turn_conversation":
        broken["events"].pop(2)
    elif case_id == "tool_call_and_response":
        broken["events"][2]["parts"][0]["response"] = {"temperature": "lost"}
    elif case_id == "state_update":
        broken["state"].pop("topic", None)
    elif case_id == "state_overwrite":
        broken["state"]["status"] = "draft"
    elif case_id == "memory_write_read":
        broken["memory"][0]["text"] = "corrupted memory"
    elif case_id == "summary_create":
        broken["summary"]["summary_text"] = "wrong summary"
    elif case_id == "summary_update":
        broken["summary"]["session_id"] = "other-session"
    elif case_id == "summary_event_truncation":
        broken["historical_events"] = []
    elif case_id == "duplicate_or_recovery":
        broken["events"].append(copy.deepcopy(broken["events"][-1]))
    else:
        raise AssertionError(f"Missing injected inconsistency for {case_id}")
    return broken


REPLAY_CASES = (
    ReplayCase(
        "single_turn_conversation",
        (
            ReplayStep("text", {"author": "user", "text": "hello"}),
            ReplayStep("text", {"author": "agent", "text": "hi"}),
        ),
    ),
    ReplayCase(
        "multi_turn_conversation",
        (
            ReplayStep("text", {"author": "user", "text": "question one"}),
            ReplayStep("text", {"author": "agent", "text": "answer one"}),
            ReplayStep("text", {"author": "user", "text": "question two"}),
            ReplayStep("text", {"author": "agent", "text": "answer two"}),
        ),
    ),
    ReplayCase(
        "tool_call_and_response",
        (
            ReplayStep("text", {"author": "user", "text": "check weather"}),
            ReplayStep("tool_call", {"tool_name": "weather", "args": {"city": "Shenzhen"}}),
            ReplayStep("tool_response", {"tool_name": "weather", "response": {"temperature": "30C"}}),
            ReplayStep("text", {"author": "agent", "text": "Shenzhen is 30C"}),
        ),
    ),
    ReplayCase(
        "state_update",
        (
            ReplayStep("text", {"author": "user", "text": "remember my topic", "state_delta": {"topic": "backend"}}),
            ReplayStep("text", {"author": "agent", "text": "topic saved"}),
        ),
    ),
    ReplayCase(
        "state_overwrite",
        (
            ReplayStep("text", {"author": "user", "text": "set status", "state_delta": {"status": "draft"}}),
            ReplayStep("text", {"author": "agent", "text": "status saved", "state_delta": {"status": "final"}}),
        ),
    ),
    ReplayCase(
        "memory_write_read",
        (
            ReplayStep("text", {"author": "user", "text": "memory keyword alpha"}),
            ReplayStep("text", {"author": "agent", "text": "stored alpha"}),
            ReplayStep("store_memory", {}),
        ),
        memory_query="alpha",
    ),
    ReplayCase(
        "summary_create",
        (
            ReplayStep("text", {"author": "user", "text": "summarize old question"}),
            ReplayStep("text", {"author": "agent", "text": "summarize old answer"}),
            ReplayStep("summarize", {}),
        ),
    ),
    ReplayCase(
        "summary_update",
        (
            ReplayStep("summary_event", {"text": "previous summary"}),
            ReplayStep("text", {"author": "user", "text": "new detail"}),
            ReplayStep("text", {"author": "agent", "text": "new answer"}),
            ReplayStep("summarize", {}),
        ),
    ),
    ReplayCase(
        "summary_event_truncation",
        tuple(
            [ReplayStep("text", {"author": "user" if idx == 0 else "agent", "text": f"message {idx}"})
             for idx in range(6)] + [ReplayStep("summarize", {})]
        ),
    ),
    ReplayCase(
        "duplicate_or_recovery",
        (
            ReplayStep("text", {"author": "user", "text": "recover once"}),
            ReplayStep("text", {"author": "agent", "text": "recover answer"}),
            ReplayStep("text", {"author": "agent", "text": "recover answer"}),
        ),
    ),
)


async def test_replay_cases_are_consistent_across_backends():
    config = _session_config(store_historical_events=True)

    for case in REPLAY_CASES:
        snapshots = {}
        for factory in _backend_factories(config):
            service = await _create_backend(factory.create)
            memory_service = await _create_backend(factory.create_memory)
            service.set_summarizer_manager(_make_summarizer_manager())
            snapshots[factory.name] = await _run_case(service, memory_service, case)
            await _close_service(memory_service)
            await _close_service(service)

        diffs = _compare_snapshots(case.case_id, "in_memory", "sqlite", snapshots["in_memory"], snapshots["sqlite"])
        assert diffs == [], json.dumps(diffs, ensure_ascii=False, indent=2)


async def test_injected_inconsistencies_are_reported_with_field_paths():
    config = _session_config(store_historical_events=True)

    for case in REPLAY_CASES:
        service = InMemorySessionService(session_config=copy.deepcopy(config))
        memory_service = InMemoryMemoryService(memory_service_config=_memory_config())
        service.set_summarizer_manager(_make_summarizer_manager())

        snapshot = await _run_case(service, memory_service, case)
        broken_snapshot = _inject_inconsistency(snapshot, case.case_id)
        diffs = _compare_snapshots(case.case_id, "expected", "injected", snapshot, broken_snapshot)

        assert diffs, case.case_id
        assert all(diff["case_id"] == case.case_id for diff in diffs)
        assert all(diff["field_path"].startswith("$") for diff in diffs)
        assert all(diff["allowed_diff"] is False for diff in diffs)
        assert all(diff["session_id"] == SESSION_ID for diff in diffs)
        if case.case_id in {"summary_create", "summary_update"}:
            assert all(diff["summary_id"] == SESSION_ID for diff in diffs)
        await memory_service.close()
        await service.close()
