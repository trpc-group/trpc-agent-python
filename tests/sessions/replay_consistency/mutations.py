# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Deterministic mutation operators for replay diff detection tests."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any
from typing import Callable


SnapshotDict = dict[str, Any]
MutationFn = Callable[[SnapshotDict], SnapshotDict]


@dataclass(frozen=True)
class MutationOperator:
    """A public mutation that must be detected by the diff engine."""

    name: str
    expected_category: str
    mutate: MutationFn
    summary_defect: str | None = None


def registered_mutations() -> list[MutationOperator]:
    """Return deterministic mutations with known expected categories."""

    return [
        MutationOperator("delete_event", "missing_entity", _delete_event),
        MutationOperator("duplicate_event", "duplicate_event", _duplicate_event),
        MutationOperator("swap_event_order", "event_order_mismatch", _swap_event_order),
        MutationOperator("change_state_value", "state_mismatch", _change_state_value),
        MutationOperator("delete_memory", "missing_entity", _delete_memory),
        MutationOperator("memory_cross_user", "memory_scope_violation", _memory_cross_user),
        MutationOperator("delete_summary", "summary_missing", _delete_summary, "loss"),
        MutationOperator("summary_wrong_owner", "summary_owner_mismatch", _summary_wrong_owner, "owner"),
        MutationOperator("summary_wrong_coverage", "summary_coverage_mismatch", _summary_wrong_coverage),
        MutationOperator(
            "old_summary_overwrites_new",
            "summary_version_mismatch",
            _old_summary_overwrites_new,
            "overwrite",
        ),
        MutationOperator("wrong_function_response_id", "tool_link_mismatch", _wrong_function_response_id),
    ]


def _copy(snapshot: SnapshotDict) -> SnapshotDict:
    return copy.deepcopy(snapshot)


def _first_session(data: SnapshotDict) -> dict[str, Any]:
    return data["sessions"][0]


def _delete_event(snapshot: SnapshotDict) -> SnapshotDict:
    data = _copy(snapshot)
    _first_session(data)["events"].pop(0)
    return data


def _duplicate_event(snapshot: SnapshotDict) -> SnapshotDict:
    data = _copy(snapshot)
    session = _first_session(data)
    session["events"].append(copy.deepcopy(session["events"][0]))
    return data


def _swap_event_order(snapshot: SnapshotDict) -> SnapshotDict:
    data = _copy(snapshot)
    events = _first_session(data)["events"]
    events[0], events[1] = events[1], events[0]
    return data


def _change_state_value(snapshot: SnapshotDict) -> SnapshotDict:
    data = _copy(snapshot)
    _first_session(data)["state"]["contract"] = "mutated"
    return data


def _delete_memory(snapshot: SnapshotDict) -> SnapshotDict:
    data = _copy(snapshot)
    data["memory"][0]["memories"] = []
    return data


def _memory_cross_user(snapshot: SnapshotDict) -> SnapshotDict:
    data = _copy(snapshot)
    data["memory"][0]["session_key"] = "replay_app/other_user"
    return data


def _delete_summary(snapshot: SnapshotDict) -> SnapshotDict:
    data = _copy(snapshot)
    data["summaries"] = []
    return data


def _summary_wrong_owner(snapshot: SnapshotDict) -> SnapshotDict:
    data = _copy(snapshot)
    data["summaries"][-1]["session_id"] = "wrong-session"
    return data


def _summary_wrong_coverage(snapshot: SnapshotDict) -> SnapshotDict:
    data = _copy(snapshot)
    data["summaries"][-1]["covered_event_ids"] = ["wrong-event"]
    return data


def _old_summary_overwrites_new(snapshot: SnapshotDict) -> SnapshotDict:
    data = _copy(snapshot)
    data["summaries"][-1]["version"] = 0
    return data


def _wrong_function_response_id(snapshot: SnapshotDict) -> SnapshotDict:
    data = _copy(snapshot)
    for session in data["sessions"]:
        for event in session["events"] + session["historical_events"]:
            for response in event.get("function_responses", []):
                response["id"] = "wrong-call-id"
                return data
    return data
