# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""递归比较器:单一 ``visit`` 处理 dict / list / 叶子,产出带内联定位的 DiffEntry。

dict 按 sorted keys 对齐,list 按下标(长度差补 ``<missing>``),叶子严格相等。
定位字段(session_id / event_index / summary_id)在递归时内联写入。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .allowed_diff import is_allowed
from .harness import AllowedDiffRule
from .harness import ReplaySnapshot

MISSING = "<missing>"


class DiffEntry(BaseModel):
    session_id: str | None = None
    event_index: int | None = None
    summary_id: str | None = None
    field_path: str
    reference_backend: str
    candidate_backend: str
    reference_value: Any
    candidate_value: Any
    allowed: bool = False
    reason: str | None = None


def _format_path(path: list[Any]) -> str:
    out = ""
    for seg in path:
        if isinstance(seg, int):
            out += f"[{seg}]"
        else:
            out += f".{seg}" if out else str(seg)
    return out


def _make_diff(path: list[Any], left: Any, right: Any, ctx: dict[str, Any]) -> DiffEntry:
    field_path = _format_path(path)
    allowed, reason = is_allowed(field_path, ctx["backend_pair"], ctx["allowed_diff"])
    return DiffEntry(
        session_id=ctx["session_id"],
        event_index=ctx.get("event_index"),
        summary_id=ctx.get("summary_id"),
        field_path=field_path,
        reference_backend=ctx["reference_backend"],
        candidate_backend=ctx["candidate_backend"],
        reference_value=left,
        candidate_value=right,
        allowed=allowed,
        reason=reason,
    )


def _visit(left: Any, right: Any, path: list[Any], ctx: dict[str, Any], diffs: list[DiffEntry]) -> None:
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            if key not in left:
                diffs.append(_make_diff(path + [key], MISSING, right[key], ctx))
            elif key not in right:
                diffs.append(_make_diff(path + [key], left[key], MISSING, ctx))
            else:
                _visit(left[key], right[key], path + [key], ctx, diffs)
    elif isinstance(left, list) and isinstance(right, list):
        for i in range(max(len(left), len(right))):
            child_ctx = dict(ctx)
            if path and path[-1] in ("events", "historical_events"):
                child_ctx["event_index"] = i
            if i >= len(left):
                diffs.append(_make_diff(path + [i], MISSING, right[i], child_ctx))
            elif i >= len(right):
                diffs.append(_make_diff(path + [i], left[i], MISSING, child_ctx))
            else:
                _visit(left[i], right[i], path + [i], child_ctx, diffs)
    else:
        if left != right:
            diffs.append(_make_diff(path, left, right, ctx))


def compare_snapshots(
    reference: ReplaySnapshot,
    candidate: ReplaySnapshot,
    *,
    reference_backend: str,
    candidate_backend: str,
    allowed_diff: list[AllowedDiffRule],
) -> list[DiffEntry]:
    """比较两个归一化后的快照,返回差异列表(已标注 allowed)。"""
    base_ctx: dict[str, Any] = {
        "session_id": reference.session_id,
        "event_index": None,
        "summary_id": None,
        "backend_pair": (reference_backend, candidate_backend),
        "reference_backend": reference_backend,
        "candidate_backend": candidate_backend,
        "allowed_diff": allowed_diff,
    }
    diffs: list[DiffEntry] = []
    _visit(reference.events, candidate.events, ["events"], base_ctx, diffs)
    _visit(reference.historical_events, candidate.historical_events, ["historical_events"], base_ctx, diffs)
    _visit(reference.state, candidate.state, ["state"], base_ctx, diffs)
    _visit(reference.memory, candidate.memory, ["memory"], base_ctx, diffs)

    summary_ctx = dict(base_ctx)
    ref_current = reference.summary.get("current") if reference.summary else None
    if ref_current:
        summary_ctx["summary_id"] = f"{reference.session_id}:summary"
    _visit(reference.summary, candidate.summary, ["summary"], summary_ctx, diffs)
    return diffs
