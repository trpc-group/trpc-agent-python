#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2025 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Session / Memory / Summary replay consistency framework."""

from __future__ import annotations

import copy
import json
import re
import tempfile
import unicodedata
import uuid
from collections.abc import AsyncGenerator
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import ClassVar
from typing import Optional

from google.genai.types import Content
from google.genai.types import Part

from trpc_agent_sdk.abc import MemoryServiceABC
from trpc_agent_sdk.abc import SessionServiceABC
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.storage import RedisCommand
from trpc_agent_sdk.storage import SqlCondition
from trpc_agent_sdk.storage import SqlKey
from trpc_agent_sdk.utils import user_key

from ._in_memory_session_service import InMemorySessionService
from ._redis_session_service import RedisSessionService
from ._session import Session
from ._session_summarizer import SessionSummarizer
from ._sql_session_service import SqlSessionService
from ._sql_session_service import StorageAppState
from ._sql_session_service import StorageUserState
from ._summarizer_manager import SummarizerSessionManager
from ._types import SessionServiceConfig


class ReplayCaseError(ValueError):
    """Raised when a replay JSONL file does not conform to the test DSL."""


class InjectedReplayFailure(RuntimeError):
    """Failure deliberately raised after a session write and before memory storage."""


@dataclass(frozen=True)
class ReplayCase:
    """A validated replay trajectory and its expected normalized snapshot."""

    _REQUIRED_HEADER_FIELDS: ClassVar[frozenset[str]] = frozenset({"case_id", "expected"})
    _REQUIRED_OPERATION_FIELDS: ClassVar[dict[str, frozenset[str]]] = {
        "create_session": frozenset({"operation_id", "session_id"}),
        "append_event": frozenset({"operation_id", "session_id", "event"}),
        "store_memory": frozenset({"operation_id", "session_id"}),
        "search_memory": frozenset({"operation_id", "query_id", "query"}),
        "summarize": frozenset({
            "operation_id",
            "session_id",
            "summary_id",
            "version",
            "updated_at",
            "text",
        }),
        "inject_failure": frozenset({
            "operation_id",
            "session_id",
            "failure",
            "event",
        }),
        "checkpoint": frozenset({"operation_id", "checkpoint_id"}),
    }

    case_id: str
    expected: dict[str, Any]
    operations: tuple[dict[str, Any], ...]
    metadata: dict[str, Any]
    allow_duplicate_event_ids: bool
    path: Path

    @classmethod
    def load(cls, path: Path) -> "ReplayCase":
        """Load and validate one JSONL replay case."""
        records: list[tuple[int, dict[str, Any]]] = []
        for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not raw_line.strip():
                continue
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise ReplayCaseError(f"{path}:{line_number}: invalid JSON: {exc.msg}") from exc
            if not isinstance(record, dict):
                raise ReplayCaseError(f"{path}:{line_number}: each JSONL line must be an object")
            records.append((line_number, record))

        if not records:
            raise ReplayCaseError(f"{path}:1: replay case is empty")
        header_line, header = records[0]
        if header.get("op") != "case":
            raise ReplayCaseError(f"{path}:{header_line}: first operation must be 'case'")
        missing = sorted(cls._REQUIRED_HEADER_FIELDS - header.keys())
        if missing:
            raise ReplayCaseError(f"{path}:{header_line}: missing case fields: {', '.join(missing)}")
        if not isinstance(header["case_id"], str) or not header["case_id"]:
            raise ReplayCaseError(f"{path}:{header_line}: case_id must be a non-empty string")
        if not isinstance(header["expected"], dict):
            raise ReplayCaseError(f"{path}:{header_line}: expected must be an object")

        operations: list[dict[str, Any]] = []
        operation_ids: set[str] = set()
        session_ids: set[str] = set()
        event_ids: set[str] = set()
        query_ids: set[str] = set()
        summary_ids: set[str] = set()
        summary_chain: dict[str, tuple[str, int, float]] = {}
        checkpoint_ids: set[str] = set()
        allow_duplicate_event_ids = header.get("allow_duplicate_event_ids", False)
        if not isinstance(allow_duplicate_event_ids, bool):
            raise ReplayCaseError(f"{path}:{header_line}: allow_duplicate_event_ids must be boolean")
        for line_number, operation in records[1:]:
            operation_name = operation.get("op")
            if operation_name not in cls._REQUIRED_OPERATION_FIELDS:
                raise ReplayCaseError(f"{path}:{line_number}: unknown operation {operation_name!r}")
            missing = sorted(cls._REQUIRED_OPERATION_FIELDS[operation_name] - operation.keys())
            if missing:
                raise ReplayCaseError(
                    f"{path}:{line_number}: {operation_name} missing fields: {', '.join(missing)}")
            operation_id = operation["operation_id"]
            if not isinstance(operation_id, str) or not operation_id:
                raise ReplayCaseError(f"{path}:{line_number}: operation_id must be a non-empty string")
            if operation_id in operation_ids:
                raise ReplayCaseError(f"{path}:{line_number}: duplicate operation_id {operation_id!r}")
            operation_ids.add(operation_id)

            if operation_name in {"append_event", "inject_failure"}:
                if not isinstance(operation["event"], dict):
                    raise ReplayCaseError(f"{path}:{line_number}: event must be an object")
                event_required = {"author", "content", "invocation_id", "timestamp"}
                event_missing = sorted(event_required - operation["event"].keys())
                if event_missing:
                    raise ReplayCaseError(
                        f"{path}:{line_number}: event missing fields: {', '.join(event_missing)}")
                if not operation["event"]["author"] or not operation["event"]["invocation_id"]:
                    raise ReplayCaseError(
                        f"{path}:{line_number}: event author and invocation_id must be non-empty")
                if not _is_number(operation["event"]["timestamp"]):
                    raise ReplayCaseError(f"{path}:{line_number}: event timestamp must be numeric")
                try:
                    Event.model_validate(operation["event"])
                except Exception as exc:
                    raise ReplayCaseError(f"{path}:{line_number}: invalid event: {exc}") from exc
                event_id = operation.get("logical_event_id") or operation["event"].get("id")
                if event_id:
                    if event_id in event_ids and not allow_duplicate_event_ids:
                        raise ReplayCaseError(f"{path}:{line_number}: duplicate event identifier {event_id!r}")
                    event_ids.add(event_id)
            if operation_name == "create_session":
                session_id = operation["session_id"]
                if session_id in session_ids:
                    raise ReplayCaseError(f"{path}:{line_number}: duplicate session_id {session_id!r}")
                session_ids.add(session_id)
            elif "session_id" in operation and operation["session_id"] not in session_ids:
                raise ReplayCaseError(
                    f"{path}:{line_number}: session_id {operation['session_id']!r} was not created")
            if operation_name == "search_memory":
                query_id = operation["query_id"]
                if query_id in query_ids:
                    raise ReplayCaseError(f"{path}:{line_number}: duplicate query_id {query_id!r}")
                query_ids.add(query_id)
            if operation_name == "summarize":
                summary_id = operation["summary_id"]
                if summary_id in summary_ids:
                    raise ReplayCaseError(f"{path}:{line_number}: duplicate summary_id {summary_id!r}")
                summary_ids.add(summary_id)
                if not isinstance(operation["version"], int) or operation["version"] < 1:
                    raise ReplayCaseError(f"{path}:{line_number}: summary version must be a positive integer")
                if not _is_number(operation["updated_at"]):
                    raise ReplayCaseError(f"{path}:{line_number}: summary updated_at must be numeric")
                session_id = operation["session_id"]
                previous = summary_chain.get(session_id)
                if previous is None:
                    if operation["version"] != 1 or operation.get("replaces_summary_id") is not None:
                        raise ReplayCaseError(
                            f"{path}:{line_number}: first summary must be version 1 without a replacement")
                else:
                    previous_id, previous_version, previous_updated_at = previous
                    if operation["version"] != previous_version + 1:
                        raise ReplayCaseError(f"{path}:{line_number}: summary version must increase by one")
                    if operation.get("replaces_summary_id") != previous_id:
                        raise ReplayCaseError(
                            f"{path}:{line_number}: summary must replace {previous_id!r}")
                    if float(operation["updated_at"]) <= previous_updated_at:
                        raise ReplayCaseError(f"{path}:{line_number}: summary updated_at must increase")
                summary_chain[session_id] = (
                    summary_id,
                    operation["version"],
                    float(operation["updated_at"]),
                )
            if operation_name == "checkpoint":
                checkpoint_id = operation["checkpoint_id"]
                if checkpoint_id in checkpoint_ids:
                    raise ReplayCaseError(f"{path}:{line_number}: duplicate checkpoint_id {checkpoint_id!r}")
                checkpoint_ids.add(checkpoint_id)
            operations.append(copy.deepcopy(operation))

        return cls(
            case_id=header["case_id"],
            expected=copy.deepcopy(header["expected"]),
            operations=tuple(operations),
            metadata={
                key: copy.deepcopy(value)
                for key, value in header.items()
                if key not in {"op", "case_id", "expected", "allow_duplicate_event_ids"}
            },
            allow_duplicate_event_ids=allow_duplicate_event_ids,
            path=path,
        )


class ReplaySummaryModel:
    """A deterministic model adapter used to replay persisted summary text."""

    name = "replay-scripted-summary"

    def __init__(self) -> None:
        self._responses: list[str] = []

    def push(self, text: str) -> None:
        """Queue the text returned by the next summary model call."""
        self._responses.append(text)

    async def generate_async(
        self,
        request: Any,
        stream: bool = False,
        ctx: Any = None,
    ) -> AsyncGenerator[LlmResponse, None]:
        """Yield exactly one deterministic LLM response."""
        del request, stream, ctx
        if not self._responses:
            raise RuntimeError("No scripted summary response was queued")
        text = self._responses.pop(0)
        yield LlmResponse(content=Content(role="model", parts=[Part.from_text(text=text)]))


@dataclass
class ReplayBackend:
    """One concrete Session + Memory backend pair used by the harness."""

    name: str
    session_service: SessionServiceABC
    memory_service: MemoryServiceABC
    summary_model: ReplaySummaryModel
    cleanup_data: bool = True
    close_services: bool = True
    touched_sessions: set[tuple[str, str, str]] = field(default_factory=set)

    @classmethod
    def in_memory(cls, name: str = "in_memory") -> "ReplayBackend":
        """Create a replay backend using the SDK in-memory services."""
        from trpc_agent_sdk.memory import InMemoryMemoryService

        model = ReplaySummaryModel()
        manager = SummarizerSessionManager(model=model, auto_summarize=False)
        return cls(
            name=name,
            session_service=InMemorySessionService(
                summarizer_manager=manager,
                session_config=SessionServiceConfig(store_historical_events=True),
            ),
            memory_service=InMemoryMemoryService(),
            summary_model=model,
        )

    @classmethod
    def sql(
        cls,
        db_url: str,
        name: str = "sql",
        cleanup_data: bool = True,
    ) -> "ReplayBackend":
        """Create a replay backend using the SDK SQL services."""
        from trpc_agent_sdk.memory import SqlMemoryService

        model = ReplaySummaryModel()
        manager = SummarizerSessionManager(model=model, auto_summarize=False)
        is_async = bool(re.search(r"\+(?:aiosqlite|aiomysql|asyncpg)(?=://)", db_url))
        return cls(
            name=name,
            session_service=SqlSessionService(
                db_url=db_url,
                is_async=is_async,
                summarizer_manager=manager,
                session_config=SessionServiceConfig(store_historical_events=True),
            ),
            memory_service=SqlMemoryService(db_url=db_url, is_async=is_async),
            summary_model=model,
            cleanup_data=cleanup_data,
        )

    @classmethod
    def redis(
        cls,
        db_url: str,
        name: str = "redis",
        cleanup_data: bool = True,
    ) -> "ReplayBackend":
        """Create a replay backend using the SDK Redis services."""
        from trpc_agent_sdk.memory import RedisMemoryService

        model = ReplaySummaryModel()
        manager = SummarizerSessionManager(model=model, auto_summarize=False)
        return cls(
            name=name,
            session_service=RedisSessionService(
                db_url=db_url,
                is_async=True,
                summarizer_manager=manager,
                session_config=SessionServiceConfig(store_historical_events=True),
            ),
            memory_service=RedisMemoryService(db_url=db_url, is_async=True),
            summary_model=model,
            cleanup_data=cleanup_data,
        )

    async def initialize(self) -> None:
        """Initialize lazy SQL engines and fail immediately on bad integration URLs."""
        for service in (self.session_service, self.memory_service):
            storage = getattr(service, "_sql_storage", None)
            if storage is not None:
                await storage.create_sql_engine()
        if isinstance(self.session_service, RedisSessionService):
            async with self.session_service._redis_storage.create_db_session() as redis_session:
                await self.session_service._redis_storage.execute_command(
                    redis_session,
                    RedisCommand(method="ping"),
                )

    async def cleanup(self) -> None:
        """Delete only records created under this run's unique app/user/session keys."""
        from trpc_agent_sdk.memory import MemStorageEvent
        from trpc_agent_sdk.memory import RedisMemoryService
        from trpc_agent_sdk.memory import SqlMemoryService

        if not self.cleanup_data:
            return
        for app_name, user_id, session_id in sorted(self.touched_sessions):
            await self.session_service.delete_session(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
            )

        if isinstance(self.memory_service, SqlMemoryService):
            storage = self.memory_service._sql_storage
            async with storage.create_db_session() as sql_session:
                save_keys = sorted({user_key(app_name, user_id) for app_name, user_id, _ in self.touched_sessions})
                for save_key in save_keys:
                    await storage.delete(
                        sql_session,
                        SqlKey(key=(save_key,), storage_cls=MemStorageEvent),
                        SqlCondition(filters=[MemStorageEvent.save_key == save_key]),
                    )
                await storage.commit(sql_session)

        if isinstance(self.session_service, SqlSessionService):
            storage = self.session_service._sql_storage
            async with storage.create_db_session() as sql_session:
                app_users = sorted({(app_name, user_id) for app_name, user_id, _ in self.touched_sessions})
                for app_name, user_id in app_users:
                    await storage.delete(
                        sql_session,
                        SqlKey(key=(app_name, user_id), storage_cls=StorageUserState),
                        SqlCondition(filters=[
                            StorageUserState.app_name == app_name,
                            StorageUserState.user_id == user_id,
                        ]),
                    )
                for app_name in sorted({item[0] for item in self.touched_sessions}):
                    await storage.delete(
                        sql_session,
                        SqlKey(key=(app_name,), storage_cls=StorageAppState),
                        SqlCondition(filters=[StorageAppState.app_name == app_name]),
                    )
                await storage.commit(sql_session)

        if isinstance(self.memory_service, RedisMemoryService):
            storage = self.memory_service._redis_storage
            async with storage.create_db_session() as redis_session:
                for app_name, user_id, session_id in sorted(self.touched_sessions):
                    await storage.delete(redis_session, f"memory:{user_key(app_name, user_id)}:{session_id}")

        if isinstance(self.session_service, RedisSessionService):
            storage = self.session_service._redis_storage
            async with storage.create_db_session() as redis_session:
                app_users = sorted({(app_name, user_id) for app_name, user_id, _ in self.touched_sessions})
                for app_name, user_id in app_users:
                    await storage.delete(redis_session, f"user_state:{app_name}:{user_id}")
                for app_name in sorted({item[0] for item in self.touched_sessions}):
                    await storage.delete(redis_session, f"app_state:{app_name}")

    async def close(self) -> None:
        """Release both services, even if they share the same remote server."""
        if not self.close_services:
            return
        await self.memory_service.close()
        await self.session_service.close()


@dataclass
class BackendReplayResult:
    """Raw and normalized results for one backend/case pair."""

    backend: str
    snapshot: dict[str, Any]
    normalized: dict[str, Any]
    allowed_diff: list[dict[str, Any]]
    normalizations: list[dict[str, Any]]
    operation_errors: list[dict[str, Any]]


class ReplayNormalizer:
    """Normalize non-business storage differences before comparison."""

    _POLICY: ClassVar[tuple[str, ...]] = (
        "NFKC and whitespace folding for message, tool payload, memory, and summary text",
        "stable aliases for backend-generated event identifiers",
        "validated timestamp tokens while preserving list order and timestamp presence",
        "recursive dictionary comparison independent of serialized key order",
        "memory result sorting only when recorded as a backend/path-scoped allowed_diff",
    )

    @property
    def policy(self) -> list[str]:
        """Return a copy of the active normalization policy."""
        return list(self._POLICY)

    def normalize(
        self,
        snapshot: dict[str, Any],
        backend: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        """Normalize one backend snapshot."""
        return _normalize_snapshot(snapshot, backend)

    def normalize_expected(self, expected: dict[str, Any]) -> dict[str, Any]:
        """Normalize semantic strings in a partial expected snapshot."""
        return _normalize_semantic_strings(expected)


class ReplayComparator:
    """Compare replay snapshots and attach actionable field locators."""

    def compare(
        self,
        reference: Any,
        candidate: Any,
        backend: str,
        reference_backend: str,
        subset: bool = False,
    ) -> list[dict[str, Any]]:
        """Compare two snapshots recursively."""
        return _compare_snapshots(
            reference,
            candidate,
            backend=backend,
            reference_backend=reference_backend,
            subset=subset,
        )


class ReplayReport:
    """Serialize replay reports without choosing an application output path."""

    @staticmethod
    def dumps(report: dict[str, Any]) -> str:
        """Serialize a report deterministically."""
        return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    @classmethod
    def write(cls, report: dict[str, Any], path: Path) -> None:
        """Write a stable UTF-8 report to a caller-provided path."""
        path.write_text(cls.dumps(report), encoding="utf-8")


@dataclass
class ReplayRunResult:
    """Structured report plus per-backend snapshots for programmatic analysis."""

    report: dict[str, Any]
    backend_results: dict[str, dict[str, BackendReplayResult]]


class ReplayHarness:
    """Drive identical replay cases through multiple backend implementations."""

    def __init__(
        self,
        backends: Sequence[ReplayBackend],
        reference_backend: Optional[str] = None,
        profile: str = "custom",
        run_prefix: Optional[str] = None,
        normalizer: Optional[ReplayNormalizer] = None,
        comparator: Optional[ReplayComparator] = None,
    ) -> None:
        if not backends:
            raise ValueError("ReplayHarness requires at least one backend")
        backend_names = [backend.name for backend in backends]
        if len(backend_names) != len(set(backend_names)):
            raise ValueError("Replay backend names must be unique")
        selected_reference = reference_backend or backend_names[0]
        if selected_reference not in backend_names:
            raise ValueError(f"Reference backend {selected_reference!r} is not configured")
        self.backends = list(backends)
        self.reference_backend = selected_reference
        self.profile = profile
        self.run_prefix = run_prefix or f"replay_{uuid.uuid4().hex[:12]}"
        self.normalizer = normalizer or ReplayNormalizer()
        self.comparator = comparator or ReplayComparator()

    @classmethod
    def create_in_memory(cls, run_prefix: Optional[str] = None) -> "ReplayHarness":
        """Create a harness containing only the in-memory backend."""
        return cls(
            backends=[ReplayBackend.in_memory()],
            reference_backend="in_memory",
            profile="in-memory",
            run_prefix=run_prefix,
        )

    @classmethod
    def create_lightweight(
        cls,
        work_dir: Optional[Path] = None,
        run_prefix: Optional[str] = None,
    ) -> "ReplayHarness":
        """Create an in-memory versus file-backed SQLite harness."""
        replay_work_dir = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="trpc-replay-"))
        replay_work_dir.mkdir(parents=True, exist_ok=True)
        sqlite_path = (replay_work_dir / "replay.sqlite3").resolve()
        return cls(
            backends=[
                ReplayBackend.in_memory(),
                ReplayBackend.sql(f"sqlite:///{sqlite_path.as_posix()}", name="sqlite"),
            ],
            reference_backend="in_memory",
            profile="lightweight",
            run_prefix=run_prefix,
        )

    @classmethod
    def create_integration(
        cls,
        sql_url: Optional[str] = None,
        redis_url: Optional[str] = None,
        run_prefix: Optional[str] = None,
    ) -> "ReplayHarness":
        """Create an opt-in harness for explicitly configured SQL/Redis services."""
        backends = [ReplayBackend.in_memory()]
        if sql_url:
            backends.append(ReplayBackend.sql(sql_url))
        if redis_url:
            backends.append(ReplayBackend.redis(redis_url))
        return cls(
            backends=backends,
            reference_backend="in_memory",
            profile="integration",
            run_prefix=run_prefix,
        )

    @staticmethod
    def load_cases(case_dir: Path) -> list[ReplayCase]:
        """Load replay cases from a caller-provided directory."""
        cases = [ReplayCase.load(path) for path in sorted(case_dir.glob("*.jsonl"))]
        case_ids = [case.case_id for case in cases]
        duplicates = sorted({case_id for case_id in case_ids if case_ids.count(case_id) > 1})
        if duplicates:
            raise ReplayCaseError(f"{case_dir}: duplicate case_id values: {', '.join(duplicates)}")
        return cases

    def integration_backend_names(self) -> list[str]:
        """Return integration backends explicitly configured by the caller."""
        if self.profile != "integration":
            return []
        return [backend.name for backend in self.backends if backend.name != self.reference_backend]

    async def run(self, cases: list[ReplayCase]) -> ReplayRunResult:
        """Replay cases through every configured backend and return a report."""
        backends = self.backends
        results: dict[str, dict[str, BackendReplayResult]] = {}
        initialized: list[ReplayBackend] = []
        try:
            for backend in backends:
                await backend.initialize()
                initialized.append(backend)
            for case in cases:
                results[case.case_id] = {}
                for backend in backends:
                    results[case.case_id][backend.name] = await self._replay_case(backend, case)
            return ReplayRunResult(
                report=self._build_report(cases, backends, results),
                backend_results=results,
            )
        finally:
            cleanup_errors = []
            for backend in reversed(initialized):
                try:
                    await backend.cleanup()
                except Exception as exc:  # pylint: disable=broad-except
                    cleanup_errors.append(f"{backend.name}: cleanup: {type(exc).__name__}")
                try:
                    await backend.close()
                except Exception as exc:  # pylint: disable=broad-except
                    cleanup_errors.append(f"{backend.name}: close: {type(exc).__name__}")
            if cleanup_errors:
                raise RuntimeError("; ".join(cleanup_errors))

    async def _replay_case(self, backend: ReplayBackend, case: ReplayCase) -> BackendReplayResult:
        app_name = f"{self.run_prefix}_{case.case_id}"
        user_id = "replay-user"
        sessions: dict[str, Session] = {}
        queries: dict[str, Any] = {}
        operation_errors: list[dict[str, Any]] = []
        id_aliases: dict[str, str] = {}
        checkpoints: list[str] = []

        for operation in case.operations:
            operation_name = operation["op"]
            try:
                if operation_name == "create_session":
                    session_id = operation["session_id"]
                    sessions[session_id] = await backend.session_service.create_session(
                        app_name=app_name,
                        user_id=user_id,
                        session_id=session_id,
                        state=copy.deepcopy(operation.get("state")),
                    )
                    backend.touched_sessions.add((app_name, user_id, session_id))
                elif operation_name == "append_event":
                    await self._append(backend, sessions, operation, id_aliases)
                elif operation_name == "store_memory":
                    session = await self._reload(backend, app_name, user_id, operation["session_id"])
                    sessions[session.id] = session
                    await backend.memory_service.store_session(session)
                elif operation_name == "search_memory":
                    response = await backend.memory_service.search_memory(
                        key=user_key(app_name, user_id),
                        query=operation["query"],
                        limit=operation.get("limit", 100),
                    )
                    queries[operation["query_id"]] = response
                elif operation_name == "summarize":
                    await self._summarize(
                        backend,
                        sessions,
                        operation,
                        app_name,
                        user_id,
                    )
                elif operation_name == "inject_failure":
                    await self._inject_failure(
                        backend,
                        sessions,
                        operation,
                        id_aliases,
                        app_name,
                        user_id,
                        operation_errors,
                    )
                elif operation_name == "checkpoint":
                    checkpoints.append(operation["checkpoint_id"])
            except Exception as exc:  # pylint: disable=broad-except
                operation_errors.append(
                    _operation_error(
                        exc,
                        operation_name,
                        operation["operation_id"],
                        operation.get("session_id"),
                        operation.get("event", {}).get("id"),
                    ))
                if operation_name in {"append_event", "summarize"} and operation.get("session_id") in sessions:
                    session_id = operation["session_id"]
                    sessions[session_id] = await self._reload(backend, app_name, user_id, session_id)

        for session_id in list(sessions):
            sessions[session_id] = await self._reload(backend, app_name, user_id, session_id)
        snapshot = await self._snapshot(
            backend,
            sessions,
            queries,
            id_aliases,
            operation_errors,
            checkpoints,
        )
        normalized, allowed_diff, normalizations = self.normalizer.normalize(snapshot, backend.name)
        return BackendReplayResult(
            backend=backend.name,
            snapshot=snapshot,
            normalized=normalized,
            allowed_diff=allowed_diff,
            normalizations=normalizations,
            operation_errors=operation_errors,
        )

    async def _append(
        self,
        backend: ReplayBackend,
        sessions: dict[str, Session],
        operation: dict[str, Any],
        id_aliases: dict[str, str],
    ) -> Event:
        session = sessions[operation["session_id"]]
        event = Event.model_validate(copy.deepcopy(operation["event"]))
        logical_event_id = operation.get("logical_event_id")
        if logical_event_id:
            id_aliases[event.id] = logical_event_id
        await backend.session_service.append_event(session, event)
        return event

    async def _summarize(
        self,
        backend: ReplayBackend,
        sessions: dict[str, Session],
        operation: dict[str, Any],
        app_name: str,
        user_id: str,
    ) -> None:
        session_id = operation["session_id"]
        session = await self._reload(backend, app_name, user_id, session_id)
        sessions[session_id] = session
        backend.summary_model.push(operation["text"])
        summarizer = SessionSummarizer(
            model=backend.summary_model,
            keep_recent_count=operation.get("keep_recent_count", 2),
            start_by_user_turn=operation.get("start_by_user_turn", True),
        )
        manager = backend.session_service.summarizer_manager
        manager.set_summarizer(summarizer, force=True)
        await manager.create_session_summary(session, force=True)
        summary_events = [event for event in session.events if event.is_summary_event()]
        if not summary_events:
            raise RuntimeError("Summarizer did not create a summary event")
        summary_event = summary_events[0]
        summary_event.id = f"summary-event-{operation['summary_id']}"
        summary_event.timestamp = float(operation["updated_at"])
        metadata = {
            "summary_id": operation["summary_id"],
            "session_id": session_id,
            "version": operation["version"],
            "updated_at": operation["updated_at"],
            "replaces_summary_id": operation.get("replaces_summary_id"),
        }
        custom_metadata = dict(summary_event.custom_metadata or {})
        custom_metadata["replay_summary"] = metadata
        summary_event.custom_metadata = custom_metadata
        cached_summary = await manager.get_session_summary(session)
        if cached_summary:
            cached_summary.summary_timestamp = float(operation["updated_at"])
        await backend.session_service.update_session(session)
        sessions[session_id] = await self._reload(backend, app_name, user_id, session_id)

    async def _inject_failure(
        self,
        backend: ReplayBackend,
        sessions: dict[str, Session],
        operation: dict[str, Any],
        id_aliases: dict[str, str],
        app_name: str,
        user_id: str,
        operation_errors: list[dict[str, Any]],
    ) -> None:
        if operation["failure"] != "after_session_before_memory":
            raise ValueError(f"Unsupported injected failure {operation['failure']!r}")
        append_operation = {
            "session_id": operation["session_id"],
            "event": operation["event"],
            "logical_event_id": operation.get("logical_event_id"),
        }
        event = await self._append(backend, sessions, append_operation, id_aliases)
        operation_errors.append(
            _operation_error(
                InjectedReplayFailure("session committed before memory write"),
                "inject_failure",
                operation["operation_id"],
                operation["session_id"],
                event.id,
            ))
        sessions[operation["session_id"]] = await self._reload(
            backend,
            app_name,
            user_id,
            operation["session_id"],
        )
        if operation.get("retry", True):
            try:
                await self._append(backend, sessions, append_operation, id_aliases)
            except Exception as exc:  # pylint: disable=broad-except
                operation_errors.append(
                    _operation_error(
                        exc,
                        "retry_append",
                        operation["operation_id"],
                        operation["session_id"],
                        event.id,
                    ))
                sessions[operation["session_id"]] = await self._reload(
                    backend,
                    app_name,
                    user_id,
                    operation["session_id"],
                )
        if operation.get("store_memory_after_retry", True):
            await backend.memory_service.store_session(sessions[operation["session_id"]])

    @staticmethod
    async def _reload(backend: ReplayBackend, app_name: str, user_id: str, session_id: str) -> Session:
        session = await backend.session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        if session is None:
            raise RuntimeError(f"Session {session_id!r} disappeared during replay")
        return session

    async def _snapshot(
        self,
        backend: ReplayBackend,
        sessions: dict[str, Session],
        queries: dict[str, Any],
        id_aliases: dict[str, str],
        operation_errors: list[dict[str, Any]],
        checkpoints: list[str],
    ) -> dict[str, Any]:
        session_snapshots = {}
        for session_id, session in sorted(sessions.items()):
            manager_summary = await backend.session_service.summarizer_manager.get_session_summary(session)
            session_snapshots[session_id] = _session_snapshot(session, manager_summary, id_aliases)
        memory_snapshots = {}
        for query_id, response in sorted(queries.items()):
            entries = []
            for memory in response.memories:
                content = memory.content.model_dump(mode="json", exclude_none=True)
                entries.append({
                    "author": memory.author,
                    "content": content,
                    "text": _content_text(content),
                    "timestamp": memory.timestamp,
                })
            memory_snapshots[query_id] = {
                "entries": entries,
                "texts": [entry["text"] for entry in entries],
            }
        return {
            "sessions": session_snapshots,
            "memory": memory_snapshots,
            "operation_errors": copy.deepcopy(operation_errors),
            "checkpoints": checkpoints,
        }

    def _build_report(
        self,
        cases: list[ReplayCase],
        backends: list[ReplayBackend],
        results: dict[str, dict[str, BackendReplayResult]],
    ) -> dict[str, Any]:
        case_reports = []
        mismatch_comparisons = 0
        for case in cases:
            backend_reports = {}
            for backend in backends:
                result = results[case.case_id][backend.name]
                expected_diffs = self.comparator.compare(
                    self.normalizer.normalize_expected(case.expected),
                    result.normalized,
                    backend=backend.name,
                    reference_backend="expected",
                    subset=True,
                )
                status = "match" if not expected_diffs and not result.operation_errors else "mismatch"
                backend_reports[backend.name] = {
                    "status": status,
                    "differences_to_expected": expected_diffs,
                    "operation_errors": result.operation_errors,
                    "normalizations": result.normalizations,
                    "allowed_diff": result.allowed_diff,
                }

            comparisons = []
            reference = results[case.case_id][self.reference_backend]
            for backend in backends:
                if backend.name == self.reference_backend:
                    continue
                candidate = results[case.case_id][backend.name]
                differences = self.comparator.compare(
                    reference.normalized,
                    candidate.normalized,
                    backend=backend.name,
                    reference_backend=self.reference_backend,
                )
                comparison_status = "match" if not differences else "mismatch"
                if differences:
                    mismatch_comparisons += 1
                comparisons.append({
                    "reference_backend": self.reference_backend,
                    "backend": backend.name,
                    "status": comparison_status,
                    "differences": differences,
                })
            case_reports.append({
                "case_id": case.case_id,
                "metadata": copy.deepcopy(case.metadata),
                "backends": backend_reports,
                "comparisons": comparisons,
            })

        comparison_count = len(cases) * max(len(backends) - 1, 0)
        return {
            "schema_version": 1,
            "mode": self.profile,
            "reference_backend": self.reference_backend,
            "backends": [backend.name for backend in backends],
            "normalization_policy": self.normalizer.policy,
            "cases": case_reports,
            "metrics": {
                "total_cases": len(cases),
                "backend_comparisons": comparison_count,
                "mismatched_backend_comparisons": mismatch_comparisons,
            },
        }


def _session_snapshot(session: Session, manager_summary: Any, id_aliases: dict[str, str]) -> dict[str, Any]:
    events = [_event_snapshot(event, id_aliases) for event in session.events]
    historical_events = [_event_snapshot(event, id_aliases) for event in session.historical_events]
    summary_events = [event for event in session.events if event.is_summary_event()]
    summary = None
    if summary_events:
        summary_event = summary_events[0]
        metadata = (summary_event.custom_metadata or {}).get("replay_summary", {})
        summary_text = summary_event.get_text()
        prefix = "Previous conversation summary:"
        if summary_text.startswith(prefix):
            summary_text = summary_text[len(prefix):].strip()
        summary = {
            "summary_id": metadata.get("summary_id"),
            "event_id": id_aliases.get(summary_event.id, summary_event.id),
            "session_id": metadata.get("session_id"),
            "manager_session_id": getattr(manager_summary, "session_id", None),
            "version": metadata.get("version"),
            "updated_at": metadata.get("updated_at"),
            "replaces_summary_id": metadata.get("replaces_summary_id"),
            "summary_text": summary_text,
            "manager_text": getattr(manager_summary, "summary_text", None),
            "original_event_count": getattr(manager_summary, "original_event_count", None),
            "compressed_event_count": getattr(manager_summary, "compressed_event_count", None),
            "manager_updated_at": getattr(manager_summary, "summary_timestamp", None),
        }
    return {
        "id": session.id,
        "state": copy.deepcopy(session.state),
        "conversation_count": session.conversation_count,
        "last_update_time": session.last_update_time,
        "events": events,
        "event_ids": [event["id"] for event in events],
        "historical_events": historical_events,
        "historical_event_ids": [event["id"] for event in historical_events],
        "summary": summary,
    }


def _event_snapshot(event: Event, id_aliases: dict[str, str]) -> dict[str, Any]:
    content = event.content.model_dump(mode="json", exclude_none=True) if event.content else None
    return {
        "id": id_aliases.get(event.id, event.id),
        "invocation_id": event.invocation_id,
        "author": event.author,
        "timestamp": event.timestamp,
        "content": content,
        "state_delta": copy.deepcopy(event.actions.state_delta),
        "partial": event.partial,
        "branch": event.branch,
        "model_flags": event.model_flags,
        "is_summary": event.is_summary_event(),
    }


def _normalize_snapshot(
    snapshot: dict[str, Any],
    backend: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize only documented non-business differences."""
    normalized = copy.deepcopy(snapshot)
    allowed_diff: list[dict[str, Any]] = []
    normalizations: list[dict[str, Any]] = []
    normalization_issues: list[dict[str, Any]] = []

    for session_id, session in normalized["sessions"].items():
        _normalize_timestamp(
            session,
            "last_update_time",
            "/sessions/{}/last_update_time".format(_pointer_escape(session_id)),
            "<timestamp>",
            normalization_issues,
        )
        for collection_name in ("events", "historical_events"):
            previous_timestamp: Optional[float] = None
            for index, event in enumerate(session[collection_name]):
                path = f"/sessions/{_pointer_escape(session_id)}/{collection_name}/{index}/timestamp"
                value = event.get("timestamp")
                if not _is_number(value):
                    normalization_issues.append({
                        "path": path,
                        "reason": "timestamp is missing or non-numeric",
                        "value": value,
                    })
                else:
                    numeric = float(value)
                    if previous_timestamp is not None and numeric < previous_timestamp:
                        normalization_issues.append({
                            "path": path,
                            "reason": "timestamp order moved backwards",
                            "value": value,
                        })
                    previous_timestamp = numeric
                    event["timestamp"] = f"<timestamp:{index}>"
                    normalizations.append({"path": path, "strategy": "validated positional timestamp token"})
        summary = session.get("summary")
        if summary is not None and _is_number(summary.get("manager_updated_at")):
            summary["manager_updated_at"] = summary["updated_at"]
            normalizations.append({
                "path": f"/sessions/{_pointer_escape(session_id)}/summary/manager_updated_at",
                "strategy": "manager clock represented by persisted logical updated_at",
            })

    normalized = _normalize_semantic_strings(normalized)
    for query_id, memory in normalized["memory"].items():
        entries = memory["entries"]
        for index, entry in enumerate(entries):
            path = f"/memory/{_pointer_escape(query_id)}/entries/{index}/timestamp"
            if entry.get("timestamp") is None:
                normalization_issues.append({
                    "path": path,
                    "reason": "memory timestamp is missing",
                    "value": None,
                })
            else:
                try:
                    datetime.fromisoformat(entry["timestamp"])
                except (TypeError, ValueError):
                    normalization_issues.append({
                        "path": path,
                        "reason": "memory timestamp is not ISO-8601",
                        "value": entry["timestamp"],
                    })
                else:
                    entry["timestamp"] = "<memory-timestamp>"
        original_order = [_memory_fingerprint(entry) for entry in entries]
        entries.sort(key=_memory_fingerprint)
        sorted_order = [_memory_fingerprint(entry) for entry in entries]
        memory["texts"] = [entry["text"] for entry in entries]
        if original_order != sorted_order:
            path = f"/memory/{_pointer_escape(query_id)}/entries"
            allowed_diff.append({
                "backend": backend,
                "path": path,
                "reason": "Memory services do not guarantee a common result order",
                "strategy": "sort by normalized author/content fingerprint",
                "backend_value": original_order,
                "normalized_value": sorted_order,
            })
            normalizations.append({"path": path, "strategy": "documented memory result ordering"})
    normalized["normalization_issues"] = normalization_issues
    return normalized, allowed_diff, normalizations


def _compare_snapshots(
    reference: Any,
    candidate: Any,
    backend: str,
    reference_backend: str,
    subset: bool = False,
) -> list[dict[str, Any]]:
    """Recursively compare snapshots and return JSON-Pointer-addressable differences."""
    differences: list[dict[str, Any]] = []

    def visit(left: Any, right: Any, path: str) -> None:
        if isinstance(left, dict) and isinstance(right, dict):
            keys = sorted(left.keys() if subset else left.keys() | right.keys())
            for key in keys:
                child_path = f"{path}/{_pointer_escape(str(key))}"
                if key not in right:
                    add_difference(child_path, left[key], "<missing>")
                elif key not in left:
                    add_difference(child_path, "<missing>", right[key])
                else:
                    visit(left[key], right[key], child_path)
            return
        if isinstance(left, list) and isinstance(right, list):
            if len(left) != len(right):
                add_difference(f"{path}/length", len(left), len(right))
            for index, (left_item, right_item) in enumerate(zip(left, right)):
                visit(left_item, right_item, f"{path}/{index}")
            return
        if type(left) is not type(right) or left != right:
            add_difference(path or "/", left, right)

    def add_difference(path: str, left: Any, right: Any) -> None:
        session_id = _session_id_from_pointer(path)
        event_match = re.search(
            r"/(?:events|historical_events|event_ids|historical_event_ids)/(\d+)(?:/|$)",
            path,
        )
        event_length_match = re.search(
            r"/(?:events|historical_events|event_ids|historical_event_ids)/length$",
            path,
        )
        memory_match = re.search(r"^/memory/([^/]+)/(?:entries|texts)/(\d+)(?:/|$)", path)
        memory_length_match = re.search(r"^/memory/([^/]+)/(?:entries|texts)/length$", path)
        summary_id = _summary_id_for_difference(reference, candidate, session_id)
        difference = {
            "backend": backend,
            "reference_backend": reference_backend,
            "session_id": session_id,
            "path": path,
            "reference_value": left,
            "backend_value": right,
            "allowed": False,
        }
        if event_match:
            difference["event_index"] = int(event_match.group(1))
        elif event_length_match and isinstance(left, int) and isinstance(right, int):
            difference["event_index"] = min(left, right)
        if memory_match:
            difference["query_id"] = _pointer_unescape(memory_match.group(1))
            difference["memory_index"] = int(memory_match.group(2))
        elif memory_length_match:
            difference["query_id"] = _pointer_unescape(memory_length_match.group(1))
            if isinstance(left, int) and isinstance(right, int):
                difference["memory_index"] = min(left, right)
        if "/summary" in path:
            difference["summary_id"] = summary_id
        differences.append(difference)

    visit(reference, candidate, "")
    return differences


def _normalize_semantic_strings(value: Any, path: str = "") -> Any:
    if isinstance(value, dict):
        return {
            key: _normalize_semantic_strings(item, f"{path}/{_pointer_escape(str(key))}")
            for key, item in sorted(value.items())
        }
    if isinstance(value, list):
        return [_normalize_semantic_strings(item, f"{path}/{index}") for index, item in enumerate(value)]
    semantic_path = (
        "/content" in path
        or path.endswith("/text")
        or path.endswith("/summary_text")
        or path.endswith("/manager_text")
    )
    if semantic_path and isinstance(value, str):
        return " ".join(unicodedata.normalize("NFKC", value).split())
    return value


def _normalize_timestamp(
    container: dict[str, Any],
    key: str,
    path: str,
    token: str,
    issues: list[dict[str, Any]],
) -> None:
    value = container.get(key)
    if not _is_number(value):
        issues.append({
            "path": path,
            "reason": "timestamp is missing or non-numeric",
            "value": value,
        })
    else:
        container[key] = token


def _memory_fingerprint(entry: dict[str, Any]) -> str:
    payload = {
        "author": entry.get("author"),
        "content": entry.get("content"),
        "text": entry.get("text"),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _content_text(content: dict[str, Any]) -> str:
    return "".join(part.get("text", "") for part in content.get("parts", []))


def _operation_error(
    exc: Exception,
    stage: str,
    operation_id: str,
    session_id: Optional[str],
    event_id: Optional[str],
) -> dict[str, Any]:
    return {
        "type": type(exc).__name__,
        "stage": stage,
        "operation_id": operation_id,
        "session_id": session_id,
        "event_id": event_id,
    }


def _summary_id_for_difference(reference: Any, candidate: Any, session_id: Optional[str]) -> Optional[str]:
    if not session_id:
        return None
    for snapshot in (candidate, reference):
        if not isinstance(snapshot, dict):
            continue
        summary = snapshot.get("sessions", {}).get(session_id, {}).get("summary")
        if isinstance(summary, dict) and summary.get("summary_id"):
            return summary["summary_id"]
    return None


def _session_id_from_pointer(path: str) -> Optional[str]:
    parts = path.split("/")
    if len(parts) > 2 and parts[1] == "sessions":
        return _pointer_unescape(parts[2])
    return None


def _pointer_escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _pointer_unescape(value: str) -> str:
    return value.replace("~1", "/").replace("~0", "~")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
