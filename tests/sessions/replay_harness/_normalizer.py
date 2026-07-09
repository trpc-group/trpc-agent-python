#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Normalization utilities for replay consistency testing.

Strips auto-generated and backend-dependent fields so that results from
different backends can be compared meaningfully.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel
from pydantic import Field


class NormalizedResult(BaseModel):
    """Normalized view of a backend result suitable for comparison."""

    events: list[dict] = Field(default_factory=list)
    """Normalized event dicts with auto-generated fields stripped."""

    state: dict = Field(default_factory=dict)
    """Deep-sorted state dictionary."""

    summaries: list[dict] = Field(default_factory=list)
    """Normalized summary dicts with timestamps stripped."""

    memory_entries: list[dict] = Field(default_factory=list)
    """Normalized memory entry dicts with timestamps stripped."""

    errors: list[str] = Field(default_factory=list)
    """Errors encountered during replay (kept as-is)."""


def _sort_dict_deep(obj: Any) -> Any:
    """Recursively sort all dict keys for deterministic serialization."""
    if isinstance(obj, dict):
        return {k: _sort_dict_deep(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [_sort_dict_deep(v) for v in obj]
    return obj


def normalize_events(events: list[dict]) -> list[dict]:
    """Normalize a list of event dicts.

    Strips auto-generated / backend-dependent fields (``id``, ``timestamp``,
    ``invocation_id``, etc.) and replaces ``timestamp`` with a sequential
    index.  Extracts text, function calls, function responses, and state
    deltas into a canonical form.
    """
    normalized: list[dict] = []
    for idx, event in enumerate(events):
        norm: dict[str, Any] = {}

        norm["index"] = idx
        norm["author"] = event.get("author", "")

        content = event.get("content") or {}
        parts = content.get("parts") if isinstance(content, dict) else []

        texts = []
        func_calls = []
        func_responses = []

        if isinstance(parts, list):
            for part in parts:
                if not isinstance(part, dict):
                    continue
                if part.get("text"):
                    texts.append(part["text"])
                if part.get("function_call"):
                    fc = part["function_call"]
                    func_calls.append({
                        "name": fc.get("name", ""),
                        "args": fc.get("args", {}),
                    })
                if part.get("function_response"):
                    fr = part["function_response"]
                    func_responses.append({
                        "name": fr.get("name", ""),
                        "response": fr.get("response", {}),
                    })

        norm["text"] = "".join(texts)
        norm["function_calls"] = func_calls
        norm["function_responses"] = func_responses

        actions = event.get("actions") or {}
        if isinstance(actions, dict):
            norm["state_delta"] = actions.get("state_delta") or {}
        else:
            norm["state_delta"] = {}

        norm["partial"] = event.get("partial", False)
        norm["visible"] = event.get("visible", True)
        norm["error_code"] = event.get("error_code")
        norm["error_message"] = event.get("error_message")

        normalized.append(norm)

    return normalized


def normalize_state(state: dict) -> dict:
    """Normalize a state dictionary by deep-sorting keys.

    Returns a deterministic representation suitable for deep comparison.
    """
    sorted_state = _sort_dict_deep(state)
    return json.loads(json.dumps(sorted_state, sort_keys=True))


def normalize_summaries(summaries: list[dict]) -> list[dict]:
    """Normalize summary dicts.

    Keeps content and structural metadata; strips ``summary_timestamp``
    and any backend-specific metadata.
    """
    normalized: list[dict] = []
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        norm: dict[str, Any] = {
            "session_id": summary.get("session_id", ""),
            "summary_text": summary.get("summary_text", ""),
            "original_event_count": summary.get("original_event_count", 0),
            "compressed_event_count": summary.get("compressed_event_count", 0),
        }
        normalized.append(norm)
    return normalized


def normalize_memory_entries(entries: list[dict]) -> list[dict]:
    """Normalize memory entry dicts.

    Keeps content text and author; strips timestamp.
    """
    normalized: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        content = entry.get("content") or {}
        text = ""
        if isinstance(content, dict):
            parts = content.get("parts") or []
            if isinstance(parts, list):
                text = "".join(
                    p.get("text", "") for p in parts if isinstance(p, dict))
        norm: dict[str, Any] = {
            "content_text": text,
            "author": entry.get("author", ""),
        }
        normalized.append(norm)
    return normalized


def normalize_backend_result(
    events: list[dict],
    state: dict,
    summaries: list[dict],
    memory_entries: list[dict],
    errors: list[str],
) -> NormalizedResult:
    """Normalize a complete backend result for comparison."""
    return NormalizedResult(
        events=normalize_events(events),
        state=normalize_state(state),
        summaries=normalize_summaries(summaries),
        memory_entries=normalize_memory_entries(memory_entries),
        errors=errors,
    )
