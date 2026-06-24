# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""State-key handling, store (de)serialisation and rendering for Task tools."""

from __future__ import annotations

from typing import Any
from typing import List

from trpc_agent_sdk.log import logger

from ._models import TaskStatus
from ._models import TaskStore

# Session-scoped (no ``temp:`` prefix, which BaseSessionService strips and
# never persists). Survives across Runner invocations.
DEFAULT_STATE_KEY_PREFIX = "tasks"


def state_key(prefix: str, branch: str) -> str:
    """Build the state key, appending ``:<branch>`` for sub-agent isolation."""
    prefix = prefix or DEFAULT_STATE_KEY_PREFIX
    return prefix if not branch else f"{prefix}:{branch}"


def decode_store(raw: Any) -> TaskStore:
    """Decode a persisted value (JSON string / dict) into a :class:`TaskStore`.

    Tolerates dirty / legacy data: anything that fails to parse degrades to
    an empty store rather than raising.
    """
    if not raw:
        return TaskStore()
    try:
        if isinstance(raw, str):
            return TaskStore.model_validate_json(raw)
        if isinstance(raw, dict):
            return TaskStore.model_validate(raw)
    except (ValueError, TypeError) as e:
        logger.warning("Task tools failed to decode persisted store: %s", e)
    return TaskStore()


def encode_store(store: TaskStore) -> str:
    """Serialise a store to a JSON string (camelCase aliases, non-ASCII kept)."""
    return store.model_dump_json(by_alias=True)


def get_task_store(
    session: Any,
    branch: str = "",
    prefix: str = DEFAULT_STATE_KEY_PREFIX,
) -> TaskStore:
    """Read the current task board for ``branch`` from a session.

    Intended for server-side / REST / audit reads. ``session`` only needs a
    ``state`` mapping attribute. Malformed data degrades to an empty store.
    """
    state = getattr(session, "state", None) or {}
    return decode_store(state.get(state_key(prefix, branch)))


def render_task_list(store: TaskStore, *, include_deleted: bool = False) -> str:
    """Render a plain-text checklist for CLIs / logs.

    Uses ``✅`` / ``🔄`` / ``⬜`` glyphs; shows blocking dependencies.
    The tools themselves never call this.
    """
    glyph = {
        TaskStatus.COMPLETED: "✅",
        TaskStatus.IN_PROGRESS: "🔄",
        TaskStatus.PENDING: "⬜",
        TaskStatus.DELETED: "—",
    }
    lines: List[str] = []
    for tid in _sorted_ids(store):
        task = store.tasks[tid]
        if task.status == TaskStatus.DELETED and not include_deleted:
            continue
        mark = glyph.get(task.status, "⬜")
        text = task.active_form if (task.status == TaskStatus.IN_PROGRESS and task.active_form) else task.subject
        suffix = f" (blocked by: {', '.join(task.blocked_by)})" if task.blocked_by else ""
        lines.append(f"{mark} #{task.id} {text}{suffix}")
    return "\n".join(lines)


def _sorted_ids(store: TaskStore) -> List[str]:

    def _key(tid: str) -> Any:
        try:
            return (0, int(tid))
        except ValueError:
            return (1, tid)

    return sorted(store.tasks.keys(), key=_key)
