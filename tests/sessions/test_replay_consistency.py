# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Cross-backend replay harness for Session, Memory, and Summary."""

from __future__ import annotations

import copy
import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

import pytest

from trpc_agent_sdk.abc import MemoryServiceABC, MemoryServiceConfig
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.memory import InMemoryMemoryService, RedisMemoryService, SqlMemoryService
from trpc_agent_sdk.models import LLMModel, LlmRequest, LlmResponse
from trpc_agent_sdk.sessions import (
    BaseSessionService,
    InMemorySessionService,
    RedisSessionService,
    Session,
    SessionSummarizer,
    SqlSessionService,
    SummarizerSessionManager,
)
from trpc_agent_sdk.sessions._types import SessionServiceConfig
from trpc_agent_sdk.types import Content, Part

from .replay_cases import OpKind, ReplayCase

_DIFF_REPORT_PATH = Path(__file__).resolve().parent / "session_memory_summary_diff_report.json"
_SUMMARY_TEXT = ("Mock summary: preserve user preferences, decisions, tool results, and "
                 "unresolved follow-up questions.")
_SUMMARY_METADATA_KEY = "_replay_summary"


class MockSummarizerModel(LLMModel):
    """Deterministic model used to remove LLM variance from replay tests."""

    def __init__(self, summary_text: str = _SUMMARY_TEXT):
        super().__init__(model_name="test-replay-summarizer")
        self._summary_text = summary_text

    @classmethod
    def supported_models(cls) -> list[str]:
        return [r"test-replay-summarizer"]

    async def _generate_async_impl(
        self,
        request: LlmRequest,
        stream: bool = False,
        ctx: InvocationContext | None = None,
    ) -> AsyncGenerator[LlmResponse, None]:
        del request, stream, ctx
        yield LlmResponse(content=Content(parts=[Part.from_text(text=self._summary_text)]))

    def validate_request(self, request: LlmRequest) -> None:
        del request


@dataclass
class DiffEntry:
    """A field-level difference with enough context to reproduce it."""

    path: str
    kind: str
    value_in_a: Any
    value_in_b: Any
    reason: str = ""
    session_id: str = ""
    event_index: Optional[int] = None
    summary_id: str = ""


@dataclass
class BackendPairResult:
    """Comparison result for either a backend pair or a backend contract."""

    a: str
    b: str
    dimension: str
    differences: list[DiffEntry] = field(default_factory=list)
    total_checks: int = 0
    mismatches: int = 0
    allowed_diffs: int = 0


@dataclass
class CaseResult:
    case_name: str
    passed: bool = True
    backend_pairs: list[BackendPairResult] = field(default_factory=list)


@dataclass
class DiffReport:
    metadata: dict[str, Any] = field(default_factory=dict)
    results: list[CaseResult] = field(default_factory=list)


class ReplayMode:
    IN_MEMORY = "inmemory"
    LIGHTWEIGHT = "lightweight"
    INTEGRATION = "integration"
    ALL = {IN_MEMORY, LIGHTWEIGHT, INTEGRATION}


@dataclass(frozen=True)
class ReplayConfig:
    """Environment-driven replay backend selection."""

    mode: str = ReplayMode.LIGHTWEIGHT
    sql_db_url: str = ""
    redis_db_url: str = ""

    @classmethod
    def from_env(cls) -> "ReplayConfig":
        mode = os.environ.get("REPLAY_MODE", ReplayMode.LIGHTWEIGHT).lower()
        if mode not in ReplayMode.ALL:
            raise ValueError(f"REPLAY_MODE must be one of {sorted(ReplayMode.ALL)}, got {mode!r}")
        return cls(
            mode=mode,
            sql_db_url=os.environ.get("REPLAY_SQL_URL", ""),
            redis_db_url=os.environ.get("REPLAY_REDIS_URL", ""),
        )

    @property
    def active_backend_names(self) -> list[str]:
        names = ["InMemory"]
        if self.mode != ReplayMode.IN_MEMORY:
            names.append("SQL")
        if self.mode == ReplayMode.INTEGRATION and self.redis_db_url:
            names.append("Redis")
        return names


@dataclass
class ReplayBackend:
    session_service: BaseSessionService
    memory_service: MemoryServiceABC

    async def initialize(self) -> None:
        for service in (self.session_service, self.memory_service):
            storage = getattr(service, "_sql_storage", None)
            if storage is not None:
                await storage.create_sql_engine()

    async def close(self) -> None:
        await self.memory_service.close()
        await self.session_service.close()


def _session_config() -> SessionServiceConfig:
    config = SessionServiceConfig(store_historical_events=True)
    config.clean_ttl_config()
    return config


def _memory_config() -> MemoryServiceConfig:
    config = MemoryServiceConfig(enabled=True)
    config.clean_ttl_config()
    return config


def build_backends(config: ReplayConfig) -> dict[str, ReplayBackend]:
    """Build matching Session and Memory implementations for every backend."""
    backends = {
        "InMemory":
        ReplayBackend(
            session_service=InMemorySessionService(session_config=_session_config()),
            memory_service=InMemoryMemoryService(memory_service_config=_memory_config()),
        )
    }
    if "SQL" in config.active_backend_names:
        sql_url = config.sql_db_url or "sqlite:///:memory:"
        backends["SQL"] = ReplayBackend(
            session_service=SqlSessionService(
                db_url=sql_url,
                session_config=_session_config(),
                is_async=False,
            ),
            memory_service=SqlMemoryService(
                db_url=sql_url,
                is_async=False,
                memory_service_config=_memory_config(),
            ),
        )
    if "Redis" in config.active_backend_names:
        backends["Redis"] = ReplayBackend(
            session_service=RedisSessionService(
                db_url=config.redis_db_url,
                session_config=_session_config(),
            ),
            memory_service=RedisMemoryService(
                db_url=config.redis_db_url,
                memory_service_config=_memory_config(),
            ),
        )
    return backends


def _new_summarizer(keep_recent_count: int = 5) -> tuple[MockSummarizerModel, SessionSummarizer]:
    model = MockSummarizerModel()
    summarizer = SessionSummarizer(model=model, keep_recent_count=keep_recent_count)
    return model, summarizer


def _new_summary_manager(keep_recent_count: int = 5) -> SummarizerSessionManager:
    model, summarizer = _new_summarizer(keep_recent_count)
    return SummarizerSessionManager(model=model, summarizer=summarizer, auto_summarize=False)


@dataclass
class _Snapshot:
    op_index: int
    op_kind: str
    session_id: str = ""
    session_dict: Optional[dict[str, Any]] = None
    memory_results: Optional[dict[str, Any]] = None
    summary: Optional[dict[str, Any]] = None
    sessions_list: Optional[list[dict[str, Any]]] = None


class ReplayHarness:
    """Execute one standard trace against each configured backend."""

    def __init__(self, backends: dict[str, ReplayBackend]):
        self._backends = backends

    async def run(self, case: ReplayCase) -> dict[str, list[_Snapshot]]:
        snapshots: dict[str, list[_Snapshot]] = {}
        for name, backend in self._backends.items():
            snapshots[name] = await self._run_on_backend(case, backend)
        return snapshots

    async def _run_on_backend(self, case: ReplayCase, backend: ReplayBackend) -> list[_Snapshot]:
        session_service = backend.session_service
        memory_service = backend.memory_service
        snapshots: list[_Snapshot] = []
        current_session: Optional[Session] = None
        last_coords: Optional[tuple[str, str, str]] = None
        all_sessions: dict[str, tuple[str, str, str]] = {}
        summary_manager = _new_summary_manager()
        session_service.set_summarizer_manager(summary_manager, force=True)
        last_event_timestamp = 0.0

        for index, operation in enumerate(case.operations):
            kind = OpKind(operation["op"])

            if kind == OpKind.CREATE_SESSION:
                app_name = f"{case.name}__{operation.get('app_name', 'replay_app')}"
                user_id = operation.get("user_id", "replay_user")
                current_session = await session_service.create_session(
                    app_name=app_name,
                    user_id=user_id,
                    session_id=operation.get("session_id"),
                    state=operation.get("state"),
                )
                last_coords = (app_name, user_id, current_session.id)
                all_sessions[current_session.id] = last_coords

            elif kind == OpKind.APPEND_EVENT:
                self._require_session(current_session, kind, index)
                event = Event.model_validate(copy.deepcopy(operation["event"]))
                # Timestamp is transport metadata, not replay input. Assign it
                # immediately before the write so SQL stale-session detection
                # and timestamp ordering observe a realistic monotonic trace.
                event.timestamp = max(time.time(), last_event_timestamp + 0.001)
                last_event_timestamp = event.timestamp
                await session_service.append_event(session=current_session, event=event)

            elif kind == OpKind.INJECT_APPEND_FAILURE:
                # A deterministic failure before the storage call models a
                # retryable transport/process failure with no committed write.
                self._require_session(current_session, kind, index)

            elif kind == OpKind.GET_SESSION:
                coords = self._resolve_coords(operation.get("session_ref"), current_session, last_coords, all_sessions)
                target = await self._load_session(session_service, coords)
                if not operation.get("session_ref"):
                    current_session = target
                snapshots.append(self._snapshot(index, kind, target, coords[2] if coords else ""))

            elif kind == OpKind.LIST_SESSIONS:
                coords = self._resolve_coords(None, current_session, last_coords, all_sessions)
                if coords is None:
                    raise RuntimeError(f"No session coordinates at operation {index}")
                response = await session_service.list_sessions(app_name=coords[0], user_id=coords[1])
                snapshots.append(
                    _Snapshot(
                        op_index=index,
                        op_kind=kind.value,
                        session_id=coords[2],
                        sessions_list=[_session_to_dict(session) for session in response.sessions or []],
                    ))

            elif kind == OpKind.DELETE_SESSION:
                coords = self._resolve_coords(None, current_session, last_coords, all_sessions)
                if coords is None:
                    raise RuntimeError(f"No session coordinates at operation {index}")
                await session_service.delete_session(app_name=coords[0], user_id=coords[1], session_id=coords[2])
                current_session = None
                snapshots.append(self._snapshot(index, kind, None, coords[2]))

            elif kind == OpKind.STORE_MEMORY:
                self._require_session(current_session, kind, index)
                await memory_service.store_session(current_session)

            elif kind == OpKind.SEARCH_MEMORY:
                self._require_session(current_session, kind, index)
                query = operation.get("query", "")
                limit = operation.get("limit", 10)
                response = await memory_service.search_memory(
                    key=current_session.save_key,
                    query=query,
                    limit=limit,
                )
                snapshots.append(
                    _Snapshot(
                        op_index=index,
                        op_kind=kind.value,
                        session_id=current_session.id,
                        session_dict=_session_to_dict(current_session),
                        memory_results=_memory_to_dict(query, limit, response),
                    ))

            elif kind == OpKind.CREATE_SUMMARY:
                self._require_session(current_session, kind, index)
                keep_recent_count = operation.get("keep_recent_count", 5)
                previous_summary = _summary_record_from_session(current_session)
                original_events = list(current_session.events)
                original_event_count = len(original_events)
                _, replacement = _new_summarizer(keep_recent_count)
                summary_manager.set_summarizer(replacement, force=True)
                await summary_manager.create_session_summary(current_session, force=True)
                generated_summary = await summary_manager.get_session_summary(current_session)
                if generated_summary is not None:
                    version = int(previous_summary.get("version", 0)) + 1 if previous_summary else 1
                    summary_record = {
                        "session_id": current_session.id,
                        "summary_text": generated_summary.summary_text,
                        "original_event_count": original_event_count,
                        "compressed_event_count": len(current_session.events),
                        "summary_timestamp": time.time(),
                        "version": version,
                        "metadata": summary_manager.get_summarizer_metadata(),
                    }
                    for event in current_session.events:
                        if not event.is_summary_event():
                            continue
                        # SQL reads events by timestamp. The summary anchor
                        # logically starts the compressed history window.
                        if original_events:
                            event.timestamp = original_events[0].timestamp
                        metadata = dict(event.custom_metadata or {})
                        metadata[_SUMMARY_METADATA_KEY] = summary_record
                        event.custom_metadata = metadata
                        break
                    await session_service.update_session(current_session)
                coords = (current_session.app_name, current_session.user_id, current_session.id)
                current_session = await self._load_session(session_service, coords)

            elif kind == OpKind.RESET_SUMMARY_READER:
                summary_manager = _new_summary_manager()
                session_service.set_summarizer_manager(summary_manager, force=True)

            elif kind == OpKind.GET_SUMMARY:
                coords = self._resolve_coords(
                    operation.get("session_ref"),
                    current_session,
                    last_coords,
                    all_sessions,
                )
                target = await self._load_session(session_service, coords)
                summary_dict = _summary_record_from_session(target) if target else None
                snapshots.append(
                    _Snapshot(
                        op_index=index,
                        op_kind=kind.value,
                        session_id=target.id if target else (coords[2] if coords else ""),
                        session_dict=_session_to_dict(target) if target else None,
                        summary=summary_dict,
                    ))

        if current_session is not None:
            coords = (current_session.app_name, current_session.user_id, current_session.id)
            current_session = await self._load_session(session_service, coords)
            snapshots.append(self._snapshot(len(case.operations), "final", current_session, coords[2]))
        return snapshots

    @staticmethod
    def _require_session(session: Optional[Session], kind: OpKind, index: int) -> None:
        if session is None:
            raise RuntimeError(f"Cannot execute {kind.value}: no active session at operation {index}")

    @staticmethod
    def _resolve_coords(
        session_ref: Optional[str],
        current_session: Optional[Session],
        last_coords: Optional[tuple[str, str, str]],
        all_sessions: dict[str, tuple[str, str, str]],
    ) -> Optional[tuple[str, str, str]]:
        if session_ref:
            return all_sessions.get(session_ref)
        if current_session is not None:
            return current_session.app_name, current_session.user_id, current_session.id
        return last_coords

    @staticmethod
    async def _load_session(
        service: BaseSessionService,
        coords: Optional[tuple[str, str, str]],
    ) -> Optional[Session]:
        if coords is None:
            return None
        return await service.get_session(app_name=coords[0], user_id=coords[1], session_id=coords[2])

    @staticmethod
    def _snapshot(index: int, kind: OpKind | str, session: Optional[Session], session_id: str) -> _Snapshot:
        return _Snapshot(
            op_index=index,
            op_kind=kind.value if isinstance(kind, OpKind) else kind,
            session_id=session.id if session else session_id,
            session_dict=_session_to_dict(session) if session else None,
        )


def _session_to_dict(session: Session) -> dict[str, Any]:
    return session.model_dump(exclude_none=True, mode="json")


def _summary_record_from_session(session: Session) -> Optional[dict[str, Any]]:
    """Read the replay envelope from a persisted summary anchor."""
    for event in session.events:
        if not event.is_summary_event() or not event.custom_metadata:
            continue
        record = event.custom_metadata.get(_SUMMARY_METADATA_KEY)
        if isinstance(record, dict):
            return copy.deepcopy(record)
    return None


def _memory_to_dict(query: str, limit: int, response: Any) -> dict[str, Any]:
    memories = []
    for memory in response.memories or []:
        content_text = ""
        if memory.content and memory.content.parts:
            content_text = "".join(part.text for part in memory.content.parts if part.text)
        memories.append({"author": memory.author, "content_text": content_text})
    memories.sort(key=lambda item: (item["content_text"], item["author"]))
    return {"query": query, "limit": limit, "count": len(memories), "memories": memories}


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)
_TIMESTAMP_FIELDS = {"timestamp", "last_update_time", "summary_timestamp", "create_time", "update_time"}
_EMPTY_COLLECTION_FIELDS = {"long_running_tool_ids", "longRunningToolIds"}


def _normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _part_has_payload(part: dict[str, Any]) -> bool:
    payload_keys = {
        "text",
        "function_call",
        "functionCall",
        "function_response",
        "functionResponse",
        "executable_code",
        "executableCode",
        "code_execution_result",
        "codeExecutionResult",
    }
    return any(part.get(key) not in (None, "", {}, []) for key in payload_keys)


def _normalize(value: Any, key: str = "") -> Any:
    """Normalize representation-only variance while retaining business fields."""
    if key in _TIMESTAMP_FIELDS and value is not None:
        return "<TIMESTAMP>"
    if key in {"id", "event_id", "invocation_id"} and isinstance(value, str) and _UUID_RE.fullmatch(value):
        return "<GENERATED_ID>"
    if key == "summary_text" and isinstance(value, str):
        return _normalize_text(value)
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for child_key in sorted(value):
            child_value = value[child_key]
            if child_key in _EMPTY_COLLECTION_FIELDS and child_value is None:
                child_value = []
            normalized[child_key] = _normalize(child_value, child_key)
        if "author" in normalized and "content" in normalized:
            for collection_key in _EMPTY_COLLECTION_FIELDS:
                normalized.setdefault(collection_key, [])
        content = normalized.get("content")
        if isinstance(content, dict) and isinstance(content.get("parts"), list):
            content["parts"] = [
                part for part in content["parts"] if not isinstance(part, dict) or _part_has_payload(part)
            ]
        return normalized
    if isinstance(value, set):
        return sorted(_normalize(item) for item in value)
    if isinstance(value, list):
        normalized_list = [_normalize(item) for item in value]
        if key in _EMPTY_COLLECTION_FIELDS:
            return sorted(normalized_list)
        return normalized_list
    return value


def normalize_session(session: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    return _normalize(copy.deepcopy(session)) if session is not None else None


def _parse_event_index(path: str) -> Optional[int]:
    match = re.search(r"(?:events|historical_events)\[(\d+)]", path)
    return int(match.group(1)) if match else None


def _recursive_compare(
    a: Any,
    b: Any,
    path: str,
    session_id: str = "",
    summary_id: str = "",
    counter: Optional[list[int]] = None,
) -> list[DiffEntry]:
    """Recursively compare normalized values and count successful checks too."""
    if counter is None:
        counter = [0]
    diffs: list[DiffEntry] = []
    event_index = _parse_event_index(path)

    if type(a) is not type(b):
        counter[0] += 1
        return [
            DiffEntry(
                path=path,
                kind="type_mismatch",
                value_in_a=a,
                value_in_b=b,
                reason=f"Type mismatch: {type(a).__name__} vs {type(b).__name__}",
                session_id=session_id,
                event_index=event_index,
                summary_id=summary_id,
            )
        ]

    if isinstance(a, dict):
        for child_key in sorted(set(a) | set(b)):
            child_path = f"{path}.{child_key}" if path else child_key
            if child_key not in a or child_key not in b:
                counter[0] += 1
                missing_a = child_key not in a
                diffs.append(
                    DiffEntry(
                        path=child_path,
                        kind="missing_in_a" if missing_a else "missing_in_b",
                        value_in_a=None if missing_a else a[child_key],
                        value_in_b=b[child_key] if missing_a else None,
                        session_id=session_id,
                        event_index=_parse_event_index(child_path),
                        summary_id=summary_id,
                    ))
                continue
            diffs.extend(_recursive_compare(
                a[child_key],
                b[child_key],
                child_path,
                session_id,
                summary_id,
                counter,
            ))
        return diffs

    if isinstance(a, list):
        for index in range(max(len(a), len(b))):
            child_path = f"{path}[{index}]"
            if index >= len(a) or index >= len(b):
                counter[0] += 1
                missing_a = index >= len(a)
                diffs.append(
                    DiffEntry(
                        path=child_path,
                        kind="missing_in_a" if missing_a else "missing_in_b",
                        value_in_a=None if missing_a else a[index],
                        value_in_b=b[index] if missing_a else None,
                        session_id=session_id,
                        event_index=_parse_event_index(child_path),
                        summary_id=summary_id,
                    ))
                continue
            diffs.extend(_recursive_compare(a[index], b[index], child_path, session_id, summary_id, counter))
        if not a and not b:
            counter[0] += 1
        return diffs

    counter[0] += 1
    if a != b:
        diffs.append(
            DiffEntry(
                path=path,
                kind="mismatch",
                value_in_a=a,
                value_in_b=b,
                session_id=session_id,
                event_index=event_index,
                summary_id=summary_id,
            ))
    return diffs


def _accumulate(diffs: list[DiffEntry], checks: int, result: BackendPairResult) -> None:
    result.total_checks += checks
    for diff in diffs:
        result.differences.append(diff)
        if diff.kind == "allowed_diff":
            result.allowed_diffs += 1
        else:
            result.mismatches += 1


def _compare_value(
    a: Any,
    b: Any,
    path: str,
    result: BackendPairResult,
    session_id: str,
    summary_id: str = "",
) -> None:
    counter = [0]
    diffs = _recursive_compare(_normalize(a), _normalize(b), path, session_id, summary_id, counter)
    _accumulate(diffs, counter[0], result)


def compare_snapshots(
    backend_snapshots: dict[str, list[_Snapshot]],
    case_name: str,
    known_diffs: Optional[list[dict[str, str]]] = None,
) -> list[BackendPairResult]:
    """Compare every configured backend pair."""
    del case_name
    results: list[BackendPairResult] = []
    names = list(backend_snapshots)
    for index, name_a in enumerate(names):
        for name_b in names[index + 1:]:
            result = _compare_snapshot_sequences(
                backend_snapshots[name_a],
                backend_snapshots[name_b],
                name_a,
                name_b,
            )
            _apply_known_diffs(result, known_diffs or [])
            results.append(result)
    return results


def _compare_snapshot_sequences(
    snapshots_a: list[_Snapshot],
    snapshots_b: list[_Snapshot],
    name_a: str,
    name_b: str,
) -> BackendPairResult:
    result = BackendPairResult(a=name_a, b=name_b, dimension="cross_backend")
    for index in range(max(len(snapshots_a), len(snapshots_b))):
        if index >= len(snapshots_a) or index >= len(snapshots_b):
            missing_a = index >= len(snapshots_a)
            available = snapshots_b[index] if missing_a else snapshots_a[index]
            _accumulate([
                DiffEntry(
                    path=f"snapshot[{index}]",
                    kind="missing_in_a" if missing_a else "missing_in_b",
                    value_in_a=None if missing_a else available.op_kind,
                    value_in_b=available.op_kind if missing_a else None,
                    session_id=available.session_id,
                )
            ], 1, result)
            continue
        _compare_single_snapshot(snapshots_a[index], snapshots_b[index], result)
    return result


def _compare_single_snapshot(a: _Snapshot, b: _Snapshot, result: BackendPairResult) -> None:
    prefix = f"snapshot[{a.op_index}]"
    session_id = a.session_id or b.session_id
    _compare_value(a.op_index, b.op_index, f"{prefix}.op_index", result, session_id)
    _compare_value(a.op_kind, b.op_kind, f"{prefix}.op_kind", result, session_id)
    _compare_value(a.session_id, b.session_id, f"{prefix}.session_id", result, session_id)
    _compare_value(normalize_session(a.session_dict), normalize_session(b.session_dict), f"{prefix}.session", result,
                   session_id)
    _compare_value(a.memory_results, b.memory_results, f"{prefix}.memory", result, session_id)
    summary_id = ""
    if a.summary:
        summary_id = str(a.summary.get("session_id", ""))
    elif b.summary:
        summary_id = str(b.summary.get("session_id", ""))
    _compare_value(a.summary, b.summary, f"{prefix}.summary", result, session_id, summary_id)
    list_a = sorted(a.sessions_list or [], key=lambda item: item.get("id", "")) if a.sessions_list is not None else None
    list_b = sorted(b.sessions_list or [], key=lambda item: item.get("id", "")) if b.sessions_list is not None else None
    _compare_value(list_a, list_b, f"{prefix}.sessions_list", result, session_id)


def _apply_known_diffs(result: BackendPairResult, known_diffs: list[dict[str, str]]) -> None:
    """Allow only documented, backend-scoped, full-path differences."""
    for diff in result.differences:
        if diff.kind == "allowed_diff":
            continue
        for allowed in known_diffs:
            backend_a = allowed.get("backend_a")
            backend_b = allowed.get("backend_b")
            pattern = allowed.get("path_pattern")
            reason = allowed.get("reason")
            if not all((backend_a, backend_b, pattern, reason)):
                continue
            if {backend_a, backend_b} != {result.a, result.b}:
                continue
            if re.fullmatch(pattern, diff.path):
                diff.kind = "allowed_diff"
                diff.reason = reason
                result.mismatches -= 1
                result.allowed_diffs += 1
                break


def _event_parts(event: dict[str, Any]) -> list[dict[str, Any]]:
    content = event.get("content") or {}
    return content.get("parts") or []


def _event_text(event: dict[str, Any]) -> str:
    return "".join(str(part.get("text", "")) for part in _event_parts(event) if part.get("text"))


def _contract_view(snapshot: _Snapshot) -> dict[str, Any]:
    session = snapshot.session_dict
    events = (session or {}).get("events") or []
    historical = (session or {}).get("historical_events") or []
    event_texts = [_event_text(event) for event in events]
    summary = copy.deepcopy(snapshot.summary)
    if summary is not None:
        summary["updated_at_present"] = isinstance(summary.get("summary_timestamp"), (int, float))
    return {
        "session_exists":
        session is not None,
        "event_count":
        len(events),
        "historical_event_count":
        len(historical),
        "summary_event_count":
        sum(1 for event in events if int(event.get("model_flags", 0)) & 2),
        "event_texts":
        event_texts,
        "event_text_suffix":
        event_texts[-2:],
        "function_call_count":
        sum(1 for event in events for part in _event_parts(event)
            if part.get("function_call") or part.get("functionCall")),
        "function_response_count":
        sum(1 for event in events for part in _event_parts(event)
            if part.get("function_response") or part.get("functionResponse")),
        "state": (session or {}).get("state") or {},
        "memory_count": (snapshot.memory_results or {}).get("count", 0),
        "summary":
        summary,
    }


def _project(actual: Any, expected: Any) -> Any:
    if isinstance(expected, dict):
        source = actual if isinstance(actual, dict) else {}
        return {key: _project(source.get(key), value) for key, value in expected.items()}
    return actual


def validate_contracts(case: ReplayCase, snapshots: dict[str, list[_Snapshot]]) -> list[BackendPairResult]:
    """Validate explicit case expectations so common backend bugs cannot pass."""
    expected_by_op = {
        index: operation["expect"]
        for index, operation in enumerate(case.operations) if "expect" in operation
    }
    results: list[BackendPairResult] = []
    for backend_name, backend_snapshots in snapshots.items():
        result = BackendPairResult(a="expected", b=backend_name, dimension="contract")
        snapshots_by_op = {snapshot.op_index: snapshot for snapshot in backend_snapshots}
        for op_index, expected in expected_by_op.items():
            snapshot = snapshots_by_op.get(op_index)
            if snapshot is None:
                _accumulate([
                    DiffEntry(
                        path=f"operation[{op_index}].contract",
                        kind="missing_in_b",
                        value_in_a=expected,
                        value_in_b=None,
                    )
                ], 1, result)
                continue
            actual = _project(_contract_view(snapshot), expected)
            _compare_value(expected, actual, f"operation[{op_index}].contract", result, snapshot.session_id,
                           snapshot.session_id if "summary" in expected else "")
        results.append(result)
    return results


def generate_report(
    case_results: list[CaseResult],
    backends_tested: list[str],
    mode: str,
    duration_seconds: float,
) -> DiffReport:
    passed_cases = sum(result.passed for result in case_results)
    return DiffReport(
        metadata={
            "generated_at":
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "mode":
            mode,
            "backends_tested":
            backends_tested,
            "total_cases":
            len(case_results),
            "passed_cases":
            passed_cases,
            "failed_cases":
            len(case_results) - passed_cases,
            "duration_seconds":
            round(duration_seconds, 3),
            "diff_entry_fields": [
                "path",
                "session_id",
                "event_index",
                "summary_id",
                "value_in_a",
                "value_in_b",
                "reason",
            ],
        },
        results=case_results,
    )


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def write_report(report: DiffReport, path: Path) -> None:
    payload = {
        "metadata":
        report.metadata,
        "results": [{
            "case_name":
            case.case_name,
            "passed":
            case.passed,
            "backend_pairs": [{
                "a":
                pair.a,
                "b":
                pair.b,
                "dimension":
                pair.dimension,
                "differences": [{
                    "path": diff.path,
                    "kind": diff.kind,
                    "session_id": diff.session_id,
                    "event_index": diff.event_index,
                    "summary_id": diff.summary_id,
                    "value_in_a": _json_safe(diff.value_in_a),
                    "value_in_b": _json_safe(diff.value_in_b),
                    "reason": diff.reason,
                } for diff in pair.differences],
                "summary": {
                    "total_checks": pair.total_checks,
                    "mismatches": pair.mismatches,
                    "allowed_diffs": pair.allowed_diffs,
                },
            } for pair in case.backend_pairs],
        } for case in report.results],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


_MODULE_CASE_RESULTS: list[CaseResult] = []
_BACKENDS_TESTED: set[str] = set()
_REPLAY_DURATION_SECONDS = 0.0


@pytest.fixture(scope="module")
def replay_config() -> ReplayConfig:
    return ReplayConfig.from_env()


@pytest.fixture(scope="module")
async def backends(replay_config: ReplayConfig):
    configured = build_backends(replay_config)
    for backend in configured.values():
        await backend.initialize()
    _BACKENDS_TESTED.update(configured)
    try:
        yield configured
    finally:
        for backend in configured.values():
            await backend.close()


@pytest.fixture(scope="module", autouse=True)
def _module_report(replay_config: ReplayConfig):
    global _REPLAY_DURATION_SECONDS
    _MODULE_CASE_RESULTS.clear()
    _BACKENDS_TESTED.clear()
    _REPLAY_DURATION_SECONDS = 0.0
    yield
    if _MODULE_CASE_RESULTS:
        write_report(
            generate_report(
                case_results=list(_MODULE_CASE_RESULTS),
                backends_tested=sorted(_BACKENDS_TESTED),
                mode=replay_config.mode,
                duration_seconds=_REPLAY_DURATION_SECONDS,
            ), _DIFF_REPORT_PATH)


async def _run_case(case: ReplayCase, backends: dict[str, ReplayBackend]) -> CaseResult:
    global _REPLAY_DURATION_SECONDS
    started = time.perf_counter()
    snapshots = await ReplayHarness(backends).run(case)
    comparisons = validate_contracts(case, snapshots)
    comparisons.extend(compare_snapshots(snapshots, case.name, case.known_diffs))
    result = CaseResult(
        case_name=case.name,
        passed=all(comparison.mismatches == 0 for comparison in comparisons),
        backend_pairs=comparisons,
    )
    _REPLAY_DURATION_SECONDS += time.perf_counter() - started
    _MODULE_CASE_RESULTS.append(result)
    return result


class TestReplayCases:

    async def test_single_turn_text(self, backends, case_single_turn_text):
        assert (await _run_case(case_single_turn_text, backends)).passed

    async def test_multi_turn_text(self, backends, case_multi_turn_text):
        assert (await _run_case(case_multi_turn_text, backends)).passed

    async def test_tool_call_response(self, backends, case_tool_call_response):
        assert (await _run_case(case_tool_call_response, backends)).passed

    async def test_state_basic_update(self, backends, case_state_basic_update):
        assert (await _run_case(case_state_basic_update, backends)).passed

    async def test_state_three_tier(self, backends, case_state_three_tier):
        assert (await _run_case(case_state_three_tier, backends)).passed

    async def test_memory_store_search(self, backends, case_memory_store_search):
        assert (await _run_case(case_memory_store_search, backends)).passed

    async def test_memory_multi_session(self, backends, case_memory_multi_session):
        assert (await _run_case(case_memory_multi_session, backends)).passed

    async def test_summary_generation(self, backends, case_summary_generation):
        assert (await _run_case(case_summary_generation, backends)).passed

    async def test_summary_truncation(self, backends, case_summary_truncation):
        assert (await _run_case(case_summary_truncation, backends)).passed

    async def test_summary_update_and_ownership(self, backends, case_summary_error_detection):
        assert (await _run_case(case_summary_error_detection, backends)).passed

    async def test_error_recovery(self, backends, case_error_duplicate_write):
        assert (await _run_case(case_error_duplicate_write, backends)).passed

    def test_lightweight_runtime_budget(self, replay_config):
        if replay_config.mode in {ReplayMode.IN_MEMORY, ReplayMode.LIGHTWEIGHT}:
            assert _REPLAY_DURATION_SECONDS <= 30.0


def _mutation_baseline() -> _Snapshot:
    return _Snapshot(
        op_index=7,
        op_kind=OpKind.GET_SUMMARY.value,
        session_id="mutation-session",
        session_dict={
            "id":
            "mutation-session",
            "app_name":
            "mutation-app",
            "user_id":
            "mutation-user",
            "save_key":
            "mutation-app/mutation-user",
            "state": {
                "counter": 2
            },
            "events": [
                {
                    "id": "event-1",
                    "author": "user",
                    "timestamp": 1.0,
                    "content": {
                        "role": "user",
                        "parts": [{
                            "text": "first"
                        }]
                    }
                },
                {
                    "id": "event-2",
                    "author": "assistant",
                    "timestamp": 2.0,
                    "content": {
                        "role": "model",
                        "parts": [{
                            "text": "second"
                        }]
                    }
                },
            ],
            "historical_events": [],
        },
        memory_results={
            "query": "coffee",
            "limit": 10,
            "count": 1,
            "memories": [{
                "author": "user",
                "content_text": "coffee preference"
            }],
        },
        summary={
            "session_id": "mutation-session",
            "summary_text": "stable summary",
            "original_event_count": 6,
            "compressed_event_count": 3,
            "summary_timestamp": 3.0,
            "version": 2,
            "metadata": {
                "model_name": "test-replay-summarizer"
            },
        },
    )


def _inject_mutation(name: str, snapshot: _Snapshot) -> None:
    if name == "event_text":
        snapshot.session_dict["events"][0]["content"]["parts"][0]["text"] = "changed"
    elif name == "event_order":
        snapshot.session_dict["events"].reverse()
    elif name == "state_value":
        snapshot.session_dict["state"]["counter"] = 99
    elif name == "memory_missing":
        snapshot.memory_results["memories"] = []
        snapshot.memory_results["count"] = 0
    elif name == "memory_value":
        snapshot.memory_results["memories"][0]["content_text"] = "polluted"
    elif name == "summary_missing":
        snapshot.summary = None
    elif name == "summary_overwrite":
        snapshot.summary["summary_text"] = "wrong replacement"
    elif name == "summary_version":
        snapshot.summary["version"] = 1
    elif name == "summary_owner":
        snapshot.summary["session_id"] = "another-session"
    elif name == "summary_metadata":
        snapshot.summary["compressed_event_count"] = 4
    else:
        raise AssertionError(f"Unknown mutation {name}")


@pytest.mark.parametrize("mutation", [
    "event_text",
    "event_order",
    "state_value",
    "memory_missing",
    "memory_value",
    "summary_missing",
    "summary_overwrite",
    "summary_version",
    "summary_owner",
    "summary_metadata",
])
def test_injected_inconsistency_is_detected(mutation):
    reference = _mutation_baseline()
    mutated = copy.deepcopy(reference)
    _inject_mutation(mutation, mutated)
    result = compare_snapshots({"reference": [reference], "mutated": [mutated]}, mutation)[0]
    assert result.mismatches > 0, f"Mutation {mutation!r} escaped detection"
    assert result.differences
    assert all(diff.session_id == "mutation-session" for diff in result.differences)
    assert all(diff.path and diff.value_in_a != diff.value_in_b for diff in result.differences)
    if mutation.startswith("event_"):
        assert any(diff.event_index is not None for diff in result.differences)
    if mutation.startswith("summary_"):
        assert any(diff.summary_id == "mutation-session" for diff in result.differences)


def test_equal_snapshots_have_checks_but_no_false_positive():
    reference = _mutation_baseline()
    result = compare_snapshots({"a": [reference], "b": [copy.deepcopy(reference)]}, "equal")[0]
    assert result.total_checks > 0
    assert result.mismatches == 0
    assert result.differences == []


def test_allowed_diff_requires_backend_path_and_reason():
    reference = _mutation_baseline()
    mutated = copy.deepcopy(reference)
    _inject_mutation("event_text", mutated)
    path = r"snapshot\[7\]\.session\.events\[0\]\.content\.parts\[0\]\.text"
    allowed = [{
        "backend_a": "reference",
        "backend_b": "mutated",
        "path_pattern": path,
        "reason": "Demonstrates a narrowly scoped backend exception.",
    }]
    result = compare_snapshots({"reference": [reference], "mutated": [mutated]}, "allowed", allowed)[0]
    assert result.mismatches == 0
    assert result.allowed_diffs == 1
    assert result.differences[0].reason
