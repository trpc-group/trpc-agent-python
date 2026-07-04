"""Recursive snapshot comparator with structured diff entries."""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
import re
from typing import Any

from .normalizer import Snapshot


@dataclass
class DiffEntry:
    case_name: str
    left_backend: str
    right_backend: str
    session_id: str | None
    event_index: int | None
    memory_index: int | None
    summary_id: str | None
    section: str
    path: str
    left: Any
    right: Any
    allowed: bool
    reason: str


_MISSING = object()


def _to_dict(value: Any) -> Any:
    if isinstance(value, Snapshot):
        return asdict(value)
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    return value


def _infer_section(path: str) -> str:
    if not path:
        return "root"
    root = path.split(".", 1)[0].split("[", 1)[0]
    if root in {"events", "state", "memories", "summary", "historical_events", "list_sessions"}:
        return root
    return root


def _parse_index(path: str, section: str) -> int | None:
    match = re.search(rf"{section}\[(\d+)\]", path)
    return int(match.group(1)) if match else None


def _summary_id(left: dict[str, Any], right: dict[str, Any], section: str) -> str | None:
    if section != "summary":
        return None
    for snapshot in (left, right):
        summary = snapshot.get("summary")
        if isinstance(summary, dict):
            metadata = summary.get("metadata") or {}
            session_id = metadata.get("session_id")
            if session_id:
                return f"summary:{session_id}:latest"
    session_id = left.get("session_id") or right.get("session_id")
    return f"summary:{session_id}:latest" if session_id else None


def _allowed_diff(path: str, left: Any, right: Any) -> tuple[bool, str]:
    if path == "backend":
        return True, "backend name differs by design"
    if path.endswith(".timestamp") or path == "timestamp":
        return True, "raw timestamps are backend generated"
    if path.endswith(".has_timestamp") and left is True and right is True:
        return True, "timestamp presence is normalized"
    return False, ""


def _display(value: Any) -> Any:
    if value is _MISSING:
        return "<missing>"
    return value


def _entry(path: str, left_value: Any, right_value: Any, left: dict[str, Any], right: dict[str, Any]) -> DiffEntry:
    section = _infer_section(path)
    allowed, reason = _allowed_diff(path, left_value, right_value)
    event_index = _parse_index(path, "events")
    if event_index is None:
        event_index = _parse_index(path, "historical_events")
    return DiffEntry(
        case_name=left.get("case_name") or right.get("case_name") or "",
        left_backend=left.get("backend") or "",
        right_backend=right.get("backend") or "",
        session_id=left.get("session_id") or right.get("session_id"),
        event_index=event_index,
        memory_index=_parse_index(path, "memories"),
        summary_id=_summary_id(left, right, section),
        section=section,
        path=path,
        left=_display(left_value),
        right=_display(right_value),
        allowed=allowed,
        reason=reason,
    )


def _join_path(parent: str, key: str) -> str:
    return f"{parent}.{key}" if parent else key


def _diff_values(current_left: Any,
                 current_right: Any,
                 root_left: dict[str, Any],
                 root_right: dict[str, Any],
                 path: str) -> list[DiffEntry]:
    if isinstance(current_left, dict) and isinstance(current_right, dict):
        diffs: list[DiffEntry] = []
        for key in sorted(set(current_left) | set(current_right)):
            next_left = current_left.get(key, _MISSING)
            next_right = current_right.get(key, _MISSING)
            next_path = _join_path(path, str(key))
            if next_left is _MISSING or next_right is _MISSING:
                diffs.append(_entry(next_path, next_left, next_right, root_left, root_right))
            else:
                diffs.extend(_diff_values(next_left, next_right, root_left, root_right, next_path))
        return diffs

    if isinstance(current_left, list) and isinstance(current_right, list):
        diffs = []
        max_len = max(len(current_left), len(current_right))
        for index in range(max_len):
            next_path = f"{path}[{index}]"
            next_left = current_left[index] if index < len(current_left) else _MISSING
            next_right = current_right[index] if index < len(current_right) else _MISSING
            if next_left is _MISSING or next_right is _MISSING:
                diffs.append(_entry(next_path, next_left, next_right, root_left, root_right))
            else:
                diffs.extend(_diff_values(next_left, next_right, root_left, root_right, next_path))
        return diffs

    if current_left != current_right:
        return [_entry(path, current_left, current_right, root_left, root_right)]
    return []


def recursive_diff(left: Any, right: Any, context: dict[str, Any] | None = None) -> list[DiffEntry]:
    left_dict = _to_dict(left)
    right_dict = _to_dict(right)
    if not isinstance(left_dict, dict) or not isinstance(right_dict, dict):
        raise TypeError("recursive_diff expects Snapshot, dataclass, or dict inputs")

    diffs = _diff_values(left_dict, right_dict, left_dict, right_dict, "")
    if context:
        for diff in diffs:
            if context.get("case_name"):
                diff.case_name = context["case_name"]
            if context.get("left_backend"):
                diff.left_backend = context["left_backend"]
            if context.get("right_backend"):
                diff.right_backend = context["right_backend"]
    return diffs


def compare_snapshot_pair(left: Snapshot, right: Snapshot) -> list[DiffEntry]:
    return recursive_diff(left, right)


def unallowed_diffs(diffs: list[DiffEntry]) -> list[DiffEntry]:
    return [diff for diff in diffs if not diff.allowed]
