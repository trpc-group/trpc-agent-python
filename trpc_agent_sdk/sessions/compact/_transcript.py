# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Convert tRPC Events into recoverable transcript records."""

from __future__ import annotations

from typing import Any

from trpc_agent_sdk.abc import ResponseABC
from trpc_agent_sdk.abc import SessionABC

TRANSCRIPT_SCHEMA_VERSION = 1


def build_event_transcript_record(
    session: SessionABC,
    event: ResponseABC,
    *,
    parent_event_id: str | None,
) -> dict[str, Any]:
    """Convert a persisted Event into a versioned transcript record."""
    event_id = getattr(event, "id", "")
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("Persisted event must have a non-empty id")
    event_timestamp = getattr(event, "timestamp", None)
    return {
        "schema_version": TRANSCRIPT_SCHEMA_VERSION,
        "kind": "event",
        "event_id": event_id,
        "parent_event_id": parent_event_id,
        "event_timestamp": event_timestamp,
        "session": {
            "id": session.id,
            "app_name": session.app_name,
            "user_id": session.user_id,
        },
        "event": event.model_dump(mode="json", by_alias=True, exclude_none=True),
    }


def find_last_event_id(records: list[dict[str, Any]]) -> str | None:
    """Find the last valid Event record identifier in a transcript."""
    for record in reversed(records):
        if record.get("kind") == "event" and isinstance(record.get("event_id"), str):
            return record["event_id"]
    return None
