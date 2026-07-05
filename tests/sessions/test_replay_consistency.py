"""Replay consistency harness for Session / Memory / Summary backends."""

from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import pytest

from trpc_agent_sdk.abc import MemoryServiceConfig
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.memory import InMemoryMemoryService
from trpc_agent_sdk.memory import RedisMemoryService
from trpc_agent_sdk.memory import SqlMemoryService
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import LlmRequest
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
from trpc_agent_sdk.types import State


APP_NAME = "replay-app"
USER_ID = "replay-user"
SUMMARY_MARKER = "Previous conversation summary: "


@dataclass(frozen=True)
class ReplayOp:
    """One normalized input operation for the replay harness."""

    kind: str
    text: str | None = None
    author: str = "assistant"
    state_delta: dict[str, Any] | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_response: dict[str, Any] | None = None
    session_id: str | None = None
    query: str | None = None


@dataclass(frozen=True)
class ReplayCase:
    """A standard replay case executed against every backend pair."""

    case_id: str
    operations: tuple[ReplayOp, ...]
    session_config: SessionServiceConfig | None = None


@dataclass
class ReplayBackend:
    """Concrete session and memory services under test."""

    name: str
    session_service: Any
    memory_service: Any
    summary_revision: int = 0

    async def close(self) -> None:
        await self.memory_service.close()
        await self.session_service.close()


class _DeterministicSummaryModel(LLMModel):
    """Small deterministic model so summary tests never call a provider."""

    def __init__(self) -> None:
        super().__init__(model_name="replay-summary-model")

    @classmethod
    def supported_models(cls) -> list[str]:
        return ["replay-summary-model"]

    async def _generate_async_impl(self,
                                   request: LlmRequest,
                                   stream: bool = False,
                                   ctx: Any = None):
        del stream, ctx
        prompt = request.contents[-1].parts[0].text if request.contents else ""
        digest = " ".join(re.findall(r"[A-Za-z0-9_-]+", prompt))[-180:]
        yield LlmResponse(content=Content(role="model", parts=[Part.from_text(text=f"summary::{digest}")]))


def _session_config(**overrides: Any) -> SessionServiceConfig:
    config = SessionServiceConfig(**overrides)
    config.clean_ttl_config()
    return config


def _memory_config() -> MemoryServiceConfig:
    config = MemoryServiceConfig(enabled=True)
    config.clean_ttl_config()
    return config


def _summary_manager() -> SummarizerSessionManager:
    summarizer = SessionSummarizer(
        model=_DeterministicSummaryModel(),
        keep_recent_count=2,
        check_summarizer_functions=[lambda session: bool(session.events)],
    )
    return SummarizerSessionManager(model=_DeterministicSummaryModel(), summarizer=summarizer, auto_summarize=True)


def _make_event(case_id: str, event_index: int, op: ReplayOp) -> Event:
    parts: list[Part] = []
    if op.text is not None:
        parts.append(Part.from_text(text=op.text))
    if op.kind == "tool_call":
        parts.append(Part(function_call=FunctionCall(name=op.tool_name or "tool", args=op.tool_args or {})))
    if op.kind == "tool_response":
        parts.append(
            Part(function_response=FunctionResponse(name=op.tool_name or "tool", response=op.tool_response or {})))

    return Event(
        id=f"{case_id}-event-{event_index}",
        invocation_id=f"{case_id}-inv-{event_index}",
        author=op.author,
        content=Content(role="user" if op.author == "user" else "model", parts=parts),
        actions=EventActions(state_delta=op.state_delta or {}),
        timestamp=1_800_000_000.0 + event_index,
    )


def _single_turn_case() -> ReplayCase:
    return ReplayCase(
        "single_turn",
        (
            ReplayOp(kind="append", author="user", text="hello agent"),
            ReplayOp(kind="append", author="assistant", text="hello user"),
            ReplayOp(kind="store_memory", query="hello"),
        ),
    )


def _multi_turn_case() -> ReplayCase:
    return ReplayCase(
        "multi_turn",
        (
            ReplayOp(kind="append", author="user", text="plan a weekend trip"),
            ReplayOp(kind="append", author="assistant", text="what city do you prefer"),
            ReplayOp(kind="append", author="user", text="hangzhou with museums"),
            ReplayOp(kind="append", author="assistant", text="museum route prepared"),
            ReplayOp(kind="store_memory", query="museum"),
        ),
    )


def _tool_call_case() -> ReplayCase:
    return ReplayCase(
        "tool_call",
        (
            ReplayOp(kind="append", author="user", text="weather in shenzhen"),
            ReplayOp(kind="tool_call", author="assistant", tool_name="weather", tool_args={"city": "shenzhen"}),
            ReplayOp(kind="tool_response", author="tool", tool_name="weather", tool_response={"temp": 31}),
            ReplayOp(kind="append", author="assistant", text="shenzhen is warm at 31c"),
            ReplayOp(kind="store_memory", query="shenzhen"),
        ),
    )


def _state_update_case() -> ReplayCase:
    return ReplayCase(
        "state_updates",
        (
            ReplayOp(kind="append", author="user", text="remember profile"),
            ReplayOp(kind="append",
                     author="assistant",
                     text="profile saved",
                     state_delta={
                         "topic": "profile",
                         f"{State.USER_PREFIX}city": "shenzhen",
                         f"{State.APP_PREFIX}region": "south",
                     }),
            ReplayOp(kind="append",
                     author="assistant",
                     text="profile updated",
                     state_delta={
                         "topic": "travel",
                         f"{State.USER_PREFIX}city": "hangzhou",
                     }),
        ),
    )


def _memory_case() -> ReplayCase:
    return ReplayCase(
        "memory_search",
        (
            ReplayOp(kind="append", author="user", text="my coffee preference is oat latte"),
            ReplayOp(kind="append", author="assistant", text="I will remember oat latte"),
            ReplayOp(kind="store_memory", query="oat latte"),
        ),
    )


def _summary_generation_case() -> ReplayCase:
    return ReplayCase(
        "summary_generation",
        (
            ReplayOp(kind="append", author="user", text="long topic starts"),
            ReplayOp(kind="append", author="assistant", text="first detail recorded"),
            ReplayOp(kind="append", author="user", text="second detail is important"),
            ReplayOp(kind="append", author="assistant", text="third detail is also important"),
            ReplayOp(kind="summary"),
            ReplayOp(kind="append", author="user", text="summary needs new fourth detail"),
            ReplayOp(kind="append", author="assistant", text="fourth detail recorded"),
            ReplayOp(kind="summary"),
        ),
        session_config=_session_config(store_historical_events=True),
    )


def _summary_truncation_case() -> ReplayCase:
    return ReplayCase(
        "summary_truncation",
        (
            ReplayOp(kind="append", author="user", text="turn one"),
            ReplayOp(kind="append", author="assistant", text="answer one"),
            ReplayOp(kind="append", author="user", text="turn two"),
            ReplayOp(kind="append", author="assistant", text="answer two"),
            ReplayOp(kind="summary"),
            ReplayOp(kind="append", author="user", text="continue after summary"),
            ReplayOp(kind="append", author="assistant", text="continued with summary context"),
        ),
        session_config=_session_config(max_events=3, num_recent_events=3, store_historical_events=True),
    )


def _duplicate_logical_write_case() -> ReplayCase:
    return ReplayCase(
        "duplicate_logical_write",
        (
            ReplayOp(kind="append", author="user", text="submit ticket"),
            ReplayOp(kind="append", author="assistant", text="ticket accepted"),
            ReplayOp(kind="append", author="assistant", text="ticket accepted"),
            ReplayOp(kind="store_memory", query="ticket"),
        ),
    )


def _cross_session_case() -> ReplayCase:
    return ReplayCase(
        "cross_session_isolation",
        (
            ReplayOp(kind="append", author="user", text="primary session fact"),
            ReplayOp(kind="append", author="assistant", text="primary saved", state_delta={"scope": "primary"}),
            ReplayOp(kind="create_session", session_id="secondary"),
            ReplayOp(kind="append", session_id="secondary", author="user", text="secondary session fact"),
            ReplayOp(kind="append", session_id="secondary", author="assistant", text="secondary saved"),
            ReplayOp(kind="store_memory", query="session"),
        ),
    )


def _recovery_like_case() -> ReplayCase:
    return ReplayCase(
        "recovery_after_failed_step",
        (
            ReplayOp(kind="append", author="user", text="start transaction"),
            ReplayOp(kind="failed_write", author="assistant", text="this event must not persist"),
            ReplayOp(kind="append", author="assistant", text="recovered cleanly", state_delta={"status": "clean"}),
            ReplayOp(kind="store_memory", query="recovered"),
        ),
    )


REPLAY_CASES: tuple[ReplayCase, ...] = (
    _single_turn_case(),
    _multi_turn_case(),
    _tool_call_case(),
    _state_update_case(),
    _memory_case(),
    _summary_generation_case(),
    _summary_truncation_case(),
    _duplicate_logical_write_case(),
    _cross_session_case(),
    _recovery_like_case(),
)


async def _make_in_memory_backend(case: ReplayCase) -> ReplayBackend:
    session_config = case.session_config.model_copy(deep=True) if case.session_config else _session_config()
    return ReplayBackend(
        name="in_memory",
        session_service=InMemorySessionService(summarizer_manager=_summary_manager(), session_config=session_config),
        memory_service=InMemoryMemoryService(memory_service_config=_memory_config()),
    )


async def _make_sqlite_backend(case: ReplayCase, tmp_path: Path) -> ReplayBackend:
    session_config = case.session_config.model_copy(deep=True) if case.session_config else _session_config()
    db_path = tmp_path / f"{case.case_id}.sqlite"
    backend = ReplayBackend(
        name="sqlite",
        session_service=SqlSessionService(
            db_url=f"sqlite:///{db_path}",
            summarizer_manager=_summary_manager(),
            session_config=session_config,
            is_async=False,
        ),
        memory_service=SqlMemoryService(
            db_url=f"sqlite:///{db_path}",
            memory_service_config=_memory_config(),
            is_async=False,
        ),
    )
    await backend.session_service._sql_storage.create_sql_engine()
    await backend.memory_service._sql_storage.create_sql_engine()
    return backend


async def _make_sql_backend(case: ReplayCase, db_url: str) -> ReplayBackend:
    session_config = case.session_config.model_copy(deep=True) if case.session_config else _session_config()
    backend = ReplayBackend(
        name="sql",
        session_service=SqlSessionService(
            db_url=db_url,
            summarizer_manager=_summary_manager(),
            session_config=session_config,
            is_async=False,
        ),
        memory_service=SqlMemoryService(
            db_url=db_url,
            memory_service_config=_memory_config(),
            is_async=False,
        ),
    )
    await backend.session_service._sql_storage.create_sql_engine()
    await backend.memory_service._sql_storage.create_sql_engine()
    return backend


async def _make_redis_backend(case: ReplayCase, redis_url: str) -> ReplayBackend:
    session_config = case.session_config.model_copy(deep=True) if case.session_config else _session_config()
    return ReplayBackend(
        name="redis",
        session_service=RedisSessionService(
            db_url=redis_url,
            summarizer_manager=_summary_manager(),
            session_config=session_config,
            is_async=False,
        ),
        memory_service=RedisMemoryService(
            db_url=redis_url,
            memory_service_config=_memory_config(),
            is_async=False,
        ),
    )


async def _run_case(case: ReplayCase, backend: ReplayBackend) -> dict[str, Any]:
    sessions: dict[str, Session] = {}
    session_ids = ["primary"]
    sessions["primary"] = await backend.session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=f"{case.case_id}-primary",
        state={"case_id": case.case_id},
    )

    memory_queries: list[str] = []
    event_index = 0
    for op in case.operations:
        session_key = op.session_id or "primary"
        if op.kind == "create_session":
            if session_key not in sessions:
                sessions[session_key] = await backend.session_service.create_session(
                    app_name=APP_NAME,
                    user_id=USER_ID,
                    session_id=f"{case.case_id}-{session_key}",
                    state={"case_id": case.case_id, "session_key": session_key},
                )
                session_ids.append(session_key)
            continue

        session = sessions[session_key]
        if op.kind in {"append", "tool_call", "tool_response"}:
            event_index += 1
            event = _make_event(case.case_id, event_index, op)
            await backend.session_service.append_event(session, event)
            continue

        if op.kind == "failed_write":
            event_index += 1
            _make_event(case.case_id, event_index, op)
            continue

        if op.kind == "store_memory":
            for key in session_ids:
                stored = await backend.session_service.get_session(
                    app_name=APP_NAME,
                    user_id=USER_ID,
                    session_id=sessions[key].id,
                )
                assert stored is not None
                await backend.memory_service.store_session(stored)
            if op.query:
                memory_queries.append(op.query)
            continue

        if op.kind == "summary":
            await backend.session_service.create_session_summary(session)
            summary_text = await backend.session_service.get_session_summary(session)
            if summary_text:
                backend.summary_revision += 1
            continue

        raise AssertionError(f"Unknown replay operation: {op.kind}")

    return await _snapshot_backend(case, backend, session_ids, memory_queries)


async def _snapshot_backend(case: ReplayCase,
                            backend: ReplayBackend,
                            session_ids: Iterable[str],
                            memory_queries: Iterable[str]) -> dict[str, Any]:
    sessions_snapshot: dict[str, Any] = {}
    summary_snapshot: dict[str, Any] = {}
    for key in session_ids:
        session = await backend.session_service.get_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=f"{case.case_id}-{key}",
        )
        if session is None:
            sessions_snapshot[key] = None
            summary_snapshot[key] = None
            continue
        sessions_snapshot[key] = _normalize_session(session)
        summary_snapshot[key] = await _normalize_summary(backend, session)

    memory_snapshot: dict[str, Any] = {}
    for query in memory_queries:
        response = await backend.memory_service.search_memory(
            key=f"{APP_NAME}/{USER_ID}",
            query=query,
            limit=100,
        )
        memory_snapshot[query] = _normalize_memory_response(response.model_dump(mode="json"))

    return {
        "backend": backend.name,
        "case_id": case.case_id,
        "sessions": sessions_snapshot,
        "memory": memory_snapshot,
        "summaries": summary_snapshot,
    }


def _normalize_session(session: Session) -> dict[str, Any]:
    return {
        "id": session.id,
        "app_name": session.app_name,
        "user_id": session.user_id,
        "state": _sort_json(session.state),
        "conversation_count": session.conversation_count,
        "events": [_normalize_event(event, index) for index, event in enumerate(session.events)],
        "historical_events": [_normalize_event(event, index) for index, event in enumerate(session.historical_events)],
    }


async def _normalize_summary(backend: ReplayBackend, session: Session) -> dict[str, Any] | None:
    summary_text = await backend.session_service.get_session_summary(session)
    manager = backend.session_service.summarizer_manager
    summary = await manager.get_session_summary(session) if manager else None
    if not summary_text or not summary:
        return None
    return {
        "summary_id": f"{summary.session_id}:summary",
        "session_id": summary.session_id,
        "version": backend.summary_revision,
        "summary_text": _normalize_summary_text(summary.summary_text),
        "updated_at": "<timestamp>",
        "original_event_count": summary.original_event_count,
        "compressed_event_count": summary.compressed_event_count,
    }


def _normalize_event(event: Event, index: int) -> dict[str, Any]:
    return {
        "index": index,
        "id": "<summary-event-id>" if event.is_summary_event() else event.id,
        "invocation_id": event.invocation_id,
        "author": event.author,
        "content": _normalize_content(event.content.model_dump(mode="json") if event.content else None),
        "actions": _sort_json(event.actions.model_dump(mode="json", exclude_none=True)),
        "long_running_tool_ids": sorted(event.long_running_tool_ids or []),
        "branch": event.branch,
        "visible": event.visible,
        "model_visible": event.is_model_visible(),
        "summary_event": event.is_summary_event(),
        "timestamp": "<timestamp>",
        "partial": event.partial,
        "turn_complete": event.turn_complete,
        "error_code": event.error_code,
        "error_message": event.error_message,
        "interrupted": event.interrupted,
    }


def _normalize_content(content: dict[str, Any] | None) -> dict[str, Any] | None:
    if content is None:
        return None
    normalized = _sort_json(content)
    for part in normalized.get("parts", []):
        if "text" in part and isinstance(part["text"], str) and part["text"].startswith(SUMMARY_MARKER):
            part["text"] = SUMMARY_MARKER + _normalize_summary_text(part["text"][len(SUMMARY_MARKER):])
    return normalized


def _normalize_memory_response(response: dict[str, Any]) -> list[dict[str, Any]]:
    memories = []
    for memory in response.get("memories", []):
        memories.append({
            "author": memory.get("author"),
            "content": _normalize_content(memory.get("content")),
            "timestamp": "<timestamp>" if memory.get("timestamp") else None,
        })
    return sorted(memories, key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False))


def _normalize_summary_text(text: str) -> str:
    return " ".join(text.split()).strip()


def _sort_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sort_json(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_sort_json(item) for item in value]
    if isinstance(value, set):
        return sorted(value)
    return value


def _diff_values(expected: Any,
                 actual: Any,
                 path: str = "$",
                 allowed_diff_rules: tuple[dict[str, str], ...] = ()) -> list[dict[str, Any]]:
    if expected == actual:
        return []
    if _is_allowed_diff(path, expected, actual, allowed_diff_rules):
        return [{
            "path": path,
            "expected": expected,
            "actual": actual,
            "allowed": True,
            "reason": _allowed_diff_reason(path, allowed_diff_rules),
        }]

    if isinstance(expected, dict) and isinstance(actual, dict):
        diffs: list[dict[str, Any]] = []
        for key in sorted(set(expected) | set(actual)):
            diffs.extend(_diff_values(expected.get(key), actual.get(key), f"{path}.{key}", allowed_diff_rules))
        return diffs

    if isinstance(expected, list) and isinstance(actual, list):
        diffs = []
        common = min(len(expected), len(actual))
        for index in range(common):
            diffs.extend(_diff_values(expected[index], actual[index], f"{path}[{index}]", allowed_diff_rules))
        for index in range(common, len(expected)):
            diffs.append({"path": f"{path}[{index}]", "expected": expected[index], "actual": "<missing>"})
        for index in range(common, len(actual)):
            diffs.append({"path": f"{path}[{index}]", "expected": "<missing>", "actual": actual[index]})
        return diffs

    return [{"path": path, "expected": expected, "actual": actual}]


def _is_allowed_diff(path: str, expected: Any, actual: Any, rules: tuple[dict[str, str], ...]) -> bool:
    del expected, actual
    return any(re.fullmatch(rule["path_pattern"], path) for rule in rules)


def _allowed_diff_reason(path: str, rules: tuple[dict[str, str], ...]) -> str:
    for rule in rules:
        if re.fullmatch(rule["path_pattern"], path):
            return rule["reason"]
    return ""


ALLOWED_DIFF_RULES: tuple[dict[str, str], ...] = ()


def _build_case_report(case: ReplayCase, snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    baseline = snapshots[0]
    baseline_comparable = _comparable_snapshot(baseline)
    backend_reports = []
    differences: list[dict[str, Any]] = []
    allowed_diffs: list[dict[str, Any]] = []

    for snapshot in snapshots:
        backend_reports.append({"backend": snapshot["backend"], "snapshot": snapshot})
        if snapshot is baseline:
            continue
        for diff in _diff_values(baseline_comparable, _comparable_snapshot(snapshot), "$", ALLOWED_DIFF_RULES):
            diff["case_id"] = case.case_id
            diff["expected_backend"] = baseline["backend"]
            diff["actual_backend"] = snapshot["backend"]
            diff.update(_diff_location(diff["path"], baseline_comparable, _comparable_snapshot(snapshot)))
            if diff.get("allowed"):
                allowed_diffs.append(diff)
            else:
                differences.append(diff)

    return {
        "case_id": case.case_id,
        "backends": [snapshot["backend"] for snapshot in snapshots],
        "differences": differences,
        "allowed_diff": allowed_diffs,
        "backend_snapshots": backend_reports,
    }


def _diff_location(path: str, expected: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    """Add concrete replay coordinates so reports are actionable."""
    location: dict[str, Any] = {}

    session_match = re.search(r"\.sessions\.([^.[]+)", path)
    if session_match:
        session_key = session_match.group(1)
        location["session_key"] = session_key
        location["session_id"] = _lookup_session_id(expected, actual, session_key)

    event_match = re.search(r"\.(events|historical_events)\[(\d+)\]", path)
    if event_match:
        location["event_collection"] = event_match.group(1)
        location["event_index"] = int(event_match.group(2))

    summary_match = re.search(r"\.summaries\.([^.[]+)", path)
    if summary_match:
        session_key = summary_match.group(1)
        location["session_key"] = session_key
        location["session_id"] = _lookup_session_id(expected, actual, session_key)
        location["summary_id"] = _lookup_summary_id(expected, actual, session_key)

    memory_match = re.search(r"\.memory\.([^.[]+)", path)
    if memory_match:
        location["memory_query"] = memory_match.group(1)

    return location


def _lookup_session_id(expected: dict[str, Any], actual: dict[str, Any], session_key: str) -> str | None:
    for snapshot in (expected, actual):
        session = snapshot.get("sessions", {}).get(session_key)
        if isinstance(session, dict):
            return session.get("id")
    return None


def _lookup_summary_id(expected: dict[str, Any], actual: dict[str, Any], session_key: str) -> str | None:
    for snapshot in (expected, actual):
        summary = snapshot.get("summaries", {}).get(session_key)
        if isinstance(summary, dict):
            return summary.get("summary_id")
    return None


def _comparable_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": snapshot["case_id"],
        "sessions": snapshot["sessions"],
        "memory": snapshot["memory"],
        "summaries": snapshot["summaries"],
    }


async def _run_consistency_report(case: ReplayCase, tmp_path: Path) -> dict[str, Any]:
    backends = [
        await _make_in_memory_backend(case),
        await _make_sqlite_backend(case, tmp_path),
    ]
    try:
        snapshots = [await _run_case(case, backend) for backend in backends]
        return _build_case_report(case, snapshots)
    finally:
        for backend in backends:
            await backend.close()


async def _run_in_memory_only_report(case: ReplayCase) -> dict[str, Any]:
    backend = await _make_in_memory_backend(case)
    try:
        snapshot = await _run_case(case, backend)
        return _build_case_report(case, [snapshot])
    finally:
        await backend.close()


def _write_report(path: Path, case_reports: list[dict[str, Any]]) -> dict[str, Any]:
    report = {
        "report_name": "session_memory_summary_diff_report",
        "mode": "lightweight",
        "backends": ["in_memory", "sqlite"],
        "case_count": len(case_reports),
        "normalization": [
            "timestamps are replaced with <timestamp>",
            "generated summary event ids are replaced with <summary-event-id>",
            "memory results are sorted after timestamp normalization",
            "summary text is whitespace-normalized",
        ],
        "cases": case_reports,
    }
    path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    return report


@pytest.mark.parametrize("case", REPLAY_CASES, ids=[case.case_id for case in REPLAY_CASES])
async def test_replay_case_consistency_across_in_memory_and_sqlite(case: ReplayCase, tmp_path: Path) -> None:
    report = await _run_consistency_report(case, tmp_path)

    assert report["differences"] == []


async def test_replay_consistency_writes_json_diff_report(tmp_path: Path) -> None:
    case_reports = [await _run_consistency_report(case, tmp_path) for case in REPLAY_CASES]

    report = _write_report(tmp_path / "session_memory_summary_diff_report.json", case_reports)

    assert report["case_count"] == 10
    assert {case["case_id"] for case in report["cases"]} == {case.case_id for case in REPLAY_CASES}
    assert all(case["differences"] == [] for case in report["cases"])


async def test_replay_summary_generation_case_records_update_metadata(tmp_path: Path) -> None:
    report = await _run_consistency_report(_summary_generation_case(), tmp_path)

    for backend_report in report["backend_snapshots"]:
        summary = backend_report["snapshot"]["summaries"]["primary"]
        assert summary["summary_text"].startswith("summary::")
        assert summary["version"] == 2
        assert summary["updated_at"] == "<timestamp>"
        assert summary["session_id"] == "summary_generation-primary"
        assert summary["summary_id"] == "summary_generation-primary:summary"


async def test_replay_harness_supports_in_memory_only_lightweight_mode() -> None:
    report = await _run_in_memory_only_report(_single_turn_case())

    assert report["backends"] == ["in_memory"]
    assert report["differences"] == []


def _event_text_mutation(snapshot: dict[str, Any]) -> None:
    snapshot["sessions"]["primary"]["events"][0]["content"]["parts"][0]["text"] = "mutated text"


def _event_order_mutation(snapshot: dict[str, Any]) -> None:
    events = snapshot["sessions"]["primary"]["events"]
    if len(events) >= 2:
        events[0], events[1] = events[1], events[0]
    else:
        events.append(copy.deepcopy(events[0]))


def _event_missing_mutation(snapshot: dict[str, Any]) -> None:
    snapshot["sessions"]["primary"]["events"].pop()


def _state_dirty_mutation(snapshot: dict[str, Any]) -> None:
    snapshot["sessions"]["primary"]["state"]["dirty"] = True


def _memory_missing_mutation(snapshot: dict[str, Any]) -> None:
    query = next(iter(snapshot["memory"]))
    snapshot["memory"][query] = []


def _memory_text_mutation(snapshot: dict[str, Any]) -> None:
    query = next(iter(snapshot["memory"]))
    snapshot["memory"][query][0]["content"]["parts"][0]["text"] = "wrong memory"


def _summary_lost_mutation(snapshot: dict[str, Any]) -> None:
    snapshot["summaries"]["primary"] = None


def _summary_overwrite_mutation(snapshot: dict[str, Any]) -> None:
    snapshot["summaries"]["primary"]["summary_text"] = "wrong summary"


def _summary_wrong_session_mutation(snapshot: dict[str, Any]) -> None:
    snapshot["summaries"]["primary"]["session_id"] = "wrong-session"


def _duplicate_event_mutation(snapshot: dict[str, Any]) -> None:
    snapshot["sessions"]["primary"]["events"].append(copy.deepcopy(snapshot["sessions"]["primary"]["events"][-1]))


def _secondary_state_mutation(snapshot: dict[str, Any]) -> None:
    snapshot["sessions"]["secondary"]["state"]["session_key"] = "wrong-secondary"


def _recovered_event_mutation(snapshot: dict[str, Any]) -> None:
    failed_event = copy.deepcopy(snapshot["sessions"]["primary"]["events"][0])
    failed_event["id"] = "unexpected-failed-write"
    failed_event["content"]["parts"][0]["text"] = "this event must not persist"
    snapshot["sessions"]["primary"]["events"].insert(1, failed_event)


INJECTED_INCONSISTENCIES: tuple[tuple[ReplayCase, str, Callable[[dict[str, Any]], None], str], ...] = (
    (_single_turn_case(), "event_text", _event_text_mutation, ".events[0].content.parts[0].text"),
    (_multi_turn_case(), "event_order", _event_order_mutation, ".events[0]"),
    (_tool_call_case(), "event_missing", _event_missing_mutation, ".events"),
    (_state_update_case(), "state_dirty", _state_dirty_mutation, ".state.dirty"),
    (_memory_case(), "memory_missing", _memory_missing_mutation, ".memory."),
    (_summary_generation_case(), "summary_lost", _summary_lost_mutation, ".summaries.primary"),
    (_summary_truncation_case(), "summary_overwrite", _summary_overwrite_mutation,
     ".summaries.primary.summary_text"),
    (_duplicate_logical_write_case(), "duplicate_event", _duplicate_event_mutation, ".events"),
    (_cross_session_case(), "cross_session_state", _secondary_state_mutation, ".sessions.secondary.state.session_key"),
    (_recovery_like_case(), "recovered_event", _recovered_event_mutation, ".events"),
)


@pytest.mark.parametrize(
    ("case", "name", "mutate", "expected_path"),
    INJECTED_INCONSISTENCIES,
    ids=[f"{case.case_id}:{name}" for case, name, _, _ in INJECTED_INCONSISTENCIES],
)
async def test_replay_harness_detects_public_injected_inconsistencies(case: ReplayCase,
                                                                     name: str,
                                                                     mutate: Callable[[dict[str, Any]], None],
                                                                     expected_path: str,
                                                                     tmp_path: Path) -> None:
    del name
    backends = [
        await _make_in_memory_backend(case),
        await _make_sqlite_backend(case, tmp_path),
    ]
    try:
        baseline = await _run_case(case, backends[0])
        candidate = await _run_case(case, backends[1])
    finally:
        for backend in backends:
            await backend.close()

    mutate(candidate)

    report = _build_case_report(case, [baseline, candidate])
    paths = [diff["path"] for diff in report["differences"]]

    assert report["differences"]
    assert any(expected_path in path for path in paths)
    assert any("path" in diff and "expected" in diff and "actual" in diff for diff in report["differences"])


@pytest.mark.parametrize(
    ("name", "mutate", "expected_path"),
    [
        ("summary_lost", _summary_lost_mutation, ".summaries.primary"),
        ("summary_overwrite", _summary_overwrite_mutation, ".summaries.primary.summary_text"),
        ("summary_wrong_session", _summary_wrong_session_mutation, ".summaries.primary.session_id"),
    ],
)
async def test_replay_harness_detects_required_summary_failures(name: str,
                                                               mutate: Callable[[dict[str, Any]], None],
                                                               expected_path: str,
                                                               tmp_path: Path) -> None:
    del name
    case = _summary_generation_case()
    backends = [
        await _make_in_memory_backend(case),
        await _make_sqlite_backend(case, tmp_path),
    ]
    try:
        baseline = await _run_case(case, backends[0])
        candidate = await _run_case(case, backends[1])
    finally:
        for backend in backends:
            await backend.close()

    mutate(candidate)
    report = _build_case_report(case, [baseline, candidate])

    assert any(expected_path in diff["path"] for diff in report["differences"])


async def test_replay_diff_report_includes_actionable_location_fields(tmp_path: Path) -> None:
    case = _summary_generation_case()
    backends = [
        await _make_in_memory_backend(case),
        await _make_sqlite_backend(case, tmp_path),
    ]
    try:
        baseline = await _run_case(case, backends[0])
        candidate = await _run_case(case, backends[1])
    finally:
        for backend in backends:
            await backend.close()

    _summary_wrong_session_mutation(candidate)
    report = _build_case_report(case, [baseline, candidate])

    diff = next(diff for diff in report["differences"] if diff["path"].endswith(".summaries.primary.session_id"))
    assert diff["session_id"] == "summary_generation-primary"
    assert diff["summary_id"] == "summary_generation-primary:summary"
    assert diff["expected"] == "summary_generation-primary"
    assert diff["actual"] == "wrong-session"


async def test_sql_integration_replay_backend_is_env_opt_in() -> None:
    sql_url = os.getenv("TRPC_AGENT_REPLAY_SQL_URL")
    if not sql_url:
        pytest.skip("Set TRPC_AGENT_REPLAY_SQL_URL to opt into external SQL replay consistency checks.")

    case = _single_turn_case()
    backends = [await _make_in_memory_backend(case), await _make_sql_backend(case, sql_url)]
    try:
        await backends[1].session_service.delete_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=f"{case.case_id}-primary",
        )
        report = _build_case_report(case, [await _run_case(case, backend) for backend in backends])
    finally:
        for backend in backends:
            await backend.close()

    assert report["differences"] == []


async def test_redis_replay_backend_is_env_opt_in(tmp_path: Path) -> None:
    redis_url = os.getenv("TRPC_AGENT_REPLAY_REDIS_URL")
    if not redis_url:
        pytest.skip("Set TRPC_AGENT_REPLAY_REDIS_URL to opt into Redis replay consistency checks.")

    del tmp_path
    case = _single_turn_case()
    backends = [await _make_in_memory_backend(case), await _make_redis_backend(case, redis_url)]
    try:
        report = _build_case_report(case, [await _run_case(case, backend) for backend in backends])
    finally:
        for backend in backends:
            await backend.close()

    assert report["differences"] == []
