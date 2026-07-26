#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Normalization, comparison, metrics, and reporting for replay snapshots."""

from __future__ import annotations

import copy
import json
import os
import unicodedata
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Iterable
from typing import Optional

REPORT_SCHEMA_VERSION = 1
SUMMARY_ANCHOR_ID = "<summary-anchor>"
STRUCTURAL_PATH_SEPARATOR = "/"
REPORT_DURATION_PRECISION = 6
JSON_INDENT = 2
NORMALIZATION_RULES = (
    "only generated summary IDs map to stable references; caller-provided event IDs remain exact",
    "timestamps compare by relative rank so ordering violations remain visible",
    "null and empty long_running_tool_ids normalize to an empty list",
    "memory timestamps are excluded while duplicate counts remain",
    "dictionary, set, and *_json field order is canonicalized",
    "summary text alone receives Unicode NFKC, newline, and whitespace normalization",
)


@dataclass(frozen=True)
class AllowedDiff:
    """One exact backend-specific difference rule."""

    backend_pair: tuple[str, str]
    field_path: str
    reason: str


@dataclass(frozen=True)
class DiffItem:
    """One path-level difference."""

    case_id: str
    session_id: str
    backend_pair: tuple[str, str]
    category: str
    field_path: str
    left: Any
    right: Any
    allowed: bool
    reason: Optional[str] = None
    event_index: Optional[int] = None
    summary_id: Optional[str] = None


DEFAULT_ALLOWED_DIFFS = (
    AllowedDiff(
        ("in_memory", "sqlite_reloaded"),
        "/summary/cache_present",
        "SessionSummary metadata is process-local; the persisted summary anchor remains authoritative after reload.",
    ),
    AllowedDiff(("in_memory", "sqlite_reloaded"), "/summary/original_event_count",
                "Original event count exists only in the live summary cache."),
    AllowedDiff(("in_memory", "sqlite_reloaded"), "/summary/compressed_event_count",
                "Compressed event count exists only in the live summary cache."),
    AllowedDiff(("in_memory", "sqlite_reloaded"), "/summary/generation",
                "Summary generation is process-local and is unavailable after service reconstruction."),
    AllowedDiff(("in_memory", "sqlite_reloaded"), "/summary_checkpoints/0",
                "Summary checkpoints are process-local validation records."),
    AllowedDiff(("in_memory", "sqlite_reloaded"), "/summary_checkpoints/1",
                "Second-generation summary checkpoint is process-local."),
    AllowedDiff(("in_memory", "sqlite_reloaded"), "/failures/0",
                "Injected failure records belong to the replay process, not backend storage."),
)


def normalize_text(value: str) -> str:
    """Normalize semantic text without fuzzy matching."""
    normalized = unicodedata.normalize("NFKC", value).replace("\r\n", "\n").replace("\r", "\n")
    return " ".join(normalized.split())


def normalize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return a stable representation of one backend read model."""
    result = copy.deepcopy(snapshot)
    result.pop("backend", None)
    result["timeline_constraints"] = _timeline_constraints(result)
    result["events"] = _normalize_events(result.get("events", []))
    result["historical_events"] = _normalize_events(result.get("historical_events", []))
    result["memory"] = _normalize_memory(result.get("memory", {}))
    result["memory_final"] = _normalize_memory(result.get("memory_final", {}))
    summary, checkpoints = _normalize_summary_series(
        result.get("summary"),
        result.get("summary_checkpoints", []),
    )
    result["summary"] = summary
    result["summary_checkpoints"] = checkpoints
    return _normalize_value(result)


def _normalize_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    timestamps = sorted({event.get("timestamp") for event in events if event.get("timestamp") is not None})
    timestamp_ranks = {value: rank for rank, value in enumerate(timestamps)}
    normalized = []
    for event in events:
        item = copy.deepcopy(event)
        if item.get("is_summary"):
            item["id"] = SUMMARY_ANCHOR_ID
        item["long_running_tool_ids"] = sorted(item.get("long_running_tool_ids") or [])
        if "timestamp" in item:
            item["timestamp"] = timestamp_ranks[item["timestamp"]]
        normalized.append(item)
    return normalized


def _normalize_memory(memory: dict[str, Any]) -> dict[str, Any]:
    normalized = {}
    for query, response in memory.items():
        entries = []
        for entry in response.get("memories", []):
            item = copy.deepcopy(entry)
            item.pop("timestamp", None)
            entries.append(_normalize_value(item))
        normalized[query] = {"memories": sorted(entries, key=_json_sort_key)}
    return normalized


def _normalize_summary(summary: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if summary is None:
        return None
    result = copy.deepcopy(summary)
    if result.get("text"):
        result["text"] = normalize_text(result["text"])
    anchor = result.get("anchor")
    if anchor:
        result["anchor"] = _normalize_events([anchor])[0]
    return result


def _normalize_summary_series(
        summary: Optional[dict[str, Any]],
        checkpoints: list[dict[str, Any]]) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]]]:
    normalized_summary = _normalize_summary(summary)
    normalized_checkpoints = [_normalize_summary(checkpoint) for checkpoint in checkpoints]
    checkpoint_times = [item.get("updated_at") for item in normalized_checkpoints]
    checkpoint_times = [value for value in checkpoint_times if _valid_timestamp(value)]
    if normalized_summary is not None:
        summary_time = normalized_summary.get("updated_at")
        normalized_summary["updated_at"] = {
            "valid":
            _valid_timestamp(summary_time),
            "not_before_checkpoints":
            _valid_timestamp(summary_time)
            and all(summary_time >= checkpoint_time for checkpoint_time in checkpoint_times),
        }
    timestamps = sorted(set(checkpoint_times))
    ranks = {value: rank for rank, value in enumerate(timestamps)}
    for item in normalized_checkpoints:
        if item.get("updated_at") is not None:
            item["updated_at"] = ranks[item["updated_at"]]
    return normalized_summary, normalized_checkpoints


def _valid_timestamp(value: Any) -> bool:
    return isinstance(value, (int, float)) and value > 0


def _timeline_constraints(snapshot: dict[str, Any]) -> dict[str, bool]:
    events = snapshot.get("events", [])
    historical = snapshot.get("historical_events", [])
    event_values = [item.get("timestamp") for item in events if not item.get("is_summary")]
    history_values = [item.get("timestamp") for item in historical]
    summary_values = [item.get("timestamp") for item in events if item.get("is_summary")]
    current_times = [value for value in event_values if _valid_timestamp(value)]
    history_times = [value for value in history_values if _valid_timestamp(value)]
    summary_times = [value for value in summary_values if _valid_timestamp(value)]
    return {
        "events_all_valid": all(_valid_timestamp(value) for value in event_values),
        "events_monotonic": current_times == sorted(current_times),
        "history_all_valid": all(_valid_timestamp(value) for value in history_values),
        "history_monotonic": history_times == sorted(history_times),
        "history_before_current": not history_times or not current_times or max(history_times) <= min(current_times),
        "summary_all_valid": all(_valid_timestamp(value) for value in summary_values),
        "summary_not_before_history": not history_times or not summary_times
        or max(history_times) <= min(summary_times),
    }


def _normalize_value(value: Any, field_name: str = "") -> Any:
    if isinstance(value, dict):
        return {key: _normalize_value(value[key], key) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalize_value(item, field_name) for item in value]
    if isinstance(value, set):
        return sorted((_normalize_value(item) for item in value), key=_json_sort_key)
    if isinstance(value, str) and field_name.endswith("_json"):
        try:
            return _normalize_value(json.loads(value))
        except (TypeError, ValueError):
            return value
    return value


def _json_sort_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def compare_snapshots(left: dict[str, Any],
                      right: dict[str, Any],
                      *,
                      allowed_diffs: Iterable[AllowedDiff] = DEFAULT_ALLOWED_DIFFS) -> list[DiffItem]:
    """Normalize and compare two backend snapshots."""
    backend_pair = (left["backend"], right["backend"])
    raw_diffs: list[tuple[str, Any, Any]] = []
    _walk_diff(normalize_snapshot(left), normalize_snapshot(right), "", raw_diffs)
    rules = {(rule.backend_pair, rule.field_path): rule.reason for rule in allowed_diffs}
    reverse_rules = {((pair[1], pair[0]), path): reason for (pair, path), reason in rules.items()}
    rules.update(reverse_rules)
    return [_make_diff(left, backend_pair, raw_diff, rules.get((backend_pair, raw_diff[0]))) for raw_diff in raw_diffs]


def compare_persisted_snapshots(left: dict[str, Any], right: dict[str, Any]) -> list[DiffItem]:
    """Compare persisted data with exact capability rules for process-local fields."""
    left_final = _with_final_memory(left)
    right_final = _with_final_memory(right)
    return compare_snapshots(left_final, right_final)


def _with_final_memory(snapshot: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(snapshot)
    result["memory"] = result.pop("memory_final", {})
    return result


def _walk_diff(left: Any, right: Any, path: str, output: list[tuple[str, Any, Any]]) -> None:
    if type(left) is not type(right):
        output.append((path or STRUCTURAL_PATH_SEPARATOR, left, right))
        return
    if isinstance(left, dict):
        for key in sorted(set(left) | set(right)):
            child_path = f"{path}/{key}"
            if key not in left:
                output.append((child_path, None, right[key]))
            elif key not in right:
                output.append((child_path, left[key], None))
            else:
                _walk_diff(left[key], right[key], child_path, output)
        return
    if isinstance(left, list):
        for index in range(max(len(left), len(right))):
            child_path = f"{path}/{index}"
            if index >= len(left):
                output.append((child_path, None, right[index]))
            elif index >= len(right):
                output.append((child_path, left[index], None))
            else:
                _walk_diff(left[index], right[index], child_path, output)
        return
    if left != right:
        output.append((path or STRUCTURAL_PATH_SEPARATOR, left, right))


def _make_diff(snapshot: dict[str, Any], backend_pair: tuple[str, str], raw_diff: tuple[str, Any, Any],
               reason: Optional[str]) -> DiffItem:
    path, left, right = raw_diff
    category = path.strip(STRUCTURAL_PATH_SEPARATOR).split(STRUCTURAL_PATH_SEPARATOR)[0] or "snapshot"
    event_index = _path_index(path, "events")
    if event_index is None:
        event_index = _path_index(path, "historical_events")
    summary_id = _summary_reference(path, category)
    return DiffItem(
        case_id=snapshot["case_id"],
        session_id=snapshot["session_id"],
        backend_pair=backend_pair,
        category=category,
        field_path=path,
        left=left,
        right=right,
        allowed=reason is not None,
        reason=reason,
        event_index=event_index,
        summary_id=summary_id,
    )


def _path_index(path: str, category: str) -> Optional[int]:
    parts = path.strip(STRUCTURAL_PATH_SEPARATOR).split(STRUCTURAL_PATH_SEPARATOR)
    if len(parts) > 1 and parts[0] == category and parts[1].isdigit():
        return int(parts[1])
    return None


def _summary_reference(path: str, category: str) -> Optional[str]:
    if category == "summary":
        return SUMMARY_ANCHOR_ID
    checkpoint_index = _path_index(path, "summary_checkpoints")
    if checkpoint_index is not None:
        return f"{SUMMARY_ANCHOR_ID}:{checkpoint_index}"
    return None


def build_report(runs: list[dict[str, Any]],
                 comparisons: list[dict[str, Any]],
                 *,
                 skips: Optional[list[dict[str, str]]] = None,
                 detection_metrics: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Build complete stable report with clean-run metrics."""
    _validate_report_identities(runs, comparisons)
    allowed_diff_audit = _allowed_diff_audit(comparisons)
    _validate_allowed_diff_usage(comparisons, allowed_diff_audit)
    unexpected = [diff for comparison in comparisons for diff in comparison["diffs"] if not diff["allowed"]]
    false_positive_pairs = sum(1 for comparison in comparisons
                               if any(not diff["allowed"] for diff in comparison["diffs"]))
    pair_count = len(comparisons)
    metrics = {
        "case_count": len({run["case_id"]
                           for run in runs}),
        "run_count": len(runs),
        "comparison_count": pair_count,
        "unexpected_diff_count": len(unexpected),
        "false_positive_case_pairs": false_positive_pairs,
        "false_positive_rate": false_positive_pairs / pair_count if pair_count else 0.0,
    }
    metrics.update(detection_metrics or {})
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "normalization_rules": list(NORMALIZATION_RULES),
        "allowed_diff": [asdict(rule) for rule in DEFAULT_ALLOWED_DIFFS],
        "allowed_diff_audit": allowed_diff_audit,
        "runs": sorted(runs, key=lambda item: (item["case_id"], item["backend"])),
        "comparisons": sorted(comparisons, key=lambda item: (item["case_id"], item["backend_pair"])),
        "metrics": metrics,
        "skips": sorted(skips or [], key=lambda item: item["backend"]),
    }


def _allowed_diff_audit(comparisons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    audit = []
    for rule in DEFAULT_ALLOWED_DIFFS:
        hits = sum(1 for comparison in comparisons for diff in comparison["diffs"]
                   if tuple(comparison["backend_pair"]) == rule.backend_pair and diff["field_path"] == rule.field_path
                   and diff["allowed"])
        item = asdict(rule)
        item["hit_count"] = hits
        audit.append(item)
    return audit


def _validate_report_identities(runs: list[dict[str, Any]], comparisons: list[dict[str, Any]]) -> None:
    run_keys = [(run["case_id"], run["backend"]) for run in runs]
    comparison_keys = [(item["case_id"], tuple(item["backend_pair"])) for item in comparisons]
    duplicate_runs = sorted({key for key in run_keys if run_keys.count(key) > 1})
    duplicate_comparisons = sorted({key for key in comparison_keys if comparison_keys.count(key) > 1})
    if duplicate_runs or duplicate_comparisons:
        raise ValueError(f"duplicate report identities: runs={duplicate_runs}, comparisons={duplicate_comparisons}")


def _validate_allowed_diff_usage(comparisons: list[dict[str, Any]], audit: list[dict[str, Any]]) -> None:
    executed_pairs = {tuple(item["backend_pair"]) for item in comparisons}
    unused = [
        item["field_path"] for item in audit if tuple(item["backend_pair"]) in executed_pairs and item["hit_count"] == 0
    ]
    if unused:
        raise ValueError(f"unused allowed-diff rules: {sorted(unused)}")


def comparison_record(case_id: str, backend_pair: tuple[str, str], diffs: Iterable[DiffItem]) -> dict[str, Any]:
    """Serialize one comparison, including zero-diff comparisons."""
    serialized = [asdict(diff) for diff in diffs]
    serialized.sort(key=lambda item: (item["field_path"], _json_sort_key(item["left"])))
    return {
        "case_id": case_id,
        "backend_pair": list(backend_pair),
        "status": "different" if any(not item["allowed"] for item in serialized) else "consistent",
        "diffs": serialized,
    }


def run_record(snapshot: dict[str, Any], duration_seconds: float) -> dict[str, Any]:
    """Create compact per-case/backend report entry."""
    return {
        "case_id": snapshot["case_id"],
        "backend": snapshot["backend"],
        "status": "passed",
        "duration_seconds": round(duration_seconds, REPORT_DURATION_PRECISION),
        "snapshot": {
            "event_count": len(snapshot["events"]),
            "historical_event_count": len(snapshot["historical_events"]),
            "state_keys": sorted(snapshot["state"]),
            "memory_queries": sorted(snapshot["memory"] or snapshot.get("memory_final", {})),
            "summary_present": snapshot["summary"] is not None,
        },
    }


def write_report(report: dict[str, Any], target: Path) -> None:
    """Atomically write a stable UTF-8 JSON report."""
    temporary = target.with_suffix(f"{target.suffix}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=JSON_INDENT, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


Mutation = Callable[[dict[str, Any]], None]


def mutate_snapshot(snapshot: dict[str, Any], mutation: Mutation) -> dict[str, Any]:
    """Apply a read-model mutation before normalization."""
    result = copy.deepcopy(snapshot)
    mutation(result)
    return result
