# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Field-level normalization for replay snapshots."""

from __future__ import annotations

import copy
import unicodedata
from typing import Any

from .snapshot import Snapshot


def canonicalize_snapshot(snapshot: Snapshot | dict[str, Any]) -> dict[str, Any]:
    """Return a stable snapshot dict suitable for strict comparison."""

    data = snapshot.to_dict() if isinstance(snapshot, Snapshot) else copy.deepcopy(snapshot)
    data.pop("backend", None)
    data.pop("backend_metadata", None)
    for session in data.get("sessions", []):
        _canonicalize_session(session)
    for probe in data.get("memory", []):
        for memory in probe.get("memories", []):
            _canonicalize_content(memory.get("content"))
            memory["timestamp_valid"] = memory.get("timestamp") is not None
            memory.pop("timestamp", None)
        probe["memories"] = sorted(probe.get("memories", []), key=lambda item: repr(item.get("content")))
    for summary in data.get("summaries", []):
        summary["text"] = _normalize_text(summary.get("text", ""))
        summary["timestamp_valid"] = summary.get("timestamp") is not None
        summary.pop("timestamp", None)
    return data


def normalize_content_part(part: dict[str, Any]) -> dict[str, Any]:
    """Normalize a content part while preserving semantic fields."""

    normalized = copy.deepcopy(part)
    if "text" in normalized:
        normalized["text"] = _normalize_text(normalized["text"])
    for key in ("function_call", "function_response"):
        if key in normalized and isinstance(normalized[key], dict):
            normalized[key] = _sort_json(normalized[key])
    return normalized


def _canonicalize_session(session: dict[str, Any]) -> None:
    session["state"] = _sort_json(session.get("state", {}))
    event_timestamps = [event.get("timestamp") for event in session.get("events", [])]
    session["event_timestamps_monotonic"] = _is_monotonic(event_timestamps)
    for collection in ("events", "historical_events"):
        for event in session.get(collection, []):
            event.pop("actual_event_id", None)
            _canonicalize_content(event.get("content"))
            event["timestamp_valid"] = isinstance(event.get("timestamp"), (int, float))
            event.pop("timestamp", None)
            event["actions"] = _sort_json(event.get("actions", {}))
            event["function_calls"] = [_sort_json(item) for item in event.get("function_calls", [])]
            event["function_responses"] = [_sort_json(item) for item in event.get("function_responses", [])]


def _canonicalize_content(content: dict[str, Any] | None) -> None:
    if not content:
        return
    content["parts"] = [normalize_content_part(part) for part in content.get("parts", [])]


def _sort_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sort_json(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_sort_json(item) for item in value]
    if isinstance(value, str):
        return _normalize_text(value)
    return value


def _normalize_text(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _is_monotonic(values: list[Any]) -> bool:
    if not all(isinstance(value, (int, float)) for value in values):
        return False
    return all(left <= right for left, right in zip(values, values[1:]))
