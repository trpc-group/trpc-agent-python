# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Replay consistency framework for Session / Memory / Summary backends.

This module provides a reusable harness that replays the same conversation
轨迹 (trace) against multiple SessionService + MemoryService backends and
compares the resulting events, state, memory entries and session summaries.

Design highlights
-----------------
* One ``ReplayCase`` describes a deterministic sequence of operations
  (create_session, append_event, update_state, summarize, store_memory,
  search_memory).
* Each backend is represented by a ``BackendBundle`` (session service,
  optional memory service, summarizer manager).  Fresh bundles are created
  for every case to avoid cross-case pollution.
* The in-memory backend acts as the reference.  Every other backend is
  compared against it after normalization.
* Normalization removes backend-specific non-business fields:
  auto-generated event ids, wall-clock timestamps, serialization field
  order and floating-point precision.  Fields that are allowed to differ
  are recorded as ``allowed_diff`` instead of hard failures.
* Summary comparison treats ``summary_text`` as semantic content (strip
  whitespace) while ``session_id``, original/compressed event counts and
  the existence of the summary are compared strictly.
* Fault injection is implemented as deterministic post-processing on the
  target backend, simulating dropped/duplicated events, corrupted state,
  lost summary, wrong summary session归属 and overridden summary text.
"""

from __future__ import annotations

import copy
import json
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Callable
from typing import Optional
from unittest.mock import MagicMock

from trpc_agent_sdk.abc import MemoryServiceABC
from trpc_agent_sdk.abc import SessionServiceABC
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.sessions import SqlSessionService
from trpc_agent_sdk.sessions._session import Session
from trpc_agent_sdk.sessions._session_summarizer import SessionSummarizer
from trpc_agent_sdk.sessions._summarizer_manager import SummarizerSessionManager
from trpc_agent_sdk.sessions._types import SessionServiceConfig
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import EventActions
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.types import SearchMemoryResponse


# ---------------------------------------------------------------------------
# Mock model for deterministic summarization
# ---------------------------------------------------------------------------


class MockSummarizerModel(LLMModel):
    """A deterministic mock LLM used by the replay summarizer.

    It returns a summary string derived from the prompt length so tests do
    not depend on external model availability or network latency.
    """

    def __init__(self):
        super().__init__(model_name="mock-summarizer")

    @classmethod
    def supported_models(cls):
        return [r"mock-summarizer"]

    async def _generate_async_impl(
        self,
        request: LlmRequest,
        stream: bool = False,
        ctx: Optional[Any] = None,
    ):
        prompt_text = ""
        for content in request.contents or []:
            if content.parts:
                for part in content.parts:
                    if part.text:
                        prompt_text += part.text
        summary = f"Summary of {len(prompt_text)} chars."
        yield LlmResponse(content=Content(parts=[Part.from_text(text=summary)]))


# ---------------------------------------------------------------------------
# Replay operations and cases
# ---------------------------------------------------------------------------


@dataclass
class ReplayOperation:
    """Base class for a single step in a replay case."""

    op_type: str


@dataclass
class CreateSessionOp(ReplayOperation):
    """Create a session."""

    app_name: str
    user_id: str
    session_id: Optional[str] = None
    state: Optional[dict[str, Any]] = None
    op_type: str = field(default="create_session", init=False, repr=False)


@dataclass
class AppendEventOp(ReplayOperation):
    """Append an event to the session at ``session_index``."""

    session_index: int
    event: Event
    op_type: str = field(default="append_event", init=False, repr=False)


@dataclass
class UpdateStateOp(ReplayOperation):
    """Apply a state delta via an event with actions.state_delta."""

    session_index: int
    state_delta: dict[str, Any]
    op_type: str = field(default="update_state", init=False, repr=False)


@dataclass
class CreateSummaryOp(ReplayOperation):
    """Trigger session summarization."""

    session_index: int
    force: bool = True
    op_type: str = field(default="create_summary", init=False, repr=False)


@dataclass
class StoreMemoryOp(ReplayOperation):
    """Store the session into the memory service."""

    session_index: int
    op_type: str = field(default="store_memory", init=False, repr=False)


@dataclass
class SearchMemoryOp(ReplayOperation):
    """Search the memory service and record the result."""

    session_index: int
    query: str
    limit: int = 10
    op_type: str = field(default="search_memory", init=False, repr=False)


@dataclass
class ReplayCase:
    """A deterministic replay scenario."""

    name: str
    description: str
    operations: list[ReplayOperation]
    expected_faults: list[str] = field(default_factory=list)
    config: Optional[SessionServiceConfig] = None


# ---------------------------------------------------------------------------
# Backend bundle and factories
# ---------------------------------------------------------------------------


@dataclass
class BackendBundle:
    """A backend under test together with its optional memory service."""

    name: str
    session_service: SessionServiceABC
    memory_service: Optional[MemoryServiceABC]
    summarizer_manager: SummarizerSessionManager

    async def close(self) -> None:
        await self.session_service.close()
        if self.memory_service:
            await self.memory_service.close()


BackendFactory = Callable[[], BackendBundle]


def _default_session_config() -> SessionServiceConfig:
    config = SessionServiceConfig(store_historical_events=True)
    config.clean_ttl_config()
    return config


def _make_summarizer_manager() -> SummarizerSessionManager:
    model = MockSummarizerModel()
    summarizer = SessionSummarizer(model=model, keep_recent_count=2)
    return SummarizerSessionManager(model=model, summarizer=summarizer, auto_summarize=True)


def in_memory_backend_factory() -> BackendBundle:
    """Factory for the InMemory backend pair."""
    summarizer_manager = _make_summarizer_manager()
    config = _default_session_config()
    session_service = InMemorySessionService(
        summarizer_manager=summarizer_manager,
        session_config=config,
    )
    from trpc_agent_sdk.memory import InMemoryMemoryService
    memory_service = InMemoryMemoryService(enabled=True)
    return BackendBundle(
        name="in_memory",
        session_service=session_service,
        memory_service=memory_service,
        summarizer_manager=summarizer_manager,
    )


def sqlite_backend_factory() -> BackendBundle:
    """Factory for the SQLite backend pair."""
    summarizer_manager = _make_summarizer_manager()
    config = _default_session_config()
    session_service = SqlSessionService(
        db_url="sqlite:///:memory:",
        is_async=False,
        summarizer_manager=summarizer_manager,
        session_config=config,
    )
    from trpc_agent_sdk.memory import SqlMemoryService
    memory_service = SqlMemoryService(
        db_url="sqlite:///:memory:",
        is_async=False,
        enabled=True,
    )
    return BackendBundle(
        name="sqlite",
        session_service=session_service,
        memory_service=memory_service,
        summarizer_manager=summarizer_manager,
    )


# ---------------------------------------------------------------------------
# Fault injection
# ---------------------------------------------------------------------------


@dataclass
class FaultSpec:
    """Specification of an injected inconsistency."""

    fault_type: str
    args: dict[str, Any] = field(default_factory=dict)


async def _apply_fault(bundle: BackendBundle, sessions: list[Session], fault: FaultSpec) -> None:
    """Apply a fault to ``bundle`` after normal operations have finished."""
    fault_type = fault.fault_type
    args = fault.args
    session = sessions[args.get("session_index", 0)]
    svc = bundle.session_service

    if fault_type == "drop_event":
        stored = await svc.get_session(
            app_name=session.app_name,
            user_id=session.user_id,
            session_id=session.id,
        )
        idx = args.get("event_index", 0)
        if stored and len(stored.events) > idx:
            stored.events.pop(idx)
            await svc.update_session(stored)

    elif fault_type == "duplicate_event":
        stored = await svc.get_session(
            app_name=session.app_name,
            user_id=session.user_id,
            session_id=session.id,
        )
        idx = args.get("event_index", 0)
        if stored and len(stored.events) > idx:
            dup = copy.deepcopy(stored.events[idx])
            dup.id = str(uuid.uuid4())
            stored.events.insert(idx + 1, dup)
            await svc.update_session(stored)

    elif fault_type == "corrupt_state":
        # State scopes (session/user/app) are persisted by append_event, not by
        # update_session.  Inject through the public write path so scoped state
        # corruption is visible on every backend.
        await svc.append_event(
            session,
            Event(
                invocation_id="fault-state-update",
                author="system",
                actions=EventActions(
                    state_delta=args.get("state_patch", {"corrupted": True})
                ),
            ),
        )

    elif fault_type == "drop_summary":
        if bundle.summarizer_manager:
            cache = bundle.summarizer_manager._summarizer_cache
            cache.get(session.app_name, {}).get(session.user_id, {}).pop(session.id, None)

    elif fault_type == "wrong_summary_session":
        if bundle.summarizer_manager:
            cache = bundle.summarizer_manager._summarizer_cache
            app_cache = cache.get(session.app_name, {})
            user_cache = app_cache.get(session.user_id, {})
            summary = user_cache.get(session.id)
            if summary:
                summary.session_id = args.get("wrong_session_id", "wrong-session")

    elif fault_type == "summary_loss":
        if bundle.summarizer_manager:
            cache = bundle.summarizer_manager._summarizer_cache
            app_cache = cache.get(session.app_name, {})
            user_cache = app_cache.get(session.user_id, {})
            summary = user_cache.get(session.id)
            if summary:
                summary.summary_text = ""

    elif fault_type == "summary_override_error":
        if bundle.summarizer_manager:
            cache = bundle.summarizer_manager._summarizer_cache
            app_cache = cache.get(session.app_name, {})
            user_cache = app_cache.get(session.user_id, {})
            summary = user_cache.get(session.id)
            if summary:
                summary.summary_text = args.get("wrong_summary_text", "wrong summary")


# ---------------------------------------------------------------------------
# Normalization and comparison
# ---------------------------------------------------------------------------


_ALLOWED_DIFF_KEYS = {"id", "timestamp", "last_update_time", "summary_timestamp"}


def _normalize_text(text: Optional[str]) -> str:
    return (text or "").strip()


def _normalize_event(event: Event) -> dict[str, Any]:
    """Return a backend-agnostic representation of an event."""
    data = event.model_dump(exclude_none=True, mode="json")
    # Auto-generated identifiers and wall-clock timestamps are allowed to differ.
    data["id"] = "__event_id__"
    data["timestamp"] = "__event_timestamp__"
    # Normalize empty collections to None for backend-agnostic comparison.
    lr = data.get("long_running_tool_ids")
    if lr is None or (isinstance(lr, (list, set, tuple)) and len(lr) == 0):
        data.pop("long_running_tool_ids", None)
    # Normalize empty/null action structures.
    actions = data.get("actions") or {}
    if not actions.get("state_delta"):
        actions.pop("state_delta", None)
    if not actions.get("transfer_to_agent"):
        actions.pop("transfer_to_agent", None)
    if not actions.get("skip_summarization"):
        actions.pop("skip_summarization", None)
    if not actions.get("escalate"):
        actions.pop("escalate", None)
    if not actions.get("requested_auth_configs"):
        actions.pop("requested_auth_configs", None)
    if actions:
        data["actions"] = actions
    else:
        data.pop("actions", None)
    # Normalize content by re-serializing so field order does not matter.
    if event.content:
        data["content"] = json.loads(event.content.model_dump_json(exclude_none=True))
    # Normalize grounding / usage metadata in the same way.
    if event.grounding_metadata:
        data["grounding_metadata"] = json.loads(event.grounding_metadata.model_dump_json(exclude_none=True))
    if event.usage_metadata:
        data["usage_metadata"] = json.loads(event.usage_metadata.model_dump_json(exclude_none=True))
    return data


def _normalize_events(events: list[Event]) -> list[dict[str, Any]]:
    """Normalize events into their logical replay order.

    Summary compression replaces an older prefix with a synthetic ``summary``
    event.  In-memory storage inserts that event at the front, while SQL reads
    rows in timestamp order and can return it last because it was created
    after the retained events.  The summary logically precedes those retained
    events regardless of its physical insertion time.
    """
    normalized = [_normalize_event(event) for event in events]
    return [
        event
        for _, event in sorted(
            enumerate(normalized),
            key=lambda item: (
                item[1].get("invocation_id") != "summary",
                item[0],
            ),
        )
    ]


def _normalize_state(state: dict[str, Any]) -> dict[str, Any]:
    """Return a sorted, JSON-roundtripped state dictionary."""
    return json.loads(json.dumps(state, sort_keys=True, default=str))


def _normalize_memory_response(response: SearchMemoryResponse) -> list[dict[str, Any]]:
    """Return normalized memory entries, sorted to ignore backend ordering."""
    result = []
    for entry in response.memories:
        item: dict[str, Any] = {}
        if entry.content:
            item["content"] = json.loads(entry.content.model_dump_json(exclude_none=True))
        item["author"] = entry.author
        item["timestamp"] = "__memory_timestamp__"
        result.append(item)
    result.sort(key=lambda x: (x.get("author", ""), json.dumps(x.get("content"), sort_keys=True, default=str)))
    return result


def _normalize_summary(summary: Any) -> Optional[dict[str, Any]]:
    """Return normalized summary or None."""
    if summary is None:
        return None
    if isinstance(summary, str):
        return {"summary_text": _normalize_text(summary), "session_id": "__unknown__"}
    data: dict[str, Any] = {
        "session_id": summary.session_id,
        "summary_text": _normalize_text(summary.summary_text),
        "original_event_count": summary.original_event_count,
        "compressed_event_count": summary.compressed_event_count,
    }
    # Timestamps may differ by a few seconds; record them but compare loosely.
    data["summary_timestamp"] = round(float(summary.summary_timestamp), 0)
    return data


def _deep_diff(
    reference: Any,
    actual: Any,
    path: str,
    allowed_paths: Optional[set[str]] = None,
    tolerance_paths: Optional[dict[str, float]] = None,
) -> list[dict[str, Any]]:
    """Recursively compute differences between two normalized values."""
    allowed_paths = allowed_paths or set()
    tolerance_paths = tolerance_paths or {}
    diffs: list[dict[str, Any]] = []

    if isinstance(reference, dict) and isinstance(actual, dict):
        keys = set(reference.keys()) | set(actual.keys())
        for key in keys:
            child_path = f"{path}.{key}" if path else str(key)
            if key not in reference:
                diffs.append({
                    "path": child_path,
                    "reference_value": None,
                    "actual_value": actual[key],
                    "allowed_diff": child_path in allowed_paths,
                    "message": f"missing in reference",
                })
            elif key not in actual:
                diffs.append({
                    "path": child_path,
                    "reference_value": reference[key],
                    "actual_value": None,
                    "allowed_diff": child_path in allowed_paths,
                    "message": f"missing in actual",
                })
            else:
                diffs.extend(_deep_diff(reference[key], actual[key], child_path, allowed_paths, tolerance_paths))
    elif isinstance(reference, list) and isinstance(actual, list):
        for i in range(max(len(reference), len(actual))):
            child_path = f"{path}[{i}]"
            if i >= len(reference):
                diffs.append({
                    "path": child_path,
                    "reference_value": None,
                    "actual_value": actual[i],
                    "allowed_diff": False,
                    "message": "extra item in actual list",
                })
            elif i >= len(actual):
                diffs.append({
                    "path": child_path,
                    "reference_value": reference[i],
                    "actual_value": None,
                    "allowed_diff": False,
                    "message": "missing item in actual list",
                })
            else:
                diffs.extend(_deep_diff(reference[i], actual[i], child_path, allowed_paths, tolerance_paths))
    else:
        if reference != actual:
            allowed = path in allowed_paths
            message = "values differ"
            if path in tolerance_paths:
                try:
                    delta = abs(float(reference) - float(actual))  # type: ignore
                    if delta <= tolerance_paths[path]:
                        return diffs
                except (TypeError, ValueError):
                    pass
            diffs.append({
                "path": path,
                "reference_value": reference,
                "actual_value": actual,
                "allowed_diff": allowed,
                "message": message,
            })
    return diffs


# ---------------------------------------------------------------------------
# Replay harness
# ---------------------------------------------------------------------------


@dataclass
class CaseResult:
    """Result of replaying one case against one backend."""

    case_name: str
    backend_name: str
    consistent: bool
    differences: list[dict[str, Any]]


class ReplayHarness:
    """Replays cases across backends and reports inconsistencies."""

    def __init__(
        self,
        factories: dict[str, BackendFactory],
        reference_backend: str = "in_memory",
    ):
        self.factories = factories
        self.reference_backend = reference_backend

    async def _create_backends(self) -> dict[str, BackendBundle]:
        backends: dict[str, BackendBundle] = {}
        for name, factory in self.factories.items():
            bundle = factory()
            # SQL backends need an explicit engine creation.
            if hasattr(bundle.session_service, "_sql_storage"):
                await bundle.session_service._sql_storage.create_sql_engine()
            if bundle.memory_service and hasattr(bundle.memory_service, "_sql_storage"):
                await bundle.memory_service._sql_storage.create_sql_engine()
            backends[name] = bundle
        return backends

    async def _execute_operation(
        self,
        bundle: BackendBundle,
        sessions: list[Session],
        op: ReplayOperation,
        memory_results: dict[int, SearchMemoryResponse],
    ) -> None:
        if op.op_type == "create_session":
            create_op = op  # type: ignore
            session = await bundle.session_service.create_session(
                app_name=create_op.app_name,
                user_id=create_op.user_id,
                session_id=create_op.session_id,
                state=create_op.state,
            )
            sessions.append(session)

        elif op.op_type == "append_event":
            append_op = op  # type: ignore
            session = sessions[append_op.session_index]
            event = copy.deepcopy(append_op.event)
            await bundle.session_service.append_event(session, event)

        elif op.op_type == "update_state":
            update_op = op  # type: ignore
            session = sessions[update_op.session_index]
            event = Event(
                invocation_id="state-update",
                author="system",
                actions=EventActions(state_delta=update_op.state_delta),
                content=Content(parts=[Part.from_text(text="state update")]),
            )
            await bundle.session_service.append_event(session, event)

        elif op.op_type == "create_summary":
            summary_op = op  # type: ignore
            session = sessions[summary_op.session_index]
            # Use the summarizer manager directly so we can force summarization
            # without relying on the conversation_count threshold in runners.
            if bundle.summarizer_manager:
                await bundle.summarizer_manager.create_session_summary(
                    session, force=summary_op.force)
            else:
                await bundle.session_service.create_session_summary(session)

        elif op.op_type == "store_memory":
            store_op = op  # type: ignore
            if bundle.memory_service:
                session = sessions[store_op.session_index]
                await bundle.memory_service.store_session(session)

        elif op.op_type == "search_memory":
            search_op = op  # type: ignore
            if bundle.memory_service:
                session = sessions[search_op.session_index]
                key = f"{session.app_name}/{session.user_id}"
                response = await bundle.memory_service.search_memory(
                    key=key,
                    query=search_op.query,
                    limit=search_op.limit,
                )
                memory_results[search_op.session_index] = response

    async def _read_backend_state(
        self,
        bundle: BackendBundle,
        sessions: list[Session],
    ) -> dict[str, Any]:
        """Read final session, summary and memory state from a backend."""
        read_sessions: list[dict[str, Any]] = []
        summaries: list[Optional[dict[str, Any]]] = []
        for session in sessions:
            stored = await bundle.session_service.get_session(
                app_name=session.app_name,
                user_id=session.user_id,
                session_id=session.id,
            )
            if stored is None:
                read_sessions.append(None)
                summaries.append(None)
                continue
            read_sessions.append({
                "id": stored.id,
                "app_name": stored.app_name,
                "user_id": stored.user_id,
                "state": _normalize_state(stored.state),
                "events": _normalize_events(stored.events),
                "historical_events": _normalize_events(stored.historical_events),
                "conversation_count": stored.conversation_count,
            })
            # SessionServiceABC exposes summary text for prompt consumption.
            # The manager retains the strict ownership/count/timestamp metadata
            # required by replay validation.
            raw_summary = await bundle.summarizer_manager.get_session_summary(stored)
            summaries.append(_normalize_summary(raw_summary))
        return {"sessions": read_sessions, "summaries": summaries}

    async def run_case(
        self,
        case: ReplayCase,
        fault: Optional[FaultSpec] = None,
    ) -> list[CaseResult]:
        """Run one replay case across all backends and return comparisons."""
        backends = await self._create_backends()
        backend_states: dict[str, dict[str, Any]] = {}

        for name, bundle in backends.items():
            sessions: list[Session] = []
            memory_results: dict[int, SearchMemoryResponse] = {}
            for op in case.operations:
                await self._execute_operation(bundle, sessions, op, memory_results)

            if fault is not None and name != self.reference_backend:
                await _apply_fault(bundle, sessions, fault)

            state = await self._read_backend_state(bundle, sessions)
            state["memory_results"] = {
                idx: _normalize_memory_response(resp)
                for idx, resp in memory_results.items()
            }
            backend_states[name] = state

            await bundle.close()

        reference_state = backend_states[self.reference_backend]
        results: list[CaseResult] = []
        for name, state in backend_states.items():
            if name == self.reference_backend:
                continue
            diffs = self._compare_states(reference_state, state)
            results.append(CaseResult(
                case_name=case.name,
                backend_name=name,
                consistent=not any(not d["allowed_diff"] for d in diffs),
                differences=diffs,
            ))
        return results

    def _compare_states(
        self,
        reference: dict[str, Any],
        actual: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Compare normalized reference and actual backend states."""
        diffs: list[dict[str, Any]] = []

        ref_sessions = reference.get("sessions", [])
        act_sessions = actual.get("sessions", [])
        if len(ref_sessions) != len(act_sessions):
            diffs.append({
                "path": "sessions",
                "reference_value": len(ref_sessions),
                "actual_value": len(act_sessions),
                "allowed_diff": False,
                "message": "session count mismatch",
            })

        allowed_paths = {
            "sessions[].events[].id",
            "sessions[].events[].timestamp",
            "sessions[].events[].actions.state_delta",
            "sessions[].historical_events[].id",
            "sessions[].historical_events[].timestamp",
            "sessions[].last_update_time",
        }
        for i, (ref_sess, act_sess) in enumerate(zip(ref_sessions, act_sessions)):
            prefix = f"sessions[{i}]"
            if ref_sess is None and act_sess is None:
                continue
            if ref_sess is None or act_sess is None:
                diffs.append({
                    "path": prefix,
                    "reference_value": ref_sess,
                    "actual_value": act_sess,
                    "allowed_diff": False,
                    "message": "session missing on one side",
                })
                continue
            diffs.extend(_deep_diff(
                ref_sess,
                act_sess,
                prefix,
                allowed_paths=allowed_paths,
                tolerance_paths={},
            ))

        ref_summaries = reference.get("summaries", [])
        act_summaries = actual.get("summaries", [])
        for i, (ref_sum, act_sum) in enumerate(zip(ref_summaries, act_summaries)):
            prefix = f"summaries[{i}]"
            if ref_sum is None and act_sum is None:
                continue
            if ref_sum is None or act_sum is None:
                diffs.append({
                    "path": prefix,
                    "reference_value": ref_sum,
                    "actual_value": act_sum,
                    "allowed_diff": False,
                    "message": "summary presence mismatch",
                })
                continue
            diffs.extend(_deep_diff(
                ref_sum,
                act_sum,
                prefix,
                allowed_paths=set(),
                tolerance_paths={f"{prefix}.summary_timestamp": 5.0},
            ))

        ref_memory = reference.get("memory_results", {})
        act_memory = actual.get("memory_results", {})
        for idx in set(ref_memory.keys()) | set(act_memory.keys()):
            prefix = f"memory_results[{idx}]"
            if idx not in ref_memory or idx not in act_memory:
                diffs.append({
                    "path": prefix,
                    "reference_value": ref_memory.get(idx),
                    "actual_value": act_memory.get(idx),
                    "allowed_diff": False,
                    "message": "memory search result missing",
                })
                continue
            diffs.extend(_deep_diff(
                ref_memory[idx],
                act_memory[idx],
                prefix,
                allowed_paths={f"{prefix}[].timestamp"},
            ))

        return diffs

    async def run_cases(
        self,
        cases: list[ReplayCase],
        faults: Optional[dict[str, FaultSpec]] = None,
    ) -> dict[str, Any]:
        """Run multiple cases and build a full report."""
        faults = faults or {}
        report_cases: list[dict[str, Any]] = []
        for case in cases:
            fault = faults.get(case.name)
            results = await self.run_case(case, fault=fault)
            for result in results:
                report_cases.append({
                    "case_name": result.case_name,
                    "backend_name": result.backend_name,
                    "consistent": result.consistent,
                    "expected_faults": case.expected_faults,
                    "injected_fault": fault.fault_type if fault else None,
                    "differences": result.differences,
                })

        normal_cases = [c for c in report_cases if not c["injected_fault"]]
        fault_cases = [c for c in report_cases if c["injected_fault"]]
        false_positives = [c for c in normal_cases if not c["consistent"]]
        detected_faults = [c for c in fault_cases if not c["consistent"]]

        summary_rate = 0.0
        summary_faults = [c for c in fault_cases if c["injected_fault"]
                          in ("drop_summary", "wrong_summary_session", "summary_loss", "summary_override_error")]
        detected_summary_faults = [c for c in summary_faults if not c["consistent"]]
        if summary_faults:
            summary_rate = len(detected_summary_faults) / len(summary_faults)

        report = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "reference_backend": self.reference_backend,
            "backends_compared": [n for n in self.factories.keys() if n != self.reference_backend],
            "cases": report_cases,
            "summary": {
                "total_cases": len(report_cases),
                "normal_cases": len(normal_cases),
                "fault_cases": len(fault_cases),
                "inconsistent_cases": len(false_positives) + len(detected_faults),
                "false_positives": len(false_positives),
                "false_positive_rate": len(false_positives) / len(normal_cases) if normal_cases else 0.0,
                "injected_fault_detection_rate": len(detected_faults) / len(fault_cases) if fault_cases else 0.0,
                "summary_fault_detection_rate": summary_rate,
            },
        }
        return report


def save_report(report: dict[str, Any], path: str) -> None:
    """Save the report to a JSON file."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
