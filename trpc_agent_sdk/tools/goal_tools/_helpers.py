# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""State-key handling, (de)serialisation and rendering for the Goal capability."""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING
from typing import Any
from typing import Optional

from trpc_agent_sdk.log import logger

from ._models import GoalRecord
from ._models import GoalStatus

if TYPE_CHECKING:
    from trpc_agent_sdk.abc import SessionServiceABC

# Session-scoped (no ``temp:`` prefix, which BaseSessionService strips and
# never persists). Survives across Runner invocations and can be resumed.
DEFAULT_STATE_KEY_PREFIX = "goal"


def state_key(prefix: str, branch: str) -> str:
    """Build the state key, appending ``:<branch>`` for sub-agent isolation."""
    prefix = prefix or DEFAULT_STATE_KEY_PREFIX
    return prefix if not branch else f"{prefix}:{branch}"


def decode_goal(raw: Any) -> Optional[GoalRecord]:
    """Decode a persisted value (JSON string / dict) into a :class:`GoalRecord`.

    Tolerates dirty / legacy data: anything that fails to parse degrades to
    ``None`` (i.e. "no goal") rather than raising.
    """
    if not raw:
        return None
    try:
        if isinstance(raw, str):
            return GoalRecord.model_validate_json(raw)
        if isinstance(raw, dict):
            return GoalRecord.model_validate(raw)
    except (ValueError, TypeError) as e:
        logger.warning("Goal tools failed to decode persisted goal: %s", e)
    return None


def encode_goal(goal: GoalRecord) -> str:
    """Serialise a goal to a JSON string (camelCase aliases, non-ASCII kept)."""
    return goal.model_dump_json(by_alias=True)


def get_goal_record(
    session: Any,
    branch: str = "",
    prefix: str = DEFAULT_STATE_KEY_PREFIX,
) -> Optional[GoalRecord]:
    """Read the current goal for ``branch`` from a session.

    Intended for server-side / REST / audit reads. ``session`` only needs a
    ``state`` mapping attribute. Malformed data degrades to ``None``.
    """
    state = getattr(session, "state", None) or {}
    return decode_goal(state.get(state_key(prefix, branch)))


async def start_goal(
    session_service: "SessionServiceABC",
    *,
    app_name: str,
    user_id: str,
    session_id: str,
    objective: str,
    state_key_prefix: str = DEFAULT_STATE_KEY_PREFIX,
    agent_name: str = "",
) -> GoalRecord:
    """Create or replace the active goal for a session from application code.

    This is the application-layer counterpart to the model-callable
    ``create_goal`` tool. Call it when *your code* (not the LLM) owns the
    objective definition — for example after parsing a ``/goal <objective>``
    slash command, reading it from a config file, or setting it
    programmatically before the first turn.

    The goal is written directly into ``session.state`` and immediately
    visible to the enforcement callbacks on the next ``Runner.run_async``
    call; the model does **not** need to call ``create_goal``.

    Mirrors Go's ``goal.Start(ctx, service, key, objective, ...)``.

    Args:
        session_service: The session service holding the target session.
        app_name: Application name (must match the runner's ``app_name``).
        user_id: User id (must match the runner's ``user_id``).
        session_id: Existing session id to inject the goal into.  If the
            session does not yet exist it is created automatically.
        objective: Non-empty completion criterion — what "done" concretely
            means.
        state_key_prefix: State-key prefix (``goal`` by default). Must match
            the prefix configured on :class:`GoalOptions`; avoid ``temp:``.
        agent_name: Agent name used to scope the state key (matches the
            ``LlmAgent.name`` the goal extension is mounted on).

    Returns:
        The newly created :class:`GoalRecord` with ``status=active``.

    Raises:
        ValueError: If ``objective`` is empty.
    """
    objective = objective.strip()
    if not objective:
        raise ValueError("start_goal: objective must be a non-empty string")

    now = int(time.time())
    goal = GoalRecord(
        id=uuid.uuid4().hex,
        objective=objective,
        status=GoalStatus.ACTIVE,
        created_at_unix=now,
        updated_at_unix=now,
    )
    skey = state_key(state_key_prefix, agent_name)
    encoded = encode_goal(goal)

    session = await session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    if session is None:
        await session_service.create_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            state={skey: encoded},
        )
    else:
        session.state[skey] = encoded
        await session_service.update_session(session)

    return goal


def render_goal(goal: Optional[GoalRecord]) -> str:
    """Render a compact ASCII status card for CLIs / logs.

    The tools themselves never call this.
    """
    if goal is None:
        return "(no goal)"
    glyph = {
        GoalStatus.ACTIVE: "🎯",
        GoalStatus.COMPLETE: "✅",
        GoalStatus.BLOCKED: "⛔",
    }.get(goal.status, "🎯")
    lines = [
        f"{glyph} Goal [{goal.status.value}]",
        f"   objective: {goal.objective}",
        f"   created:   {goal.created_at_unix}",
    ]
    if goal.terminal_at_unix is not None:
        lines.append(f"   terminal:  {goal.terminal_at_unix}")
    return "\n".join(lines)
