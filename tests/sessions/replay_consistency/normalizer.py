# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Snapshot normalizer for cross-backend replay consistency comparison.

Replaces volatile fields (timestamps, auto-generated IDs) with stable
placeholders, strips ephemeral temp: state keys, and canonicalizes
serialization order so that semantically identical snapshots from
different backends compare as equal.
"""

from __future__ import annotations

import copy
import json
from typing import Any

NORMALIZED = "<normalized>"
"""Sentinel value for replaced volatile fields."""

# Fields whose values are replaced with NORMALIZED (key preserved).
_NORMALIZED_KEYS = frozenset({
    "timestamp",
    "id",
    "invocation_id",
    "last_update_time",
    "update_time",
    "expired_at",
    "summary_timestamp",
    "created_at",
    "updated_at",
    "response_id",
})

# Fields that represent backend metadata and should be normalized to empty.
_EMPTYABLE_KEYS = frozenset({
    "long_running_tool_ids",
    "custom_metadata",
    "grounding_metadata",
    "usage_metadata",
    "object",
    "turn_complete",
    "interrupted",
})

# Keys that reference IDs and should be normalized if they look auto-generated.
_NORMALIZED_ID_KEYS = frozenset({
    "session_id",
    "save_key",
})

# State key prefixes that are stripped from comparison.
_TEMP_PREFIX = "temp:"


def normalize_snapshot(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a backend snapshot for cross-backend comparison.

    Applies the following transformations:
    1. Replace volatile fields with NORMALIZED sentinel
    2. Strip temp:* state keys
    3. Sort memory results by deterministic content keys
    4. Canonicalize JSON serialization order

    Args:
        raw: The raw snapshot dict from a single backend.

    Returns:
        A deep-copied, normalized snapshot dict.
    """
    snapshot = copy.deepcopy(raw)
    _normalize_dict(snapshot)
    _strip_temp_state(snapshot)
    _sort_memories(snapshot)
    return snapshot


def _normalize_dict(obj: Any) -> Any:
    """Recursively replace volatile field values in dicts and lists."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in _NORMALIZED_KEYS:
                obj[key] = NORMALIZED
            elif key in _EMPTYABLE_KEYS:
                # Normalize None/empty containers to a consistent sentinel
                if value is None or value == [] or value == {}:
                    obj[key] = NORMALIZED
            elif key in _NORMALIZED_ID_KEYS:
                if isinstance(value, str) and _looks_auto_generated(value):
                    obj[key] = NORMALIZED
            else:
                _normalize_dict(value)
    elif isinstance(obj, list):
        for item in obj:
            _normalize_dict(item)
    return obj


def _looks_auto_generated(value: str) -> bool:
    """Heuristic to detect auto-generated identifiers.

    UUID-like patterns, long hex strings, and base64-looking tokens
    are considered auto-generated.

    Args:
        value: The identifier string to check.

    Returns:
        True if the value appears to be auto-generated.
    """
    if len(value) < 8:
        return False
    # UUID pattern: 8-4-4-4-12
    if "-" in value and all(len(part) in (4, 8, 12) for part in value.split("-")):
        return True
    # Long hex strings (32+ chars, typical for SHA hashes)
    if len(value) >= 32 and all(c in "0123456789abcdef" for c in value.lower()):
        return True
    return False


def _strip_temp_state(snapshot: dict[str, Any]) -> None:
    """Remove temp:* prefixed keys from state dict.

    Modifies the snapshot in place.

    Args:
        snapshot: The snapshot dict to clean.
    """
    state = snapshot.get("state")
    if isinstance(state, dict):
        keys_to_remove = [k for k in state if k.startswith(_TEMP_PREFIX)]
        for k in keys_to_remove:
            del state[k]

    # Also strip from individual events' state_delta
    for events_key in ("events", "historical_events"):
        events = snapshot.get(events_key)
        if isinstance(events, list):
            for event in events:
                if isinstance(event, dict):
                    _strip_temp_from_event(event)


def _strip_temp_from_event(event: dict[str, Any]) -> None:
    """Strip temp:* keys from an event's state_delta and actions."""
    # Strip from state_delta (top-level on event)
    state_delta = event.get("state_delta")
    if isinstance(state_delta, dict):
        keys_to_remove = [k for k in state_delta if k.startswith(_TEMP_PREFIX)]
        for k in keys_to_remove:
            del state_delta[k]

    # Strip from actions.state_delta (nested in actions)
    actions = event.get("actions")
    if isinstance(actions, dict):
        actions_sd = actions.get("state_delta")
        if isinstance(actions_sd, dict):
            keys_to_remove = [k for k in actions_sd if k.startswith(_TEMP_PREFIX)]
            for k in keys_to_remove:
                del actions_sd[k]


def _sort_memories(snapshot: dict[str, Any]) -> None:
    """Sort memory entries by deterministic content keys.

    Since different backends may return memory search results in
    different orders, we sort by a deterministic key derived from
    the entry content.

    Args:
        snapshot: The snapshot dict to sort memories in.
    """
    memories = snapshot.get("memories")
    if not isinstance(memories, list) or len(memories) <= 1:
        return

    def _sort_key(entry: dict[str, Any]) -> str:
        """Build a stable sort key from memory entry content."""
        parts: list[str] = []
        text = entry.get("text") or entry.get("content") or ""
        if isinstance(text, str):
            parts.append(text[:200])
        author = entry.get("author", "")
        if isinstance(author, str):
            parts.append(author)
        return json.dumps(parts, sort_keys=True, ensure_ascii=False)

    try:
        memories.sort(key=_sort_key)
    except (TypeError, KeyError):
        # If sorting fails, keep original order
        pass
