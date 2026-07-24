"""Snapshot normalization for replay consistency tests."""

from __future__ import annotations

from dataclasses import dataclass
import re
import uuid
from typing import Any

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import MemoryEntry
from trpc_agent_sdk.types import State

from .cases import MemoryQuerySpec
from .cases import ReplayCase


@dataclass
class Snapshot:
    backend: str
    case_name: str
    session_id: str
    app_name: str
    user_id: str
    state: dict[str, Any]
    events: list[dict[str, Any]]
    historical_events: list[dict[str, Any]]
    memories: list[dict[str, Any]]
    summary: dict[str, Any] | None
    list_sessions: list[dict[str, Any]]


def canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [canonicalize(item) for item in value]
    if hasattr(value, "model_dump"):
        return canonicalize(value.model_dump(exclude_none=True, mode="json"))
    return value


def normalize_summary_text(text: str | None) -> str | None:
    if text is None:
        return None
    return re.sub(r"\s+", " ", text).strip()


def _strip_temp_state(state: dict[str, Any] | None) -> dict[str, Any]:
    if not state:
        return {}
    return canonicalize({key: value for key, value in state.items() if not key.startswith(State.TEMP_PREFIX)})


def _content_text(content: Content | None) -> str | None:
    if not content or not content.parts:
        return None
    text = "".join(part.text for part in content.parts if part.text)
    return text or None


def _is_uuid_like(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def _normalize_event_id(event: Event) -> str:
    if event.is_summary_event() or not event.id or _is_uuid_like(event.id):
        return "normalized"
    return event.id


def normalize_event(event: Event, index: int) -> dict[str, Any]:
    function_calls = [
        {
            "id": call.id,
            "name": call.name,
            "args": canonicalize(call.args or {}),
        }
        for call in event.get_function_calls()
    ]
    function_responses = [
        {
            "id": response.id,
            "name": response.name,
            "response": canonicalize(response.response or {}),
        }
        for response in event.get_function_responses()
    ]
    role = event.content.role if event.content else None
    state_delta = _strip_temp_state(event.actions.state_delta if event.actions else None)

    return {
        "stable_index": index,
        "event_id": _normalize_event_id(event),
        "invocation_id": event.invocation_id,
        "author": event.author,
        "role": role,
        "text": normalize_summary_text(event.get_text()) if event.get_text() else None,
        "function_calls": function_calls,
        "function_responses": function_responses,
        "state_delta": state_delta,
        "branch": event.branch,
        "tag": event.tag,
        "filter_key": event.filter_key,
        "partial": bool(event.partial),
        "turn_complete": bool(event.turn_complete),
        "error_code": event.error_code,
        "error_message": event.error_message,
        "model_visible": event.is_model_visible(),
        "is_summary_event": event.is_summary_event(),
    }


def normalize_memory_entry(query_spec: MemoryQuerySpec, key: str, memory: MemoryEntry) -> dict[str, Any]:
    return {
        "query": query_spec.query,
        "key": key,
        "author": memory.author,
        "text": normalize_summary_text(_content_text(memory.content)),
        "has_timestamp": bool(memory.timestamp),
    }


def normalize_memory_results(memory_records: list[tuple[MemoryQuerySpec, str, list[MemoryEntry]]]) -> list[dict[str,
                                                                                                                Any]]:
    memories: list[dict[str, Any]] = []
    for query_spec, key, entries in memory_records:
        memories.extend(normalize_memory_entry(query_spec, key, entry) for entry in entries)
    # Memory search ranking is backend-specific here; content/scope are strict, order is canonicalized.
    return sorted(
        memories,
        key=lambda item: (
            item.get("query") or "",
            item.get("author") or "",
            item.get("text") or "",
            item.get("key") or "",
        ),
    )


def _normalize_list_session(session: Session) -> dict[str, Any]:
    return {
        "id": session.id,
        "app_name": session.app_name,
        "user_id": session.user_id,
        "state": _strip_temp_state(session.state),
    }


async def _normalize_summary(session_service: Any, session: Session) -> dict[str, Any] | None:
    summary_text = await session_service.get_session_summary(session)
    if summary_text is None:
        return None

    summary_events = [event for event in session.events if event.is_summary_event()]
    summary_event_text = summary_events[-1].get_text() if summary_events else None

    metadata: dict[str, Any] = {
        "session_id": session.id,
        "has_summary": True,
        "summary_event_count": len(summary_events),
        "summary_event_text": normalize_summary_text(summary_event_text),
        "compressed_event_count": len(session.events),
        "historical_event_count": len(session.historical_events),
    }

    manager = getattr(session_service, "summarizer_manager", None)
    if manager is not None:
        manager_summary = await manager.get_session_summary(session)
        if manager_summary is not None:
            metadata.update(
                {
                    "manager_session_id": manager_summary.session_id,
                    "original_event_count": manager_summary.original_event_count,
                    "manager_compressed_event_count": manager_summary.compressed_event_count,
                    "has_summary_timestamp": bool(manager_summary.summary_timestamp),
                }
            )

    return {
        "text": normalize_summary_text(summary_text),
        "metadata": canonicalize(metadata),
    }


async def normalize_snapshot(
    *,
    backend: str,
    case: ReplayCase,
    session: Session,
    session_service: Any,
    memory_records: list[tuple[MemoryQuerySpec, str, list[MemoryEntry]]],
) -> Snapshot:
    list_response = await session_service.list_sessions(app_name=case.app_name, user_id=case.user_id)
    list_sessions = sorted(
        (_normalize_list_session(listed_session) for listed_session in list_response.sessions),
        key=lambda item: item["id"],
    )

    return Snapshot(
        backend=backend,
        case_name=case.name,
        session_id=session.id,
        app_name=session.app_name,
        user_id=session.user_id,
        state=_strip_temp_state(session.state),
        events=[normalize_event(event, index) for index, event in enumerate(session.events)],
        historical_events=[normalize_event(event, index) for index, event in enumerate(session.historical_events)],
        memories=normalize_memory_results(memory_records),
        summary=await _normalize_summary(session_service, session),
        list_sessions=list_sessions,
    )
