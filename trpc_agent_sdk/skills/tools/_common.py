# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared helpers for skill/workspace tools."""

from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import TypeVar

from trpc_agent_sdk.context import InvocationContext

T = TypeVar("T")

CreateWorkspaceNameCallback = Callable[[InvocationContext], str]
"""Callback to create a workspace name."""


def default_create_ws_name_callback(ctx: InvocationContext) -> str:
    """Default callback to create a workspace name."""
    return ctx.session.id


def require_non_empty(value: str, *, field_name: str) -> str:
    """Validate a required string field and return trimmed value."""
    normalized = (value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


async def put_session(
    sessions: dict[str, T],
    lock: asyncio.Lock,
    sid: str,
    session: T,
    gc: Callable[[], Awaitable[None]],
) -> None:
    """Insert session after running gc in lock."""
    async with lock:
        await gc()
        sessions[sid] = session


async def get_session(
    sessions: dict[str, T],
    lock: asyncio.Lock,
    sid: str,
    gc: Callable[[], Awaitable[None]],
) -> T:
    """Lookup session after running gc in lock."""
    async with lock:
        await gc()
        session = sessions.get(sid)
    if session is None:
        raise ValueError(f"unknown session_id: {sid}")
    return session


async def remove_session(
    sessions: dict[str, T],
    lock: asyncio.Lock,
    sid: str,
    gc: Callable[[], Awaitable[None]],
) -> T:
    """Remove session after running gc in lock."""
    async with lock:
        await gc()
        session = sessions.pop(sid, None)
    if session is None:
        raise ValueError(f"unknown session_id: {sid}")
    return session


async def _await_if_needed(value: object) -> None:
    if inspect.isawaitable(value):
        await value


async def cleanup_expired_sessions(
    sessions: dict[str, T],
    *,
    ttl: float,
    refresh_exit_state: Callable[[T, float], object],
    close_session: Callable[[T], object],
) -> None:
    """Refresh exit state and evict expired sessions in-place."""
    if ttl <= 0:
        return
    now = time.time()
    expired: list[str] = []
    for sid, session in sessions.items():
        await _await_if_needed(refresh_exit_state(session, now))
        exited_at = getattr(session, "exited_at", None)
        if exited_at is not None and (now - exited_at) >= ttl:
            expired.append(sid)

    for sid in expired:
        session = sessions.pop(sid, None)
        if session is None:
            continue
        try:
            await _await_if_needed(close_session(session))
        except Exception:  # pylint: disable=broad-except
            # Best-effort cleanup: mirror Go behavior and keep evicting others.
            pass


def inline_json_schema_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline $ref references in JSON Schema by replacing them with actual definitions."""
    defs = schema.get('$defs', {})
    if not defs:
        return schema

    def resolve_ref(obj: Any) -> Any:
        if isinstance(obj, dict):
            if '$ref' in obj:
                ref_path = obj['$ref']
                if ref_path.startswith('#/$defs/'):
                    ref_name = ref_path.replace('#/$defs/', '')
                    if ref_name in defs:
                        resolved = resolve_ref(defs[ref_name])
                        merged = {**resolved, **{k: v for k, v in obj.items() if k != '$ref'}}
                        return merged
                return obj
            else:
                return {k: resolve_ref(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [resolve_ref(item) for item in obj]
        else:
            return obj

    result = {k: v for k, v in schema.items() if k != '$defs'}
    result = resolve_ref(result)
    return result


SKILL_STAGED_WORKSPACE_DIR_KEY = "__trpc_agent_skills_staged_workspace_dir"
"""Key for staged workspace directory."""


def get_staged_workspace_dir(ctx: InvocationContext, skill_name: str) -> str:
    """Get the staged workspace directory."""
    dir_map = ctx.agent_context.get_metadata(SKILL_STAGED_WORKSPACE_DIR_KEY, {})
    return dir_map.get(skill_name, "")


def set_staged_workspace_dir(ctx: InvocationContext, skill_name: str, dir: str) -> None:
    """Set the staged workspace directory."""
    dir_map = ctx.agent_context.get_metadata(SKILL_STAGED_WORKSPACE_DIR_KEY, {})
    dir_map[skill_name] = dir
    ctx.agent_context.with_metadata(SKILL_STAGED_WORKSPACE_DIR_KEY, dir_map)
