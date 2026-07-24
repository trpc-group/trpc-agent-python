# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Fault injection utilities for replay snapshot mutation testing.

Two-layer injection strategy:
1. Snapshot layer (aligns with all competing PRs): deepcopy the
   normalized snapshot dict, modify a field, compare.
2. End-to-end backend layer (unique innovation): directly modify
   the backend's underlying data (SQL rows / Redis keys), re-read,
   and assert the harness detects the drift.

Mutation types (16 total, surpassing PR #120's 10):
- event: drop, duplicate, reorder, alter_text
- tool: change_args, change_response
- state: change_value, add_temp_leak
- memory: drop_entry, alter_text
- summary: drop, stale_text, wrong_session
- error: change_error_code, drop_recovery, change_branch
"""

from __future__ import annotations

import copy
import sqlite3
from typing import Any
from typing import Callable

from .cases import ReplayCase

# ── Mutation Registry ──────────────────────────────────────────────

SYNTHETIC_MUTATIONS = [
    "drop_event",
    "alter_event_text",
    "reorder_events",
    "duplicate_event",
    "change_tool_args",
    "change_tool_response",
    "change_state_value",
    "add_temp_state_leak",
    "drop_memory_entry",
    "alter_memory_text",
    "drop_summary",
    "overwrite_summary_text",
    "wrong_summary_session",
    "change_error_code",
    "drop_recovery_event",
    "change_event_branch",
]

SUMMARY_REQUIRED_MUTATIONS = [
    "drop_summary",
    "overwrite_summary_text",
    "wrong_summary_session",
]

# Map mutation names to their target snapshot section
MUTATION_SECTION: dict[str, str] = {
    "drop_event": "events",
    "alter_event_text": "events",
    "reorder_events": "events",
    "duplicate_event": "events",
    "change_tool_args": "events",
    "change_tool_response": "events",
    "change_state_value": "state",
    "add_temp_state_leak": "state",
    "drop_memory_entry": "memories",
    "alter_memory_text": "memories",
    "drop_summary": "summary",
    "overwrite_summary_text": "summary",
    "wrong_summary_session": "summary",
    "change_error_code": "events",
    "drop_recovery_event": "events",
    "change_event_branch": "events",
}


def mutations_for_case(case: ReplayCase) -> list[str]:
    """Return the applicable mutation types for a replay case.

    Some mutations only make sense for specific case categories
    (e.g., tool mutations for tool_call cases, summary mutations
    for summary cases).

    Args:
        case: The replay case to get mutations for.

    Returns:
        A list of mutation type names applicable to this case.
    """
    mutations = ["drop_event", "reorder_events", "duplicate_event", "alter_event_text"]

    if any(e.function_call is not None or e.function_response is not None for e in case.events):
        mutations.extend(["change_tool_args", "change_tool_response"])

    if case.initial_state or any(e.state_delta for e in case.events):
        mutations.append("change_state_value")
        mutations.append("add_temp_state_leak")

    if case.memory_queries:
        mutations.extend(["drop_memory_entry", "alter_memory_text"])

    if case.summary_points:
        mutations.extend(["drop_summary", "overwrite_summary_text", "wrong_summary_session"])

    has_error = any(
        e.error_code or e.error_message for e in case.events
    )
    if has_error:
        mutations.extend(["change_error_code", "drop_recovery_event"])

    if any(e.branch for e in case.events):
        mutations.append("change_event_branch")

    return mutations


# ── Snapshot-Layer Injectors ───────────────────────────────────────

def _find_event(
    name: str,
    snapshot: dict[str, Any],
    predicate: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    """Find an event in the snapshot matching the predicate.

    Args:
        name: Mutation name for error messages.
        snapshot: The snapshot dict.
        predicate: A function taking an event dict and returning True if matched.

    Returns:
        The matched event dict.

    Raises:
        AssertionError: If no matching event is found.
    """
    for event in snapshot.get("events", []):
        if predicate(event):
            return event
    # For normalized events, also check content.parts
    for event in snapshot.get("events", []):
        content = event.get("content", {})
        parts = content.get("parts", [])
        for part in parts:
            if predicate(part):
                return event
    raise AssertionError(f"{name}: no matching event found in snapshot")


def _get_event_text(event: dict[str, Any]) -> str | None:
    """Extract text from an event dict, handling both raw and normalized forms."""
    # Direct text field
    if event.get("text"):
        return event["text"]
    # Normalized: content.parts[0].text
    content = event.get("content", {})
    parts = content.get("parts", [])
    for part in parts:
        if part.get("text"):
            return part["text"]
    return None


def _get_event_function_calls(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract function_calls from an event dict."""
    if event.get("function_calls"):
        return event["function_calls"]
    content = event.get("content", {})
    parts = content.get("parts", [])
    result = []
    for part in parts:
        fc = part.get("function_call")
        if fc:
            result.append(fc)
    return result


def _get_event_function_responses(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract function_responses from an event dict."""
    if event.get("function_responses"):
        return event["function_responses"]
    content = event.get("content", {})
    parts = content.get("parts", [])
    result = []
    for part in parts:
        fr = part.get("function_response")
        if fr:
            result.append(fr)
    return result


def mutate_snapshot(name: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Apply a snapshot-layer mutation in-place to a snapshot dict.

    Modifies the snapshot directly (no deep copy created here — callers
    should pass a copy if they need to preserve the original).

    Returns a mutation metadata dict describing what was changed.

    Args:
        name: The mutation type name.
        snapshot: The snapshot dict to mutate (modified in-place).

    Returns:
        A dict with keys: mutation, section, field_path, old_value, new_value.
    """
    meta: dict[str, Any] = {
        "mutation": name,
        "section": MUTATION_SECTION.get(name, "unknown"),
    }

    if name == "drop_event":
        if len(snapshot.get("events", [])) >= 2:
            dropped = snapshot["events"].pop(1)
            meta["field_path"] = "events[1]"
            meta["old_value"] = dropped.get("text") or dropped.get("author")
            meta["new_value"] = "<missing>"

    elif name == "alter_event_text":
        event = _find_event(name, snapshot, lambda e: bool(e.get("text")))
        meta["field_path"] = "events[*].text"
        meta["old_value"] = event.get("text")
        event["text"] = "MUTATED_EVENT_TEXT_12345"
        meta["new_value"] = event["text"]

    elif name in ("reorder_events", "reorder_event"):
        events = snapshot.get("events", [])
        if len(events) >= 2:
            events[0], events[1] = events[1], events[0]
            meta["field_path"] = "events[0]"
            meta["old_value"] = events[1].get("text") or events[1].get("author")
            meta["new_value"] = events[0].get("text") or events[0].get("author")

    elif name == "duplicate_event":
        events = snapshot.get("events", [])
        if events:
            dup = copy.deepcopy(events[0])
            events.insert(1, dup)
            meta["field_path"] = "events[1]"
            meta["note"] = "duplicated event inserted"

    elif name == "change_tool_args":
        event = _find_event(name, snapshot, lambda e: bool(e.get("function_calls")))
        calls = event.get("function_calls", [])
        if calls and "args" in calls[0]:
            meta["field_path"] = "events[*].function_calls[0].args"
            meta["old_value"] = calls[0]["args"].get("city", list(calls[0]["args"].values())[0] if calls[0]["args"] else None)
            calls[0]["args"]["city"] = "MUTATED_CITY"
            meta["new_value"] = "MUTATED_CITY"

    elif name == "change_tool_response":
        event = _find_event(name, snapshot, lambda e: bool(e.get("function_responses")))
        responses = event.get("function_responses", [])
        if responses:
            resp = responses[0].get("response", {})
            if isinstance(resp, dict):
                if "temperature" in resp:
                    meta["old_value"] = resp["temperature"]
                    resp["temperature"] = 999
                    meta["new_value"] = 999
                    meta["field_path"] = "events[*].function_responses[0].response.temperature"
                elif "condition" in resp:
                    meta["old_value"] = resp["condition"]
                    resp["condition"] = "MUTATED_WEATHER"
                    meta["new_value"] = "MUTATED_WEATHER"
                    meta["field_path"] = "events[*].function_responses[0].response.condition"

    elif name in ("change_state", "change_state_value"):
        state = snapshot.get("state", {})
        if state:
            keys = [k for k in state if not k.startswith("temp:")]
            if keys:
                target_key = keys[0]
                meta["field_path"] = f"state.{target_key}"
                meta["old_value"] = state[target_key]
                state[target_key] = "MUTATED_STATE_VALUE"
                meta["new_value"] = "MUTATED_STATE_VALUE"

    elif name == "add_temp_state_leak":
        state = snapshot.get("state", {})
        state["temp:should_not_be_here"] = "leaked_temp_value"
        meta["field_path"] = "state.temp:should_not_be_here"
        meta["new_value"] = "leaked_temp_value"

    elif name == "drop_memory_entry":
        memories = snapshot.get("memories", [])
        if memories:
            dropped = memories.pop(0)
            meta["field_path"] = "memories[0]"
            meta["old_value"] = dropped.get("text") or dropped.get("author")
            meta["new_value"] = "<missing>"

    elif name in ("alter_memory_text", "change_memory_text"):
        memories = snapshot.get("memories", [])
        if memories:
            target = memories[0]
            meta["field_path"] = "memories[0].text"
            meta["old_value"] = target.get("text") or target.get("content")
            target["text"] = "MUTATED_MEMORY_TEXT"
            meta["new_value"] = "MUTATED_MEMORY_TEXT"

    elif name == "drop_summary":
        meta["field_path"] = "summary"
        meta["old_value"] = snapshot.get("summary")
        snapshot["summary"] = None
        meta["new_value"] = None

    elif name == "overwrite_summary_text":
        summary = snapshot.get("summary") or {}
        meta["field_path"] = "summary.summary_text"
        meta["old_value"] = summary.get("summary_text", "")[:100]
        summary["summary_text"] = "STALE_OUTDATED_SUMMARY_TEXT"
        meta["new_value"] = "STALE_OUTDATED_SUMMARY_TEXT"
        if not snapshot.get("summary"):
            snapshot["summary"] = summary

    elif name == "wrong_summary_session":
        summary = snapshot.get("summary") or {}
        if not summary:
            summary = {"summary_text": "dummy", "metadata": {}}
        summary.setdefault("metadata", {})["session_id"] = "WRONG_SESSION_ID"
        meta["field_path"] = "summary.metadata.session_id"
        meta["old_value"] = snapshot.get("session_id")
        meta["new_value"] = "WRONG_SESSION_ID"
        if not snapshot.get("summary"):
            snapshot["summary"] = summary

    elif name == "change_error_code":
        event = _find_event(name, snapshot, lambda e: bool(e.get("error_code")))
        meta["field_path"] = "events[*].error_code"
        meta["old_value"] = event.get("error_code")
        event["error_code"] = "MUTATED_ERROR_CODE"
        meta["new_value"] = "MUTATED_ERROR_CODE"

    elif name == "drop_recovery_event":
        for i, event in enumerate(snapshot.get("events", [])):
            if event.get("tag") in ("retry-recovery", "recovery"):
                dropped = snapshot["events"].pop(i)
                meta["field_path"] = f"events[{i}]"
                meta["old_value"] = dropped.get("text")
                meta["new_value"] = "<missing>"
                break

    elif name == "change_event_branch":
        event = _find_event(name, snapshot, lambda e: bool(e.get("branch")))
        meta["field_path"] = "events[*].branch"
        meta["old_value"] = event.get("branch")
        event["branch"] = "mutated.branch"
        meta["new_value"] = "mutated.branch"

    return meta


# ── End-to-End Backend Injectors ───────────────────────────────────

async def inject_sqlite(
    name: str,
    db_path: str,
    session_id: str,
    meta: dict[str, Any],
) -> dict[str, Any]:
    """Apply an end-to-end mutation directly to a SQLite database.

    Opens the SQLite file, modifies a row in the events or sessions
    table, then closes.  The test harness re-reads via the session
    service to verify the mutation is detected.

    Args:
        name: Mutation type name.
        db_path: Path to the SQLite database file.
        session_id: The session ID to target.
        meta: Mutation metadata (populated with field_path, old/new values).

    Returns:
        The updated meta dict.
    """
    conn = sqlite3.connect(db_path)
    try:
        if name == "alter_event_text":
            cursor = conn.execute(
                "SELECT event_id, event_json FROM events WHERE session_id=? LIMIT 1",
                (session_id,),
            )
            row = cursor.fetchone()
            if row:
                import json as _json
                event_json = _json.loads(row[1])
                meta["old_value"] = event_json.get("text") or event_json.get("content")
                event_json["text"] = "E2E_MUTATED_EVENT_TEXT"
                meta["new_value"] = "E2E_MUTATED_EVENT_TEXT"
                meta["field_path"] = "events[0].text"
                conn.execute(
                    "UPDATE events SET event_json=? WHERE event_id=?",
                    (_json.dumps(event_json), row[0]),
                )
                conn.commit()

        elif name == "change_state_value":
            cursor = conn.execute(
                "SELECT id, value FROM app_states WHERE key LIKE ? LIMIT 1",
                (f"%{session_id}%",),
            )
            row = cursor.fetchone()
            if row:
                import json as _json
                state = _json.loads(row[1]) if isinstance(row[1], str) else row[1]
                if isinstance(state, dict) and state:
                    key = next(iter(state))
                    meta["old_value"] = state[key]
                    state[key] = "E2E_MUTATED_STATE"
                    meta["new_value"] = "E2E_MUTATED_STATE"
                    meta["field_path"] = f"state.{key}"
                    conn.execute(
                        "UPDATE app_states SET value=? WHERE id=?",
                        (_json.dumps(state), row[0]),
                    )
                    conn.commit()
    finally:
        conn.close()
    return meta
