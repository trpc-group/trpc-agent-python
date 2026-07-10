# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Comparison functions for replay consistency tests."""

from __future__ import annotations

from typing import Any

from .constants import ALLOWED_DIFFS


def diff_snapshots(expected: Any, actual: Any, field_path: str = "") -> list[dict[str, Any]]:
    """Compare two snapshots and return differences."""
    if type(expected) is not type(actual):
        return [make_diff(field_path, expected, actual)]

    if isinstance(expected, dict):
        return diff_dicts(expected, actual, field_path)

    if isinstance(expected, list):
        return diff_lists(expected, actual, field_path)

    if expected != actual:
        return [make_diff(field_path, expected, actual)]
    return []


def diff_dicts(expected: dict[str, Any], actual: dict[str, Any], field_path: str) -> list[dict[str, Any]]:
    """Compare two dicts and return differences."""
    diffs = []
    keys = sorted(set(expected) | set(actual))
    for key in keys:
        child_path = join_path(field_path, key)
        if key not in expected:
            diffs.append(make_diff(child_path, "__missing__", actual[key]))
        elif key not in actual:
            diffs.append(make_diff(child_path, expected[key], "__missing__"))
        else:
            diffs.extend(diff_snapshots(expected[key], actual[key], child_path))
    return diffs


def diff_lists(expected: list[Any], actual: list[Any], field_path: str) -> list[dict[str, Any]]:
    """Compare two lists and return differences."""
    diffs = []
    max_len = max(len(expected), len(actual))
    for index in range(max_len):
        child_path = f"{field_path}[{index}]" if field_path else f"[{index}]"
        if index >= len(expected):
            diffs.append(make_diff(child_path, "__missing__", actual[index]))
        elif index >= len(actual):
            diffs.append(make_diff(child_path, expected[index], "__missing__"))
        else:
            diffs.extend(diff_snapshots(expected[index], actual[index], child_path))
    return diffs


def join_path(parent: str, child: str) -> str:
    """Join two path components."""
    return f"{parent}.{child}" if parent else child


def make_diff(field_path: str, expected: Any, actual: Any) -> dict[str, Any]:
    """Create a diff entry."""
    return {
        "field_path": field_path,
        "expected": expected,
        "actual": actual,
    }


def allowed_diff_reason(case_name: str, backend_expected: str, backend_actual: str, field_path: str) -> str | None:
    """Check if a diff is allowed and return the reason."""
    for rule in ALLOWED_DIFFS:
        if (
            rule["case_name"] == case_name
            and rule["backend_expected"] == backend_expected
            and rule["backend_actual"] == backend_actual
            and field_path in rule["field_paths"]
        ):
            return rule["reason"]
    return None


def extract_event_location(field_path: str) -> tuple[str | None, int | None]:
    """Extract event collection and index from a field path."""
    for collection in ("events", "historical_events"):
        prefix = f"{collection}["
        if field_path.startswith(prefix):
            suffix = field_path[len(prefix):]
            index_text = suffix.split("]", maxsplit=1)[0]
            if index_text.isdigit():
                return collection, int(index_text)
    return None, None


def extract_summary_id(
    diff: dict[str, Any],
    expected_snapshot: dict[str, Any],
    actual_snapshot: dict[str, Any],
) -> str | None:
    """Extract summary ID from a diff."""
    for value in (diff["expected"], diff["actual"]):
        summary_id = find_summary_id(value)
        if summary_id:
            return summary_id

    event_collection, event_index = extract_event_location(diff["field_path"])
    if event_collection is None or event_index is None:
        return None

    for snapshot in (expected_snapshot, actual_snapshot):
        summary_id = find_summary_id(event_at(snapshot, event_collection, event_index))
        if summary_id:
            return summary_id
    return None


def event_at(snapshot: dict[str, Any], event_collection: str, event_index: int) -> dict[str, Any] | None:
    """Get an event at a specific index from a snapshot."""
    events = snapshot.get(event_collection, [])
    if not isinstance(events, list) or event_index >= len(events):
        return None
    event = events[event_index]
    return event if isinstance(event, dict) else None


def find_summary_id(value: Any) -> str | None:
    """Recursively find a summary ID in a value."""
    if isinstance(value, dict):
        summary_id = value.get("summary_id")
        if isinstance(summary_id, str):
            return summary_id
        custom_metadata = value.get("custom_metadata")
        if isinstance(custom_metadata, dict):
            summary_id = custom_metadata.get("summary_id")
            if isinstance(summary_id, str):
                return summary_id
        for child in value.values():
            summary_id = find_summary_id(child)
            if summary_id:
                return summary_id
    if isinstance(value, list):
        for item in value:
            summary_id = find_summary_id(item)
            if summary_id:
                return summary_id
    return None


def is_event_diff(diff: dict[str, Any]) -> bool:
    """Check if a diff is an event diff."""
    return diff["event_collection"] is not None


def is_state_diff(diff: dict[str, Any]) -> bool:
    """Check if a diff is a state diff."""
    return diff["field_path"] == "state" or diff["field_path"].startswith("state.")


def is_summary_diff(diff: dict[str, Any]) -> bool:
    """Check if a diff is a summary diff."""
    return diff["summary_id"] is not None


def is_session_metadata_diff(diff: dict[str, Any]) -> bool:
    """Check if a diff is a session metadata diff."""
    return not is_event_diff(diff) and not is_state_diff(diff) and not is_summary_diff(diff)
