# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Normalization functions for replay consistency tests."""

from __future__ import annotations

import json
from typing import Any

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.events._event import _EVENT_FLAG_SUMMARY
from trpc_agent_sdk.sessions._session import Session
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import EventActions
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import MemoryEntry
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.types import SearchMemoryResponse


def make_event(record: dict[str, Any], base_timestamp: float) -> Event:
    """Create an Event from a replay record."""
    model_flags = 0
    if record.get("is_summary"):
        model_flags |= _EVENT_FLAG_SUMMARY
    return Event(
        id=record["id"],
        invocation_id=record["invocation_id"],
        author=record["author"],
        content=Content(parts=[make_part(part) for part in record.get("parts", [])]),
        actions=EventActions(**record.get("actions", {})),
        branch=record.get("branch"),
        timestamp=base_timestamp + (record.get("timestamp_offset_ms", 0) / 1000.0),
        partial=record.get("partial", False),
        version=record.get("version", 0),
        custom_metadata=record.get("custom_metadata"),
        model_flags=model_flags,
    )


def make_part(part_record: dict[str, Any]) -> Part:
    """Create a Part from a replay part record."""
    if "text" in part_record:
        return Part.from_text(text=part_record["text"])
    if "function_call" in part_record:
        return Part(function_call=FunctionCall.model_validate(part_record["function_call"]))
    if "function_response" in part_record:
        return Part(function_response=FunctionResponse.model_validate(part_record["function_response"]))
    raise ValueError(f"Unsupported replay part: {part_record}")


async def normalize_session(session: Session, service: Any) -> dict[str, Any]:
    """Normalize a session for comparison."""
    return {
        "session_id": session.id,
        "app_name": session.app_name,
        "user_id": session.user_id,
        "state": session.state,
        "conversation_count": session.conversation_count,
        "events": [normalize_event(event) for event in session.events],
        "historical_events": [normalize_event(event) for event in session.historical_events],
        "summary": await normalize_session_summary(session, service),
    }


async def normalize_session_summary(session: Session, service: Any) -> dict[str, Any] | None:
    """Normalize a session summary for comparison."""
    summary_text = await service.get_session_summary(session)
    if summary_text is None:
        return None

    summary_events_list = [event for event in session.events if event.is_summary_event()]
    summary_event_text = summary_events_list[-1].get_text() if summary_events_list else None
    metadata = {
        "session_id": session.id,
        "has_summary": True,
        "summary_event_count": len(summary_events_list),
        "summary_event_text": compact_text(summary_event_text),
        "compressed_event_count": len(session.events),
        "historical_event_count": len(session.historical_events),
    }

    manager = service.summarizer_manager
    if manager is not None:
        manager_summary = await manager.get_session_summary(session)
        if manager_summary is not None:
            metadata.update({
                "manager_session_id": manager_summary.session_id,
                "original_event_count": manager_summary.original_event_count,
                "manager_compressed_event_count": manager_summary.compressed_event_count,
                "has_summary_timestamp": bool(manager_summary.summary_timestamp),
            })

    return {
        "text": compact_text(summary_text),
        "metadata": canonicalize(metadata),
    }


def normalize_event(event: Event) -> dict[str, Any]:
    """Normalize an event for comparison."""
    return {
        "invocation_id": event.invocation_id,
        "author": event.author,
        "branch": event.branch,
        "partial": event.partial,
        "model_flags": event.model_flags,
        "version": event.version,
        "is_summary": event.is_summary_event(),
        "actions": event.actions.model_dump(exclude_none=True, mode="json"),
        "custom_metadata": event.custom_metadata,
        "parts": normalize_content_parts(event),
    }


def normalize_content_parts(event: Event) -> list[dict[str, Any]]:
    """Normalize content parts from an event."""
    if not event.content or not event.content.parts:
        return []
    return [normalize_part(part) for part in event.content.parts]


def normalize_part(part: Part) -> dict[str, Any]:
    """Normalize a part for comparison."""
    normalized_part = {}
    if part.text is not None:
        normalized_part["text"] = part.text
    if part.function_call is not None:
        normalized_part["function_call"] = part.function_call.model_dump(exclude_none=True, mode="json")
    if part.function_response is not None:
        normalized_part["function_response"] = part.function_response.model_dump(exclude_none=True, mode="json")
    return normalized_part


def normalize_memory_response(response: SearchMemoryResponse) -> list[dict[str, Any]]:
    """Normalize a memory search response for comparison."""
    memories = [normalize_memory_entry(memory) for memory in response.memories]
    return sorted(memories, key=lambda memory: (memory["author"] or "", json.dumps(memory["parts"], sort_keys=True)))


def normalize_memory_entry(memory: MemoryEntry) -> dict[str, Any]:
    """Normalize a memory entry for comparison."""
    return {
        "author": memory.author,
        "parts": [normalize_part(part) for part in memory.content.parts or []],
    }


def summary_events(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract summary events from a snapshot."""
    return [event for event in all_normalized_events(snapshot) if event["is_summary"]]


def event_texts(snapshot: dict[str, Any]) -> list[str]:
    """Extract event texts from a snapshot."""
    return [part["text"] for event in all_normalized_events(snapshot) for part in event["parts"] if "text" in part]


def all_normalized_events(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Get all normalized events from a snapshot."""
    return snapshot["historical_events"] + snapshot["events"]


def compact_text(text: str | None) -> str | None:
    """Compact whitespace in text."""
    if text is None:
        return None
    return " ".join(text.split())


def normalize_summary_text(text: str | None) -> str | None:
    """Normalize summary text for comparison."""
    if text is None:
        return None
    return compact_text(text).casefold()


def canonicalize(value: Any) -> Any:
    """Canonicalize a value for comparison (sort dicts, etc.)."""
    if isinstance(value, dict):
        return {key: canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [canonicalize(item) for item in value]
    return value


def summary_metadata(event: dict[str, Any]) -> dict[str, Any]:
    """Extract summary metadata from an event."""
    custom_metadata = event.get("custom_metadata") or {}
    return {
        "summary_id": custom_metadata.get("summary_id"),
        "session_id": custom_metadata.get("session_id"),
        "event_version": event.get("version"),
        "summary_version": custom_metadata.get("summary_version"),
        "supersedes": custom_metadata.get("supersedes"),
        "source_event_ids": custom_metadata.get("source_event_ids"),
        "compressed_event_ids": custom_metadata.get("compressed_event_ids"),
        "updated_at_offset_ms": custom_metadata.get("updated_at_offset_ms"),
    }


def summary_records_by_id(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Get summary records from events by ID."""
    records = {}
    for index, event in enumerate(all_normalized_events(snapshot)):
        if not event.get("is_summary"):
            continue
        metadata = summary_metadata(event)
        summary_id = metadata.get("summary_id") or f"summary_index:{index}"
        records[summary_id] = {
            "text": summary_text(event),
            "metadata": metadata,
        }
    return records


def summary_text(event: dict[str, Any]) -> str:
    """Extract text from a summary event."""
    return "".join(part["text"] for part in event.get("parts", []) if "text" in part)
