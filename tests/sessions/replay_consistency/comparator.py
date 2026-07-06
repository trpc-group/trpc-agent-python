# Tencent is pleased to support the open source community by making
# tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Comparator for replay consistency testing.

Recursively compares two normalized snapshots and produces a list of
DiffEntry objects describing every divergence.
"""

from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass
class DiffEntry:
    """A single normalized field mismatch. Mirrors Go DiffEntry."""
    session_id: str | None = None
    event_index: int | None = None
    memory_id: str | None = None
    summary_id: str | None = None
    track_name: str | None = None
    section: str = ""
    path: str = ""
    left: Any = None
    right: Any = None
    allowed: bool = False
    reason: str = ""


def recursive_diff(
    left: Any,
    right: Any,
    path: str = "",
    case_name: str = "",
) -> list[DiffEntry]:
    """Recursively compare two values and return a list of diffs.

    Args:
        left: Left-side value for comparison.
        right: Right-side value for comparison.
        path: Current JSON-path for tracking.
        case_name: Optional case name for grouping.

    Returns:
        A list of DiffEntry objects describing every divergence.
    """
    diffs: list[DiffEntry] = []

    if isinstance(left, dict) and isinstance(right, dict):
        all_keys = set(left.keys()) | set(right.keys())
        for key in sorted(all_keys):
            child_path = f"{path}.{key}" if path else key
            left_val = left.get(key)
            right_val = right.get(key)
            if left_val != right_val:
                diffs.extend(
                    recursive_diff(left_val, right_val, child_path, case_name)
                )

    elif isinstance(left, list) and isinstance(right, list):
        max_len = max(len(left), len(right))
        for i in range(max_len):
            child_path = f"{path}[{i}]"
            left_val = left[i] if i < len(left) else None
            right_val = right[i] if i < len(right) else None
            if left_val != right_val:
                diffs.extend(
                    recursive_diff(left_val, right_val, child_path, case_name)
                )
    else:
        if left != right:
            section = ""
            if path:
                # Extract top-level key from path as section.
                top = path.split("[")[0].split(".")[0]
                section = top
            diffs.append(DiffEntry(
                section=section,
                path=path,
                left=left,
                right=right,
            ))

    return diffs
