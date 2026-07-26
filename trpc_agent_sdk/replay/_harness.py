# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Replay harness — drives every selected backend through the same JSONL steps."""
from __future__ import annotations

import copy
import time
import uuid
from pathlib import Path
from typing import Any
from typing import Iterable
from typing import Mapping
from typing import Optional

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import EventActions
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import Part

from ._backends import ReplayBackend
from ._backends import _build_backend
from ._cases import DEFAULT_CASES_PATH
from ._cases import load_replay_cases
from ._diff import _canonical_json
from ._diff import _invariant_failure
from ._normalizer import normalize_summary_text
from ._summarizer import summary_anchor_text

# Replay-injected failure tokens raised by the harness to simulate partial
# commit failures. Re-raised by surrounding test code to assert recovery
# semantics.
_INJECTED_FAILURE_TOKEN = "injected_failure_after_summary_persistence"


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
    )


def _canonical_event(
    event: Event,
    index: int,
    summary_id: Optional[str] = None,
    summary_version: Optional[int] = None,
    summary_supersedes: Optional[str] = None,
    summary_session_id: Optional[str] = None,
) -> dict[str, Any]:
    data = event.model_dump(mode="json", exclude_none=True)
    # Always replace the physical event id with a replay-stable identifier so
    # different backends (which allocate IDs at different layers) are
    # compared by content, not by UUID.
    if summary_id:
        data["id"] = summary_id
        if summary_version is not None:
            data["version"] = summary_version
        if summary_supersedes:
            data.setdefault("actions", {})["replay_supersedes"] = summary_supersedes
        if summary_session_id:
            data.setdefault("actions", {})["replay_session_id"] = summary_session_id
    else:
        data["id"] = f"event-{index}"
    data["timestamp"] = "<timestamp>"
    if event.is_summary_event() and data.get("content", {}).get("parts"):
        text = data["content"]["parts"][0].get("text")
        if text and text.startswith("Previous conversation summary:"):
            data["content"]["parts"][0]["text"] = normalize_summary_text(
                text.removeprefix("Previous conversation summary:"))
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
    import json
    return json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


async def _capture_live_summary(backend: ReplayBackend, session: Session, replay_metadata: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    summary = await backend.manager.get_session_summary(session)
    service_text = await backend.session_service.get_session_summary(session)
    anchors = [event for event in session.events if event.is_summary_event()]
    if summary is None and not anchors:
        return None

    anchor = anchors[0] if anchors else None
    anchor_text = summary_anchor_text(anchor) if anchor else None
    metadata = next(
        (record for record in replay_metadata if anchor is None or record.get("anchor_event_id") == anchor.id),
        None,
    )
    if metadata is None and replay_metadata:
        metadata = replay_metadata[-1]
    if metadata is None and summary is not None:
        metadata = {}
    metadata = metadata or {}

    from ._summarizer import is_valid_summary_timestamp
    timestamp_is_valid = bool(
        summary and is_valid_summary_timestamp(getattr(summary, "summary_timestamp", None))
        and float(summary.summary_timestamp) > 0)
    return {
        "summary_id": metadata.get("summary_id"),
        "session_id": metadata.get("session_id") or (summary.session_id if summary else None),
        "version": metadata.get("version"),
        "supersedes": metadata.get("supersedes"),
        "text": normalize_summary_text(service_text or (summary.summary_text if summary else None)),
        "anchor_text": anchor_text,
        "anchor_count": len(anchors),
        "original_event_count": summary.original_event_count if summary else None,
        "compressed_event_count": summary.compressed_event_count if summary else None,
        "updated_at": "<timestamp>" if timestamp_is_valid else "<invalid_timestamp>",
    }


async def _perform_summary(
    backend: ReplayBackend,
    session: Session,
    operation: Mapping[str, Any],
    previous_summary_timestamp: Optional[float],
) -> Session:
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
    live_summary = await backend.manager.get_session_summary(reloaded)
    from ._summarizer import is_valid_summary_timestamp
    if live_summary is None or not is_valid_summary_timestamp(live_summary.summary_timestamp):
        raise RuntimeError(f"Summary {operation['summary_id']} has an invalid update timestamp")
    if previous_summary_timestamp is not None and float(live_summary.summary_timestamp) <= previous_summary_timestamp:
        raise RuntimeError(f"Summary {operation['summary_id']} did not advance its update timestamp")
    return reloaded


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
    memory: dict[str, list[dict[str, Any]]] = {}
    raw_memory_order: dict[str, list[dict[str, Any]]] = {}
    summary_revisions: list[Optional[dict[str, Any]]] = []
    operation_audit: list[dict[str, Any]] = []
    recovery_raw: list[dict[str, Any]] = []
    timestamp_base = time.time()
    summary_timestamp: Optional[float] = None
    replay_metadata_log: list[dict[str, Any]] = []

    for operation in case["operations"]:
        op = operation["op"]
        if op in {"append_event", "state_update"}:
            await backend.session_service.append_event(session, _build_event(operation["event"], timestamp_base))
        elif op == "store_memory":
            await backend.memory_service.store_session(session)
        elif op == "search_memory":
            response = await backend.memory_service.search_memory(
                session.save_key,
                operation["query"],
                limit=operation.get("limit", 10),
            )
            raw_entries = [_canonical_memory_entry(entry) for entry in response.memories]
            raw_memory_order[operation["label"]] = raw_entries
            memory[operation["label"]] = sorted(raw_entries, key=_memory_sort_key)
        elif op == "summarize":
            session = await _perform_summary(backend, session, operation, summary_timestamp)
            replay_metadata_log.extend(backend.summarizer.drain_metadata())
            live = await backend.manager.get_session_summary(session)
            summary_timestamp = float(live.summary_timestamp) if live else None
            summary_revisions.append(await _capture_live_summary(backend, session, replay_metadata_log))
        elif op == "duplicate_append":
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
        elif op == "fail_summary":
            session_checkpoint = session.model_copy(deep=True)
            summary_checkpoint = copy.deepcopy(_summary_cache_entry(backend, session))
            try:
                await _perform_summary(backend, session, operation, summary_timestamp)
                raise RuntimeError(_INJECTED_FAILURE_TOKEN)
            except RuntimeError as exc:
                if str(exc) != _INJECTED_FAILURE_TOKEN:
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
            live_summary = await _capture_live_summary(backend, session, replay_metadata_log)
            restored_summary = await backend.manager.get_session_summary(session)
            recovered = bool(
                live_summary
                and summary_checkpoint
                and restored_summary
                and live_summary["summary_id"] == (summary_revisions[-1]["summary_id"] if summary_revisions else None)
                and restored_summary.summary_timestamp == summary_checkpoint.summary_timestamp
            )
            operation_audit.append({
                "kind": "summary_update",
                "recovered": recovered,
                "final_event_count": len(session.events),
            })
            recovery_raw.append({
                "kind": "summary_update",
                "mechanism": "compensating_update",
                "append_error": _INJECTED_FAILURE_TOKEN,
            })
        else:
            raise ValueError(f"Unsupported operation {op!r} in case {case['case_id']}")

    session = await backend.session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=case["session_id"],
    )
    if session is None:
        raise RuntimeError(f"Session {case['session_id']} was not readable after replay")

    # Build canonical snapshot from the canonical events list. Summary anchors
    # are decorated with their replay metadata so the diff engine can pinpoint
    # which summary was overwritten/owned.
    summary_events = [event for event in session.events if event.is_summary_event()]
    summary_index_by_event_id = {event.id: index for index, event in enumerate(session.events) if event.is_summary_event()}
    canonical_events = []
    for index, event in enumerate(session.events):
        if event.is_summary_event():
            meta = next((m for m in replay_metadata_log if m.get("anchor_event_id") == event.id), None)
            canonical_events.append(_canonical_event(
                event,
                index,
                summary_id=(meta or {}).get("summary_id") or f"summary-{summary_index_by_event_id[event.id]}",
                summary_version=(meta or {}).get("version"),
                summary_supersedes=(meta or {}).get("supersedes"),
                summary_session_id=(meta or {}).get("session_id"),
            ))
        else:
            canonical_events.append(_canonical_event(event, index))
    canonical_historical_events = [_canonical_event(event, index) for index, event in enumerate(session.historical_events)]

    current_summary = await _capture_live_summary(backend, session, replay_metadata_log)
    snapshot = {
        "events": canonical_events,
        "historical_events": canonical_historical_events,
        "state": _canonical_json(session.state),
        "memory": _canonical_json(memory),
        "summary": {
            "current": current_summary,
            "revisions": summary_revisions,
            "anchor_count": len(summary_events),
        },
        "operation_audit": operation_audit,
    }
    from ._diff import validate_expectations
    invariant_failures = validate_expectations(case, snapshot)
    return {
        "backend": backend.name,
        "case_id": case["case_id"],
        "session_id": case["session_id"],
        "operation_count": len(case["operations"]),
        "snapshot": snapshot,
        "raw_memory_order": raw_memory_order,
        "recovery_raw": recovery_raw,
        "replay_metadata": replay_metadata_log,
        "invariant_failures": invariant_failures,
        "error": None,
    }


async def run_replay_harness(
    work_dir: Path,
    cases_path: Path = DEFAULT_CASES_PATH,
    backend_names: Optional[list[str]] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """Replay every case against each selected backend."""
    import os as _os
    started = time.perf_counter()
    work_dir.mkdir(parents=True, exist_ok=True)
    cases = load_replay_cases(cases_path)
    effective_environ: Mapping[str, str] = environ if environ is not None else _os.environ
    if backend_names is None:
        from ._backends import resolve_backend_names
        names = resolve_backend_names(effective_environ)
    else:
        names = list(backend_names)
    run_token = uuid.uuid4().hex[:10]
    results = []
    backends = []
    try:
        for name in names:
            backend_dir = work_dir / name
            backend_dir.mkdir(parents=True, exist_ok=True)
            backends.append(await _build_backend(name, backend_dir, effective_environ))

        for backend in backends:
            for case in cases:
                try:
                    results.append(await _run_case(backend, case, run_token))
                except Exception as exc:  # pylint: disable=broad-except
                    results.append({
                        "backend": backend.name,
                        "case_id": case["case_id"],
                        "session_id": case["session_id"],
                        "operation_count": len(case["operations"]),
                        "snapshot": {
                            "error": {
                                "type": type(exc).__name__,
                                "message": str(exc),
                            },
                        },
                        "raw_memory_order": {},
                        "recovery_raw": [],
                        "replay_metadata": [],
                        "invariant_failures": [
                            _invariant_failure(case, "$.replay", "completed", f"{type(exc).__name__}: {exc}")
                        ],
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
        "backend_names": [b.name for b in backends],
        "results": results,
        "elapsed_seconds": time.perf_counter() - started,
    }


def write_diff_report(report: Mapping[str, Any], path: Path) -> None:
    """Write a stable, UTF-8 JSON report."""
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")