# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.

"""Backend-neutral replay and comparison helpers for session tests."""

from __future__ import annotations

import copy
import fnmatch
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Iterable

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import EventActions
from trpc_agent_sdk.types import Part

NORMALIZED_TIMESTAMP = "<normalized:timestamp>"


@dataclass(frozen=True)
class ReplayCase:
    """One standard input trace and its explicitly permitted differences."""

    case_id: str
    description: str
    operations: list[dict[str, Any]]
    allowed_diff: list[dict[str, str]]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ReplayCase":
        return cls(
            case_id=value["case_id"],
            description=value["description"],
            operations=value["operations"],
            allowed_diff=value.get("allowed_diff", []),
        )


@dataclass
class ReplayBackend:
    """Session and memory services that together form a replay target."""

    name: str
    session_service: Any
    memory_service: Any

    async def close(self) -> None:
        await self.memory_service.close()
        await self.session_service.close()


def load_replay_cases(path: Path) -> list[ReplayCase]:
    """Load non-empty JSONL records from ``path``."""
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            cases.append(ReplayCase.from_dict(json.loads(line)))
    return cases


def _event_from_operation(case_id: str, index: int, operation: dict[str, Any]) -> Event:
    event_id = operation.get("event_id", f"{case_id}-event-{index:02d}")
    event_type = operation["type"]
    if event_type == "function_call":
        part = Part.from_function_call(name=operation["name"], args=operation.get("args", {}))
    elif event_type == "function_response":
        part = Part.from_function_response(name=operation["name"], response=operation.get("response", {}))
    else:
        part = Part.from_text(text=operation.get("text", ""))

    return Event(
        id=event_id,
        invocation_id=operation.get("invocation_id", f"{case_id}-invocation"),
        author=operation.get("author", "assistant"),
        content=Content(role=operation.get("role"), parts=[part]),
        actions=EventActions(state_delta=operation.get("state_delta", {})),
        timestamp=time.time() + index / 1000,
    )


def _summary_record(event: Event) -> dict[str, Any]:
    metadata = event.custom_metadata or {}
    return {
        "summary_id": event.id,
        "session_id": metadata.get("session_id"),
        "version": metadata.get("summary_version"),
        "supersedes": metadata.get("supersedes"),
        "content": event.get_text(),
        "updated_at": NORMALIZED_TIMESTAMP,
    }


async def _write_summary(
    backend: ReplayBackend,
    session: Any,
    case_id: str,
    operation: dict[str, Any],
    summary_history: list[dict[str, Any]],
) -> None:
    version = len(summary_history) + 1
    keep_recent = max(0, int(operation.get("keep_recent", 0)))
    recent_events = session.events[-keep_recent:] if keep_recent else []
    summarized_events = session.events[:-keep_recent] if keep_recent else list(session.events)
    for event in summarized_events:
        event.set_model_visible(False)
    session.historical_events.extend(summarized_events)

    summary_id = f"{case_id}-summary-v{version}"
    previous_id = summary_history[-1]["summary_id"] if summary_history else None
    summary_timestamp = recent_events[0].timestamp - 0.000001 if recent_events else time.time()
    summary_event = Event(
        id=summary_id,
        invocation_id=f"{case_id}-summary",
        author="system",
        content=Content(role="user", parts=[Part.from_text(text=operation["text"])]),
        custom_metadata={
            "session_id": session.id,
            "summary_version": version,
            "supersedes": previous_id,
        },
        timestamp=summary_timestamp,
    )
    summary_event.set_summary_event(True)
    session.events = [summary_event, *recent_events]
    await backend.session_service.update_session(session)
    summary_history.append(_summary_record(summary_event))


def _normal_event(event: Event) -> dict[str, Any]:
    content = event.content.model_dump(mode="json", exclude_none=True) if event.content else None
    return {
        "id": event.id,
        "author": event.author,
        "invocation_id": event.invocation_id,
        "content": content,
        "state_delta": dict(event.actions.state_delta or {}),
        "model_visible": event.is_model_visible(),
        "is_summary": event.is_summary_event(),
        "custom_metadata": copy.deepcopy(event.custom_metadata),
        "timestamp": NORMALIZED_TIMESTAMP,
    }


def _normal_memories(response: Any) -> list[dict[str, Any]]:
    memories = [{
        "author": memory.author,
        "content": memory.content.model_dump(mode="json", exclude_none=True),
        "timestamp": NORMALIZED_TIMESTAMP,
    } for memory in response.memories]
    return sorted(memories, key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=True))


async def replay_case(backend: ReplayBackend, case: ReplayCase) -> dict[str, Any]:
    """Replay ``case`` and return a deterministic, backend-neutral snapshot."""
    app_name = f"replay-consistency-{case.case_id}"
    user_id = "fixture-user"
    session_id = f"replay-{case.case_id}"
    await backend.session_service.delete_session(app_name=app_name, user_id=user_id, session_id=session_id)
    session = await backend.session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    summary_history: list[dict[str, Any]] = []
    memory_reads: dict[str, list[dict[str, Any]]] = {}

    async def reload_session() -> Any:
        stored_session = await backend.session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        if stored_session is None:
            raise AssertionError(f"Backend {backend.name} lost session {session_id}")
        return stored_session

    for index, operation in enumerate(case.operations):
        operation_type = operation["type"]
        if operation_type in {"event", "function_call", "function_response", "state"}:
            event = _event_from_operation(case.case_id, index, operation)
            session = await reload_session()
            if not any(existing.id == event.id for existing in session.events):
                await backend.session_service.append_event(session, event)
        elif operation_type == "retry_event":
            retried = _event_from_operation(case.case_id, index, {**operation, "type": "event"})
            session = await reload_session()
            if not any(existing.id == retried.id for existing in session.events):
                await backend.session_service.append_event(session, retried)
        elif operation_type == "fail_before_commit":
            continue
        elif operation_type == "memory_store":
            session = await reload_session()
            await backend.memory_service.store_session(session)
        elif operation_type == "memory_search":
            response = await backend.memory_service.search_memory(
                session.save_key,
                operation["query"],
                limit=operation.get("limit", 10),
            )
            memory_reads[operation.get("label", operation["query"])] = _normal_memories(response)
        elif operation_type == "summary":
            session = await reload_session()
            await _write_summary(backend, session, case.case_id, operation, summary_history)
        else:
            raise ValueError(f"Unsupported replay operation: {operation_type}")

    stored = await backend.session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    if stored is None:
        raise AssertionError(f"Backend {backend.name} lost session {session_id}")
    leading_summary = stored.events[0] if stored.events and stored.events[0].is_summary_event() else None
    return {
        "session_id": stored.id,
        "events": [_normal_event(event) for event in stored.events],
        "historical_events": [_normal_event(event) for event in stored.historical_events],
        "state": copy.deepcopy(stored.state),
        "memory": memory_reads,
        "summary": {
            "current": _summary_record(leading_summary) if leading_summary else None,
            "history": summary_history,
        },
    }


def _allowed_reason(path: str, allowed_diff: Iterable[dict[str, str]]) -> str | None:
    canonical_path = re.sub(r"\[\d+\]", "[*]", path)
    for rule in allowed_diff:
        candidate_path = canonical_path.replace("[*]", "__INDEX__")
        rule_path = rule["path"].replace("[*]", "__INDEX__")
        if fnmatch.fnmatchcase(candidate_path, rule_path):
            return rule["reason"]
    return None


def compare_snapshots(
    reference: dict[str, Any],
    candidate: dict[str, Any],
    *,
    reference_backend: str,
    candidate_backend: str,
    allowed_diff: Iterable[dict[str, str]] = (),
) -> list[dict[str, Any]]:
    """Return leaf-level differences with replay location and backend values."""
    differences: list[dict[str, Any]] = []
    missing = object()

    def visit(left: Any, right: Any, path: str) -> None:
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right)):
                visit(left.get(key, missing), right.get(key, missing), f"{path}.{key}" if path else key)
            return
        if isinstance(left, list) and isinstance(right, list):
            for index in range(max(len(left), len(right))):
                left_value = left[index] if index < len(left) else missing
                right_value = right[index] if index < len(right) else missing
                visit(left_value, right_value, f"{path}[{index}]")
            return
        if left == right:
            return

        reason = _allowed_reason(path, allowed_diff)
        event_index = None
        if path.startswith("events["):
            event_index = int(path.split("[", 1)[1].split("]", 1)[0])
        summary = reference.get("summary", {}).get("current") or candidate.get("summary", {}).get("current") or {}
        differences.append({
            "session_id": reference.get("session_id", candidate.get("session_id")),
            "event_index": event_index,
            "summary_id": summary.get("summary_id"),
            "path": path,
            "reference_backend": reference_backend,
            "candidate_backend": candidate_backend,
            "reference_value": "<missing>" if left is missing else left,
            "candidate_value": "<missing>" if right is missing else right,
            "allowed": reason is not None,
            "reason": reason,
        })

    visit(reference, candidate, "")
    return differences


def build_diff_report(
    snapshots: dict[str, dict[str, dict[str, Any]]],
    cases: Iterable[ReplayCase],
) -> dict[str, Any]:
    """Compare every backend with the first backend and build a JSON report."""
    case_by_id = {case.case_id: case for case in cases}
    backend_names = list(snapshots)
    reference_name = backend_names[0]
    report_cases = []
    for case_id in case_by_id:
        differences = []
        for backend_name in backend_names[1:]:
            differences.extend(
                compare_snapshots(
                    snapshots[reference_name][case_id],
                    snapshots[backend_name][case_id],
                    reference_backend=reference_name,
                    candidate_backend=backend_name,
                    allowed_diff=case_by_id[case_id].allowed_diff,
                ))
        report_cases.append({
            "case_id": case_id,
            "session_id": snapshots[reference_name][case_id]["session_id"],
            "allowed_diff": case_by_id[case_id].allowed_diff,
            "differences": differences,
            "status": "match" if not [item for item in differences if not item["allowed"]] else "mismatch",
        })
    return {
        "schema_version": 1,
        "reference_backend": reference_name,
        "compared_backends": backend_names[1:],
        "cases": report_cases,
    }
