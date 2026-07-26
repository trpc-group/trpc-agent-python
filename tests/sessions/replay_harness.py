#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Reusable execution harness for session, memory, and summary replay traces."""

from __future__ import annotations

import asyncio
import copy
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import AsyncIterator
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

from .replay_cases import APP_NAME
from .replay_cases import OperationKind
from .replay_cases import ReplayCase
from .replay_cases import ExpectedOutcome
from .replay_cases import ReplayOperation
from .replay_cases import SESSION_ID
from .replay_cases import TIMESTAMP_STEP_SECONDS
from .replay_cases import USER_ID

OPERATION_TIMEOUT_SECONDS = 5.0
SERVICE_CLOSE_TIMEOUT_SECONDS = 5.0
SUMMARY_KEEP_RECENT_EVENTS = 2
SUMMARY_PREFIX = "deterministic-summary-v"
SUMMARY_TEXT_LIMIT = 500
SQLITE_BACKEND = "sqlite"
IN_MEMORY_BACKEND = "in_memory"
REDIS_BACKEND = "redis"
MEMORY_RESULT_SUFFIX_START = 2
SQL_STORAGE_ATTRIBUTE = "_sql_storage"
REDIS_STORAGE_ATTRIBUTE = "_redis_storage"
SQL_STORAGE_METHODS = ("commit", "create_sql_engine")
REDIS_STORAGE_METHODS = ("create_db_session", "delete")
STORAGE_CONTRACT_ERROR = "replay storage contract changed"


def _require_storage_contract(service: Any, attribute: str, methods: tuple[str, ...]) -> Any:
    """Resolve an intentional private test seam with a readable drift error."""
    storage = getattr(service, attribute, None)
    missing = list(methods) if storage is None else [name for name in methods if not hasattr(storage, name)]
    if storage is None or missing:
        detail = attribute if storage is None else f"{attribute}.{','.join(missing)}"
        raise AssertionError(f"{STORAGE_CONTRACT_ERROR}: {type(service).__name__} missing {detail}")
    return storage


def _require_sql_storage(service: Any) -> Any:
    return _require_storage_contract(service, SQL_STORAGE_ATTRIBUTE, SQL_STORAGE_METHODS)


def _require_redis_storage(service: Any) -> Any:
    return _require_storage_contract(service, REDIS_STORAGE_ATTRIBUTE, REDIS_STORAGE_METHODS)


class DeterministicSummaryModel:
    """Small deterministic LLM substitute used by the real summarizer."""

    name = "replay-deterministic-summary"

    def __init__(self) -> None:
        self._generation = 0

    async def generate_async(self, request: Any, stream: bool = False, ctx: Any = None) -> AsyncIterator[LlmResponse]:
        del stream, ctx
        self._generation += 1
        prompt = request.contents[0].parts[0].text
        conversation = prompt.split("Conversation:\n", maxsplit=1)[-1].rsplit("\n\nSummary:", maxsplit=1)[0]
        text = f"{SUMMARY_PREFIX}{self._generation}: {conversation.strip()}"[:SUMMARY_TEXT_LIMIT]
        yield LlmResponse(content=Content(parts=[Part.from_text(text=text)]))


class InjectedReplayFailure(RuntimeError):
    """Expected failure used by replay recovery operations only."""


@dataclass(frozen=True)
class ReplayIdentity:
    """Storage identity used to isolate one replay run."""

    app_name: str = APP_NAME
    user_id: str = USER_ID
    session_id: str = SESSION_ID


@dataclass
class BackendBundle:
    """Session and memory services representing one replay backend."""

    name: str
    session_service: Any
    memory_service: Any
    db_url: Optional[str] = None
    identity: ReplayIdentity = ReplayIdentity()

    async def close(self) -> None:
        """Close both services even if the first close fails."""
        cleanup_error = None
        if self.name == REDIS_BACKEND:
            try:
                await asyncio.wait_for(self._cleanup_redis(), SERVICE_CLOSE_TIMEOUT_SECONDS)
            except Exception as error:  # pylint: disable=broad-except
                cleanup_error = error
        close_all = asyncio.gather(self.memory_service.close(), self.session_service.close(), return_exceptions=True)
        results = await asyncio.wait_for(close_all, SERVICE_CLOSE_TIMEOUT_SECONDS)
        errors = [result for result in results if isinstance(result, Exception)]
        if cleanup_error:
            errors.insert(0, cleanup_error)
        if errors:
            raise errors[0]

    async def _cleanup_redis(self) -> None:
        session = await self.session_service.get_session(
            app_name=self.identity.app_name,
            user_id=self.identity.user_id,
            session_id=self.identity.session_id,
        )
        if session is not None:
            key = f"memory:{session.save_key}:{session.id}"
            storage = _require_redis_storage(self.memory_service)
            async with storage.create_db_session() as connection:
                await storage.delete(connection, key)
        await self.session_service.delete_session(
            app_name=self.identity.app_name,
            user_id=self.identity.user_id,
            session_id=self.identity.session_id,
        )


def _session_config() -> SessionServiceConfig:
    config = SessionServiceConfig(store_historical_events=True)
    config.clean_ttl_config()
    return config


def _memory_config() -> MemoryServiceConfig:
    config = MemoryServiceConfig(enabled=True)
    config.clean_ttl_config()
    return config


def _summary_manager() -> SummarizerSessionManager:
    model = DeterministicSummaryModel()
    summarizer = SessionSummarizer(
        model=model,
        check_summarizer_functions=[lambda _: True],
        keep_recent_count=SUMMARY_KEEP_RECENT_EVENTS,
        start_by_user_turn=False,
    )
    return SummarizerSessionManager(model=model, summarizer=summarizer)


async def create_in_memory_backend(identity: Optional[ReplayIdentity] = None) -> BackendBundle:
    """Create lightweight in-memory services."""
    session_service = InMemorySessionService(
        summarizer_manager=_summary_manager(),
        session_config=_session_config(),
    )
    memory_service = InMemoryMemoryService(memory_service_config=_memory_config())
    return BackendBundle(IN_MEMORY_BACKEND, session_service, memory_service, identity=identity or ReplayIdentity())


async def create_sqlite_backend(db_path: Path, identity: Optional[ReplayIdentity] = None) -> BackendBundle:
    """Create file-backed SQLite services and initialize their tables."""
    db_url = f"sqlite:///{db_path.as_posix()}"
    session_service = SqlSessionService(
        db_url=db_url,
        summarizer_manager=_summary_manager(),
        session_config=_session_config(),
        is_async=False,
    )
    memory_service = SqlMemoryService(
        db_url=db_url,
        memory_service_config=_memory_config(),
        is_async=False,
    )
    await _require_sql_storage(session_service).create_sql_engine()
    await _require_sql_storage(memory_service).create_sql_engine()
    return BackendBundle(SQLITE_BACKEND, session_service, memory_service, db_url, identity or ReplayIdentity())


async def create_redis_backend(db_url: str, identity: ReplayIdentity) -> BackendBundle:
    """Create optional Redis integration services."""
    session_service = RedisSessionService(
        db_url=db_url,
        summarizer_manager=_summary_manager(),
        session_config=_session_config(),
        is_async=True,
        decode_responses=True,
    )
    memory_service = RedisMemoryService(
        db_url=db_url,
        memory_service_config=_memory_config(),
        is_async=True,
        decode_responses=True,
    )
    return BackendBundle(REDIS_BACKEND, session_service, memory_service, db_url, identity)


def _event_from_operation(operation: ReplayOperation, operation_index: int, timestamp_origin: float) -> Event:
    payload = operation.payload
    part_type = payload.get("part_type", "text")
    if part_type == "function_call":
        part = Part(function_call=FunctionCall(**payload["value"]))
    elif part_type == "function_response":
        part = Part(function_response=FunctionResponse(**payload["value"]))
    else:
        part = Part.from_text(text=str(payload["text"]))
    return Event(
        id=str(payload["event_id"]),
        invocation_id=f"replay-{operation_index}",
        author=str(payload["author"]),
        content=Content(parts=[part]),
        actions=EventActions(state_delta=dict(payload.get("state_delta", {}))),
        timestamp=timestamp_origin + operation_index * TIMESTAMP_STEP_SECONDS,
    )


class ReplayRunner:
    """Execute one trace against one backend and capture its read model."""

    def __init__(self, backend: BackendBundle) -> None:
        self.backend = backend
        self.session: Optional[Session] = None
        self.memory_results: dict[str, Any] = {}
        self.memory_query_by_key: dict[str, str] = {}
        self.summary_generation = 0
        self.summary_checkpoints: list[dict[str, Any]] = []
        self.failures: list[dict[str, Any]] = []
        self.timestamp_origin = time.time()

    async def run(self, replay_case: ReplayCase) -> dict[str, Any]:
        """Execute all operations and return a backend snapshot."""
        for index, operation in enumerate(replay_case.operations):
            try:
                isolated = _clone_operation(operation)
                await asyncio.wait_for(self._execute(isolated, index), OPERATION_TIMEOUT_SECONDS)
            except InjectedReplayFailure as error:
                self.failures.append({
                    "operation_index": index,
                    "error_type": type(error).__name__,
                    "message": str(error),
                })
        self.session = await asyncio.wait_for(self._read_session(), OPERATION_TIMEOUT_SECONDS)
        if self.session is None:
            raise AssertionError(f"case {replay_case.case_id} did not create a session")
        return await asyncio.wait_for(self.snapshot(replay_case.case_id), OPERATION_TIMEOUT_SECONDS)

    async def _execute(self, operation: ReplayOperation, operation_index: int) -> None:
        handlers = {
            OperationKind.CREATE: self._create,
            OperationKind.APPEND: self._append,
            OperationKind.STORE_MEMORY: self._store_memory,
            OperationKind.SEARCH_MEMORY: self._search_memory,
            OperationKind.SUMMARY: self._summarize,
            OperationKind.UNKNOWN_OUTCOME_RETRY: self._unknown_outcome_retry,
            OperationKind.UNKNOWN_MEMORY_RETRY: self._unknown_memory_retry,
            OperationKind.UNKNOWN_SUMMARY_RETRY: self._unknown_summary_retry,
            OperationKind.BEFORE_CALL_FAILURE: self._before_call_failure,
        }
        await handlers[operation.kind](operation, operation_index)

    async def _create(self, operation: ReplayOperation, operation_index: int) -> None:
        del operation_index
        identity = self.backend.identity
        self.session = await self.backend.session_service.create_session(
            app_name=identity.app_name,
            user_id=identity.user_id,
            session_id=identity.session_id,
            state=dict(operation.payload.get("state", {})),
        )
        _validate_session_scope(self.session, identity)

    async def _append(self, operation: ReplayOperation, operation_index: int) -> None:
        session = self._require_session()
        event = _event_from_operation(operation, operation_index, self.timestamp_origin)
        await self.backend.session_service.append_event(session, event)

    async def _store_memory(self, operation: ReplayOperation, operation_index: int) -> None:
        del operation, operation_index
        session = await self._read_session()
        if session is None:
            raise AssertionError("memory operation requires a session")
        await self.backend.memory_service.store_session(session)

    async def _search_memory(self, operation: ReplayOperation, operation_index: int) -> None:
        del operation_index
        session = self._require_session()
        query = str(operation.payload["query"])
        result_key = _next_memory_result_key(self.memory_results, query)
        self.memory_results[result_key] = await self.backend.memory_service.search_memory(session.save_key, query)
        self.memory_query_by_key[result_key] = query

    async def _summarize(self, operation: ReplayOperation, operation_index: int) -> None:
        del operation, operation_index
        session = self._require_session()
        await self.backend.session_service.create_session_summary(session)
        self.summary_generation += 1
        self.session = await self._read_session()
        await self._record_summary_checkpoint()

    async def _unknown_outcome_retry(self, operation: ReplayOperation, operation_index: int) -> None:
        try:
            await self._append(operation, operation_index)
            raise InjectedReplayFailure("injected response loss after event commit")
        except InjectedReplayFailure:
            pass
        stored = await self._read_session()
        event_id = str(operation.payload["event_id"])
        if stored is None or not any(event.id == event_id for event in stored.events):
            await self._append(operation, operation_index)
        self.session = stored

    async def _unknown_memory_retry(self, operation: ReplayOperation, operation_index: int) -> None:
        await self._store_memory(operation, operation_index)
        await self._store_memory(operation, operation_index)

    async def _unknown_summary_retry(self, operation: ReplayOperation, operation_index: int) -> None:
        try:
            await self._summarize(operation, operation_index)
            raise InjectedReplayFailure("injected response loss after summary commit")
        except InjectedReplayFailure:
            pass
        stored = await self._read_session()
        if stored is None or not any(event.is_summary_event() for event in stored.events):
            await self._summarize(operation, operation_index)
        self.session = stored

    async def _before_call_failure(self, operation: ReplayOperation, operation_index: int) -> None:
        del operation, operation_index
        raise InjectedReplayFailure("injected before-call failure")

    async def _read_session(self) -> Optional[Session]:
        identity = self.backend.identity
        session = await self.backend.session_service.get_session(
            app_name=identity.app_name,
            user_id=identity.user_id,
            session_id=identity.session_id,
        )
        if session is not None:
            _validate_session_scope(session, identity)
        return session

    def _require_session(self) -> Session:
        if self.session is None:
            raise AssertionError("operation requires a session")
        return self.session

    async def snapshot(self, case_id: str) -> dict[str, Any]:
        """Capture public session/memory data plus public summary metadata."""
        session = self._require_session()
        manager = self.backend.session_service.summarizer_manager
        summary = await manager.get_session_summary(session) if manager else None
        summary_events = [event for event in session.events if event.is_summary_event()]
        final_memory = await self._read_final_memory(session)
        return {
            "case_id": case_id,
            "backend": self.backend.name,
            "session_id": session.id,
            "app_name": session.app_name,
            "user_id": session.user_id,
            "state": dict(session.state),
            "events": [_event_to_dict(event) for event in session.events],
            "historical_events": [_event_to_dict(event) for event in session.historical_events],
            "memory": {
                query: result.model_dump(mode="json")
                for query, result in self.memory_results.items()
            },
            "memory_final": final_memory,
            "summary": _summary_to_dict(summary, summary_events, self.summary_generation, session.id),
            "summary_checkpoints": [dict(checkpoint) for checkpoint in self.summary_checkpoints],
            "failures": list(self.failures),
        }

    async def _read_final_memory(self, session: Session) -> dict[str, Any]:
        results = {}
        for result_key, query in self.memory_query_by_key.items():
            response = await self.backend.memory_service.search_memory(session.save_key, query)
            results[result_key] = response.model_dump(mode="json")
        return results

    async def _record_summary_checkpoint(self) -> None:
        session = self._require_session()
        manager = self.backend.session_service.summarizer_manager
        summary = await manager.get_session_summary(session)
        anchors = [event for event in session.events if event.is_summary_event()]
        checkpoint = _summary_to_dict(summary, anchors, self.summary_generation, session.id)
        if checkpoint is None:
            raise AssertionError("successful summary did not create metadata or anchor")
        if checkpoint["text"] != _summary_text(anchors[0]) or checkpoint["anchor_count"] != 1:
            raise AssertionError("summary cache and anchor disagree")
        if self.summary_checkpoints and checkpoint["updated_at"] <= self.summary_checkpoints[-1]["updated_at"]:
            raise AssertionError("summary timestamp is not monotonic")
        self.summary_checkpoints.append(checkpoint)

    async def reload_snapshot(self, replay_case: ReplayCase) -> dict[str, Any]:
        """Read a persisted backend after service reconstruction."""
        self.memory_results.clear()
        self.memory_query_by_key.clear()
        self.summary_checkpoints.clear()
        self.failures.clear()
        self.summary_generation = 0
        self.session = await asyncio.wait_for(self._read_session(), OPERATION_TIMEOUT_SECONDS)
        if self.session is None:
            raise AssertionError("persisted session is missing")
        for operation in replay_case.operations:
            if operation.kind == OperationKind.SEARCH_MEMORY:
                query = str(operation.payload["query"])
                result_key = _next_memory_result_key(self.memory_query_by_key, query)
                self.memory_query_by_key[result_key] = query
        snapshot = await asyncio.wait_for(self.snapshot(replay_case.case_id), OPERATION_TIMEOUT_SECONDS)
        snapshot["backend"] = f"{self.backend.name}_reloaded"
        return snapshot


def _clone_operation(operation: ReplayOperation) -> ReplayOperation:
    return copy.deepcopy(operation)


def _validate_session_scope(session: Session, identity: ReplayIdentity) -> None:
    actual = (session.app_name, session.user_id, session.id)
    expected = (identity.app_name, identity.user_id, identity.session_id)
    if actual != expected:
        raise AssertionError(f"physical session scope mismatch: expected {expected!r}, got {actual!r}")


def _next_memory_result_key(results: dict[str, Any], query: str) -> str:
    if query not in results:
        return query
    suffix = MEMORY_RESULT_SUFFIX_START
    while f"{query}#{suffix}" in results:
        suffix += 1
    return f"{query}#{suffix}"


def _event_to_dict(event: Event) -> dict[str, Any]:
    data = event.model_dump(mode="json", exclude_none=True)
    data["is_summary"] = event.is_summary_event()
    return data


def _summary_to_dict(summary: Any, summary_events: list[Event], generation: int,
                     session_id: str) -> Optional[dict[str, Any]]:
    if summary is None and not summary_events:
        return None
    anchor = _event_to_dict(summary_events[0]) if summary_events else None
    return {
        "session_id": summary.session_id if summary else session_id,
        "text": summary.summary_text if summary else _summary_text(summary_events[0]),
        "generation": generation,
        "updated_at": summary.summary_timestamp if summary else summary_events[0].timestamp,
        "original_event_count": summary.original_event_count if summary else None,
        "compressed_event_count": summary.compressed_event_count if summary else None,
        "anchor": anchor,
        "anchor_count": len(summary_events),
        "cache_present": summary is not None,
    }


def _summary_text(event: Event) -> str:
    text = event.get_text()
    marker = "Previous conversation summary: "
    return text[len(marker):] if text.startswith(marker) else text


def validate_snapshot(expected: ExpectedOutcome,
                      snapshot: dict[str, Any],
                      require_cache: bool = True,
                      require_runtime: bool = True) -> list[str]:
    """Validate one backend against case-owned business expectations."""
    errors = []
    event_ids = tuple(event["id"] for event in snapshot["events"] if not event["is_summary"])
    if event_ids != expected.event_ids:
        errors.append(f"event ids: expected {expected.event_ids}, got {event_ids}")
    for key, value in expected.state.items():
        if snapshot["state"].get(key) != value:
            errors.append(f"state {key}: expected {value!r}, got {snapshot['state'].get(key)!r}")
    if any(key.startswith("temp:") for key in snapshot["state"]):
        errors.append("temporary state was persisted")
    memory_field = "memory" if require_runtime else "memory_final"
    for query, count in expected.memory_counts.items():
        actual = len(snapshot[memory_field].get(query, {}).get("memories", []))
        if actual != count:
            errors.append(f"memory {query}: expected {count}, got {actual}")
    errors.extend(_validate_summary(expected, snapshot, require_cache))
    if len(snapshot["historical_events"]) < expected.minimum_historical_events:
        errors.append("historical event count is below expectation")
    if require_runtime and len(snapshot["failures"]) != expected.failure_count:
        errors.append(f"failure count: expected {expected.failure_count}, got {len(snapshot['failures'])}")
    return errors


def _validate_summary(expected: ExpectedOutcome, snapshot: dict[str, Any], require_cache: bool) -> list[str]:
    summary = snapshot["summary"]
    if expected.summary_generation == 0:
        return [] if summary is None else ["unexpected summary"]
    if summary is None:
        return ["expected summary is missing"]
    errors = []
    if require_cache and summary["generation"] != expected.summary_generation:
        errors.append("summary generation mismatch")
    if expected.summary_fact not in summary["text"]:
        errors.append(f"summary lost fact {expected.summary_fact!r}")
    if summary["session_id"] != snapshot["session_id"] or summary["anchor_count"] != 1:
        errors.append("summary ownership or anchor replacement mismatch")
    if require_cache and (not summary["cache_present"]
                          or len(snapshot["summary_checkpoints"]) != expected.summary_generation):
        errors.append("summary cache/checkpoint mismatch")
    return errors
