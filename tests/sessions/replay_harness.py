"""Replay harness for session/memory consistency tests.

This module provides:
- backend adapters for replaying the same case on multiple backends,
- snapshot extraction helpers,
- normalization helpers for backend-specific noise,
- structured diff generation,
- JSON report writing.

The first iteration intentionally focuses on deterministic, LLM-free smoke
cases. More complex summary and failure-injection cases can be added on top of
the same protocol without changing the adapter boundary.
"""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import json
import re
import tempfile
import time
import uuid
from typing import Any
from typing import Iterator
from typing import Optional
from urllib.parse import urlparse

from google.genai.types import Content
from google.genai.types import FunctionCall
from google.genai.types import FunctionResponse
from google.genai.types import Part
from trpc_agent_sdk.abc import MemoryServiceConfig
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.memory import BaseMemoryService
from trpc_agent_sdk.memory import InMemoryMemoryService
from trpc_agent_sdk.memory import RedisMemoryService
from trpc_agent_sdk.memory import SqlMemoryService
from trpc_agent_sdk.sessions import BaseSessionService
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.sessions import RedisSessionService
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.sessions import SessionServiceConfig
from trpc_agent_sdk.sessions import SqlSessionService

from .replay_models import BackendSnapshot
from .replay_models import DiffEntry
from .replay_models import EventSpec
from .replay_models import FunctionCallSpec
from .replay_models import FunctionResponseSpec
from .replay_models import MemoryQuerySpec
from .replay_models import ReplayCase
from .replay_models import ReplayStep
from .replay_models import ReplayStepKind
from .replay_models import RuntimeFault
from .replay_models import RuntimeFaultOperation
from .replay_models import SessionSnapshot
from .replay_models import SnapshotMutation
from .replay_models import SnapshotMutationOperation
from .replay_models import SummarySnapshot
from .replay_summary import build_replay_summarizer_manager


DEFAULT_REPORT_PATH = Path(__file__).with_name("session_memory_summary_diff_report.json")
_EVENT_INDEX_RE = re.compile(r"\[(\d+)\]")
_SUMMARY_EVENT_METADATA_KEY = "session_summary"
_CASE_TIME_BASES: dict[str, float] = {}
_BASELINE_BACKEND_NAME = "inmemory"
_PERSISTENT_BACKEND_TARGETS = {"persistent", "secondary", "non_baseline"}
_BASELINE_BACKEND_TARGETS = {"baseline", "primary", "inmemory"}
_CASE_TIME_FUTURE_SKEW_SECONDS = 60.0
_REPLAY_CLOCK_MODE_ENV = "TRPC_AGENT_REPLAY_CLOCK_MODE"
_REPLAY_FIXED_EPOCH_ENV = "TRPC_AGENT_REPLAY_FIXED_EPOCH"
_REPLAY_CLOCK_MODE_FRESHNESS_SAFE = "freshness_safe"
_REPLAY_CLOCK_MODE_FIXED_SAFE = "fixed_safe"
_DEFAULT_FIXED_SAFE_EPOCH = 4_102_444_800.0


def _make_memory_config() -> MemoryServiceConfig:
    config = MemoryServiceConfig(enabled=True)
    config.clean_ttl_config()
    return config


def _make_session_config() -> SessionServiceConfig:
    config = SessionServiceConfig()
    config.clean_ttl_config()
    return config


class ReplayBackendAdapter(ABC):
    """Shared backend replay logic.

    Concrete adapters only need to build the concrete session and memory
    services. The replay protocol itself stays backend-agnostic.
    """

    name: str

    def __init__(self) -> None:
        self._session_service: BaseSessionService | None = None
        self._memory_service: BaseMemoryService | None = None
        self._session: Session | None = None
        self._sessions: dict[str, Session] = {}
        self._session_identities: dict[str, dict[str, str]] = {}
        self._active_session_alias = "default"
        self._memory_results: dict[str, Any] = {}
        self._case: ReplayCase | None = None
        self._event_sequence = 0
        self._event_time_base = 0.0

    @property
    def session_service(self) -> BaseSessionService:
        if self._session_service is None:
            raise RuntimeError("Session service is not initialized.")
        return self._session_service

    @property
    def memory_service(self) -> BaseMemoryService:
        if self._memory_service is None:
            raise RuntimeError("Memory service is not initialized.")
        return self._memory_service

    @property
    def session(self) -> Session:
        if self._session is None:
            raise RuntimeError("Replay session has not been created.")
        return self._session

    @property
    def case(self) -> ReplayCase:
        if self._case is None:
            raise RuntimeError("Replay case is not initialized.")
        return self._case

    async def setup(self, case: ReplayCase) -> None:
        """Initialize backend services for one replay run."""

        self._case = case
        self._memory_results = {}
        self._session = None
        self._sessions = {}
        self._session_identities = {}
        self._active_session_alias = "default"
        self._event_sequence = 0
        self._event_time_base = _deterministic_time_base(case.case_id)
        self._session_service = await self._build_session_service()
        self._memory_service = await self._build_memory_service()

    async def _close_services(self) -> None:
        """Dispose backend services while preserving replay state."""
        if self._memory_service is not None:
            await self._memory_service.close()
            self._memory_service = None
        if self._session_service is not None:
            await self._session_service.close()
            self._session_service = None

    async def close(self) -> None:
        """Dispose backend services."""

        await self._close_services()
        self._session = None
        self._sessions = {}
        self._session_identities = {}
        self._active_session_alias = "default"
        self._memory_results = {}
        self._case = None
        self._event_sequence = 0
        self._event_time_base = 0.0

    async def run_case(self, case: ReplayCase) -> BackendSnapshot:
        """Replay all steps in a case and collect a snapshot."""

        for step_index, step in enumerate(case.steps):
            await self._run_step(case, step, step_index)
            await self._apply_runtime_faults(step_index)
        if self.should_restart_before_snapshot():
            await self._restart_services()
        return await self.collect_snapshot(case)

    def should_restart_before_snapshot(self) -> bool:
        """Return whether the adapter should validate persisted read-back."""

        return False

    def get_runtime_metadata(self) -> dict[str, Any]:
        return {
            "backend_name": self.name,
            "storage_kind": self.name,
        }

    def get_report_metadata(self) -> dict[str, Any]:
        return self.get_runtime_metadata()

    async def _restart_services(self) -> None:
        """Restart backend services and rebuild the read snapshot from persistence."""

        await self._close_services()
        self._session_service = await self._build_session_service()
        self._memory_service = await self._build_memory_service()
        reopened_sessions: dict[str, Session] = {}
        for session_alias, identity in self._session_identities.items():
            reopened_session = await self.session_service.get_session(
                app_name=identity["app_name"],
                user_id=identity["user_id"],
                session_id=identity["session_id"],
            )
            if reopened_session is not None:
                reopened_sessions[session_alias] = reopened_session
        self._sessions = reopened_sessions
        self._session = self._sessions.get(self._active_session_alias)
        if self._session is None and self._sessions:
            self._active_session_alias = next(iter(self._sessions))
            self._session = self._sessions[self._active_session_alias]
        if self._session is None:
            return

    def should_restart_during_replay(self) -> bool:
        """Return whether RESTART_SERVICES steps should perform a real restart."""

        return False

    async def _run_step(self, case: ReplayCase, step: ReplayStep, step_index: int) -> None:
        if step.kind == ReplayStepKind.CREATE_SESSION:
            session_id = step.session_id or case.session_id
            session = await self.session_service.create_session(
                app_name=step.app_name or case.app_name,
                user_id=step.user_id or case.user_id,
                session_id=session_id,
                state=step.initial_state,
            )
            self._session = session
            self._sessions[step.session_alias] = session
            self._session_identities[step.session_alias] = self._make_session_identity(session)
            self._active_session_alias = step.session_alias
            return

        if step.kind in {ReplayStepKind.APPEND_EVENT, ReplayStepKind.APPEND_STATE}:
            if step.event is None:
                raise ValueError(f"Replay step {step.kind} requires an event.")
            target_session = self._resolve_session(step.session_alias)
            self._event_sequence += 1
            event = build_event(
                step.event,
                sequence=self._event_sequence,
                timestamp=self._event_time_base + (self._event_sequence / 1000.0),
            )
            await self.session_service.append_event(target_session, event)
            self._session = target_session
            return

        if step.kind == ReplayStepKind.STORE_MEMORY:
            target_session = self._resolve_session(step.session_alias)
            await self.memory_service.store_session(target_session)
            self._session = target_session
            return

        if step.kind == ReplayStepKind.SEARCH_MEMORY:
            if step.memory_query is None:
                raise ValueError("SEARCH_MEMORY step requires a query specification.")
            target_session = self._resolve_session(step.session_alias)
            entries = await self._search_memory_for_session(target_session, step.memory_query)
            self._memory_results[self._memory_observation_key(step_index, step)] = {
                "query_name": step.memory_query.name,
                "session_alias": step.session_alias,
                "app_name": target_session.app_name,
                "user_id": target_session.user_id,
                "session_id": target_session.id,
                "step_index": step_index,
                "entries": entries,
            }
            self._session = target_session
            return

        if step.kind == ReplayStepKind.CREATE_SUMMARY:
            target_session = self._resolve_session(step.session_alias)
            self._event_sequence += 1
            summary_timestamp = self._event_time_base + (self._event_sequence / 1000.0)
            manager = getattr(self.session_service, "summarizer_manager", None)
            with _patched_time_time(summary_timestamp):
                if manager is not None and step.force_summary:
                    await manager.create_session_summary(target_session, force=True)
                else:
                    await self.session_service.create_session_summary(target_session)
            self._session = target_session
            return

        if step.kind == ReplayStepKind.RESTART_SERVICES:
            if self.should_restart_during_replay():
                await self._restart_services()
            return

        raise ValueError(f"Unsupported replay step kind: {step.kind}")

    async def _search_memory(self, query: MemoryQuerySpec) -> list[dict[str, Any]]:
        return await self._search_memory_for_session(self.session, query)

    async def _search_memory_for_session(
        self,
        session: Session,
        query: MemoryQuerySpec,
    ) -> list[dict[str, Any]]:
        response = await self.memory_service.search_memory(
            key=session.save_key,
            query=query.query,
            limit=query.limit,
        )
        return [_memory_entry_to_snapshot(entry) for entry in response.memories]

    async def collect_snapshot(self, case: ReplayCase) -> BackendSnapshot:
        """Collect a backend snapshot after replay."""

        if self._session is None:
            raise RuntimeError(f"Replay case '{case.case_id}' did not produce an active session.")
        sessions_by_alias: dict[str, SessionSnapshot] = {}
        for session_alias, identity in self._session_identities.items():
            session = await self.session_service.get_session(
                app_name=identity["app_name"],
                user_id=identity["user_id"],
                session_id=identity["session_id"],
            )
            if session is None:
                raise RuntimeError(f"Replay session '{identity['session_id']}' was not found.")
            self._sessions[session_alias] = session
            sessions_by_alias[session_alias] = await self._build_session_snapshot(session_alias, session)

        active_alias = self._active_session_alias
        active_snapshot = sessions_by_alias.get(active_alias)
        if active_snapshot is None and sessions_by_alias:
            active_alias = next(iter(sessions_by_alias))
            active_snapshot = sessions_by_alias[active_alias]
            self._active_session_alias = active_alias
            self._session = self._sessions[active_alias]
        if active_snapshot is None:
            raise RuntimeError(f"Replay case '{case.case_id}' did not produce an active session.")

        return BackendSnapshot(
            backend_name=self.name,
            case_id=case.case_id,
            app_name=active_snapshot.app_name,
            user_id=active_snapshot.user_id,
            session_id=active_snapshot.session_id,
            active_session_alias=active_alias,
            session=active_snapshot.session,
            state=active_snapshot.state,
            memory=dict(self._memory_results),
            summary=active_snapshot.summary,
            sessions_by_alias=sessions_by_alias,
        )

    def _resolve_session(self, session_alias: str) -> Session:
        session = self._sessions.get(session_alias)
        if session is None:
            raise RuntimeError(f"Replay session alias '{session_alias}' has not been created.")
        self._active_session_alias = session_alias
        self._session = session
        return session

    async def _get_summary_snapshot(self, session: Session) -> Optional[SummarySnapshot]:
        manager = getattr(self.session_service, "summarizer_manager", None)
        if manager is None:
            return None

        summary = await manager.get_session_summary(session)
        if summary is None:
            return None

        return SummarySnapshot(
            session_id=summary.session_id,
            summary_text=summary.summary_text,
            original_event_count=summary.original_event_count,
            compressed_event_count=summary.compressed_event_count,
            summary_id=(summary.metadata or {}).get("summary_id"),
            version=(summary.metadata or {}).get("version"),
            replaces=(summary.metadata or {}).get("replaces"),
            summarized_event_count=(summary.metadata or {}).get("summarized_event_count"),
            summary_timestamp=getattr(summary, "summary_timestamp", None),
            metadata=dict(getattr(summary, "metadata", {}) or {}),
        )

    async def _apply_runtime_faults(self, step_index: int) -> None:
        for fault in self.case.runtime_faults:
            if not _backend_target_matches(fault.backend_name, self.name) or fault.after_step != step_index:
                continue
            await self._apply_runtime_fault(fault)

    async def _apply_runtime_fault(self, fault: RuntimeFault) -> None:
        target_session = self._get_fault_session(fault)
        if fault.operation == RuntimeFaultOperation.DUPLICATE_LAST_EVENT:
            if not target_session.events:
                raise RuntimeError("Cannot duplicate the last event of an empty session.")
            duplicated_event = target_session.events[-1].model_copy(deep=True)
            duplicated_event.id = f"{duplicated_event.id}-duplicate"
            duplicated_event.invocation_id = f"{duplicated_event.invocation_id}-duplicate"
            duplicated_event.timestamp += 0.0001
            target_session.events.append(duplicated_event)
            await self.session_service.update_session(target_session)
            return

        if fault.operation == RuntimeFaultOperation.DROP_LAST_EVENT_KEEP_STATE:
            if not target_session.events:
                raise RuntimeError("Cannot drop the last event of an empty session.")
            target_session.events.pop()
            await self.session_service.update_session(target_session)
            return

        if fault.operation == RuntimeFaultOperation.SET_SESSION_VALUE:
            if not fault.path:
                raise ValueError("SET_SESSION_VALUE fault requires a target path.")
            _set_path_value(target_session, fault.path, fault.value)
            await self.session_service.update_session(target_session)
            return

        manager = getattr(self.session_service, "summarizer_manager", None)
        if manager is None:
            raise RuntimeError("Runtime summary fault requires a summarizer manager.")

        if fault.operation == RuntimeFaultOperation.DELETE_SUMMARY:
            summary_cache = getattr(manager, "_summarizer_cache", {})
            summary_cache.get(target_session.app_name, {}).get(target_session.user_id, {}).pop(target_session.id, None)
            summary_event = _get_summary_event(target_session)
            if summary_event is not None:
                custom_metadata = dict(summary_event.custom_metadata or {})
                custom_metadata.pop(_SUMMARY_EVENT_METADATA_KEY, None)
                summary_event.custom_metadata = custom_metadata or None
                await self.session_service.update_session(target_session)
            return

        if fault.operation == RuntimeFaultOperation.SET_SUMMARY_VALUE:
            if not fault.path:
                raise ValueError("SET_SUMMARY_VALUE fault requires a target path.")
            summary = await manager.get_session_summary(target_session)
            if summary is None:
                raise RuntimeError("Cannot mutate summary value because no cached summary exists.")
            _set_path_value(summary, fault.path, fault.value)
            summary_event = _get_summary_event(target_session)
            if summary_event is not None and summary_event.custom_metadata:
                persisted_summary = summary_event.custom_metadata.get(_SUMMARY_EVENT_METADATA_KEY)
                if isinstance(persisted_summary, dict):
                    _set_path_value(persisted_summary, fault.path, fault.value)
                    await self.session_service.update_session(target_session)
            return

        raise ValueError(f"Unsupported runtime fault operation: {fault.operation}")

    def _make_session_identity(self, session: Session) -> dict[str, str]:
        return {
            "app_name": session.app_name,
            "user_id": session.user_id,
            "session_id": session.id,
        }

    def _memory_observation_key(self, step_index: int, step: ReplayStep) -> str:
        if step.memory_query is None:
            raise ValueError("Memory observation key requires a query specification.")
        return f"step_{step_index:03d}:{step.session_alias}:{step.memory_query.name}"

    def _get_fault_session(self, fault: RuntimeFault) -> Session:
        session = self._sessions.get(fault.session_alias)
        if session is None:
            raise RuntimeError(f"Runtime fault session alias '{fault.session_alias}' has not been created.")
        return session

    async def _build_session_snapshot(self, session_alias: str, session: Session) -> SessionSnapshot:
        return SessionSnapshot(
            session_alias=session_alias,
            app_name=session.app_name,
            user_id=session.user_id,
            session_id=session.id,
            session={
                "conversation_count": session.conversation_count,
                "events": [_event_to_snapshot(event) for event in session.events],
                "historical_events": [_event_to_snapshot(event) for event in session.historical_events],
            },
            state=dict(session.state),
            summary=await self._get_summary_snapshot(session),
        )

    @abstractmethod
    async def _build_session_service(self) -> BaseSessionService:
        """Create the session service used by this adapter."""

    @abstractmethod
    async def _build_memory_service(self) -> BaseMemoryService:
        """Create the memory service used by this adapter."""


class InMemoryReplayAdapter(ReplayBackendAdapter):
    """Replay adapter backed by in-memory session and memory services."""

    name = "inmemory"

    async def _build_session_service(self) -> BaseSessionService:
        config = _make_session_config()
        config.store_historical_events = self.case.store_historical_events
        summarizer_manager = None
        if self.case.enable_summary:
            summarizer_manager = build_replay_summarizer_manager(
                keep_recent_count=self.case.summary_keep_recent_count,
            )
        return InMemorySessionService(
            summarizer_manager=summarizer_manager,
            session_config=config,
        )

    async def _build_memory_service(self) -> BaseMemoryService:
        return InMemoryMemoryService(memory_service_config=_make_memory_config())


class SqliteReplayAdapter(ReplayBackendAdapter):
    """Replay adapter backed by SQLite-based session and memory services."""

    name = "sqlite"

    def __init__(self) -> None:
        super().__init__()
        self._temp_dir = tempfile.TemporaryDirectory(prefix="trpc-replay-sqlite-")
        self._session_db_url = self._sqlite_url("sessions.sqlite")
        self._memory_db_url = self._sqlite_url("memory.sqlite")

    def _sqlite_url(self, filename: str) -> str:
        return f"sqlite:///{(Path(self._temp_dir.name) / filename).as_posix()}"

    def should_restart_before_snapshot(self) -> bool:
        return True

    def should_restart_during_replay(self) -> bool:
        return True

    async def _build_session_service(self) -> BaseSessionService:
        config = _make_session_config()
        config.store_historical_events = self.case.store_historical_events
        summarizer_manager = None
        if self.case.enable_summary:
            summarizer_manager = build_replay_summarizer_manager(
                keep_recent_count=self.case.summary_keep_recent_count,
            )
        service = SqlSessionService(
            db_url=self._session_db_url,
            summarizer_manager=summarizer_manager,
            is_async=False,
            session_config=config,
        )
        await service._sql_storage.create_sql_engine()
        return service

    async def _build_memory_service(self) -> BaseMemoryService:
        service = SqlMemoryService(
            db_url=self._memory_db_url,
            is_async=False,
            memory_service_config=_make_memory_config(),
        )
        await service._sql_storage.create_sql_engine()
        return service

    async def close(self) -> None:
        await super().close()
        self._temp_dir.cleanup()

    def get_runtime_metadata(self) -> dict[str, Any]:
        return {
            "backend_name": self.name,
            "storage_kind": "sqlite",
            "connection": {
                "scheme": "sqlite",
                "session_db": Path(urlparse(self._session_db_url).path).name,
                "memory_db": Path(urlparse(self._memory_db_url).path).name,
            },
        }


class RedisReplayAdapter(ReplayBackendAdapter):
    """Replay adapter backed by Redis session and memory services."""

    name = "redis"

    def __init__(self, redis_url: str) -> None:
        super().__init__()
        self._redis_url = redis_url
        self._logical_case: ReplayCase | None = None
        self._logical_session_identities: dict[str, dict[str, str]] = {}
        self._storage_namespace = f"replay-{uuid.uuid4().hex[:8]}"

    @property
    def logical_case(self) -> ReplayCase:
        if self._logical_case is None:
            raise RuntimeError("Logical replay case is not initialized.")
        return self._logical_case

    async def setup(self, case: ReplayCase) -> None:
        self._logical_case = case
        self._logical_session_identities = _collect_logical_session_identities(case)
        await super().setup(self._namespaced_case(case))

    def should_restart_before_snapshot(self) -> bool:
        return True

    def should_restart_during_replay(self) -> bool:
        return True

    async def run_case(self, case: ReplayCase) -> BackendSnapshot:
        snapshot = await super().run_case(self.case)
        sessions_by_alias = {
            alias: self._project_session_snapshot(alias, session_snapshot)
            for alias, session_snapshot in snapshot.sessions_by_alias.items()
        }
        active_snapshot = sessions_by_alias[snapshot.active_session_alias]
        memory = {
            key: self._project_memory_observation(value)
            for key, value in snapshot.memory.items()
        }
        summary = active_snapshot.summary
        return replace(
            snapshot,
            app_name=active_snapshot.app_name,
            user_id=active_snapshot.user_id,
            session_id=active_snapshot.session_id,
            session=active_snapshot.session,
            state=active_snapshot.state,
            memory=memory,
            summary=summary,
            sessions_by_alias=sessions_by_alias,
        )

    async def _build_session_service(self) -> BaseSessionService:
        config = _make_session_config()
        config.store_historical_events = self.case.store_historical_events
        summarizer_manager = None
        if self.case.enable_summary:
            summarizer_manager = build_replay_summarizer_manager(
                keep_recent_count=self.case.summary_keep_recent_count,
            )
        return RedisSessionService(
            db_url=self._redis_url,
            summarizer_manager=summarizer_manager,
            is_async=False,
            session_config=config,
        )

    async def _build_memory_service(self) -> BaseMemoryService:
        return RedisMemoryService(
            db_url=self._redis_url,
            enabled=True,
            is_async=False,
            memory_service_config=_make_memory_config(),
        )

    async def close(self) -> None:
        await super().close()
        self._logical_case = None
        self._logical_session_identities = {}

    def _namespaced_case(self, case: ReplayCase) -> ReplayCase:
        namespaced_steps = tuple(
            replace(
                step,
                app_name=(f"{step.app_name}:{self._storage_namespace}" if step.app_name is not None else None),
                user_id=(f"{step.user_id}:{self._storage_namespace}" if step.user_id is not None else None),
                session_id=(f"{step.session_id}:{self._storage_namespace}" if step.session_id is not None else None),
            )
            for step in case.steps
        )
        return replace(
            case,
            app_name=f"{case.app_name}:{self._storage_namespace}",
            user_id=f"{case.user_id}:{self._storage_namespace}",
            session_id=f"{case.session_id}:{self._storage_namespace}",
            steps=namespaced_steps,
        )

    def _project_identifier(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return self._project_scalar(value)

    def _project_scalar(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self._project_scalar(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._project_scalar(item) for item in value]
        if isinstance(value, tuple):
            return [self._project_scalar(item) for item in value]
        if isinstance(value, str):
            for logical_identity in self._logical_session_identities.values():
                value = value.replace(f"{logical_identity['app_name']}:{self._storage_namespace}", logical_identity["app_name"])
                value = value.replace(f"{logical_identity['user_id']}:{self._storage_namespace}", logical_identity["user_id"])
                value = value.replace(f"{logical_identity['session_id']}:{self._storage_namespace}", logical_identity["session_id"])
            value = value.replace(self.case.app_name, self.logical_case.app_name)
            value = value.replace(self.case.user_id, self.logical_case.user_id)
            value = value.replace(self.case.session_id, self.logical_case.session_id)
            return value
        return value

    def _project_session_snapshot(self, session_alias: str, snapshot: SessionSnapshot) -> SessionSnapshot:
        logical_identity = self._logical_session_identities.get(session_alias)
        projected_summary = snapshot.summary
        if projected_summary is not None:
            projected_summary = replace(
                projected_summary,
                session_id=logical_identity["session_id"] if logical_identity is not None else self._project_scalar(projected_summary.session_id),
                summary_id=self._project_identifier(projected_summary.summary_id),
                replaces=self._project_identifier(projected_summary.replaces),
                metadata=self._project_scalar(projected_summary.metadata),
            )
        return replace(
            snapshot,
            app_name=logical_identity["app_name"] if logical_identity is not None else self._project_scalar(snapshot.app_name),
            user_id=logical_identity["user_id"] if logical_identity is not None else self._project_scalar(snapshot.user_id),
            session_id=logical_identity["session_id"] if logical_identity is not None else self._project_scalar(snapshot.session_id),
            state=self._project_scalar(snapshot.state),
            summary=projected_summary,
        )

    def _project_memory_observation(self, observation: Any) -> Any:
        if not isinstance(observation, dict):
            return self._project_scalar(observation)
        projected = self._project_scalar(observation)
        session_alias = projected.get("session_alias")
        logical_identity = self._logical_session_identities.get(session_alias)
        if logical_identity is not None:
            projected["app_name"] = logical_identity["app_name"]
            projected["user_id"] = logical_identity["user_id"]
            projected["session_id"] = logical_identity["session_id"]
        return projected

    def get_runtime_metadata(self) -> dict[str, Any]:
        return {
            "backend_name": self.name,
            "storage_kind": "redis",
            "connection": _summarize_connection_url(self._redis_url),
            "storage_namespace": self._storage_namespace,
            "storage_identity": {
                "app_name": self.case.app_name,
                "user_id": self.case.user_id,
                "session_id": self.case.session_id,
            },
            "logical_identity": {
                "app_name": self.logical_case.app_name,
                "user_id": self.logical_case.user_id,
                "session_id": self.logical_case.session_id,
            },
        }

    def get_report_metadata(self) -> dict[str, Any]:
        return {
            "backend_name": self.name,
            "storage_kind": "redis",
            "connection": _summarize_connection_url(self._redis_url),
            "namespace_strategy": "per_case_random",
        }


def build_event(spec: EventSpec, *, sequence: int, timestamp: float) -> Event:
    """Materialize an Event from an EventSpec."""

    parts: list[Part] = []
    if spec.text is not None:
        parts.append(Part.from_text(text=spec.text))

    for function_call in spec.function_calls:
        parts.append(Part(function_call=_build_function_call(function_call)))

    for function_response in spec.function_responses:
        parts.append(Part(function_response=_build_function_response(function_response)))

    content: Content | None = None
    if parts:
        content = Content(role=spec.role, parts=parts)

    event = Event(
        id=spec.event_id or f"replay-event-{sequence}",
        invocation_id=f"replay-invocation-{sequence}",
        author=spec.author,
        branch=spec.branch,
        content=content,
        visible=spec.visible,
        partial=spec.partial,
        timestamp=timestamp,
    )
    if spec.state_delta:
        event.actions.state_delta = dict(spec.state_delta)
    if spec.is_summary_event:
        event.set_summary_event(True)
    return event


def _build_function_call(spec: FunctionCallSpec) -> FunctionCall:
    return FunctionCall(name=spec.name, args=dict(spec.args), id=spec.call_id)


def _build_function_response(spec: FunctionResponseSpec) -> FunctionResponse:
    return FunctionResponse(name=spec.name, response=dict(spec.response), id=spec.call_id)


def _event_to_snapshot(event: Event) -> dict[str, Any]:
    return {
        "author": event.author,
        "branch": event.branch,
        "visible": event.visible,
        "partial": event.partial,
        "error_code": event.error_code,
        "error_message": event.error_message,
        "text": event.get_text(),
        "is_summary_event": event.is_summary_event(),
        "model_visible": event.is_model_visible(),
        "state_delta": dict(event.actions.state_delta or {}),
        "function_calls": [
            {
                "name": call.name,
                "args": _normalize_scalar(call.args),
            }
            for call in event.get_function_calls()
        ],
        "function_responses": [
            {
                "name": response.name,
                "response": _normalize_scalar(response.response),
            }
            for response in event.get_function_responses()
        ],
    }


def _memory_entry_to_snapshot(entry: Any) -> dict[str, Any]:
    parts = getattr(getattr(entry, "content", None), "parts", None) or []
    text = "".join(part.text for part in parts if getattr(part, "text", None))
    role = getattr(getattr(entry, "content", None), "role", None)
    return {
        "author": getattr(entry, "author", None),
        "role": role,
        "text": text,
        "timestamp": getattr(entry, "timestamp", None),
    }


def normalize_backend_snapshot(snapshot: BackendSnapshot) -> dict[str, Any]:
    """Normalize backend noise before diffing."""

    return {
        "backend_name": snapshot.backend_name,
        "case_id": snapshot.case_id,
        "app_name": snapshot.app_name,
        "user_id": snapshot.user_id,
        "session_id": snapshot.session_id,
        "active_session_alias": snapshot.active_session_alias,
        "session": _normalize_scalar(snapshot.session),
        "state": _normalize_scalar(snapshot.state),
        "memory": _normalize_memory(snapshot.memory),
        "summary": _normalize_summary(snapshot.summary),
        "sessions_by_alias": _normalize_sessions_by_alias(snapshot.sessions_by_alias, snapshot.active_session_alias),
    }


def _normalize_summary(summary: Optional[SummarySnapshot]) -> Optional[dict[str, Any]]:
    if summary is None:
        return None
    return {
        "session_id": summary.session_id,
        "summary_text": summary.summary_text.strip(),
        "original_event_count": summary.original_event_count,
        "compressed_event_count": summary.compressed_event_count,
        "summary_id": summary.summary_id,
        "version": summary.version,
        "replaces": summary.replaces,
        "summarized_event_count": summary.summarized_event_count,
        "summary_timestamp": _normalize_timestamp(summary.summary_timestamp),
        "metadata": _normalize_scalar(summary.metadata),
    }


def _normalize_memory(memory_results: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for observation_key, observation in memory_results.items():
        if isinstance(observation, dict):
            entries = observation.get("entries", [])
            normalized_observation = {
                "query_name": observation.get("query_name"),
                "session_alias": observation.get("session_alias"),
                "app_name": observation.get("app_name"),
                "user_id": observation.get("user_id"),
                "session_id": observation.get("session_id"),
                "step_index": observation.get("step_index"),
            }
        else:
            entries = observation
            normalized_observation = {}
        normalized_entries = []
        for entry in entries:
            normalized_entries.append(
                {
                    "author": entry.get("author"),
                    "role": entry.get("role"),
                    "text": (entry.get("text") or "").strip(),
                })
        normalized_observation["entries"] = sorted(
            normalized_entries,
            key=lambda item: (item["text"], item["author"] or "", item["role"] or ""),
        )
        normalized[observation_key] = _normalize_scalar(normalized_observation)
    return normalized


def _normalize_sessions_by_alias(
    sessions_by_alias: dict[str, SessionSnapshot],
    active_session_alias: str,
) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for session_alias, snapshot in sessions_by_alias.items():
        if session_alias == active_session_alias:
            continue
        normalized[session_alias] = {
            "session_alias": snapshot.session_alias,
            "app_name": snapshot.app_name,
            "user_id": snapshot.user_id,
            "session_id": snapshot.session_id,
            "session": _normalize_scalar(snapshot.session),
            "state": _normalize_scalar(snapshot.state),
            "summary": _normalize_summary(snapshot.summary),
        }
    return normalized


def _normalize_scalar(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize_scalar(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalize_scalar(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_scalar(item) for item in value]
    if isinstance(value, str):
        return value.strip()
    return value


def _normalize_timestamp(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), 6)


def get_replay_clock_metadata() -> dict[str, Any]:
    mode = (os.getenv(_REPLAY_CLOCK_MODE_ENV) or _REPLAY_CLOCK_MODE_FRESHNESS_SAFE).strip().lower()
    if mode == _REPLAY_CLOCK_MODE_FIXED_SAFE:
        configured_epoch = _parse_optional_float(os.getenv(_REPLAY_FIXED_EPOCH_ENV))
        effective_epoch = configured_epoch if configured_epoch is not None else _DEFAULT_FIXED_SAFE_EPOCH
        return {
            "mode": _REPLAY_CLOCK_MODE_FIXED_SAFE,
            "future_skew_seconds": _CASE_TIME_FUTURE_SKEW_SECONDS,
            "configured_fixed_epoch": configured_epoch,
            "default_fixed_epoch": _DEFAULT_FIXED_SAFE_EPOCH,
            "effective_fixed_epoch": effective_epoch,
            "freshness_safe": True,
        }
    return {
        "mode": _REPLAY_CLOCK_MODE_FRESHNESS_SAFE,
        "future_skew_seconds": _CASE_TIME_FUTURE_SKEW_SECONDS,
        "freshness_safe": True,
    }


def _parse_optional_float(raw: Optional[str]) -> Optional[float]:
    if raw is None or not raw.strip():
        return None
    return float(raw.strip())


def _summarize_connection_url(url: str) -> dict[str, Any]:
    parsed = urlparse(url)
    database = parsed.path.lstrip("/") or None
    if database and database.isdigit():
        database = int(database)
    return {
        "scheme": parsed.scheme,
        "host": parsed.hostname,
        "port": parsed.port,
        "database": database,
        "has_auth": bool(parsed.username or parsed.password),
    }


def _collect_logical_session_identities(case: ReplayCase) -> dict[str, dict[str, str]]:
    identities: dict[str, dict[str, str]] = {}
    for step in case.steps:
        if step.kind != ReplayStepKind.CREATE_SESSION:
            continue
        identities[step.session_alias] = {
            "app_name": step.app_name or case.app_name,
            "user_id": step.user_id or case.user_id,
            "session_id": step.session_id or case.session_id,
        }
    return identities


def _backend_target_matches(target_name: str, backend_name: str) -> bool:
    normalized_target = target_name.strip().lower()
    normalized_backend = backend_name.strip().lower()
    if normalized_target == normalized_backend:
        return True
    if normalized_target in _PERSISTENT_BACKEND_TARGETS:
        return normalized_backend != _BASELINE_BACKEND_NAME
    if normalized_target in _BASELINE_BACKEND_TARGETS:
        return normalized_backend == _BASELINE_BACKEND_NAME
    return False


def expected_diff_paths_for_backend_pair(
    case: ReplayCase,
    *,
    backend_a: str,
    backend_b: str,
) -> tuple[str, ...]:
    if not case.expected_diff_paths:
        return ()

    for target_name in [mutation.backend_name for mutation in case.snapshot_mutations] + [
        fault.backend_name for fault in case.runtime_faults
    ]:
        if _backend_target_matches(target_name, backend_a) or _backend_target_matches(target_name, backend_b):
            return case.expected_diff_paths
    return ()


def diff_backend_snapshots(
    *,
    case: ReplayCase,
    left: BackendSnapshot,
    right: BackendSnapshot,
) -> list[DiffEntry]:
    """Generate structured diffs for two snapshots."""

    left_view = normalize_backend_snapshot(left)
    right_view = normalize_backend_snapshot(right)
    _apply_snapshot_mutations(left_view, case.snapshot_mutations, left.backend_name)
    _apply_snapshot_mutations(right_view, case.snapshot_mutations, right.backend_name)
    diffs: list[DiffEntry] = []

    for scope in ("session", "state", "memory", "summary"):
        scope_summary_id = _resolve_summary_id(left_view.get(scope), right_view.get(scope)) if scope == "summary" else None
        _diff_values(
            case=case,
            backend_a=left.backend_name,
            backend_b=right.backend_name,
            scope=scope,
            path=scope,
            left=left_view[scope],
            right=right_view[scope],
            out=diffs,
            session_id=_resolve_session_id(left_view, right_view, case.session_id),
            summary_id=scope_summary_id,
        )
    left_aliases = left_view.get("sessions_by_alias", {})
    right_aliases = right_view.get("sessions_by_alias", {})
    for session_alias in sorted(set(left_aliases) | set(right_aliases)):
        left_alias_snapshot = left_aliases.get(session_alias)
        right_alias_snapshot = right_aliases.get(session_alias)
        _diff_values(
            case=case,
            backend_a=left.backend_name,
            backend_b=right.backend_name,
            scope="sessions_by_alias",
            path=f"sessions_by_alias.{session_alias}",
            left=left_alias_snapshot,
            right=right_alias_snapshot,
            out=diffs,
            session_id=_resolve_value_session_id(left_alias_snapshot, right_alias_snapshot, case.session_id),
            summary_id=_resolve_summary_id(
                left_alias_snapshot.get("summary") if isinstance(left_alias_snapshot, dict) else None,
                right_alias_snapshot.get("summary") if isinstance(right_alias_snapshot, dict) else None,
            ),
        )
    return diffs


def _apply_snapshot_mutations(
    snapshot_view: dict[str, Any],
    mutations: tuple[SnapshotMutation, ...],
    backend_name: str,
) -> None:
    for mutation in mutations:
        if not _backend_target_matches(mutation.backend_name, backend_name):
            continue
        _apply_snapshot_mutation(snapshot_view, mutation)


def _apply_snapshot_mutation(snapshot_view: dict[str, Any], mutation: SnapshotMutation) -> None:
    tokens = _parse_path_tokens(mutation.path)
    if not tokens:
        raise ValueError(f"Invalid mutation path: {mutation.path}")

    parent, last_token = _resolve_parent(snapshot_view, tokens)
    if mutation.operation == SnapshotMutationOperation.SET:
        _set_token(parent, last_token, mutation.value)
        return
    if mutation.operation == SnapshotMutationOperation.DELETE:
        _delete_token(parent, last_token)
        return
    raise ValueError(f"Unsupported snapshot mutation operation: {mutation.operation}")


def _parse_path_tokens(path: str) -> list[Any]:
    tokens: list[Any] = []
    for part in path.split("."):
        if not part:
            continue
        cursor = part
        while cursor:
            match = re.match(r"^([^\[]+)(\[(\d+)\])?(.*)$", cursor)
            if not match:
                raise ValueError(f"Invalid path segment: {cursor}")
            key, _, index, rest = match.groups()
            if key:
                tokens.append(key)
            if index is not None:
                tokens.append(int(index))
            cursor = rest
    return tokens


def _resolve_parent(root: dict[str, Any], tokens: list[Any]) -> tuple[Any, Any]:
    target = root
    for token in tokens[:-1]:
        target = _get_token(target, token)
    return target, tokens[-1]


def _set_token(container: Any, token: Any, value: Any) -> None:
    if isinstance(token, int):
        container[token] = value
    elif isinstance(container, dict):
        container[token] = value
    else:
        setattr(container, token, value)


def _delete_token(container: Any, token: Any) -> None:
    if isinstance(token, int):
        del container[token]
    elif isinstance(container, dict):
        container.pop(token, None)
    else:
        delattr(container, token)


def _get_token(container: Any, token: Any) -> Any:
    if isinstance(token, int):
        return container[token]
    if isinstance(container, dict):
        return container[token]
    return getattr(container, token)


def _set_path_value(root: Any, path: str, value: Any) -> None:
    tokens = _parse_path_tokens(path)
    if not tokens:
        raise ValueError(f"Invalid mutation path: {path}")
    parent, last_token = _resolve_parent(root, tokens)
    _set_token(parent, last_token, value)


def _diff_values(
    *,
    case: ReplayCase,
    backend_a: str,
    backend_b: str,
    scope: str,
    path: str,
    left: Any,
    right: Any,
    out: list[DiffEntry],
    session_id: str,
    summary_id: Optional[str],
) -> None:
    if type(left) is not type(right):
        out.append(_make_diff(case, backend_a, backend_b, scope, path, left, right, session_id, summary_id))
        return

    if isinstance(left, dict):
        for key in sorted(set(left) | set(right)):
            next_path = f"{path}.{key}"
            if key not in left or key not in right:
                out.append(
                    _make_diff(
                        case,
                        backend_a,
                        backend_b,
                        scope,
                        next_path,
                        left.get(key),
                        right.get(key),
                        session_id,
                    summary_id,
                    ))
                continue
            _diff_values(
                case=case,
                backend_a=backend_a,
                backend_b=backend_b,
                scope=scope,
                path=next_path,
                left=left[key],
                right=right[key],
                out=out,
                session_id=session_id,
                summary_id=summary_id,
            )
        return

    if isinstance(left, list):
        if len(left) != len(right):
            out.append(_make_diff(case, backend_a, backend_b, scope, f"{path}.length", len(left), len(right),
                                  session_id, summary_id))
            return
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            _diff_values(
                case=case,
                backend_a=backend_a,
                backend_b=backend_b,
                scope=scope,
                path=f"{path}[{index}]",
                left=left_item,
                right=right_item,
                out=out,
                session_id=session_id,
                summary_id=summary_id,
            )
        return

    if left != right:
        out.append(_make_diff(case, backend_a, backend_b, scope, path, left, right, session_id, summary_id))


def _make_diff(
    case: ReplayCase,
    backend_a: str,
    backend_b: str,
    scope: str,
    path: str,
    left: Any,
    right: Any,
    session_id: str,
    summary_id: Optional[str],
) -> DiffEntry:
    allowed = path in set(case.allowed_diff_paths)
    event_index = _extract_event_index(path)
    reason = "Allowed backend-specific difference." if allowed else None
    return DiffEntry(
        case_id=case.case_id,
        backend_a=backend_a,
        backend_b=backend_b,
        scope=scope,
        path=path,
        left=left,
        right=right,
        allowed=allowed,
        session_id=session_id,
        event_index=event_index,
        summary_id=summary_id,
        reason=reason,
    )


def _resolve_summary_id(left: Any, right: Any) -> Optional[str]:
    for value in (left, right):
        if isinstance(value, dict):
            summary_id = value.get("summary_id")
            if summary_id is not None:
                return str(summary_id)
    return None


def _resolve_session_id(left: dict[str, Any], right: dict[str, Any], fallback: str) -> str:
    left_session_id = left.get("session_id")
    right_session_id = right.get("session_id")
    if isinstance(left_session_id, str) and left_session_id == right_session_id:
        return left_session_id
    return fallback


def _resolve_value_session_id(left: Any, right: Any, fallback: str) -> str:
    for value in (left, right):
        if isinstance(value, dict):
            session_id = value.get("session_id")
            if isinstance(session_id, str):
                return session_id
    return fallback


def _deterministic_time_base(case_id: str) -> float:
    if case_id not in _CASE_TIME_BASES:
        digest = hashlib.sha1(case_id.encode("utf-8")).digest()
        offset_millis = int.from_bytes(digest[:6], "big") % 1_000
        clock_metadata = get_replay_clock_metadata()
        if clock_metadata["mode"] == _REPLAY_CLOCK_MODE_FIXED_SAFE:
            base_epoch = float(clock_metadata["effective_fixed_epoch"])
        else:
            base_epoch = round(time.time(), 3) + _CASE_TIME_FUTURE_SKEW_SECONDS
        _CASE_TIME_BASES[case_id] = base_epoch + (offset_millis / 1000.0)
    return _CASE_TIME_BASES[case_id]


@contextmanager
def _patched_time_time(timestamp: float) -> Iterator[None]:
    original_time = time.time
    time.time = lambda: timestamp
    try:
        yield
    finally:
        time.time = original_time


def _get_summary_event(session: Session) -> Optional[Event]:
    for event in session.events:
        if event.is_summary_event():
            return event
    return None


def _extract_event_index(path: str) -> Optional[int]:
    match = _EVENT_INDEX_RE.search(path)
    if not match:
        return None
    return int(match.group(1))


def format_diffs(diffs: list[DiffEntry]) -> str:
    """Render human-readable diffs for assertion failures."""

    if not diffs:
        return "No differences detected."
    lines = []
    for diff in diffs:
        prefix = "ALLOWED" if diff.allowed else "DIFF"
        lines.append(f"{prefix} {diff.path}: {diff.left!r} != {diff.right!r}")
    return "\n".join(lines)


def build_case_report(case: ReplayCase, diffs: list[DiffEntry]) -> dict[str, Any]:
    """Build a grouped report for one replay case."""

    expected_paths = set(case.expected_diff_paths)
    allowed_paths = set(case.allowed_diff_paths)
    detected_paths = {diff.path for diff in diffs if not diff.allowed}
    return {
        "case_id": case.case_id,
        "description": case.description,
        "expects_diffs": bool(expected_paths),
        "expected_diff_paths": sorted(expected_paths),
        "allowed_diff_paths": sorted(allowed_paths),
        "detected_diff_paths": sorted(detected_paths),
        "missing_expected_paths": sorted(expected_paths - detected_paths),
        "unexpected_diff_paths": sorted(detected_paths - expected_paths),
        "diffs": [diff.to_dict() for diff in diffs],
    }


def build_comparison_report(
    case: ReplayCase,
    *,
    backend_a: str,
    backend_b: str,
    diffs: list[DiffEntry],
    runtime_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    expected_paths = set(expected_diff_paths_for_backend_pair(case, backend_a=backend_a, backend_b=backend_b))
    allowed_paths = set(case.allowed_diff_paths)
    detected_paths = {diff.path for diff in diffs if not diff.allowed}
    return {
        "backend_a": backend_a,
        "backend_b": backend_b,
        "expected_diff_paths": sorted(expected_paths),
        "allowed_diff_paths": sorted(allowed_paths),
        "detected_diff_paths": sorted(detected_paths),
        "missing_expected_paths": sorted(expected_paths - detected_paths),
        "unexpected_diff_paths": sorted(detected_paths - expected_paths),
        "runtime_context": runtime_context or {},
        "diffs": [diff.to_dict() for diff in diffs],
    }


def build_case_matrix_report(case: ReplayCase, comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    expected_paths = set(case.expected_diff_paths)
    allowed_paths = set(case.allowed_diff_paths)
    detected_paths = {
        path
        for comparison in comparisons
        for path in comparison.get("detected_diff_paths", [])
    }
    all_diffs = [
        diff
        for comparison in comparisons
        for diff in comparison.get("diffs", [])
    ]
    return {
        "case_id": case.case_id,
        "description": case.description,
        "expects_diffs": bool(expected_paths),
        "expected_diff_paths": sorted(expected_paths),
        "allowed_diff_paths": sorted(allowed_paths),
        "detected_diff_paths": sorted(detected_paths),
        "missing_expected_paths": sorted(expected_paths - detected_paths),
        "unexpected_diff_paths": sorted(detected_paths - expected_paths),
        "comparison_count": len(comparisons),
        "comparisons": comparisons,
        "diffs": all_diffs,
    }


def write_diff_report(
    report_path: Path,
    case_reports: list[dict[str, Any]],
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    """Write grouped replay reports to JSON."""

    payload = {
        "meta": metadata or {},
        "cases": case_reports,
    }
    report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
