# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Report building functions for replay consistency tests."""

from __future__ import annotations

import asyncio
from typing import Any

from .backends import BASELINE_BACKEND_NAME
from .backends import DEFAULT_BACKEND_CONFIG
from .backends import ReplayBackendConfig
from .backends import comparison_backend_names
from .backends import run_memory_replay_case
from .backends import run_session_replay_case
from .comparators import allowed_diff_reason
from .comparators import diff_snapshots
from .comparators import extract_event_location
from .comparators import extract_summary_id
from .comparators import is_event_diff
from .comparators import is_session_metadata_diff
from .comparators import is_state_diff
from .comparators import is_summary_diff
from .constants import ALLOWED_DIFFS
from .loaders import REPLAY_CASES
from .models import ReplayCase
from .normalizers import all_normalized_events
from .normalizers import normalize_summary_text
from .normalizers import summary_metadata


def build_replay_diff_report(
    backend_config: ReplayBackendConfig = DEFAULT_BACKEND_CONFIG,
) -> dict[str, Any]:
    """Build the complete replay diff report for all cases."""
    cases = [build_case_diff_report(replay_case, backend_config) for replay_case in REPLAY_CASES]
    return {
        "schema_version": 2,
        "backend_pairs": {
            "session": actual_report_backend_names(cases, "session"),
            "memory": actual_report_backend_names(cases, "memory"),
        },
        "allowed_diffs": serialize_allowed_diffs(),
        "cases": cases,
        "totals": build_report_totals(cases),
    }


def build_case_diff_report(
    replay_case: ReplayCase,
    backend_config: ReplayBackendConfig = DEFAULT_BACKEND_CONFIG,
) -> dict[str, Any]:
    """Build diff report for a single case."""
    session_report = build_case_session_report(replay_case, backend_config)
    memory_report = build_case_memory_report(replay_case, backend_config)
    return {
        "case_name": replay_case.name,
        "app_name": replay_case.app_name,
        "user_id": replay_case.user_id,
        "session_id": replay_case.session_id,
        "status": combined_status([session_report["status"], memory_report["status"]]),
        "session": session_report,
        "memory": memory_report,
    }


def build_case_session_report(
    replay_case: ReplayCase,
    backend_config: ReplayBackendConfig = DEFAULT_BACKEND_CONFIG,
) -> dict[str, Any]:
    """Build session report for a single case."""
    snapshots = asyncio.run(run_session_replay_case(replay_case, backend_config))
    backend_names = comparison_backend_names(snapshots)
    if not backend_names:
        return no_comparison_session_report()

    diffs = []
    summary_content_checks = []
    summary_metadata_checks = []
    for backend_name in backend_names:
        diffs.extend(build_diff_report(
            case_name=replay_case.name,
            session_id=replay_case.session_id,
            backend_expected=BASELINE_BACKEND_NAME,
            backend_actual=backend_name,
            diffs=diff_snapshots(snapshots[BASELINE_BACKEND_NAME], snapshots[backend_name]),
            expected_snapshot=snapshots[BASELINE_BACKEND_NAME],
            actual_snapshot=snapshots[backend_name],
        ))
        summary_content_checks.extend(
            build_summary_content_checks(snapshots[BASELINE_BACKEND_NAME], snapshots[backend_name])
        )
        summary_metadata_checks.extend(
            build_summary_metadata_checks(snapshots[BASELINE_BACKEND_NAME], snapshots[backend_name])
        )
    return {
        "backend_expected": BASELINE_BACKEND_NAME,
        "backend_actual": backend_names[0] if len(backend_names) == 1 else backend_names,
        "backend_actuals": backend_names,
        "status": session_report_status(diffs, summary_content_checks, summary_metadata_checks),
        "event_diffs": [diff for diff in diffs if is_event_diff(diff) and not is_summary_diff(diff)],
        "state_diffs": [diff for diff in diffs if is_state_diff(diff)],
        "summary_diffs": [diff for diff in diffs if is_summary_diff(diff)],
        "summary_content_checks": summary_content_checks,
        "summary_metadata_checks": summary_metadata_checks,
        "session_metadata_diffs": [diff for diff in diffs if is_session_metadata_diff(diff)],
    }


def no_comparison_session_report() -> dict[str, Any]:
    """Build a session report for InMemory-only light mode."""
    return {
        "backend_expected": BASELINE_BACKEND_NAME,
        "backend_actual": None,
        "backend_actuals": [],
        "status": "not_applicable",
        "event_diffs": [],
        "state_diffs": [],
        "summary_diffs": [],
        "summary_content_checks": [],
        "summary_metadata_checks": [],
        "session_metadata_diffs": [],
    }


def build_summary_content_checks(
    expected_snapshot: dict[str, Any],
    actual_snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build summary content comparison checks."""
    expected_records = summary_comparison_records(expected_snapshot)
    actual_records = summary_comparison_records(actual_snapshot)
    checks = []
    for summary_id in sorted(set(expected_records) | set(actual_records)):
        expected_record = expected_records.get(summary_id)
        actual_record = actual_records.get(summary_id)
        expected_text = expected_record["text"] if expected_record else None
        actual_text = actual_record["text"] if actual_record else None
        expected_normalized = normalize_summary_text(expected_text)
        actual_normalized = normalize_summary_text(actual_text)
        checks.append({
            "summary_id": summary_id,
            "comparison": "normalized_text",
            "expected_text": expected_text,
            "actual_text": actual_text,
            "expected_normalized_text": expected_normalized,
            "actual_normalized_text": actual_normalized,
            "matched": expected_normalized == actual_normalized,
        })
    return checks


def build_summary_metadata_checks(
    expected_snapshot: dict[str, Any],
    actual_snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build summary metadata comparison checks."""
    expected_records = summary_comparison_records(expected_snapshot)
    actual_records = summary_comparison_records(actual_snapshot)
    checks = []
    for summary_id in sorted(set(expected_records) | set(actual_records)):
        expected_metadata = expected_records.get(summary_id, {}).get("metadata", {})
        actual_metadata = actual_records.get(summary_id, {}).get("metadata", {})
        fields = [
            summary_metadata_field_check(field, expected_metadata, actual_metadata)
            for field in sorted(set(expected_metadata) | set(actual_metadata))
        ]
        checks.append({
            "summary_id": summary_id,
            "comparison": "strict_metadata",
            "matched": all(field["matched"] for field in fields),
            "fields": fields,
        })
    return checks


def summary_metadata_field_check(
    field: str,
    expected_metadata: dict[str, Any],
    actual_metadata: dict[str, Any],
) -> dict[str, Any]:
    """Check a single summary metadata field."""
    expected = expected_metadata.get(field)
    actual = actual_metadata.get(field)
    return {
        "field": field,
        "expected": expected,
        "actual": actual,
        "matched": expected == actual,
    }


def summary_comparison_records(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Get summary comparison records, preferring cache records."""
    cache_records = summary_cache_records_by_id(snapshot)
    return cache_records or summary_records_by_id(snapshot)


def summary_cache_records_by_id(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Get summary records from cache."""
    summary = snapshot.get("summary")
    if not isinstance(summary, dict):
        return {}

    metadata = summary.get("metadata") or {}
    session_id = metadata.get("session_id") or snapshot.get("session_id")
    summary_id = f"summary:{session_id}:latest" if session_id else "summary:latest"
    return {
        summary_id: {
            "text": summary.get("text"),
            "metadata": metadata,
        }
    }


def summary_records_by_id(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Get summary records from events by ID."""
    records = {}
    for index, event in enumerate(all_normalized_events(snapshot)):
        if not event.get("is_summary"):
            continue
        metadata = summary_metadata(event)
        summary_id = metadata.get("summary_id") or f"summary_index:{index}"
        records[summary_id] = {
            "text": summary_text(event),
            "metadata": metadata,
        }
    return records


def summary_text(event: dict[str, Any]) -> str:
    """Extract text from a summary event."""
    return "".join(part["text"] for part in event.get("parts", []) if "text" in part)


def session_report_status(
    diffs: list[dict[str, Any]],
    summary_content_checks: list[dict[str, Any]],
    summary_metadata_checks: list[dict[str, Any]],
) -> str:
    """Determine session report status."""
    if any(not check["matched"] for check in summary_content_checks):
        return "unallowed_diff"
    if any(not check["matched"] for check in summary_metadata_checks):
        return "unallowed_diff"
    return diff_status(diffs)


def build_case_memory_report(
    replay_case: ReplayCase,
    backend_config: ReplayBackendConfig = DEFAULT_BACKEND_CONFIG,
) -> dict[str, Any]:
    """Build memory report for a single case."""
    if not replay_case.memory_search_records:
        return {
            "backend_expected": None,
            "backend_actual": None,
            "status": "not_applicable",
            "memory_diffs": [],
        }

    snapshots = asyncio.run(run_memory_replay_case(replay_case, backend_config))
    backend_names = comparison_backend_names(snapshots)
    if not backend_names:
        return {
            "backend_expected": BASELINE_BACKEND_NAME,
            "backend_actual": None,
            "backend_actuals": [],
            "status": "not_applicable",
            "memory_diffs": [],
        }

    diffs = []
    for backend_name in backend_names:
        diffs.extend(build_diff_report(
            case_name=replay_case.name,
            session_id=replay_case.session_id,
            backend_expected=BASELINE_BACKEND_NAME,
            backend_actual=backend_name,
            diffs=diff_snapshots(snapshots[BASELINE_BACKEND_NAME], snapshots[backend_name]),
            expected_snapshot=snapshots[BASELINE_BACKEND_NAME],
            actual_snapshot=snapshots[backend_name],
        ))
    return {
        "backend_expected": BASELINE_BACKEND_NAME,
        "backend_actual": backend_names[0] if len(backend_names) == 1 else backend_names,
        "backend_actuals": backend_names,
        "status": diff_status(diffs),
        "memory_diffs": diffs,
    }


def build_report_totals(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Build report totals."""
    session_event_diffs = count_case_diffs(cases, "session", "event_diffs")
    session_state_diffs = count_case_diffs(cases, "session", "state_diffs")
    session_summary_diffs = count_case_diffs(cases, "session", "summary_diffs")
    session_metadata_diffs = count_case_diffs(cases, "session", "session_metadata_diffs")
    summary_content_mismatches = count_summary_check_mismatches(cases, "summary_content_checks")
    summary_metadata_mismatches = count_summary_check_mismatches(cases, "summary_metadata_checks")
    memory_diffs = count_case_diffs(cases, "memory", "memory_diffs")
    all_diffs = iter_case_diffs(cases)
    return {
        "cases": len(cases),
        "session_event_diffs": session_event_diffs,
        "session_state_diffs": session_state_diffs,
        "session_summary_diffs": session_summary_diffs,
        "session_metadata_diffs": session_metadata_diffs,
        "summary_content_mismatches": summary_content_mismatches,
        "summary_metadata_mismatches": summary_metadata_mismatches,
        "memory_diffs": memory_diffs,
        "allowed_diffs": sum(1 for diff in all_diffs if diff["allowed"]),
        "unallowed_diffs": sum(1 for diff in iter_case_diffs(cases) if not diff["allowed"]),
    }


def actual_report_backend_names(cases: list[dict[str, Any]], section: str) -> list[str]:
    """Return backend names that actually appear in report cases."""
    names = [BASELINE_BACKEND_NAME]
    for case in cases:
        actuals = case[section].get("backend_actuals")
        if actuals is None:
            actual = case[section].get("backend_actual")
            actuals = [actual] if actual else []
        for backend_name in actuals:
            if backend_name not in names:
                names.append(backend_name)
    return names


def count_summary_check_mismatches(cases: list[dict[str, Any]], bucket: str) -> int:
    """Count summary check mismatches."""
    return sum(
        1
        for case in cases
        for check in case["session"][bucket]
        if not check["matched"]
    )


def count_case_diffs(cases: list[dict[str, Any]], section: str, bucket: str) -> int:
    """Count diffs in a specific section and bucket."""
    return sum(len(case[section][bucket]) for case in cases)


def iter_case_diffs(cases: list[dict[str, Any]]):
    """Iterate over all diffs in all cases."""
    for case in cases:
        yield from case["session"]["event_diffs"]
        yield from case["session"]["state_diffs"]
        yield from case["session"]["summary_diffs"]
        yield from case["session"]["session_metadata_diffs"]
        yield from case["memory"]["memory_diffs"]


def combined_status(statuses: list[str]) -> str:
    """Combine multiple statuses into one."""
    active_statuses = [status for status in statuses if status != "not_applicable"]
    if any(status == "unallowed_diff" for status in active_statuses):
        return "unallowed_diff"
    if any(status == "allowed_diff" for status in active_statuses):
        return "allowed_diff"
    return "matched"


def diff_status(diffs: list[dict[str, Any]]) -> str:
    """Determine status from diffs."""
    if not diffs:
        return "matched"
    if any(not diff["allowed"] for diff in diffs):
        return "unallowed_diff"
    return "allowed_diff"


def serialize_allowed_diffs() -> list[dict[str, Any]]:
    """Serialize allowed diffs for the report."""
    return [{
        "case_name": rule["case_name"],
        "backend_expected": rule["backend_expected"],
        "backend_actual": rule["backend_actual"],
        "field_paths": sorted(rule["field_paths"]),
        "reason": rule["reason"],
    } for rule in ALLOWED_DIFFS]


def build_diff_report(
    *,
    case_name: str,
    session_id: str,
    backend_expected: str,
    backend_actual: str,
    diffs: list[dict[str, Any]],
    expected_snapshot: dict[str, Any],
    actual_snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build a detailed diff report."""
    report = []
    for diff in diffs:
        allowed_reason = allowed_diff_reason(case_name, backend_expected, backend_actual, diff["field_path"])
        event_collection, event_index = extract_event_location(diff["field_path"])
        report.append({
            "case_name": case_name,
            "session_id": session_id,
            "backend_expected": backend_expected,
            "backend_actual": backend_actual,
            "field_path": diff["field_path"],
            "event_collection": event_collection,
            "event_index": event_index,
            "summary_id": extract_summary_id(diff, expected_snapshot, actual_snapshot),
            "expected": diff["expected"],
            "actual": diff["actual"],
            "allowed": allowed_reason is not None,
            "allowed_reason": allowed_reason,
        })
    return report
