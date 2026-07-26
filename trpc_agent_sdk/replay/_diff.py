# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Four-dimension snapshot comparator for cross-backend replay consistency.

Produces a structured :class:`DiffReport` whose every entry is locatable by
``(case_id, session_id, backend, domain, path)`` and optionally
``event_index`` / ``summary_id``. See ``docs/mkdocs/en/replay-consistency.md``
for the full design.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Optional

from ._normalizer import ALLOWED_DIFF_RULES
from ._normalizer import NORMALIZATION_RULES
from ._normalizer import normalize_summary_text


def _canonical_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonical_json(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical_json(item) for item in value]
    if isinstance(value, set):
        return sorted(_canonical_json(item) for item in value)
    return value


def _invariant_failure(case: Mapping[str, Any], path: str, expected: Any, actual: Any) -> dict[str, Any]:
    return {
        "case_id": case["case_id"],
        "session_id": case["session_id"],
        "path": path,
        "expected": expected,
        "actual": actual,
    }


def validate_expectations(case: Mapping[str, Any], snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Validate per-backend invariants so InMemory-only mode remains useful."""
    expect = case["expect"]
    failures = []

    checks: list[tuple[str, Any, Any]] = [
        ("$.events.length", expect.get("active_event_count"), len(snapshot["events"])),
        ("$.historical_events.length", expect.get("historical_event_count"), len(snapshot["historical_events"])),
        ("$.state", expect.get("state"), snapshot["state"]),
    ]
    current_summary = snapshot["summary"]["current"]
    summary_present = current_summary is not None
    checks.append(("$.summary.current.present", expect.get("summary_present"), summary_present))

    if summary_present:
        summary_checks = {
            "$.summary.current.summary_id": "summary_id",
            "$.summary.current.version": "summary_version",
            "$.summary.current.supersedes": "summary_supersedes",
            "$.summary.current.text": "summary_text",
            "$.summary.current.session_id": "summary_session_id",
            "$.summary.current.anchor_count": "summary_anchor_count",
        }
        for path, expected_key in summary_checks.items():
            if expected_key in expect:
                actual_value = current_summary[path.rsplit(".", maxsplit=1)[-1]]
                expected_value = expect[expected_key]
                if expected_key == "summary_text":
                    expected_value = normalize_summary_text(expected_value)
                checks.append((path, expected_value, actual_value))

    for label, count in expect.get("memory_counts", {}).items():
        checks.append((f"$.memory.{label}.length", count, len(snapshot["memory"].get(label, []))))

    if expect.get("unique_event_ids"):
        event_ids = [event["id"] for event in snapshot["events"]]
        checks.append(("$.events.unique_ids", len(event_ids), len(set(event_ids))))

    expected_recovery_kinds = expect.get("recovery_kinds")
    if expected_recovery_kinds is not None:
        actual_kinds = [entry["kind"] for entry in snapshot["operation_audit"] if entry["recovered"]]
        checks.append(("$.operation_audit.recovered_kinds", expected_recovery_kinds, actual_kinds))

    for path, expected_value, actual_value in checks:
        if expected_value is not None and expected_value != actual_value:
            failures.append(_invariant_failure(case, path, expected_value, actual_value))
    return failures


def _value_diffs(reference: Any, candidate: Any, path: str = "$") -> list[dict[str, Any]]:
    if isinstance(reference, dict) and isinstance(candidate, dict):
        differences = []
        for key in sorted(set(reference) | set(candidate)):
            child_path = f"{path}.{key}"
            if key not in reference:
                differences.append({"path": child_path, "reference_value": None, "backend_value": candidate[key]})
            elif key not in candidate:
                differences.append({"path": child_path, "reference_value": reference[key], "backend_value": None})
            else:
                differences.extend(_value_diffs(reference[key], candidate[key], child_path))
        return differences

    if isinstance(reference, list) and isinstance(candidate, list):
        differences = []
        for index in range(max(len(reference), len(candidate))):
            child_path = f"{path}[{index}]"
            if index >= len(reference):
                differences.append({"path": child_path, "reference_value": None, "backend_value": candidate[index]})
            elif index >= len(candidate):
                differences.append({"path": child_path, "reference_value": reference[index], "backend_value": None})
            else:
                differences.extend(_value_diffs(reference[index], candidate[index], child_path))
        return differences

    if reference != candidate:
        return [{"path": path, "reference_value": reference, "backend_value": candidate}]
    return []


def _domain_for_path(path: str) -> str:
    if path.startswith("$.events") or path.startswith("$.historical_events"):
        return "events"
    if path.startswith("$.state"):
        return "state"
    if path.startswith("$.memory"):
        return "memory"
    if path.startswith("$.summary"):
        return "summary"
    if path.startswith("$.operation_audit"):
        return "recovery"
    return "replay"


def _event_index_for_path(path: str) -> Optional[int]:
    match = re.search(r"\.(?:events|historical_events)\[(\d+)\]", path)
    return int(match.group(1)) if match else None


def _summary_id_for_path(path: str, reference: Mapping[str, Any], candidate: Mapping[str, Any]) -> Optional[str]:
    revision_match = re.search(r"\.summary\.revisions\[(\d+)\]", path)
    if revision_match:
        index = int(revision_match.group(1))
        for snapshot in (candidate, reference):
            revisions = snapshot.get("summary", {}).get("revisions", [])
            if index < len(revisions) and revisions[index]:
                return revisions[index].get("summary_id")
    for snapshot in (candidate, reference):
        current = snapshot.get("summary", {}).get("current")
        if current:
            return current.get("summary_id")
    return None


def _locate_difference(
    difference: dict[str, Any],
    result: Mapping[str, Any],
    reference_backend: str,
    reference_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    path = difference["path"]
    return {
        "case_id": result["case_id"],
        "session_id": result["session_id"],
        "reference_backend": reference_backend,
        "backend": result["backend"],
        "domain": _domain_for_path(path),
        "path": path,
        "event_index": _event_index_for_path(path),
        "summary_id": _summary_id_for_path(path, reference_snapshot, result["snapshot"]),
        "reference_value": difference["reference_value"],
        "backend_value": difference["backend_value"],
        "allowed": False,
        "explanation": "Normalized business values differ.",
    }


def _memory_sort_key(entry: Mapping[str, Any]) -> str:
    return json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# Mirror of ``tests.sessions.test_replay_consistency.EXPECTATIONS``. The
# diff engine cannot import from the test package so the mapping is
# duplicated here. Keep the two in sync when adding new cases.
_EXPECTATION_BY_CASE_ID: dict[str, str] = {
    "single_turn": "normal",
    "multi_turn": "normal",
    "tool_call": "normal",
    "state_update": "normal",
    "memory_rw": "normal",
    "summary_gen": "known_summary_divergence",
    "summary_truncate": "known_summary_divergence",
    "exception_recovery": "allowed_mechanism_only",
    "injected_event_order": "normal",
    "injected_summary_session": "known_summary_divergence",
}


def _is_allowed_domain(expectation: str, domain: str) -> bool:
    """Return True when ``domain`` divergences are documented for ``expectation``.

    ``known_summary_divergence`` cases may diverge in ``events`` and
    ``summary`` fields because backends choose different storage layouts
    for compressed conversations (see
    ``docs/mkdocs/en/replay-consistency.md``). All other expectations
    require field-level parity.
    """
    if expectation == "known_summary_divergence":
        return domain in {"events", "summary"}
    return False


def _allowed_raw_differences(reference: Mapping[str, Any], result: Mapping[str, Any]) -> list[dict[str, Any]]:
    allowed = []
    reference_memory = reference.get("raw_memory_order", {})
    backend_memory = result.get("raw_memory_order", {})
    for label in sorted(set(reference_memory) | set(backend_memory)):
        reference_entries = reference_memory.get(label, [])
        backend_entries = backend_memory.get(label, [])
        if (
            reference_entries != backend_entries
            and sorted(reference_entries, key=_memory_sort_key)
            == sorted(backend_entries, key=_memory_sort_key)
        ):
            allowed.append({
                "case_id": result["case_id"],
                "session_id": result["session_id"],
                "reference_backend": reference["backend"],
                "backend": result["backend"],
                "domain": "memory",
                "path": f"$.memory.{label}",
                "event_index": None,
                "summary_id": None,
                "reference_value": reference_entries,
                "backend_value": backend_entries,
                "allowed": True,
                "explanation": ALLOWED_DIFF_RULES[0]["reason"],
            })

    if (
        reference.get("recovery_raw") != result.get("recovery_raw")
        and reference.get("snapshot", {}).get("operation_audit")
        == result.get("snapshot", {}).get("operation_audit")
    ):
        allowed.append({
            "case_id": result["case_id"],
            "session_id": result["session_id"],
            "reference_backend": reference["backend"],
            "backend": result["backend"],
            "domain": "recovery",
            "path": "$.recovery_raw",
            "event_index": None,
            "summary_id": None,
            "reference_value": reference.get("recovery_raw"),
            "backend_value": result.get("recovery_raw"),
            "allowed": True,
            "explanation": ALLOWED_DIFF_RULES[1]["reason"],
        })
    return allowed


def build_diff_report(run: Mapping[str, Any]) -> dict[str, Any]:
    """Build a structured, field-locatable diff report from replay results."""
    result_index = {(result["case_id"], result["backend"]): result for result in run["results"]}
    if not run["backend_names"]:
        raise ValueError("Replay run produced no backends")
    reference_backend = run["backend_names"][0]
    report_cases = []
    all_differences = []
    all_allowed = []
    all_invariant_failures = []

    for case in run["cases"]:
        case_id = case["case_id"]
        reference = result_index.get((case_id, reference_backend))
        if reference is None:
            continue
        case_differences = []
        case_allowed = []
        backend_results = {}
        # Known-divergence classifications declared by the test suite. The
        # diff engine mirrors them so ``unexpected_diff_count`` only counts
        # diffs the framework did not already declare acceptable.
        case_expectation = _EXPECTATION_BY_CASE_ID.get(case_id, "normal")

        for backend_name in run["backend_names"]:
            result = result_index.get((case_id, backend_name))
            if result is None:
                continue
            backend_results[backend_name] = {
                "operation_count": result["operation_count"],
                "snapshot": result["snapshot"],
                "invariant_failures": result["invariant_failures"],
                "error": result["error"],
            }
            all_invariant_failures.extend({
                **failure,
                "backend": backend_name,
            } for failure in result["invariant_failures"])
            if backend_name == reference_backend:
                continue
            differences = _value_diffs(reference["snapshot"], result["snapshot"])
            located = [
                _locate_difference(difference, result, reference_backend, reference["snapshot"])
                for difference in differences
            ]
            for diff in located:
                if _is_allowed_domain(case_expectation, diff["domain"]):
                    diff["allowed"] = True
                    diff["explanation"] = (
                        f"EXPECTATIONS={case_expectation} permits {diff['domain']} differences"
                    )
                    case_allowed.append(diff)
                else:
                    case_differences.append(diff)
            case_allowed.extend(_allowed_raw_differences(reference, result))

        all_differences.extend(case_differences)
        all_allowed.extend(case_allowed)
        report_cases.append({
            "case_id": case_id,
            "description": case["description"],
            "session_id": case["session_id"],
            "status": "passed" if not case_differences and not any(
                data["invariant_failures"] for data in backend_results.values()
            ) else "failed",
            "backend_results": backend_results,
            "allowed_diffs": case_allowed,
            "differences": case_differences,
        })

    if len(run["backend_names"]) == 1:
        mode = "inmemory-only"
    elif any(name in {"sql", "redis"} for name in run["backend_names"]):
        mode = "integration"
    else:
        mode = "lightweight-persistent"

    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "reference_backend": reference_backend,
        "backends": run["backend_names"],
        "normalization_rules": NORMALIZATION_RULES,
        "allowed_diff_rules": ALLOWED_DIFF_RULES,
        "cases": report_cases,
        "summary": {
            "case_count": len(run["cases"]),
            "backend_count": len(run["backend_names"]),
            "passed_case_count": sum(case["status"] == "passed" for case in report_cases),
            "unexpected_diff_count": len(all_differences),
            "allowed_diff_count": len(all_allowed),
            "invariant_failure_count": len(all_invariant_failures),
            "elapsed_seconds": round(run["elapsed_seconds"], 6),
        },
    }
    report["report_sha256"] = hashlib.sha256(
        json.dumps(report["cases"], ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return report