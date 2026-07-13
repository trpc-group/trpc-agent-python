# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Snapshot readers for replay consistency comparisons."""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from typing import Any

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions import Session


@dataclass
class MemoryProbe:
    """A memory query to include in a snapshot."""

    probe_id: str
    session_key: str
    query: str


@dataclass
class SummaryRecord:
    """Derived summary metadata observed by the replay adapter."""

    client_summary_id: str
    session_id: str
    user_id: str
    app_name: str
    event_id: str
    text: str
    version: int
    active: bool
    covered_event_ids: list[str]
    timestamp: float


@dataclass
class Snapshot:
    """Stable snapshot shape consumed by canonicalization and diffing."""

    case_id: str
    backend: str
    sessions: list[dict[str, Any]]
    memory: list[dict[str, Any]]
    summaries: list[dict[str, Any]]
    backend_metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def read_snapshot(
    *,
    backend: str,
    case_id: str,
    session_service,
    memory_service,
    sessions: list[Session],
    actual_to_client_event_id: dict[str, str],
    memory_probes: list[MemoryProbe],
    summary_records: list[SummaryRecord],
) -> Snapshot:
    """Read a backend snapshot using the public service APIs."""

    session_snapshots = []
    for original in sessions:
        session = await session_service.get_session(
            app_name=original.app_name,
            user_id=original.user_id,
            session_id=original.id,
        )
        if session is None:
            continue
        session_snapshots.append(_session_to_snapshot(session, actual_to_client_event_id))

    memory_snapshots = []
    for probe in memory_probes:
        result = await memory_service.search_memory(probe.session_key, probe.query, limit=10)
        memory_snapshots.append(
            {
                "probe_id": probe.probe_id,
                "session_key": probe.session_key,
                "query": probe.query,
                "memories": [
                    {
                        "author": entry.author,
                        "timestamp": entry.timestamp,
                        "content": _content_to_snapshot(entry.content),
                    }
                    for entry in result.memories
                ],
            }
        )

    return Snapshot(
        case_id=case_id,
        backend=backend,
        sessions=sorted(session_snapshots, key=lambda item: (item["app_name"], item["user_id"], item["session_id"])),
        memory=sorted(memory_snapshots, key=lambda item: item["probe_id"]),
        summaries=[asdict(record) for record in sorted(summary_records, key=lambda item: item.client_summary_id)],
        backend_metadata={"backend": backend},
    )


def _session_to_snapshot(session: Session, actual_to_client_event_id: dict[str, str]) -> dict[str, Any]:
    return {
        "app_name": session.app_name,
        "user_id": session.user_id,
        "session_id": session.id,
        "state": session.state,
        "events": [
            _event_to_snapshot(event, idx, actual_to_client_event_id) for idx, event in enumerate(session.events)
        ],
        "historical_events": [
            _event_to_snapshot(event, idx, actual_to_client_event_id)
            for idx, event in enumerate(session.historical_events)
        ],
        "conversation_count": session.conversation_count,
    }


def _event_to_snapshot(event: Event, index: int, actual_to_client_event_id: dict[str, str]) -> dict[str, Any]:
    return {
        "index": index,
        "event_id": actual_to_client_event_id.get(event.id, event.id),
        "actual_event_id": event.id,
        "invocation_id": event.invocation_id,
        "author": event.author,
        "timestamp": event.timestamp,
        "is_summary": event.is_summary_event(),
        "is_model_visible": event.is_model_visible(),
        "content": _content_to_snapshot(event.content),
        "actions": event.actions.model_dump(mode="json", exclude_none=True),
        "function_calls": [_function_like_to_dict(call) for call in event.get_function_calls()],
        "function_responses": [_function_like_to_dict(response) for response in event.get_function_responses()],
        "error_code": event.error_code,
        "error_message": event.error_message,
        "version": event.version,
    }


def _content_to_snapshot(content) -> dict[str, Any] | None:
    if content is None:
        return None
    parts = []
    for part in content.parts or []:
        item: dict[str, Any] = {}
        if part.text is not None:
            item["text"] = part.text
        if part.function_call is not None:
            item["function_call"] = _function_like_to_dict(part.function_call)
        if part.function_response is not None:
            item["function_response"] = _function_like_to_dict(part.function_response)
        parts.append(item)
    return {"role": content.role, "parts": parts}


def _function_like_to_dict(value) -> dict[str, Any]:
    return value.model_dump(mode="json", exclude_none=True, by_alias=False)
