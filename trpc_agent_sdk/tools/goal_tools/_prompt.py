# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool descriptions and behavioural guidance for the Goal capability."""

from __future__ import annotations

# Per-tool short descriptions fed to the model as part of each function schema.
DEFAULT_GOAL_GET_DESCRIPTION = (
    "Read the current session goal, if any. Use this before deciding whether a persistent goal is active.")

DEFAULT_GOAL_CREATE_DESCRIPTION = (
    "Create a session goal for a user-requested multi-step objective that should remain active until it is"
    " completed or blocked. Do not call this for ordinary one-turn requests.")

DEFAULT_GOAL_UPDATE_DESCRIPTION = (
    "Mark the active session goal complete or blocked. Use complete only when the objective has actually been"
    " achieved. Use blocked only after the same blocking condition has repeated across goal attempts and"
    " progress cannot continue without user input or an external-state change.")

# Long-form guidance, injected once into the system instruction while a goal is
# being enforced (kept idempotent per turn via ``_GUIDANCE_MARKER``).
DEFAULT_GUIDANCE = """\
You have access to session goal tools. A goal is a durable objective for this conversation, not a \
todo list and not a generic memory entry.

Goal tools require serial semantics. In one model response, call at most one goal tool. Do not call \
create_goal and update_goal in the same response; create the goal first, then continue in a later \
model turn before marking it complete or blocked.

Use create_goal only when the user explicitly asks you to keep working toward a multi-step objective \
across model-loop boundaries, or when their request clearly requires a persistent session objective. \
Do not create goals for ordinary one-turn questions.

Use get_goal when you need to inspect the current session goal.

Use update_goal to mark the active goal complete only after the objective has actually been achieved. \
Mark it blocked only when the same blocking condition has repeated across goal attempts and you cannot \
make meaningful progress without user input or an external-state change. Do not mark a goal blocked \
merely because the work is hard, slow, uncertain, incomplete, or would benefit from clarification.

While a goal is active, a final answer is not enough. Either continue working, or call update_goal \
with complete or blocked.\
"""

# Sentinel substring used to avoid injecting the long guidance more than once.
_GUIDANCE_MARKER = "You have access to session goal tools."

# Nudge appended (as a user-role message) when re-running after a premature
# final response. ``attempt`` / ``max_retries`` / ``objective`` are filled in.
DEFAULT_NUDGE = """\
[goal reminder] You marked your response as final, but the session goal is still active \
(attempt {attempt} of {max_retries}).

Active goal:
{objective}

You must either continue working toward the goal, or call update_goal with status complete or \
blocked. Use blocked only when the same blocking condition has repeated across goal attempts and \
you cannot make meaningful progress without user input or an external-state change. Do not produce \
a final answer while the goal remains active.\
"""
