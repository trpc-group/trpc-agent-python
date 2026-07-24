"""Fault injection helpers for replay snapshot mutation tests."""

from __future__ import annotations

import copy
from typing import Any
from typing import Callable

from .cases import ReplayCase


SYNTHETIC_MUTATIONS = [
    "drop_event",
    "reorder_event",
    "change_tool_args",
    "change_state",
    "drop_memory",
    "change_memory_text",
    "drop_summary",
    "overwrite_summary_text",
    "wrong_summary_session",
    "duplicate_event",
]

SUMMARY_REQUIRED_MUTATIONS = [
    "drop_summary",
    "overwrite_summary_text",
    "wrong_summary_session",
]

MUTATION_SECTION = {
    "drop_event": "events",
    "alter_event_text": "events",
    "reorder_event": "events",
    "reorder_events": "events",
    "duplicate_event": "events",
    "change_tool_args": "events",
    "change_tool_response": "events",
    "change_error_code": "events",
    "drop_recovery_event": "events",
    "change_state": "state",
    "change_state_value": "state",
    "drop_memory": "memories",
    "alter_memory_text": "memories",
    "change_memory_text": "memories",
    "change_memory_text_or_metadata": "memories",
    "drop_summary": "summary",
    "overwrite_summary_text": "summary",
    "overwrite_summary_with_stale_text": "summary",
    "wrong_summary_session": "summary",
    "summary_wrong_session_id": "summary",
}


def mutations_for_case(case: ReplayCase) -> list[str]:
    mutations = ["drop_event", "reorder_events", "duplicate_event", "alter_event_text"]
    if case.name in {"tool_call_roundtrip", "serialization_order_nested_payload"}:
        mutations.extend(["change_tool_args", "change_tool_response"])
    if case.name in {"scoped_state_overwrite", "state_temp_key_ignored_but_persistent_key_compared"}:
        mutations.append("change_state_value")
    if case.name in {"memory_preference_search", "memory_multi_session_isolation"}:
        mutations.extend(["drop_memory", "alter_memory_text"])
    if case.name in {
        "summary_generation",
        "summary_update_overwrite",
        "summary_with_event_truncation",
        "summary_truncation_preserves_recent_context",
    }:
        mutations.extend(["drop_summary", "overwrite_summary_with_stale_text", "summary_wrong_session_id"])
    if case.name == "duplicate_or_error_recovery":
        mutations.extend(["change_error_code", "drop_recovery_event"])
    return mutations


def _find_mutation_event(
    name: str,
    snapshot: dict[str, Any],
    predicate: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    for event in snapshot["events"]:
        if predicate(event):
            return event
    raise AssertionError(f"{name} mutation target was not found")


def mutate_snapshot(name: str, snapshot: dict[str, Any]) -> None:
    if name == "drop_event":
        del snapshot["events"][1]
    elif name == "alter_event_text":
        event = _find_mutation_event(name, snapshot, lambda item: item.get("text") is not None)
        event["text"] = "mutated event text"
    elif name in {"reorder_event", "reorder_events"}:
        snapshot["events"][0], snapshot["events"][1] = snapshot["events"][1], snapshot["events"][0]
    elif name == "change_tool_args":
        event = _find_mutation_event(name, snapshot, lambda item: item["function_calls"])
        event["function_calls"][0]["args"]["city"] = "Shanghai"
    elif name == "change_tool_response":
        event = _find_mutation_event(name, snapshot, lambda item: item["function_responses"])
        response = event["function_responses"][0]["response"]
        if "temperature" in response:
            response["temperature"] = 30
        else:
            response["condition"] = "rainy"
    elif name in {"change_state", "change_state_value"}:
        if "user:tier" in snapshot["state"]:
            snapshot["state"]["user:tier"] = "silver"
        elif "preference" in snapshot["state"]:
            snapshot["state"]["preference"] = "coffee"
        elif snapshot["state"]:
            key = sorted(snapshot["state"])[0]
            snapshot["state"][key] = "mutated-state"
        else:
            raise AssertionError(f"{name} mutation target was not found")
    elif name == "drop_memory":
        del snapshot["memories"][0]
    elif name in {"alter_memory_text", "change_memory_text", "change_memory_text_or_metadata"}:
        snapshot["memories"][0]["text"] = "I prefer coffee in the morning."
    elif name == "drop_summary":
        snapshot["summary"] = None
    elif name in {"overwrite_summary_text", "overwrite_summary_with_stale_text"}:
        if snapshot["summary"] is None:
            raise AssertionError(f"{name} mutation target was not found")
        snapshot["summary"]["text"] = "summary(session-mutation): stale overwritten summary"
    elif name in {"wrong_summary_session", "summary_wrong_session_id"}:
        if snapshot["summary"] is None:
            raise AssertionError(f"{name} mutation target was not found")
        snapshot["summary"]["metadata"]["session_id"] = "wrong-session"
    elif name == "duplicate_event":
        snapshot["events"].append(copy.deepcopy(snapshot["events"][0]))
    elif name == "change_error_code":
        event = _find_mutation_event(name, snapshot, lambda item: item.get("error_code"))
        event["error_code"] = "WRONG_ERROR_CODE"
    elif name == "drop_recovery_event":
        for index, event in enumerate(snapshot["events"]):
            if event["text"] == "Recovery succeeded after retry.":
                del snapshot["events"][index]
                break
        else:
            raise AssertionError("drop_recovery_event mutation target was not found")
    else:
        raise ValueError(f"Unknown mutation {name}")
