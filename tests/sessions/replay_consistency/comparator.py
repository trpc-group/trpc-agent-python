# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Recursive snapshot comparator with structured diff entries.

Compares two normalized snapshots by recursively walking their dict/list
structure, producing DiffEntry objects that precisely locate every
divergence (session_id, event_index, summary_id, field_path, both values).
"""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any

from .allowed_diff import AllowedDiffRule
from .harness import DiffEntry
from .harness import ReplaySnapshot
from .normalizer import NORMALIZED

_MISSING = object()
"""Sentinel for keys/indices present in one snapshot but not the other."""


def _to_dict(value: Any) -> Any:
    """Convert a value to a plain dict for recursive comparison.

    Handles Snapshot, dataclass, and Pydantic model instances.
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, ReplaySnapshot):
        return value.model_dump()
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value


def _infer_section(path: str) -> str:
    """Infer the logical section name from a field path.

    Maps top-level keys and list containers to human-readable section names.
    """
    if not path:
        return "root"
    root = path.split(".", 1)[0].split("[", 1)[0]
    section_map = {
        "events": "events",
        "historical_events": "historical_events",
        "state": "state",
        "memories": "memories",
        "summary": "summary",
        "list_sessions": "list_sessions",
    }
    return section_map.get(root, root)


def _parse_index(path: str, section: str) -> int | None:
    """Extract a numeric index from a section array path.

    Example: "events[2].text" with section="events" → 2
    """
    m = re.search(rf"{re.escape(section)}\[(\d+)\]", path)
    return int(m.group(1)) if m else None


def _summary_id(left: dict[str, Any], right: dict[str, Any], section: str) -> str | None:
    """Derive a human-readable summary identifier from snapshot metadata.

    Uses session_id from the summary metadata if available; falls back
    to the top-level session_id.
    """
    if section != "summary":
        return None
    for snapshot in (left, right):
        summary = snapshot.get("summary")
        if isinstance(summary, dict):
            metadata = summary.get("metadata") or {}
            sid = metadata.get("session_id")
            if sid:
                return f"summary:{sid}:latest"
    sid = left.get("session_id") or right.get("session_id")
    return f"summary:{sid}:latest" if sid else None


def _allowed(path: str, left_value: Any, right_value: Any, rules: tuple[AllowedDiffRule, ...] = ()) -> tuple[bool, str]:
    """Check whether a diff at the given path is an allowed (non-semantic) diff.

    Applies the AllowedDiffRule set; if no rules match, the diff is
    treated as unallowed (strict).
    """
    if rules:
        for rule in rules:
            if rule.matches(path):
                return True, rule.reason

    if path == "backend":
        return True, "backend name differs by design"
    if path.endswith(".timestamp") or path == "timestamp":
        return True, "raw timestamps are backend-generated"
    if path.endswith(".has_timestamp") and left_value is True and right_value is True:
        return True, "timestamp presence is normalized"
    if left_value == NORMALIZED or right_value == NORMALIZED:
        return True, "normalized volatile field"
    if path.startswith("backend"):
        return True, "backend metadata field"
    return False, ""


def _display(value: Any) -> Any:
    """Render a value for display in a DiffEntry, using a sentinel for missing."""
    if value is _MISSING:
        return "<missing>"
    return value


def _entry(
    path: str,
    left_value: Any,
    right_value: Any,
    left: dict[str, Any],
    right: dict[str, Any],
    rules: tuple[AllowedDiffRule, ...] = (),
) -> DiffEntry:
    """Build a single DiffEntry from a path and pair of values."""
    section = _infer_section(path)
    allowed_flag, reason = _allowed(path, left_value, right_value, rules)
    event_index = _parse_index(path, "events")
    if event_index is None:
        event_index = _parse_index(path, "historical_events")
    return DiffEntry(
        case_name=left.get("case_name") or right.get("case_name") or "",
        left_backend=left.get("backend") or "",
        right_backend=right.get("backend") or "",
        session_id=left.get("session_id") or right.get("session_id"),
        event_index=event_index,
        memory_index=_parse_index(path, "memories"),
        summary_id=_summary_id(left, right, section),
        section=section,
        path=path,
        left=_display(left_value),
        right=_display(right_value),
        allowed=allowed_flag,
        reason=reason,
    )


def _join_path(parent: str, key: str) -> str:
    """Join a path segment, handling the root case."""
    return f"{parent}.{key}" if parent else str(key)


def _diff_values(
    current_left: Any,
    current_right: Any,
    root_left: dict[str, Any],
    root_right: dict[str, Any],
    path: str,
    rules: tuple[AllowedDiffRule, ...] = (),
) -> list[DiffEntry]:
    """Recursively compare two values, producing a list of DiffEntry objects.

    Strategy:
    - dict → align by sorted union of keys
    - list → align by positional index
    - leaf → compare by equality

    Args:
        current_left: Left-side value at the current path.
        current_right: Right-side value at the current path.
        root_left: Root-level left snapshot (for context in DiffEntry).
        root_right: Root-level right snapshot (for context in DiffEntry).
        path: Current dot-separated field path.
        rules: AllowedDiffRule set for this comparison.

    Returns:
        A list of DiffEntry objects, empty if the values are identical.
    """
    if isinstance(current_left, dict) and isinstance(current_right, dict):
        diffs: list[DiffEntry] = []
        for key in sorted(set(current_left) | set(current_right)):
            next_left = current_left.get(key, _MISSING)
            next_right = current_right.get(key, _MISSING)
            next_path = _join_path(path, str(key))
            if next_left is _MISSING or next_right is _MISSING:
                diffs.append(_entry(next_path, next_left, next_right, root_left, root_right, rules))
            else:
                diffs.extend(_diff_values(next_left, next_right, root_left, root_right, next_path, rules))
        return diffs

    if isinstance(current_left, list) and isinstance(current_right, list):
        diffs = []
        max_len = max(len(current_left), len(current_right))
        for index in range(max_len):
            next_path = f"{path}[{index}]"
            next_left = current_left[index] if index < len(current_left) else _MISSING
            next_right = current_right[index] if index < len(current_right) else _MISSING
            if next_left is _MISSING or next_right is _MISSING:
                diffs.append(_entry(next_path, next_left, next_right, root_left, root_right, rules))
            else:
                diffs.extend(_diff_values(next_left, next_right, root_left, root_right, next_path, rules))
        return diffs

    if current_left != current_right:
        return [_entry(path, current_left, current_right, root_left, root_right, rules)]
    return []


def recursive_diff(
    left: Any,
    right: Any,
    context: dict[str, Any] | None = None,
    rules: tuple[AllowedDiffRule, ...] = (),
) -> list[DiffEntry]:
    """Compare two snapshots (or dicts) recursively.

    Args:
        left: Left-side snapshot or dict.
        right: Right-side snapshot or dict.
        context: Optional dict with case_name, left_backend, right_backend
            to backfill into DiffEntry fields.
        rules: Optional set of AllowedDiffRule for this comparison.

    Returns:
        A list of DiffEntry objects capturing every divergence.

    Raises:
        TypeError: If either input cannot be converted to a dict.
    """
    left_dict = _to_dict(left)
    right_dict = _to_dict(right)
    if not isinstance(left_dict, dict) or not isinstance(right_dict, dict):
        raise TypeError("recursive_diff expects ReplaySnapshot, dataclass, or dict inputs")

    diffs = _diff_values(left_dict, right_dict, left_dict, right_dict, "", rules)
    if context:
        for diff in diffs:
            if context.get("case_name"):
                diff.case_name = context["case_name"]
            if context.get("left_backend"):
                diff.left_backend = context["left_backend"]
            if context.get("right_backend"):
                diff.right_backend = context["right_backend"]
    return diffs


def compare_snapshot_pair(
    left: Any,
    right: Any,
    rules: tuple[AllowedDiffRule, ...] = (),
) -> list[DiffEntry]:
    """Compare two snapshots and return structured diffs.

    Convenience wrapper around recursive_diff.

    Args:
        left: Left-side normalized snapshot.
        right: Right-side normalized snapshot.
        rules: Optional AllowedDiffRule set.

    Returns:
        A list of DiffEntry objects.
    """
    return recursive_diff(left, right, rules=rules)


def unallowed_diffs(diffs: list[DiffEntry]) -> list[DiffEntry]:
    """Filter to only unallowed (business-relevant) diffs."""
    return [d for d in diffs if not d.allowed]
