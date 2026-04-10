# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Legacy skill state migration utilities.

- migrate legacy unscoped skill state keys once per session
- infer skill owners from historical tool responses
- write new scoped keys and clear old legacy keys
"""

from __future__ import annotations

import re
from typing import Any
from typing import Callable

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event

from ._constants import SKILL_DOCS_STATE_KEY_PREFIX
from ._constants import SKILL_LOADED_STATE_KEY_PREFIX
from ._constants import SkillToolsNames
from ._state_keys import docs_key
from ._state_keys import loaded_key

SKILLS_LEGACY_MIGRATION_STATE_KEY = "processor:skills:legacy_migrated"

ScopedKeysBuilder = Callable[[str, str], str]


def _state_has_key(ctx: InvocationContext, key: str) -> bool:
    if key in ctx.actions.state_delta:
        return True
    return key in ctx.session.state


def _snapshot_state(ctx: InvocationContext) -> dict[str, Any]:
    state = dict(ctx.session.state or {})
    for k, v in ctx.actions.state_delta.items():
        if v is None:
            state.pop(k, None)
        else:
            state[k] = v
    return state


def _migrate_legacy_state_key(
    ctx: InvocationContext,
    state: dict[str, Any],
    delta: dict[str, Any],
    legacy_key: str,
    legacy_val: Any,
    skill_name: str,
    owners: dict[str, str],
    build_keys: ScopedKeysBuilder,
) -> None:
    name = (skill_name or "").strip()
    if not name:
        return
    # Skip already scoped entries.
    if ":" in name:
        return

    owner = (owners.get(name, "") or "").strip()
    if not owner:
        owner = (getattr(ctx.agent, "name", "") or "").strip()
    if not owner:
        return

    temp_key = build_keys(owner, name)
    temp_existing = state.get(temp_key, None)
    if temp_existing:
        delta[legacy_key] = None
        return

    delta[temp_key] = legacy_val
    delta[legacy_key] = None


def _legacy_skill_owners(events: list[Event]) -> dict[str, str]:
    owners: dict[str, str] = {}
    for ev in reversed(events or []):
        _add_owners_from_event(ev, owners)
    return owners


def _add_owners_from_event(ev: Event, owners: dict[str, str]) -> None:
    if not ev or not ev.content or not ev.content.parts:
        return
    author = (ev.author or "").strip()
    if not author:
        return
    for part in reversed(ev.content.parts):
        fr = part.function_response
        if not fr:
            continue
        tool_name = (fr.name or "").strip()
        if tool_name not in (SkillToolsNames.LOAD, SkillToolsNames.SELECT_DOCS):
            continue
        skill_name = _skill_name_from_tool_response(fr.response)
        if not skill_name or skill_name in owners:
            continue
        owners[skill_name] = author


def _skill_name_from_tool_response(response: Any) -> str:
    """Extract skill name from tool response payload."""
    if isinstance(response, dict):
        for key in ("skill", "skill_name", "name"):
            value = response.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        result = response.get("result")
        if isinstance(result, str):
            matched = re.search(r"skill\s+'([^']+)'\s+loaded", result)
            if matched:
                return matched.group(1).strip()
    elif isinstance(response, str):
        matched = re.search(r"skill\s+'([^']+)'\s+loaded", response)
        if matched:
            return matched.group(1).strip()
    return ""


def maybe_migrate_legacy_skill_state(ctx: InvocationContext) -> None:
    """Migrate legacy skill state keys into scoped keys once.

    This function is idempotent per session via
    ``SKILLS_LEGACY_MIGRATION_STATE_KEY``.
    """
    if ctx is None or ctx.session is None:
        return
    if _state_has_key(ctx, SKILLS_LEGACY_MIGRATION_STATE_KEY):
        return
    ctx.actions.state_delta[SKILLS_LEGACY_MIGRATION_STATE_KEY] = True

    state = _snapshot_state(ctx)
    if not state:
        return
    has_loaded = any(k.startswith(SKILL_LOADED_STATE_KEY_PREFIX) for k in state.keys())
    has_docs = any(k.startswith(SKILL_DOCS_STATE_KEY_PREFIX) for k in state.keys())
    if not has_loaded and not has_docs:
        return

    owners: dict[str, str] | None = None
    delta: dict[str, Any] = {}

    for key, value in state.items():
        if value is None or value == "":
            continue
        if key.startswith(SKILL_LOADED_STATE_KEY_PREFIX):
            if owners is None:
                owners = _legacy_skill_owners(getattr(ctx.session, "events", []))
            name = key[len(SKILL_LOADED_STATE_KEY_PREFIX):].strip()
            _migrate_legacy_state_key(ctx, state, delta, key, value, name, owners, loaded_key)
        elif key.startswith(SKILL_DOCS_STATE_KEY_PREFIX):
            if owners is None:
                owners = _legacy_skill_owners(getattr(ctx.session, "events", []))
            name = key[len(SKILL_DOCS_STATE_KEY_PREFIX):].strip()
            _migrate_legacy_state_key(ctx, state, delta, key, value, name, owners, docs_key)

    if delta:
        ctx.actions.state_delta.update(delta)
