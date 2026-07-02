# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Goal tool family — persistent, cross-turn session objective.

A goal lets a user set a single objective that survives across LLM calls:
while it is ``active`` a "looks final" response does not end the task — the
model must keep working or explicitly mark the goal ``complete`` / ``blocked``.

The capability is a lightweight bundle on a single :class:`LlmAgent`:
``GoalToolSet`` (three model tools) + a pair of model callbacks that inject
guidance / nudges and intercept premature final responses to re-run within the
same invocation. Mount everything in one call with :func:`setup_goal`.
"""

from ._setup import GoalOptions
from ._setup import OnRetry
from ._setup import RetryEvent
from ._setup import setup_goal
from ._goal_create_tool import GoalCreateTool
from ._goal_get_tool import GoalGetTool
from ._goal_toolset import GoalToolSet
from ._goal_update_tool import GoalUpdateTool
from ._helpers import DEFAULT_STATE_KEY_PREFIX
from ._helpers import decode_goal
from ._helpers import encode_goal
from ._helpers import get_goal_record
from ._helpers import render_goal
from ._helpers import start_goal
from ._helpers import state_key
from ._models import GoalRecord
from ._models import GoalStatus
from ._prompt import DEFAULT_GUIDANCE
from ._prompt import DEFAULT_NUDGE

__all__ = [
    "GoalStatus",
    "GoalRecord",
    "GoalToolSet",
    "GoalGetTool",
    "GoalCreateTool",
    "GoalUpdateTool",
    "GoalOptions",
    "RetryEvent",
    "OnRetry",
    "setup_goal",
    "get_goal_record",
    "decode_goal",
    "encode_goal",
    "start_goal",
    "state_key",
    "render_goal",
    "DEFAULT_STATE_KEY_PREFIX",
    "DEFAULT_GUIDANCE",
    "DEFAULT_NUDGE",
]
