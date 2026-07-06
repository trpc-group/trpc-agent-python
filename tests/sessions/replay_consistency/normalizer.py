# Tencent is pleased to support the open source community by making
# tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Normalizer for replay consistency testing.

Strips auto-generated fields from events and snapshots so that
cross-backend comparisons are deterministic and meaningful.
"""

from __future__ import annotations

from typing import Any

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions._session import Session


def normalize_event(event: Event) -> dict[str, Any]:
    """Strip auto-generated fields from an event for comparison.

    Args:
        event: The raw Event from a session service.

    Returns:
        A dict with only the business-relevant fields: author, text, and
        optionally state_delta.
    """
    norm: dict[str, Any] = {
        "author": event.author,
    }

    # Extract text from content parts.
    if event.content and event.content.parts:
        texts: list[str] = []
        for part in event.content.parts:
            if hasattr(part, "text") and part.text:
                texts.append(part.text)
        norm["text"] = " ".join(texts)
    else:
        norm["text"] = ""

    # Include state_delta if present.
    if event.actions and event.actions.state_delta:
        norm["state_delta"] = {
            k: v for k, v in event.actions.state_delta.items()
        }

    return norm


def normalize_snapshot(
    session: Session,
    memories: list[dict[str, Any]],
) -> dict[str, Any]:
    """Produce a normalized snapshot for cross-backend comparison.

    Args:
        session: The Session object after replay.
        memories: List of memory dicts with at least "content" key.

    Returns:
        A dict with keys: session_id, state, events, memories, summaries.
    """
    events_norm = [normalize_event(e) for e in session.events]

    return {
        "session_id": session.id,
        "state": dict(session.state) if session.state else {},
        "events": events_norm,
        "memories": sorted(memories, key=lambda m: m.get("content", "")),
        "summaries": (
            dict(session.summaries)
            if hasattr(session, "summaries") and session.summaries
            else {}
        ),
    }
