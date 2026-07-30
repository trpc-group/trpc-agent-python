# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Hard-contract validators for the Task tool family.

These enforce structural invariants in code (well-formed input, valid
status transitions, acyclic dependency graph, optional single
``in_progress``). Softer style guidance lives in :mod:`._prompt`.
"""

from __future__ import annotations

from typing import List
from typing import Optional

from ._models import TaskRecord
from ._models import TaskStatus
from ._models import TaskStore

# Statuses a model may set via task_update.
_ASSIGNABLE_STATUSES = {
    TaskStatus.PENDING,
    TaskStatus.IN_PROGRESS,
    TaskStatus.COMPLETED,
    TaskStatus.DELETED,
}


def validate_status(status: str) -> Optional[str]:
    """Return an error string if ``status`` is not assignable, else ``None``."""
    try:
        parsed = TaskStatus(status)
    except ValueError:
        valid = ", ".join(s.value for s in _ASSIGNABLE_STATUSES)
        return f"invalid status {status!r}; must be one of: {valid}"
    if parsed not in _ASSIGNABLE_STATUSES:
        return f"status {status!r} is not assignable"
    return None


def validate_single_in_progress(store: TaskStore, exclude_id: Optional[str] = None) -> Optional[str]:
    """Return an error if more than one non-deleted task is ``in_progress``.

    ``exclude_id`` is ignored when counting so the caller can validate a
    prospective state for a single task it is about to update.
    """
    in_progress = [tid for tid, t in store.tasks.items() if t.status == TaskStatus.IN_PROGRESS and tid != exclude_id]
    if len(in_progress) > 1:
        return f"at most one task may be in_progress (found {in_progress})"
    return None


def detect_cycle(store: TaskStore) -> Optional[str]:
    """Detect a cycle in the ``blocked_by`` dependency graph.

    Edge semantics: ``A.blocked_by = [B]`` means B must complete before A,
    i.e. a directed edge ``A -> B``. A cycle means a circular dependency
    that can never be satisfied. Deleted tasks are skipped. Returns an
    error string naming a task on the cycle, or ``None`` when acyclic.
    """
    # 0 = unvisited, 1 = on current DFS stack, 2 = fully explored.
    color: dict[str, int] = {}

    def visit(node: str) -> Optional[str]:
        task = store.tasks.get(node)
        if task is None or task.status == TaskStatus.DELETED:
            color[node] = 2
            return None
        color[node] = 1
        for dep in task.blocked_by:
            state = color.get(dep, 0)
            if state == 1:
                return f"dependency cycle detected involving task {dep!r}"
            if state == 0:
                err = visit(dep)
                if err is not None:
                    return err
        color[node] = 2
        return None

    for tid in store.tasks:
        if color.get(tid, 0) == 0:
            err = visit(tid)
            if err is not None:
                return err
    return None


def validate_dependencies_exist(store: TaskStore, ids: List[str]) -> Optional[str]:
    """Return an error if any id in ``ids`` is missing or deleted."""
    for tid in ids:
        task = store.tasks.get(tid)
        if task is None:
            return f"referenced task {tid!r} does not exist"
        if task.status == TaskStatus.DELETED:
            return f"referenced task {tid!r} is deleted"
    return None


def validate_task(task: TaskRecord) -> Optional[str]:
    """Validate a single record's basic field contract."""
    if not task.subject or not task.subject.strip():
        return "subject must not be empty"
    return None
