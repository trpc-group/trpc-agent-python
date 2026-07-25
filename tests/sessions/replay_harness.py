# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Reusable Session, Memory, and Summary multi-backend replay harness."""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import math
import os
import re
import time
import unicodedata
import uuid
from collections import deque
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from typing import Iterable
from typing import Mapping
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
from trpc_agent_sdk.sessions import SqlSessionService
from trpc_agent_sdk.sessions import SummarizerSessionManager
from trpc_agent_sdk.sessions._session_summarizer import SessionSummarizer
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import EventActions
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import Part

DEFAULT_CASES_PATH = Path(__file__).with_name("replay_cases") / "session_memory_summary.jsonl"
SUMMARY_PREFIX = "Previous conversation summary:"

NORMALIZATION_RULES = [
    {
        "path": "$.events[*].timestamp",
        "strategy": "replace_with_<timestamp>",
        "reason": "Wall-clock timestamps are non-business metadata.",
    },
    {
        "path": "$.events[*].id",
        "strategy": "logical_replay_id_or_stable_index",
        "reason": "Backends and summary generation may allocate different physical IDs.",
    },
    {
        "path": "$.summary.*.text",
        "strategy": "unicode_nfkc_casefold_and_whitespace_collapse",
        "reason": "Summary content is compared semantically for formatting-only differences.",
    },
    {
        "path": "$.memory.*",
        "strategy": "sort_by_normalized_content_author",
        "reason": "MemoryService does not define result ordering for equal keyword matches.",
    },
    {
        "path": "$.*",
        "strategy": "structural_json_comparison",
        "reason": "Serialized object key order is not business data.",
    },
]

ALLOWED_DIFF_RULES = [
    {
        "path": "$.memory.*",
        "scope": "order_only",
        "reason": "Keyword-memory ranking order is backend-specific; entry content and count must still match.",
    },
    {
        "path": "$.recovery_raw[*].mechanism",
        "scope": "mechanism_only",
        "reason": "A backend may reject a duplicate transactionally or require compensating cleanup.",
    },
]


class ReplaySummaryModel:
    """Deterministic model used to exercise the real summarizer pipeline."""

    name = "replay-summary-model"

    def __init__(self) -> None:
        self._responses: deque[str] = deque()

    def enqueue(self, text: str) -> None:
        """Queue one deterministic summary response."""
        self._responses.append(text)

    async def generate_async(self, _request, stream: bool = False, ctx=None):
        """Yield the queued response through the model interface."""
        del stream, ctx
        if not self._responses:
            raise RuntimeError("No replay summary response was queued")
        yield LlmResponse(content=Content(role="model", parts=[Part.from_text(text=self._responses.popleft())]))


class ReplaySessionSummarizer(SessionSummarizer):
    """Annotate generated summary anchors with replay ownership metadata."""

    def __init__(self, model: ReplaySummaryModel) -> None:
        super().__init__(
            model=model,
            check_summarizer_functions=[lambda _session: True],
            keep_recent_count=2,
        )
        self._revision: dict[str, Any] = {}

    def set_revision(self, summary_id: str, version: int, supersedes: Optional[str]) -> None:
        """Set metadata for the next generated summary anchor."""
        self._revision = {
            "summary_id": summary_id,
            "version": version,
            "supersedes": supersedes,
        }

    async def create_session_summary(
        self,
        session: Session,
        ctx=None,
        store_historical_events: bool = False,
    ) -> Optional[str]:
        """Generate a summary and attach metadata before backend persistence."""
        summary_text = await super().create_session_summary(
            session,
            ctx=ctx,
            store_historical_events=store_historical_events,
        )
        if not summary_text:
            return summary_text

        summary_event = next((event for event in session.events if event.is_summary_event()), None)
        if summary_event is None:
            raise RuntimeError("Summarizer returned text without a summary anchor event")

        retained_events = [event for event in session.events if event is not summary_event]
        if retained_events:
            summary_event.timestamp = min(event.timestamp for event in retained_events) - 0.001
        summary_event.version = self._revision["version"]
        metadata = dict(summary_event.custom_metadata or {})
        metadata["replay_summary"] = {
            **self._revision,
            "session_id": session.id,
        }
        summary_event.custom_metadata = metadata
        return summary_text


class ReplayBackend:
    """A paired SessionService and MemoryService backend."""

    def __init__(self, name: str, session_service, memory_service, model: ReplaySummaryModel,
                 summarizer: ReplaySessionSummarizer, manager: SummarizerSessionManager) -> None:
        self.name = name
        self.session_service = session_service
        self.memory_service = memory_service
        self.model = model
        self.summarizer = summarizer
        self.manager = manager

    async def close(self) -> None:
        """Release both backend services."""
        await self.memory_service.close()
        await self.session_service.close()


def load_replay_cases(path: Path = DEFAULT_CASES_PATH) -> list[dict[str, Any]]:
    """Load and minimally validate JSONL replay cases."""
    cases = []
    with path.open("r", encoding="utf-8") as case_file:
        for line_number, line in enumerate(case_file, start=1):
            if not line.strip():
                continue
            try:
                case = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid replay JSON on line {line_number}: {exc}") from exc
            missing = {"case_id", "session_id", "operations", "expect"} - set(case)
            if missing:
                raise ValueError(f"Replay case on line {line_number} is missing {sorted(missing)}")
            cases.append(case)

    case_ids = [case["case_id"] for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Replay case IDs must be unique")
    return cases


def normalize_summary_text(text: Optional[str]) -> Optional[str]:
    """Normalize summary formatting while preserving words and punctuation."""
    if text is None:
        return None
    normalized = unicodedata.normalize("NFKC", text)
    return " ".join(normalized.split()).casefold()


def _memory_config() -> MemoryServiceConfig:
    config = MemoryServiceConfig(enabled=True)
    config.clean_ttl_config()
    return config


def _session_config():
    from trpc_agent_sdk.sessions import SessionServiceConfig

    config = SessionServiceConfig(store_historical_events=True)
    config.clean_ttl_config()
    return config


async def _build_backend(name: str, work_dir: Path, environ: Mapping[str, str]) -> ReplayBackend:
    model = ReplaySummaryModel()
    summarizer = ReplaySessionSummarizer(model)
    manager = SummarizerSessionManager(model=model, summarizer=summarizer)

    if name == "inmemory":
        session_service = InMemorySessionService(
            summarizer_manager=manager,
            session_config=_session_config(),
        )
        memory_service = InMemoryMemoryService(memory_service_config=_memory_config())
    elif name == "sqlite":
        sqlite_path = work_dir / "replay.sqlite3"
        db_url = f"sqlite:///{sqlite_path}"
        session_service = SqlSessionService(
            db_url=db_url,
            summarizer_manager=manager,
            session_config=_session_config(),
        )
        memory_service = SqlMemoryService(
            db_url=db_url,
            memory_service_config=_memory_config(),
        )
        await session_service._sql_storage.create_sql_engine()
        await memory_service._sql_storage.create_sql_engine()
    elif name == "sql":
        db_url = environ.get("TRPC_REPLAY_SQL_URL")
        if not db_url:
            raise ValueError("TRPC_REPLAY_SQL_URL is required when the sql backend is selected")
        session_service = SqlSessionService(
            db_url=db_url,
            summarizer_manager=manager,
            session_config=_session_config(),
        )
        memory_service = SqlMemoryService(
            db_url=db_url,
            memory_service_config=_memory_config(),
        )
        await session_service._sql_storage.create_sql_engine()
        await memory_service._sql_storage.create_sql_engine()
    elif name == "redis":
        db_url = environ.get("TRPC_REPLAY_REDIS_URL")
        if not db_url:
            raise ValueError("TRPC_REPLAY_REDIS_URL is required when the redis backend is selected")
        session_service = RedisSessionService(
            db_url=db_url,
            summarizer_manager=manager,
            session_config=_session_config(),
        )
        memory_service = RedisMemoryService(
            db_url=db_url,
            memory_service_config=_memory_config(),
        )
    else:
        raise ValueError(f"Unsupported replay backend: {name}")

    return ReplayBackend(name, session_service, memory_service, model, summarizer, manager)


def resolve_backend_names(environ: Mapping[str, str] = os.environ) -> list[str]:
    """Resolve lightweight and opt-in integration backends from environment."""
    configured = environ.get("TRPC_REPLAY_BACKENDS")
    if configured:
        names = [name.strip().lower() for name in configured.split(",") if name.strip()]
    else:
        names = ["inmemory", "sqlite"]
        if environ.get("TRPC_REPLAY_SQL_URL"):
            names.append("sql")
        if environ.get("TRPC_REPLAY_REDIS_URL"):
            names.append("redis")

    if not names:
        raise ValueError("At least one replay backend must be selected")
    unknown = set(names) - {"inmemory", "sqlite", "sql", "redis"}
    if unknown:
        raise ValueError(f"Unsupported replay backends: {sorted(unknown)}")
    return list(dict.fromkeys(names))


def _build_event(data: Mapping[str, Any], timestamp_base: float) -> Event:
    parts = []
    if "text" in data:
        parts.append(Part.from_text(text=data["text"]))
    if "function_call" in data:
        function_call = data["function_call"]
        parts.append(
            Part(function_call=FunctionCall(
                id=function_call.get("id"),
                name=function_call["name"],
                args=function_call.get("args", {}),
            )))
    if "function_response" in data:
        function_response = data["function_response"]
        parts.append(
            Part(function_response=FunctionResponse(
                id=function_response.get("id"),
                name=function_response["name"],
                response=function_response.get("response", {}),
            )))

    content = Content(role=data.get("role"), parts=parts) if parts else None
    return Event(
        id=data["id"],
        invocation_id=data.get("invocation_id", f"invocation-{data['id']}"),
        author=data.get("author", "system"),
        content=content,
        actions=EventActions(state_delta=data.get("state_delta", {})),
        timestamp=timestamp_base + (float(data["timestamp"]) - 1_700_000_000.0) / 1_000.0,
        custom_metadata={"replay_event_id": data["id"]},
    )


def _canonical_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonical_json(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical_json(item) for item in value]
    if isinstance(value, set):
        return sorted(_canonical_json(item) for item in value)
    return value


def _canonical_event(event: Event, index: int) -> dict[str, Any]:
    data = event.model_dump(mode="json", exclude_none=True)
    metadata = dict(data.get("custom_metadata") or {})
    replay_event_id = metadata.pop("replay_event_id", None)
    summary_metadata = metadata.get("replay_summary")

    data["id"] = (summary_metadata or {}).get("summary_id") or replay_event_id or f"event-{index}"
    data["timestamp"] = "<timestamp>"
    if metadata:
        data["custom_metadata"] = metadata
    else:
        data.pop("custom_metadata", None)
    if event.is_summary_event() and data.get("content", {}).get("parts"):
        text = data["content"]["parts"][0].get("text")
        if text and text.startswith(SUMMARY_PREFIX):
            data["content"]["parts"][0]["text"] = normalize_summary_text(text.removeprefix(SUMMARY_PREFIX))
    if data.get("long_running_tool_ids"):
        data["long_running_tool_ids"] = sorted(data["long_running_tool_ids"])
    else:
        data.pop("long_running_tool_ids", None)
    return _canonical_json(data)


def _canonical_memory_entry(entry) -> dict[str, Any]:
    return _canonical_json({
        "author": entry.author,
        "content": entry.content.model_dump(mode="json", exclude_none=True),
        "timestamp": "<timestamp>" if entry.timestamp else None,
    })


def _memory_sort_key(entry: Mapping[str, Any]) -> str:
    return json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _summary_anchor_text(anchor: Optional[Event]) -> Optional[str]:
    if not anchor or not anchor.content or not anchor.content.parts:
        return None
    text = anchor.content.parts[0].text
    if text and text.startswith(SUMMARY_PREFIX):
        text = text.removeprefix(SUMMARY_PREFIX)
    return normalize_summary_text(text)


async def _capture_live_summary(backend: ReplayBackend, session: Session) -> Optional[dict[str, Any]]:
    summary = await backend.manager.get_session_summary(session)
    service_text = await backend.session_service.get_session_summary(session)
    anchors = [event for event in session.events if event.is_summary_event()]
    if summary is None and not anchors:
        return None

    anchor = anchors[0] if anchors else None
    metadata = ((anchor.custom_metadata or {}).get("replay_summary", {}) if anchor else {})
    timestamp_is_valid = bool(summary and math.isfinite(summary.summary_timestamp) and summary.summary_timestamp > 0)
    return {
        "summary_id": metadata.get("summary_id"),
        "session_id": metadata.get("session_id") or (summary.session_id if summary else None),
        "version": metadata.get("version") if metadata else (anchor.version if anchor else None),
        "supersedes": metadata.get("supersedes"),
        "text": normalize_summary_text(service_text or (summary.summary_text if summary else None)),
        "anchor_text": _summary_anchor_text(anchor),
        "anchor_count": len(anchors),
        "original_event_count": summary.original_event_count if summary else None,
        "compressed_event_count": summary.compressed_event_count if summary else None,
        "updated_at": "<timestamp>" if timestamp_is_valid else "<invalid_timestamp>",
    }


async def _perform_summary(backend: ReplayBackend, session: Session, operation: Mapping[str, Any], revision_number: int,
                           previous_summary_timestamp: Optional[float]) -> tuple[Session, dict[str, Any], float]:
    backend.model.enqueue(operation["text"])
    backend.summarizer.set_revision(
        operation["summary_id"],
        operation["version"],
        operation.get("supersedes"),
    )
    await backend.manager.create_session_summary(session, force=True)
    reloaded = await backend.session_service.get_session(
        app_name=session.app_name,
        user_id=session.user_id,
        session_id=session.id,
    )
    if reloaded is None:
        raise RuntimeError(f"Session {session.id} disappeared after summary update")
    revision = await _capture_live_summary(backend, reloaded)
    if revision is None:
        raise RuntimeError(f"Summary {operation['summary_id']} was not readable after persistence")
    live_summary = await backend.manager.get_session_summary(reloaded)
    if live_summary is None or not math.isfinite(live_summary.summary_timestamp) or live_summary.summary_timestamp <= 0:
        raise RuntimeError(f"Summary {operation['summary_id']} has an invalid update timestamp")
    if previous_summary_timestamp is not None and live_summary.summary_timestamp <= previous_summary_timestamp:
        raise RuntimeError(f"Summary {operation['summary_id']} did not advance its update timestamp")
    revision["updated_at_order"] = revision_number
    return reloaded, revision, live_summary.summary_timestamp


def _deduplicate_events(events: Iterable[Event]) -> list[Event]:
    seen = set()
    deduplicated = []
    for event in events:
        if event.id in seen:
            continue
        seen.add(event.id)
        deduplicated.append(event)
    return deduplicated


def _summary_cache_entry(backend: ReplayBackend, session: Session):
    return backend.manager._summarizer_cache.get(session.app_name, {}).get(session.user_id, {}).get(session.id)


def _restore_summary_cache(backend: ReplayBackend, session: Session, summary) -> None:
    app_cache = backend.manager._summarizer_cache.setdefault(session.app_name, {})
    user_cache = app_cache.setdefault(session.user_id, {})
    if summary is None:
        user_cache.pop(session.id, None)
    else:
        user_cache[session.id] = summary


async def _run_case(backend: ReplayBackend, case: Mapping[str, Any], run_token: str) -> dict[str, Any]:
    app_name = f"replay-{run_token}-{case['case_id']}"
    user_id = "replay-user"
    session = await backend.session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=case["session_id"],
        state=case.get("initial_state"),
    )
    memory = {}
    raw_memory_order = {}
    revisions = []
    operation_audit = []
    recovery_raw = []
    timestamp_base = time.time()
    summary_timestamp = None

    for operation in case["operations"]:
        operation_name = operation["op"]
        if operation_name in {"append_event", "state_update"}:
            await backend.session_service.append_event(session, _build_event(operation["event"], timestamp_base))
        elif operation_name == "store_memory":
            await backend.memory_service.store_session(session)
        elif operation_name == "search_memory":
            response = await backend.memory_service.search_memory(
                session.save_key,
                operation["query"],
                limit=operation.get("limit", 10),
            )
            raw_entries = [_canonical_memory_entry(entry) for entry in response.memories]
            raw_memory_order[operation["label"]] = raw_entries
            memory[operation["label"]] = sorted(raw_entries, key=_memory_sort_key)
        elif operation_name == "summarize":
            session, revision, summary_timestamp = await _perform_summary(
                backend,
                session,
                operation,
                len(revisions) + 1,
                summary_timestamp,
            )
            revisions.append(revision)
        elif operation_name == "duplicate_append":
            duplicate_event = _build_event(operation["event"], timestamp_base)
            mechanism = "backend_append"
            append_error = None
            try:
                await backend.session_service.append_event(session, duplicate_event)
            except Exception as exc:  # pylint: disable=broad-except
                append_error = type(exc).__name__
                mechanism = "transactional_rejection"

            fresh = await backend.session_service.get_session(
                app_name=app_name,
                user_id=user_id,
                session_id=case["session_id"],
            )
            if fresh is None:
                raise RuntimeError("Session disappeared during duplicate recovery")
            duplicate_count = sum(event.id == duplicate_event.id for event in fresh.events)
            if duplicate_count > 1:
                mechanism = "compensating_deduplication"
                fresh.events = _deduplicate_events(fresh.events)
                fresh.historical_events = _deduplicate_events(fresh.historical_events)
                await backend.session_service.update_session(fresh)
                fresh = await backend.session_service.get_session(
                    app_name=app_name,
                    user_id=user_id,
                    session_id=case["session_id"],
                )
            session = fresh
            final_count = sum(event.id == duplicate_event.id for event in session.events)
            operation_audit.append({
                "kind": "duplicate_append",
                "recovered": final_count == 1,
                "final_event_count": final_count,
            })
            recovery_raw.append({
                "kind": "duplicate_append",
                "mechanism": mechanism,
                "append_error": append_error,
                "observed_duplicate_count": duplicate_count,
            })
        elif operation_name == "fail_summary":
            session_checkpoint = session.model_copy(deep=True)
            summary_checkpoint = copy.deepcopy(_summary_cache_entry(backend, session))
            try:
                await _perform_summary(
                    backend,
                    session,
                    operation,
                    len(revisions) + 1,
                    summary_timestamp,
                )
                raise RuntimeError("injected_failure_after_summary_persistence")
            except RuntimeError as exc:
                if str(exc) != "injected_failure_after_summary_persistence":
                    raise
                await backend.session_service.update_session(session_checkpoint)
                _restore_summary_cache(backend, session_checkpoint, summary_checkpoint)
            session = await backend.session_service.get_session(
                app_name=app_name,
                user_id=user_id,
                session_id=case["session_id"],
            )
            if session is None:
                raise RuntimeError("Session disappeared during summary recovery")
            live_summary = await _capture_live_summary(backend, session)
            restored_summary = await backend.manager.get_session_summary(session)
            recovered = bool(live_summary and summary_checkpoint and restored_summary
                             and live_summary["summary_id"] == revisions[-1]["summary_id"]
                             and restored_summary.summary_timestamp == summary_checkpoint.summary_timestamp)
            operation_audit.append({
                "kind": "summary_update",
                "recovered": recovered,
                "final_event_count": len(session.events),
            })
            recovery_raw.append({
                "kind": "summary_update",
                "mechanism": "compensating_update",
                "append_error": "injected_failure_after_summary_persistence",
            })
        else:
            raise ValueError(f"Unsupported operation {operation_name!r} in case {case['case_id']}")

    session = await backend.session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=case["session_id"],
    )
    if session is None:
        raise RuntimeError(f"Session {case['session_id']} was not readable after replay")

    snapshot = {
        "events": [_canonical_event(event, index) for index, event in enumerate(session.events)],
        "historical_events": [_canonical_event(event, index) for index, event in enumerate(session.historical_events)],
        "state": _canonical_json(session.state),
        "memory": _canonical_json(memory),
        "summary": {
            "current": await _capture_live_summary(backend, session),
            "revisions": revisions,
        },
        "operation_audit": operation_audit,
    }
    invariant_failures = validate_expectations(case, snapshot)
    return {
        "backend": backend.name,
        "case_id": case["case_id"],
        "session_id": case["session_id"],
        "operation_count": len(case["operations"]),
        "snapshot": snapshot,
        "raw_memory_order": raw_memory_order,
        "recovery_raw": recovery_raw,
        "invariant_failures": invariant_failures,
        "error": None,
    }


def _invariant_failure(case: Mapping[str, Any], path: str, expected: Any, actual: Any) -> dict[str, Any]:
    return {
        "case_id": case["case_id"],
        "session_id": case["session_id"],
        "path": path,
        "expected": expected,
        "actual": actual,
    }


def validate_expectations(case: Mapping[str, Any], snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Validate per-backend invariants so InMemory-only mode remains useful."""
    expect = case["expect"]
    failures = []

    checks = [
        ("$.events.length", expect.get("active_event_count"), len(snapshot["events"])),
        ("$.historical_events.length", expect.get("historical_event_count"), len(snapshot["historical_events"])),
        ("$.state", expect.get("state"), snapshot["state"]),
    ]
    current_summary = snapshot["summary"]["current"]
    summary_present = current_summary is not None
    checks.append(("$.summary.current.present", expect.get("summary_present"), summary_present))

    if summary_present:
        summary_checks = {
            "$.summary.current.summary_id": "summary_id",
            "$.summary.current.version": "summary_version",
            "$.summary.current.supersedes": "summary_supersedes",
            "$.summary.current.text": "summary_text",
            "$.summary.current.session_id": "summary_session_id",
            "$.summary.current.anchor_count": "summary_anchor_count",
        }
        for path, expected_key in summary_checks.items():
            if expected_key in expect:
                checks.append((path, expect[expected_key], current_summary[path.rsplit(".", maxsplit=1)[-1]]))

    for label, count in expect.get("memory_counts", {}).items():
        checks.append((f"$.memory.{label}.length", count, len(snapshot["memory"].get(label, []))))

    if expect.get("unique_event_ids"):
        event_ids = [event["id"] for event in snapshot["events"]]
        checks.append(("$.events.unique_ids", len(event_ids), len(set(event_ids))))

    expected_recovery_kinds = expect.get("recovery_kinds")
    if expected_recovery_kinds is not None:
        actual_kinds = [entry["kind"] for entry in snapshot["operation_audit"] if entry["recovered"]]
        checks.append(("$.operation_audit.recovered_kinds", expected_recovery_kinds, actual_kinds))

    for path, expected_value, actual_value in checks:
        if expected_value is not None and expected_value != actual_value:
            failures.append(_invariant_failure(case, path, expected_value, actual_value))
    return failures


async def run_replay_harness(
    work_dir: Path,
    cases_path: Path = DEFAULT_CASES_PATH,
    backend_names: Optional[list[str]] = None,
    environ: Mapping[str, str] = os.environ,
) -> dict[str, Any]:
    """Replay every case against each selected backend."""
    started = time.perf_counter()
    work_dir.mkdir(parents=True, exist_ok=True)
    cases = load_replay_cases(cases_path)
    names = backend_names or resolve_backend_names(environ)
    run_token = uuid.uuid4().hex[:10]
    results = []
    backends = []
    try:
        for name in names:
            backend_dir = work_dir / name
            backend_dir.mkdir(parents=True, exist_ok=True)
            backends.append(await _build_backend(name, backend_dir, environ))

        for backend in backends:
            for case in cases:
                try:
                    results.append(await _run_case(backend, case, run_token))
                except Exception as exc:  # pylint: disable=broad-except
                    results.append({
                        "backend":
                        backend.name,
                        "case_id":
                        case["case_id"],
                        "session_id":
                        case["session_id"],
                        "operation_count":
                        len(case["operations"]),
                        "snapshot": {
                            "error": {
                                "type": type(exc).__name__,
                                "message": str(exc),
                            }
                        },
                        "raw_memory_order": {},
                        "recovery_raw": [],
                        "invariant_failures":
                        [_invariant_failure(case, "$.replay", "completed", f"{type(exc).__name__}: {exc}")],
                        "error": {
                            "type": type(exc).__name__,
                            "message": str(exc),
                        },
                    })
    finally:
        for backend in reversed(backends):
            await backend.close()

    return {
        "cases": cases,
        "backend_names": names,
        "results": results,
        "elapsed_seconds": time.perf_counter() - started,
    }


def _value_diffs(reference: Any, candidate: Any, path: str = "$") -> list[dict[str, Any]]:
    if isinstance(reference, dict) and isinstance(candidate, dict):
        differences = []
        for key in sorted(set(reference) | set(candidate)):
            child_path = f"{path}.{key}"
            if key not in reference:
                differences.append({"path": child_path, "reference_value": None, "backend_value": candidate[key]})
            elif key not in candidate:
                differences.append({"path": child_path, "reference_value": reference[key], "backend_value": None})
            else:
                differences.extend(_value_diffs(reference[key], candidate[key], child_path))
        return differences

    if isinstance(reference, list) and isinstance(candidate, list):
        differences = []
        for index in range(max(len(reference), len(candidate))):
            child_path = f"{path}[{index}]"
            if index >= len(reference):
                differences.append({"path": child_path, "reference_value": None, "backend_value": candidate[index]})
            elif index >= len(candidate):
                differences.append({"path": child_path, "reference_value": reference[index], "backend_value": None})
            else:
                differences.extend(_value_diffs(reference[index], candidate[index], child_path))
        return differences

    if reference != candidate:
        return [{"path": path, "reference_value": reference, "backend_value": candidate}]
    return []


def _domain_for_path(path: str) -> str:
    if path.startswith("$.events") or path.startswith("$.historical_events"):
        return "events"
    if path.startswith("$.state"):
        return "state"
    if path.startswith("$.memory"):
        return "memory"
    if path.startswith("$.summary"):
        return "summary"
    if path.startswith("$.operation_audit"):
        return "recovery"
    return "replay"


def _event_index_for_path(path: str) -> Optional[int]:
    match = re.search(r"\.(?:events|historical_events)\[(\d+)\]", path)
    return int(match.group(1)) if match else None


def _summary_id_for_path(path: str, reference: Mapping[str, Any], candidate: Mapping[str, Any]) -> Optional[str]:
    revision_match = re.search(r"\.summary\.revisions\[(\d+)\]", path)
    if revision_match:
        index = int(revision_match.group(1))
        for snapshot in (candidate, reference):
            revisions = snapshot.get("summary", {}).get("revisions", [])
            if index < len(revisions):
                return revisions[index].get("summary_id")
    for snapshot in (candidate, reference):
        current = snapshot.get("summary", {}).get("current")
        if current:
            return current.get("summary_id")
    return None


def _locate_difference(difference: dict[str, Any], result: Mapping[str, Any], reference_backend: str,
                       reference_snapshot: Mapping[str, Any]) -> dict[str, Any]:
    path = difference["path"]
    return {
        "case_id": result["case_id"],
        "session_id": result["session_id"],
        "reference_backend": reference_backend,
        "backend": result["backend"],
        "domain": _domain_for_path(path),
        "path": path,
        "event_index": _event_index_for_path(path),
        "summary_id": _summary_id_for_path(path, reference_snapshot, result["snapshot"]),
        "reference_value": difference["reference_value"],
        "backend_value": difference["backend_value"],
        "allowed": False,
        "explanation": "Normalized business values differ.",
    }


def _allowed_raw_differences(reference: Mapping[str, Any], result: Mapping[str, Any]) -> list[dict[str, Any]]:
    allowed = []
    reference_memory = reference.get("raw_memory_order", {})
    backend_memory = result.get("raw_memory_order", {})
    for label in sorted(set(reference_memory) | set(backend_memory)):
        reference_entries = reference_memory.get(label, [])
        backend_entries = backend_memory.get(label, [])
        if reference_entries != backend_entries and sorted(reference_entries, key=_memory_sort_key) == sorted(
                backend_entries, key=_memory_sort_key):
            allowed.append({
                "case_id": result["case_id"],
                "session_id": result["session_id"],
                "reference_backend": reference["backend"],
                "backend": result["backend"],
                "domain": "memory",
                "path": f"$.memory.{label}",
                "event_index": None,
                "summary_id": None,
                "reference_value": reference_entries,
                "backend_value": backend_entries,
                "allowed": True,
                "explanation": ALLOWED_DIFF_RULES[0]["reason"],
            })

    if (reference.get("recovery_raw") != result.get("recovery_raw") and reference.get(
            "snapshot", {}).get("operation_audit") == result.get("snapshot", {}).get("operation_audit")):
        allowed.append({
            "case_id": result["case_id"],
            "session_id": result["session_id"],
            "reference_backend": reference["backend"],
            "backend": result["backend"],
            "domain": "recovery",
            "path": "$.recovery_raw",
            "event_index": None,
            "summary_id": None,
            "reference_value": reference.get("recovery_raw"),
            "backend_value": result.get("recovery_raw"),
            "allowed": True,
            "explanation": ALLOWED_DIFF_RULES[1]["reason"],
        })
    return allowed


def build_diff_report(run: Mapping[str, Any]) -> dict[str, Any]:
    """Build a structured, field-locatable diff report from replay results."""
    result_index = {(result["case_id"], result["backend"]): result for result in run["results"]}
    reference_backend = run["backend_names"][0]
    report_cases = []
    all_differences = []
    all_allowed = []
    all_invariant_failures = []

    for case in run["cases"]:
        case_id = case["case_id"]
        reference = result_index[(case_id, reference_backend)]
        case_differences = []
        case_allowed = []
        backend_results = {}

        for backend_name in run["backend_names"]:
            result = result_index[(case_id, backend_name)]
            backend_results[backend_name] = {
                "operation_count": result["operation_count"],
                "snapshot": result["snapshot"],
                "invariant_failures": result["invariant_failures"],
                "error": result["error"],
            }
            all_invariant_failures.extend({
                **failure,
                "backend": backend_name,
            } for failure in result["invariant_failures"])
            if backend_name == reference_backend:
                continue
            differences = _value_diffs(reference["snapshot"], result["snapshot"])
            located = [
                _locate_difference(difference, result, reference_backend, reference["snapshot"])
                for difference in differences
            ]
            case_differences.extend(located)
            case_allowed.extend(_allowed_raw_differences(reference, result))

        all_differences.extend(case_differences)
        all_allowed.extend(case_allowed)
        report_cases.append({
            "case_id":
            case_id,
            "description":
            case["description"],
            "session_id":
            case["session_id"],
            "status":
            "passed" if not case_differences and not any(data["invariant_failures"]
                                                         for data in backend_results.values()) else "failed",
            "backend_results":
            backend_results,
            "allowed_diffs":
            case_allowed,
            "differences":
            case_differences,
        })

    if len(run["backend_names"]) == 1:
        mode = "inmemory-only"
    elif any(name in {"sql", "redis"} for name in run["backend_names"]):
        mode = "integration"
    else:
        mode = "lightweight-persistent"

    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "reference_backend": reference_backend,
        "backends": run["backend_names"],
        "normalization_rules": NORMALIZATION_RULES,
        "allowed_diff_rules": ALLOWED_DIFF_RULES,
        "cases": report_cases,
        "summary": {
            "case_count": len(run["cases"]),
            "backend_count": len(run["backend_names"]),
            "passed_case_count": sum(case["status"] == "passed" for case in report_cases),
            "unexpected_diff_count": len(all_differences),
            "allowed_diff_count": len(all_allowed),
            "invariant_failure_count": len(all_invariant_failures),
            "elapsed_seconds": round(run["elapsed_seconds"], 6),
        },
    }
    report["report_sha256"] = hashlib.sha256(
        json.dumps(report["cases"], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return report


def write_diff_report(report: Mapping[str, Any], path: Path) -> None:
    """Write a stable, UTF-8 JSON report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _print_report_summary(report: Mapping[str, Any], output_path: Path) -> None:
    print("Session / Memory / Summary Replay Consistency")
    print(f"Mode: {report['mode']}")
    print(f"Backends: {', '.join(report['backends'])}")
    print("")
    print(f"{'CASE':32} {'STATUS':8} {'DIFFS':>5} {'ALLOWED':>7}")
    print("-" * 58)
    for case in report["cases"]:
        print(f"{case['case_id'][:32]:32} {case['status'].upper():8} "
              f"{len(case['differences']):5d} {len(case['allowed_diffs']):7d}")
    print("-" * 58)
    summary = report["summary"]
    print(f"Passed: {summary['passed_case_count']}/{summary['case_count']} cases")
    print(f"Unexpected diffs: {summary['unexpected_diff_count']}")
    print(f"Invariant failures: {summary['invariant_failure_count']}")
    print(f"Elapsed: {summary['elapsed_seconds']:.3f}s")
    print(f"Report: {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--output", type=Path, default=Path("session_memory_summary_diff_report.json"))
    parser.add_argument("--work-dir", type=Path, default=Path(".replay-work"))
    parser.add_argument("--backends", help="Comma-separated override: inmemory,sqlite,sql,redis")
    args = parser.parse_args()
    backend_names = None
    if args.backends:
        backend_names = [name.strip() for name in args.backends.split(",") if name.strip()]

    run = asyncio.run(run_replay_harness(
        work_dir=args.work_dir,
        cases_path=args.cases,
        backend_names=backend_names,
    ))
    report = build_diff_report(run)
    write_diff_report(report, args.output)
    _print_report_summary(report, args.output)
    summary = report["summary"]
    return 1 if summary["unexpected_diff_count"] or summary["invariant_failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
