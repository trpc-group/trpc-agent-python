# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Semantic oracle checks for canonical replay snapshots."""

from __future__ import annotations

from typing import Any


def validate_snapshot_contract(snapshot: dict[str, Any]) -> list[str]:
    """Return human-readable contract violations for a canonical snapshot."""

    violations: list[str] = []
    _validate_sessions(snapshot, violations)
    _validate_memory(snapshot, violations)
    _validate_summaries(snapshot, violations)
    return violations


def _validate_sessions(snapshot: dict[str, Any], violations: list[str]) -> None:
    for session in snapshot.get("sessions", []):
        session_id = session.get("session_id")
        seen_event_ids: set[str] = set()
        previous_index = -1
        for event in session.get("events", []):
            event_id = event.get("event_id")
            if event_id in seen_event_ids:
                violations.append(f"duplicate event id {event_id} in session {session_id}")
            seen_event_ids.add(event_id)
            index = event.get("index")
            if not isinstance(index, int) or index <= previous_index:
                violations.append(f"event index is not strictly increasing in session {session_id}")
            previous_index = index
            if not event.get("timestamp_valid"):
                violations.append(f"invalid event timestamp for {event_id} in session {session_id}")
        if not isinstance(session.get("state", {}), dict):
            violations.append(f"state is not a dict in session {session_id}")


def _validate_memory(snapshot: dict[str, Any], violations: list[str]) -> None:
    for probe in snapshot.get("memory", []):
        if "/" not in probe.get("session_key", ""):
            violations.append(f"memory probe {probe.get('probe_id')} has invalid session key")
        for index, memory in enumerate(probe.get("memories", [])):
            if not memory.get("timestamp_valid"):
                violations.append(f"memory probe {probe.get('probe_id')} hit {index} has invalid timestamp")
            if not memory.get("content"):
                violations.append(f"memory probe {probe.get('probe_id')} hit {index} has empty content")


def _validate_summaries(snapshot: dict[str, Any], violations: list[str]) -> None:
    active_by_session: dict[tuple[str, str, str], int] = {}
    versions_by_session: dict[tuple[str, str, str], list[int]] = {}
    for summary in snapshot.get("summaries", []):
        key = (summary.get("app_name"), summary.get("user_id"), summary.get("session_id"))
        if summary.get("active"):
            active_by_session[key] = active_by_session.get(key, 0) + 1
        versions_by_session.setdefault(key, []).append(summary.get("version"))
        if not summary.get("timestamp_valid"):
            violations.append(f"summary {summary.get('client_summary_id')} has invalid timestamp")
        if not summary.get("covered_event_ids"):
            violations.append(f"summary {summary.get('client_summary_id')} has empty coverage")
    for key, active_count in active_by_session.items():
        if active_count != 1:
            violations.append(f"session {key} has {active_count} active summaries")
    for key, versions in versions_by_session.items():
        if versions != sorted(versions) or len(set(versions)) != len(versions):
            violations.append(f"session {key} summary versions are not strictly increasing")
