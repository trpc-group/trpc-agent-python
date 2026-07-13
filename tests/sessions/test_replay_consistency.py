"""Replay consistency tests for session backends.

This module starts with a small, reusable harness that drives InMemory and
SQLite-backed SessionService implementations with the same replay cases, then
compares normalized snapshots from both backends.
"""

from __future__ import annotations

import json
import os
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pytest

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


APP_NAME = "replay-consistency-app"
USER_ID = "replay-user"
REPORT_PATH = Path("session_memory_summary_diff_report.json")
LIGHTWEIGHT_ONLY_ENV = "TRPC_REPLAY_LIGHTWEIGHT_ONLY"
SQL_URL_ENV = "TRPC_REPLAY_SQL_URL"
MEMORY_SQL_URL_ENV = "TRPC_REPLAY_MEMORY_SQL_URL"
REDIS_URL_ENV = "TRPC_REPLAY_REDIS_URL"
ALLOWED_DIFFS: dict[str, str] = {
    "$.summary.summary_timestamp": "summary update time is normalized to timestamp presence",
    "$.events[*].event_index_id": "auto-generated summary event ids are normalized to logical ids",
    "$.historical_events[*].event_index_id": "auto-generated summary event ids are normalized to logical ids",
}


def _event_timestamp(event_index: int) -> float:
    return time.time() + 60 + event_index / 1000


@dataclass(frozen=True)
class ReplayCase:
    case_id: str
    description: str
    session_id: str
    operations: list[dict[str, Any]]


@dataclass
class ReplayBackend:
    name: str
    session_service: Any
    memory_service: Any
    summarizer_manager: SummarizerSessionManager


def _make_session_config(**kwargs: Any) -> SessionServiceConfig:
    config = SessionServiceConfig(**kwargs)
    config.clean_ttl_config()
    return config


class _FixedSummaryModel:
    name = "fixed-summary-model"

    async def generate_async(self, request: Any, stream: bool = False, ctx: Any = None):
        yield LlmResponse(content=Content(parts=[Part.from_text(text="summary: stable replay context")]))


def _make_summarizer_manager() -> SummarizerSessionManager:
    model = _FixedSummaryModel()
    summarizer = SessionSummarizer(
        model=model,
        check_summarizer_functions=[lambda session: True],
        keep_recent_count=2,
    )
    return SummarizerSessionManager(model=model, summarizer=summarizer)


async def _make_in_memory_backend() -> ReplayBackend:
    summarizer_manager = _make_summarizer_manager()
    return ReplayBackend(
        name="in_memory",
        session_service=InMemorySessionService(
            session_config=_make_session_config(store_historical_events=True),
            summarizer_manager=summarizer_manager,
        ),
        memory_service=InMemoryMemoryService(),
        summarizer_manager=summarizer_manager,
    )


async def _make_sqlite_backend() -> ReplayBackend:
    summarizer_manager = _make_summarizer_manager()
    db_url = os.environ.get("TRPC_REPLAY_SQL_URL", "sqlite:///:memory:")
    session_service = SqlSessionService(
        db_url=db_url,
        session_config=_make_session_config(store_historical_events=True),
        is_async=False,
        summarizer_manager=summarizer_manager,
    )
    await session_service._sql_storage.create_sql_engine()

    memory_db_url = os.environ.get("TRPC_REPLAY_MEMORY_SQL_URL", "sqlite:///:memory:")
    memory_service = SqlMemoryService(db_url=memory_db_url, is_async=False)
    await memory_service._sql_storage.create_sql_engine()
    return ReplayBackend(
        name="sqlite",
        session_service=session_service,
        memory_service=memory_service,
        summarizer_manager=summarizer_manager,
    )


async def _make_redis_backend() -> ReplayBackend:
    redis_url = os.environ[REDIS_URL_ENV]
    summarizer_manager = _make_summarizer_manager()
    session_service = RedisSessionService(
        db_url=redis_url,
        session_config=_make_session_config(store_historical_events=True),
        summarizer_manager=summarizer_manager,
    )
    memory_service = RedisMemoryService(db_url=redis_url)
    return ReplayBackend(
        name="redis",
        session_service=session_service,
        memory_service=memory_service,
        summarizer_manager=summarizer_manager,
    )


def _selected_backend_factories() -> list[Callable[[], Any]]:
    if os.environ.get(LIGHTWEIGHT_ONLY_ENV) == "1":
        return [_make_in_memory_backend]

    factories: list[Callable[[], Any]] = [_make_in_memory_backend, _make_sqlite_backend]
    if os.environ.get(REDIS_URL_ENV):
        factories.append(_make_redis_backend)
    return factories


def _text_event(
    *,
    author: str,
    text: str,
    event_index: int,
    state_delta: dict[str, Any] | None = None,
) -> Event:
    return Event(
        id=f"event-{event_index:03d}",
        invocation_id=f"invocation-{event_index:03d}",
        author=author,
        content=Content(parts=[Part.from_text(text=text)]),
        actions=EventActions(state_delta=state_delta or {}),
        timestamp=_event_timestamp(event_index),
    )


def _function_call_event(*, event_index: int, name: str, args: dict[str, Any]) -> Event:
    return Event(
        id=f"event-{event_index:03d}",
        invocation_id=f"invocation-{event_index:03d}",
        author="agent",
        content=Content(parts=[Part(function_call=FunctionCall(name=name, args=args))]),
        timestamp=_event_timestamp(event_index),
    )


def _function_response_event(*, event_index: int, name: str, response: dict[str, Any]) -> Event:
    return Event(
        id=f"event-{event_index:03d}",
        invocation_id=f"invocation-{event_index:03d}",
        author="tool",
        content=Content(parts=[Part(function_response=FunctionResponse(name=name, response=response))]),
        timestamp=_event_timestamp(event_index),
    )


def _long_conversation_operations(message_pairs: int = 18) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = [{"op": "create_session", "state": {"topic": "long-summary"}}]
    for index in range(message_pairs):
        operations.extend([
            {"op": "append_text", "author": "user", "text": f"Long conversation user turn {index}."},
            {"op": "append_text", "author": "agent", "text": f"Long conversation agent answer {index}."},
        ])
    operations.append({"op": "create_summary", "version": 1})
    return operations


def _replay_cases() -> list[ReplayCase]:
    return [
        ReplayCase(
            case_id="single_turn_text",
            description="Single user message followed by a single agent text response.",
            session_id="replay-single-turn",
            operations=[
                {"op": "create_session", "state": {"topic": "weather"}},
                {"op": "append_text", "author": "user", "text": "What is the weather today?"},
                {"op": "append_text", "author": "agent", "text": "It is sunny today."},
            ],
        ),
        ReplayCase(
            case_id="multi_turn_text",
            description="Multiple user and agent text events appended in order.",
            session_id="replay-multi-turn",
            operations=[
                {"op": "create_session", "state": {"topic": "travel"}},
                {"op": "append_text", "author": "user", "text": "Plan a weekend trip."},
                {"op": "append_text", "author": "agent", "text": "Do you prefer mountains or beaches?"},
                {"op": "append_text", "author": "user", "text": "Mountains."},
                {"op": "append_text", "author": "agent", "text": "Consider a cabin near a hiking trail."},
            ],
        ),
        ReplayCase(
            case_id="state_update_overwrite",
            description="Session state receives repeated writes and last-write-wins overwrites.",
            session_id="replay-state-overwrite",
            operations=[
                {"op": "create_session", "state": {"topic": "shopping", "budget": 100}},
                {
                    "op": "append_text",
                    "author": "agent",
                    "text": "Budget recorded.",
                    "state_delta": {"budget": 120, "currency": "USD"},
                },
                {
                    "op": "append_text",
                    "author": "agent",
                    "text": "Budget updated.",
                    "state_delta": {"budget": 80, "preference": "compact"},
                },
            ],
        ),
        ReplayCase(
            case_id="scoped_state_update",
            description="Session, user, and app scoped state are written and merged back into the session view.",
            session_id="replay-scoped-state",
            operations=[
                {
                    "op": "create_session",
                    "state": {
                        "topic": "state-scopes",
                        "user:locale": "zh-CN",
                        "app:release": "2026.07",
                    },
                },
                {
                    "op": "append_text",
                    "author": "agent",
                    "text": "Scoped state updated.",
                    "state_delta": {
                        "topic": "state-scopes-updated",
                        "user:locale": "en-US",
                        "app:release": "2026.08",
                    },
                },
            ],
        ),
        ReplayCase(
            case_id="tool_call_conversation",
            description="Conversation includes a function_call event and matching function_response event.",
            session_id="replay-tool-call",
            operations=[
                {"op": "create_session", "state": {"topic": "calendar"}},
                {"op": "append_text", "author": "user", "text": "Check my meeting time."},
                {"op": "append_function_call", "name": "calendar_lookup", "args": {"date": "2026-07-13"}},
                {
                    "op": "append_function_response",
                    "name": "calendar_lookup",
                    "response": {"meeting": "design review", "time": "10:00"},
                },
                {"op": "append_text", "author": "agent", "text": "Your design review is at 10:00."},
            ],
        ),
        ReplayCase(
            case_id="memory_store_and_search",
            description="Session is stored to memory and queried for a user preference.",
            session_id="replay-memory",
            operations=[
                {"op": "create_session", "state": {"topic": "preferences"}},
                {"op": "append_text", "author": "user", "text": "Remember that I prefer compact keyboards."},
                {"op": "append_text", "author": "agent", "text": "I will remember your compact keyboard preference."},
                {"op": "store_memory"},
                {"op": "search_memory", "query": "compact keyboard", "limit": 5},
            ],
        ),
        ReplayCase(
            case_id="summary_long_conversation",
            description="A long conversation over thirty messages is compressed into a summary.",
            session_id="replay-summary-long",
            operations=_long_conversation_operations(),
        ),
        ReplayCase(
            case_id="summary_generate_and_update",
            description="A generated summary is updated after new events are appended.",
            session_id="replay-summary-update",
            operations=[
                {"op": "create_session", "state": {"topic": "long-chat"}},
                {"op": "append_text", "author": "user", "text": "We need a launch checklist."},
                {"op": "append_text", "author": "agent", "text": "The checklist should include QA and rollback."},
                {"op": "append_text", "author": "user", "text": "Add monitoring and customer comms."},
                {"op": "append_text", "author": "agent", "text": "Monitoring and comms are now part of the plan."},
                {"op": "create_summary", "version": 1},
                {"op": "append_text", "author": "user", "text": "Also include owner assignment."},
                {"op": "create_summary", "version": 2},
            ],
        ),
        ReplayCase(
            case_id="summary_event_truncation",
            description="Summary compression keeps the summary anchor with recent post-summary events.",
            session_id="replay-summary-truncation",
            operations=[
                {"op": "create_session", "state": {"topic": "history-compression"}},
                {"op": "append_text", "author": "user", "text": "Message one sets context."},
                {"op": "append_text", "author": "agent", "text": "Message two confirms context."},
                {"op": "append_text", "author": "user", "text": "Message three adds details."},
                {"op": "append_text", "author": "agent", "text": "Message four keeps recent detail."},
                {"op": "create_summary", "version": 1},
                {"op": "append_text", "author": "user", "text": "New question after compression."},
                {"op": "append_text", "author": "agent", "text": "Answer uses summary plus recent events."},
            ],
        ),
        ReplayCase(
            case_id="duplicate_event_recovery",
            description="Repeated persistence of the same session must not create duplicate events or dirty state.",
            session_id="replay-duplicate-recovery",
            operations=[
                {"op": "create_session", "state": {"topic": "retry"}},
                {"op": "append_text", "author": "user", "text": "This event may be retried."},
                {"op": "append_duplicate_last_event"},
            ],
        ),
    ]


async def _run_case(backend: ReplayBackend, replay_case: ReplayCase) -> dict[str, Any]:
    session: Session | None = None
    next_event_index = 1
    memory_searches: list[dict[str, Any]] = []

    for operation in replay_case.operations:
        if operation["op"] == "create_session":
            session = await backend.session_service.create_session(
                app_name=APP_NAME,
                user_id=USER_ID,
                session_id=replay_case.session_id,
                state=operation.get("state"),
            )
            continue

        if session is None:
            raise AssertionError(f"{replay_case.case_id}: first operation must create a session")

        if operation["op"] == "append_text":
            event = _text_event(
                author=operation["author"],
                text=operation["text"],
                event_index=next_event_index,
                state_delta=operation.get("state_delta"),
            )
            next_event_index += 1
            await backend.session_service.append_event(session, event)
            continue

        if operation["op"] == "append_function_call":
            event = _function_call_event(
                event_index=next_event_index,
                name=operation["name"],
                args=operation["args"],
            )
            next_event_index += 1
            await backend.session_service.append_event(session, event)
            continue

        if operation["op"] == "append_function_response":
            event = _function_response_event(
                event_index=next_event_index,
                name=operation["name"],
                response=operation["response"],
            )
            next_event_index += 1
            await backend.session_service.append_event(session, event)
            continue

        if operation["op"] == "store_memory":
            await backend.memory_service.store_session(session)
            continue

        if operation["op"] == "search_memory":
            response = await backend.memory_service.search_memory(
                session.save_key,
                operation["query"],
                limit=operation.get("limit", 10),
            )
            memory_searches.append({
                "query": operation["query"],
                "results": _normalize_memory_results(response.memories),
            })
            continue

        if operation["op"] == "create_summary":
            await backend.session_service.create_session_summary(session)
            summary = await backend.summarizer_manager.get_session_summary(session)
            if summary and "version" in operation:
                summary.metadata["version"] = operation["version"]
            session = await backend.session_service.get_session(
                app_name=APP_NAME,
                user_id=USER_ID,
                session_id=replay_case.session_id,
            )
            assert session is not None
            continue

        if operation["op"] == "append_duplicate_last_event":
            await backend.session_service.update_session(session)
            await backend.session_service.update_session(session)
            continue

        raise AssertionError(f"{replay_case.case_id}: unsupported replay operation {operation['op']}")

    if session is None:
        raise AssertionError(f"{replay_case.case_id}: replay case did not create a session")

    persisted = await backend.session_service.get_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=replay_case.session_id,
    )
    assert persisted is not None
    summary = await backend.summarizer_manager.get_session_summary(persisted)
    return _normalize_session_snapshot(persisted, memory_searches, summary)


def _normalize_session_snapshot(
    session: Session,
    memory_searches: list[dict[str, Any]],
    summary: Any,
) -> dict[str, Any]:
    return {
        "session_id": session.id,
        "app_name": session.app_name,
        "user_id": session.user_id,
        "conversation_count": session.conversation_count,
        "state": _normalize_json_value(session.state),
        "events": [_normalize_event(event) for event in session.events],
        "historical_events": [_normalize_event(event) for event in session.historical_events],
        "memory": memory_searches,
        "summary": _normalize_summary(summary),
    }


def _normalize_event(event: Event) -> dict[str, Any]:
    is_summary = event.is_summary_event()
    return {
        "event_index_id": "summary" if is_summary else event.id,
        "invocation_id": "summary" if is_summary else event.invocation_id,
        "author": event.author,
        "is_summary": is_summary,
        "content": _normalize_content(event.content),
        "state_delta": _normalize_json_value(event.actions.state_delta if event.actions else {}),
    }


def _normalize_memory_results(memories: list[Any]) -> list[dict[str, Any]]:
    results = [
        {
            "author": memory.author,
            "content": _normalize_content(memory.content),
            "timestamp": "<timestamp>" if memory.timestamp else None,
        }
        for memory in memories
    ]
    return sorted(results, key=lambda item: json.dumps(item["content"], sort_keys=True))


def _normalize_summary(summary: Any) -> dict[str, Any] | None:
    if summary is None:
        return None
    return {
        "session_id": summary.session_id,
        "summary_text": _normalize_summary_text(summary.summary_text),
        "original_event_count": summary.original_event_count,
        "compressed_event_count": summary.compressed_event_count,
        "summary_timestamp": "<timestamp>" if summary.summary_timestamp else None,
        "version": summary.metadata.get("version", "unsupported"),
        "metadata": _normalize_json_value(summary.metadata),
    }


def _normalize_summary_text(text: str) -> str:
    return " ".join(text.split())


def _normalize_content(content: Content | None) -> list[dict[str, Any]]:
    if not content or not content.parts:
        return []

    parts = []
    for part in content.parts:
        if part.text is not None:
            parts.append({"type": "text", "text": part.text})
        elif part.function_call is not None:
            parts.append({
                "type": "function_call",
                "name": part.function_call.name,
                "args": _normalize_json_value(part.function_call.args or {}),
            })
        elif part.function_response is not None:
            parts.append({
                "type": "function_response",
                "name": part.function_response.name,
                "response": _normalize_json_value(part.function_response.response or {}),
            })
    return parts


def _normalize_json_value(value: Any) -> Any:
    if value == "":
        return None
    if isinstance(value, dict):
        return {key: _normalize_json_value(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, set):
        return sorted(_normalize_json_value(item) for item in value)
    return value


def _snapshot_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    summary = snapshot["summary"] or {}
    return {
        "session_id": snapshot["session_id"],
        "event_count": len(snapshot["events"]),
        "historical_event_count": len(snapshot["historical_events"]),
        "state_key_count": len(snapshot["state"]),
        "memory_query_count": len(snapshot["memory"]),
        "has_summary": snapshot["summary"] is not None,
        "summary_session_id": summary.get("session_id"),
        "summary_version": summary.get("version"),
        "conversation_count": snapshot["conversation_count"],
    }


def _compare_values(left: Any, right: Any, path: str = "$") -> list[dict[str, Any]]:
    if type(left) is not type(right):
        return [{"path": path, "left": left, "right": right, "reason": "type_mismatch"}]

    if isinstance(left, dict):
        diffs = []
        for key in sorted(set(left) | set(right)):
            child_path = f"{path}.{key}"
            if key not in left:
                diffs.append({"path": child_path, "left": None, "right": right[key], "reason": "missing_left"})
            elif key not in right:
                diffs.append({"path": child_path, "left": left[key], "right": None, "reason": "missing_right"})
            else:
                diffs.extend(_compare_values(left[key], right[key], child_path))
        return diffs

    if isinstance(left, list):
        diffs = []
        shared_length = min(len(left), len(right))
        for index in range(shared_length):
            diffs.extend(_compare_values(left[index], right[index], f"{path}[{index}]"))
        if len(left) != len(right):
            diffs.append({"path": f"{path}.length", "left": len(left), "right": len(right), "reason": "length"})
        return diffs

    if left != right:
        return [{"path": path, "left": left, "right": right, "reason": "value_mismatch"}]
    return []


def _split_allowed_diffs(diffs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    allowed = []
    unallowed = []
    for diff in diffs:
        explanation = _allowed_diff_explanation(diff["field_path"])
        if explanation:
            allowed.append({**diff, "allowed_diff": explanation})
        else:
            unallowed.append(diff)
    return allowed, unallowed


def _format_diff(diff: dict[str, Any], left_backend: str, right_backend: str) -> dict[str, Any]:
    path = diff["path"]
    return {
        "severity": _diff_severity(path),
        "field_path": path,
        "event_index": _event_index_from_path(path),
        "difference_type": diff["reason"],
        f"value_{left_backend}": diff["left"],
        f"value_{right_backend}": diff["right"],
    }


def _diff_severity(path: str) -> str:
    if path.startswith("$.summary.session_id") or path == "$.summary":
        return "CRITICAL"
    if path.startswith("$.events") or path.startswith("$.historical_events"):
        return "ERROR"
    if path.startswith("$.summary"):
        return "ERROR"
    if path.startswith("$.state") or path.startswith("$.memory"):
        return "WARNING"
    return "INFO"


def _event_index_from_path(path: str) -> int | None:
    for prefix in ("$.events[", "$.historical_events["):
        if path.startswith(prefix):
            index_text = path.removeprefix(prefix).split("]", maxsplit=1)[0]
            if index_text.isdigit():
                return int(index_text)
    return None


def _allowed_diff_explanation(path: str) -> str | None:
    for pattern, explanation in ALLOWED_DIFFS.items():
        if _path_matches(pattern, path):
            return explanation
    return None


def _path_matches(pattern: str, path: str) -> bool:
    if "[*]" not in pattern:
        return pattern == path
    prefix, suffix = pattern.split("[*]", maxsplit=1)
    return path.startswith(prefix + "[") and path.endswith(suffix)


def _write_report(report: list[dict[str, Any]]) -> None:
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _mutation_specs() -> dict[str, tuple[Callable[[dict[str, Any]], None], str]]:
    return {
        "single_turn_text": (_mutate_first_event_text, "$.events[0].content[0].text"),
        "multi_turn_text": (_mutate_event_order, "$.events[0].author"),
        "state_update_overwrite": (_mutate_session_state, "$.state.budget"),
        "scoped_state_update": (_mutate_scoped_state, "$.state.user:locale"),
        "tool_call_conversation": (_mutate_tool_args, "$.events[1].content[0].args.date"),
        "memory_store_and_search": (_mutate_memory_result, "$.memory[0].results.length"),
        "summary_long_conversation": (_mutate_missing_summary, "$.summary"),
        "summary_generate_and_update": (_mutate_summary_overwrite_version, "$.summary.version"),
        "summary_event_truncation": (_mutate_summary_session_owner, "$.summary.session_id"),
        "duplicate_event_recovery": (_mutate_duplicate_event, "$.events.length"),
    }


def _mutate_first_event_text(snapshot: dict[str, Any]) -> None:
    snapshot["events"][0]["content"][0]["text"] = "corrupted text"


def _mutate_event_order(snapshot: dict[str, Any]) -> None:
    snapshot["events"][0], snapshot["events"][1] = snapshot["events"][1], snapshot["events"][0]


def _mutate_session_state(snapshot: dict[str, Any]) -> None:
    snapshot["state"]["budget"] = 999


def _mutate_scoped_state(snapshot: dict[str, Any]) -> None:
    snapshot["state"]["user:locale"] = "broken-locale"


def _mutate_tool_args(snapshot: dict[str, Any]) -> None:
    snapshot["events"][1]["content"][0]["args"]["date"] = "2099-01-01"


def _mutate_memory_result(snapshot: dict[str, Any]) -> None:
    snapshot["memory"][0]["results"].append({
        "author": "agent",
        "content": [{"type": "text", "text": "dirty memory"}],
        "timestamp": "<timestamp>",
    })


def _mutate_missing_summary(snapshot: dict[str, Any]) -> None:
    snapshot["summary"] = None


def _mutate_summary_overwrite_version(snapshot: dict[str, Any]) -> None:
    snapshot["summary"]["version"] = 1


def _mutate_summary_session_owner(snapshot: dict[str, Any]) -> None:
    snapshot["summary"]["session_id"] = "wrong-session"


def _mutate_duplicate_event(snapshot: dict[str, Any]) -> None:
    snapshot["events"].append(deepcopy(snapshot["events"][-1]))


@pytest.mark.parametrize("replay_case", _replay_cases(), ids=lambda replay_case: replay_case.case_id)
async def test_replay_case_mutations_are_detected(replay_case: ReplayCase):
    mutation, expected_path = _mutation_specs()[replay_case.case_id]
    backend = await _make_in_memory_backend()
    try:
        baseline = await _run_case(backend, replay_case)
        corrupted = deepcopy(baseline)
        mutation(corrupted)

        diffs = _compare_values(baseline, corrupted)
        formatted_diffs = [_format_diff(diff, "expected", "corrupted") for diff in diffs]
        assert any(diff["field_path"] == expected_path for diff in formatted_diffs)

        if replay_case.case_id in {
            "summary_long_conversation",
            "summary_generate_and_update",
            "summary_event_truncation",
        }:
            assert any(diff["severity"] in {"ERROR", "CRITICAL"} for diff in formatted_diffs)
    finally:
        await backend.memory_service.close()
        await backend.session_service.close()


async def test_session_replay_consistency_across_backends():
    backend_factories = _selected_backend_factories()
    report: list[dict[str, Any]] = []

    for replay_case in _replay_cases():
        backends = [await factory() for factory in backend_factories]
        try:
            snapshots = {}
            for backend in backends:
                snapshots[backend.name] = await _run_case(backend, replay_case)

            baseline = backends[0].name
            comparisons = []
            for backend in backends[1:]:
                diffs = _compare_values(snapshots[baseline], snapshots[backend.name])
                formatted_diffs = [_format_diff(diff, baseline, backend.name) for diff in diffs]
                allowed_diffs, unallowed_diffs = _split_allowed_diffs(formatted_diffs)
                comparisons.append({
                    "left_backend": baseline,
                    "right_backend": backend.name,
                    "passed": not unallowed_diffs,
                    "allowed_diffs": allowed_diffs,
                    "diffs": unallowed_diffs,
                })

            case_diffs = [diff for comparison in comparisons for diff in comparison["diffs"]]
            report.append({
                "case_id": replay_case.case_id,
                "description": replay_case.description,
                "session_id": replay_case.session_id,
                "backends": list(snapshots),
                "backend_summaries": {
                    backend_name: _snapshot_summary(snapshot) for backend_name, snapshot in snapshots.items()
                },
                "comparisons": comparisons,
                "summary": {
                    "passed": not case_diffs,
                    "total_diffs": sum(len(comparison["diffs"]) + len(comparison["allowed_diffs"])
                                       for comparison in comparisons),
                    "unallowed_diffs": len(case_diffs),
                    "critical_diffs": sum(1 for diff in case_diffs if diff["severity"] == "CRITICAL"),
                },
            })
        finally:
            for backend in backends:
                await backend.memory_service.close()
                await backend.session_service.close()

    _write_report(report)
    unallowed_diffs = [
        diff
        for case_result in report
        for comparison in case_result["comparisons"]
        for diff in comparison["diffs"]
    ]
    assert unallowed_diffs == []
