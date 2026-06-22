# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""In-session CRUD over :class:`TaskStore` with dependency maintenance.

All functions operate on an in-memory :class:`TaskStore`; persistence is
the caller's responsibility (tools write the serialised store back through
``tool_context.state``). The store is the single source of truth for one
branch's task board, so a read-modify-write of the whole blob keeps the
two-way ``blocks`` / ``blocked_by`` edges consistent.
"""

from __future__ import annotations

from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ._models import TaskListSummary
from ._models import TaskRecord
from ._models import TaskStatus
from ._models import TaskStore


def create_task(
    store: TaskStore,
    *,
    subject: str,
    description: str = "",
    active_form: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> TaskRecord:
    """Allocate a new id and insert a pending task. Mutates ``store``."""
    store.highwatermark += 1
    task_id = str(store.highwatermark)
    record = TaskRecord(
        id=task_id,
        subject=subject,
        description=description,
        active_form=active_form,
        status=TaskStatus.PENDING,
        metadata=metadata,
    )
    store.tasks[task_id] = record
    return record


def get_task(store: TaskStore, task_id: str) -> Optional[TaskRecord]:
    """Return the record for ``task_id`` or ``None``."""
    return store.tasks.get(task_id)


def list_summaries(store: TaskStore, *, include_deleted: bool = False) -> List[TaskListSummary]:
    """Return token-optimised summaries, sorted by numeric id."""
    summaries: List[TaskListSummary] = []
    for tid in _sorted_ids(store):
        task = store.tasks[tid]
        if task.status == TaskStatus.DELETED and not include_deleted:
            continue
        summaries.append(
            TaskListSummary(
                id=task.id,
                subject=task.subject,
                status=task.status,
                owner=task.owner,
                active_form=task.active_form,
                blocked_by=list(task.blocked_by),
            ))
    return summaries


def stats(store: TaskStore) -> Dict[str, int]:
    """Count non-deleted tasks by status."""
    counts = {s.value: 0 for s in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED)}
    for task in store.tasks.values():
        if task.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED):
            counts[task.status.value] += 1
    return counts


def add_blocked_by(store: TaskStore, task_id: str, upstream_ids: List[str]) -> None:
    """Add upstream dependencies, maintaining the reverse ``blocks`` edge."""
    task = store.tasks[task_id]
    for upstream in upstream_ids:
        if upstream == task_id:
            continue
        if upstream not in task.blocked_by:
            task.blocked_by.append(upstream)
        up = store.tasks.get(upstream)
        if up is not None and task_id not in up.blocks:
            up.blocks.append(task_id)


def remove_blocked_by(store: TaskStore, task_id: str, upstream_ids: List[str]) -> None:
    """Remove upstream dependencies, maintaining the reverse ``blocks`` edge."""
    task = store.tasks[task_id]
    remove = set(upstream_ids)
    task.blocked_by = [u for u in task.blocked_by if u not in remove]
    for upstream in upstream_ids:
        up = store.tasks.get(upstream)
        if up is not None and task_id in up.blocks:
            up.blocks.remove(task_id)


def add_blocks(store: TaskStore, task_id: str, downstream_ids: List[str]) -> None:
    """Add downstream blocks, maintaining the reverse ``blocked_by`` edge."""
    task = store.tasks[task_id]
    for downstream in downstream_ids:
        if downstream == task_id:
            continue
        if downstream not in task.blocks:
            task.blocks.append(downstream)
        down = store.tasks.get(downstream)
        if down is not None and task_id not in down.blocked_by:
            down.blocked_by.append(task_id)


def remove_blocks(store: TaskStore, task_id: str, downstream_ids: List[str]) -> None:
    """Remove downstream blocks, maintaining the reverse ``blocked_by`` edge."""
    task = store.tasks[task_id]
    remove = set(downstream_ids)
    task.blocks = [d for d in task.blocks if d not in remove]
    for downstream in downstream_ids:
        down = store.tasks.get(downstream)
        if down is not None and task_id in down.blocked_by:
            down.blocked_by.remove(task_id)


def clear_dependency(store: TaskStore, completed_id: str) -> List[str]:
    """Remove ``completed_id`` from every other task's ``blocked_by``.

    Returns the ids of tasks that became fully unblocked (no remaining
    ``blocked_by`` and still pending) as a result.
    """
    unblocked: List[str] = []
    for tid in _sorted_ids(store):
        task = store.tasks[tid]
        if completed_id in task.blocked_by:
            task.blocked_by.remove(completed_id)
            if not task.blocked_by and task.status == TaskStatus.PENDING:
                unblocked.append(tid)
    # The completed task no longer blocks anything.
    completed = store.tasks.get(completed_id)
    if completed is not None:
        completed.blocks = []
    return unblocked


def _sorted_ids(store: TaskStore) -> List[str]:
    """Task ids sorted numerically (ids are stringified integers)."""

    def _key(tid: str) -> Any:
        try:
            return (0, int(tid))
        except ValueError:
            return (1, tid)

    return sorted(store.tasks.keys(), key=_key)
