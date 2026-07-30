# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool descriptions and behavioural guidance for the Task tool family."""

from __future__ import annotations

# Per-tool short descriptions fed to the model as part of each function schema.
DEFAULT_TASK_CREATE_DESCRIPTION = """\
Create a new task on the structured task board and return its assigned id.
- Use this to plan a multi-step task up front: create one task per distinct step.
- The id is assigned by the server and returned in the result; do NOT invent ids.
- Set dependencies and status afterwards with task_update.\
"""

DEFAULT_TASK_UPDATE_DESCRIPTION = """\
Incrementally update one task by id: change status, fields, owner or dependencies.
- Set status to 'in_progress' before starting a task and 'completed' the moment it is done.
- Keep at most one task 'in_progress' at a time.
- Use addBlockedBy/removeBlockedBy (or addBlocks/removeBlocks) to maintain dependencies.
- Set status to 'deleted' to remove a task; its id will not be reused.\
"""

DEFAULT_TASK_GET_DESCRIPTION = """\
Get the full details of a single task by id, including its description and dependencies.\
"""

DEFAULT_TASK_LIST_DESCRIPTION = """\
List all tasks as a compact summary (id, subject, status, owner, blockedBy).
- The summary intentionally omits descriptions to save tokens; use task_get for details.\
"""

# Long-form guidance, injected once into the system instruction.
DEFAULT_TASK_PROMPT = """\
You have access to a structured task board via the tools: task_create, task_update,
task_get and task_list. Use it to plan, track and order multi-step work.

When to use it:
  - Use the task board for work with multiple steps, dependencies between steps, or work
    that spans several turns. Plan first by creating tasks, then execute them one by one.
  - When the user gives multiple requests, create a task for each before starting.
  - Skip it for single trivial steps or purely informational questions.

How to use it:
  - Plan: call task_create once per step. The id comes back in the result — never invent ids.
  - Order: declare dependencies with task_update addBlockedBy (an upstream task must complete
    first). Do not start a task whose dependencies are unfinished.
  - Progress: before working on a task call task_update with status 'in_progress'; mark it
    'completed' the moment it is done. Keep exactly one task 'in_progress' at a time.
  - Read back: use task_list to review the board (summaries only) and task_get for the full
    details of a specific task. Do not assume task_list returns descriptions.
  - Remove a task with task_update status 'deleted'.

After calling these tools, do not repeat the whole board back to the user — just continue the
work and summarise meaningful changes.
"""

# Sentinel substring used to avoid injecting the long prompt more than once
# when several task tools are mounted on the same agent.
_PROMPT_MARKER = "structured task board via the tools: task_create"
