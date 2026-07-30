# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Task tool family.

Structured, incrementally-updated task board aligned with Claude Code's
``TaskCreate`` / ``TaskUpdate`` / ``TaskGet`` / ``TaskList`` tools. Tasks are
identified by server-assigned ids, support ``blockedBy`` / ``blocks``
dependency edges, and are persisted to branch-scoped session state.

This complements :mod:`trpc_agent_sdk.tools._todo_tool` (whole-list
replacement); mount one or the other depending on the use case.
"""

from ._helpers import DEFAULT_STATE_KEY_PREFIX
from ._helpers import decode_store
from ._helpers import encode_store
from ._helpers import get_task_store
from ._helpers import render_task_list
from ._helpers import state_key
from ._models import TaskListSummary
from ._models import TaskRecord
from ._models import TaskStatus
from ._models import TaskStore
from ._prompt import DEFAULT_TASK_CREATE_DESCRIPTION
from ._prompt import DEFAULT_TASK_GET_DESCRIPTION
from ._prompt import DEFAULT_TASK_LIST_DESCRIPTION
from ._prompt import DEFAULT_TASK_PROMPT
from ._prompt import DEFAULT_TASK_UPDATE_DESCRIPTION
from ._store import clear_dependency
from ._store import create_task
from ._store import list_summaries
from ._task_create_tool import TaskCreateTool
from ._task_get_tool import TaskGetTool
from ._task_list_tool import TaskListTool
from ._task_toolset import TaskToolSet
from ._task_update_tool import TaskUpdateTool
from ._validators import detect_cycle
from ._validators import validate_status

__all__ = [
    "TaskStatus",
    "TaskRecord",
    "TaskStore",
    "TaskListSummary",
    "TaskCreateTool",
    "TaskUpdateTool",
    "TaskGetTool",
    "TaskListTool",
    "TaskToolSet",
    "get_task_store",
    "decode_store",
    "encode_store",
    "state_key",
    "render_task_list",
    "create_task",
    "list_summaries",
    "clear_dependency",
    "detect_cycle",
    "validate_status",
    "DEFAULT_STATE_KEY_PREFIX",
    "DEFAULT_TASK_PROMPT",
    "DEFAULT_TASK_CREATE_DESCRIPTION",
    "DEFAULT_TASK_UPDATE_DESCRIPTION",
    "DEFAULT_TASK_GET_DESCRIPTION",
    "DEFAULT_TASK_LIST_DESCRIPTION",
]
