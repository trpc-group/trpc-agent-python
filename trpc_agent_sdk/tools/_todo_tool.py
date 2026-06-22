# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""TodoWrite tool for TRPC Agent framework.

Provides a client-side :class:`TodoWriteTool` that lets an LLM agent
plan, track and report multi-step tasks via a single structured
checklist. The tool follows the Claude Code / DeepAgents "whole-list
replacement" model:

- The model sends the **complete, updated** todo list on every call; the
  new list entirely replaces the previous one (no smart merge).
- The list is persisted in **session-level state** (key prefix ``todos``,
  no ``temp:`` prefix) so it survives across ``Runner.run_async``
  invocations. Keys with the ``temp:`` prefix are stripped by
  :class:`~trpc_agent_sdk.sessions.BaseSessionService` and never land in
  storage.
- Different branches (parent / sub agents) keep **independent** lists.

The tool enforces a small set of *hard contracts* (well-formed input,
at most one ``in_progress`` item, unique ``content``) in code; softer
style guidance lives in :data:`DEFAULT_TODO_PROMPT`, appended to the
system instruction automatically via :meth:`TodoWriteTool.process_request`.

The function-response payload echoes ``{message, todos, oldTodos}`` so a
front-end / CLI can render the current list and a diff without re-reading
session state.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any
from typing import Callable
from typing import List
from typing import Optional
from typing_extensions import override

from pydantic import BaseModel
from pydantic import Field
from pydantic import ValidationError

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

from ._base_tool import BaseTool

# Default tool name. ``snake_case`` satisfies the strict
# ``^[a-zA-Z0-9_-]+$`` function-name constraint some providers enforce.
_DEFAULT_TOOL_NAME = "todo_write"
# Default state-key prefix. Session-scoped (no ``temp:``) so
# BaseSessionService persists the list across Runner invocations.
DEFAULT_STATE_KEY_PREFIX = "todos"
# Default nudge appended to every successful response to keep the model
# anchored on the plan.
DEFAULT_NUDGE_MESSAGE = ("Todo list updated. Keep exactly one item in_progress, mark items completed "
                         "the moment they are done, and call todo_write again whenever the plan changes.")

# Short description fed to the model as part of the function schema.
DEFAULT_TODO_DESCRIPTION = """\
Create and manage a structured todo list for the current task.
- Call this to plan a multi-step task up front, then to flip each item's status as you make progress.
- Send the COMPLETE, updated list every time; it replaces the previous list entirely.
- Keep AT MOST ONE item `in_progress` at any moment; mark an item `completed` as soon as it is finished.
- The only way to clear the list is to send an explicit empty array (`todos: []`).

USE WHEN:
  - the task has 3+ distinct steps, or is non-trivial and benefits from explicit planning
  - the user provides multiple tasks, or you discover follow-up work mid-task

DO NOT USE WHEN:
  - the task is a single trivial step, or is purely conversational/informational\
"""

# Long-form guidance appended via :meth:`TodoWriteTool.process_request`.
# Kept separate from the hard contract enforced by :func:`validate_todos`.
# Still exported for tests and callers who need the raw text.
DEFAULT_TODO_PROMPT = """\
You have access to the `todo_write` tool to plan and track multi-step work.

When to use it:
  - Use it for any task with 3+ distinct steps, multi-file changes, or non-trivial work that benefits
    from explicit planning. Plan first, then execute item by item.
  - When the user gives multiple requests, capture each as a todo item before starting.
  - Skip it for single trivial steps or purely informational questions.

How to use it:
  - Always send the COMPLETE, updated list. The new list replaces the old one entirely.
  - Each item has `content` (imperative, e.g. "Run tests"), `activeForm` (present-continuous, e.g.
    "Running tests"), and `status` (one of `pending`, `in_progress`, `completed`).
  - Keep exactly one item `in_progress` while you work on it; never leave a stale `in_progress`.
  - Mark an item `completed` the moment it is done — do not batch completions at the end.
  - To start a new step, set the previous one to `completed` and the next one to `in_progress` in the
    same call.
  - To clear the list, send `todos: []` (an explicit empty array).

After calling `todo_write`, do not repeat the whole list back to the user — just continue the work and
summarise meaningful changes.
"""

# Optional policy callback. Invoked after persistence, before returning;
# any non-empty string it returns is appended to the response message.
# Hooks are read-only and MUST NOT mutate the lists.
NudgeHook = Callable[[List["TodoItem"], List["TodoItem"]], Optional[str]]


class TodoStatus(str, Enum):
    """Lifecycle state of a single todo item."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class TodoItem(BaseModel):
    """A single todo entry.

    ``active_form`` is exposed to the model / persisted under the
    camelCase alias ``activeForm`` to stay compatible with the Go
    implementation and Claude Code's schema.
    """

    content: str = Field(description="Imperative description of the task, e.g. 'Run tests'.")
    active_form: str = Field(
        alias="activeForm",
        description="Present-continuous form shown while active, e.g. 'Running tests'.",
    )
    status: TodoStatus = Field(description="One of: pending, in_progress, completed.")

    model_config = {"populate_by_name": True}


def validate_todos(todos: List[TodoItem]) -> Optional[str]:
    """Enforce the hard contract on a todo list.

    Returns an error string when the list is invalid, or ``None`` when it
    passes. Rules:

    - ``content`` and ``activeForm`` must be non-empty.
    - At most one item may be ``in_progress``.
    - ``content`` must be unique across the list (exact match; no trim /
      case folding, to avoid silently merging items).
    """
    in_progress = 0
    seen: dict[str, int] = {}
    for i, item in enumerate(todos):
        if not item.content or not item.content.strip():
            return f"todos[{i}].content must not be empty"
        if not item.active_form or not item.active_form.strip():
            return f"todos[{i}].activeForm must not be empty"
        if item.status == TodoStatus.IN_PROGRESS:
            in_progress += 1
            if in_progress > 1:
                return f"at most one item may be in_progress (second one at todos[{i}])"
        if item.content in seen:
            return f"todos[{i}].content {item.content!r} duplicates todos[{seen[item.content]}]"
        seen[item.content] = i
    return None


def state_key(prefix: str, branch: str) -> str:
    """Build the state key, appending ``:<branch>`` for sub-agent isolation."""
    prefix = prefix or DEFAULT_STATE_KEY_PREFIX
    return prefix if not branch else f"{prefix}:{branch}"


def _decode_todos(raw: Any) -> List[TodoItem]:
    """Decode a persisted value (JSON string or list) into ``TodoItem``s.

    Tolerates dirty / legacy data: anything that fails to parse is
    treated as an empty list rather than raising.
    """
    if not raw:
        return []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(data, list):
            return []
        return [TodoItem.model_validate(x) for x in data]
    except (ValueError, ValidationError, TypeError) as e:
        logger.warning("TodoWriteTool failed to decode persisted todos: %s", e)
        return []


def get_todos(
    session: Any,
    branch: str = "",
    prefix: str = DEFAULT_STATE_KEY_PREFIX,
) -> List[TodoItem]:
    """Read the current todo list for ``branch`` from a session.

    Intended for server-side / REST / audit reads. ``session`` only needs
    a ``state`` mapping attribute. Malformed data degrades to ``[]``.
    """
    state = getattr(session, "state", None) or {}
    return _decode_todos(state.get(state_key(prefix, branch)))


def render_todos(todos: List[TodoItem]) -> str:
    """Render a plain-text checklist (``✅`` / ``🔄`` / ``⬜``).

    Convenience for CLIs / logs; the tool itself never calls this.
    """
    glyph = {
        TodoStatus.COMPLETED: "✅",
        TodoStatus.IN_PROGRESS: "🔄",
        TodoStatus.PENDING: "⬜",
    }
    lines = []
    for item in todos:
        mark = glyph.get(item.status, "⬜")
        text = item.active_form if item.status == TodoStatus.IN_PROGRESS else item.content
        lines.append(f"{mark} {text}")
    return "\n".join(lines)


class TodoWriteTool(BaseTool):
    """LLM tool that maintains a structured, persistent todo checklist.

    The model sends the complete updated list on each call; the tool
    validates it, persists it to branch-scoped session state, and returns
    a nudge plus the new and previous lists for downstream rendering.

    Args:
        state_key_prefix: State-key prefix; ``todos`` by default. Avoid
            ``temp:`` — that prefix is invocation-only and is not stored.
        clear_on_all_done: When every item is ``completed``, store an empty
            list instead so finished items do not pile up across turns
            (default ``True``).
        default_nudge: Base message appended to every successful response.
        nudge_hooks: Optional read-only policy callbacks; each returned
            non-empty string is appended to the response message.
        filters_name / filters: forwarded to :class:`BaseTool`.
    """

    def __init__(
        self,
        *,
        state_key_prefix: str = DEFAULT_STATE_KEY_PREFIX,
        clear_on_all_done: bool = True,
        default_nudge: str = DEFAULT_NUDGE_MESSAGE,
        nudge_hooks: Optional[List[NudgeHook]] = None,
        filters_name: Optional[List[str]] = None,
        filters: Optional[List[BaseFilter]] = None,
    ) -> None:
        super().__init__(
            name=_DEFAULT_TOOL_NAME,
            description=DEFAULT_TODO_DESCRIPTION,
            filters_name=filters_name,
            filters=filters,
        )
        self._prefix = state_key_prefix or DEFAULT_STATE_KEY_PREFIX
        self._clear_on_all_done = bool(clear_on_all_done)
        self._default_nudge = default_nudge
        self._nudge_hooks = list(nudge_hooks or [])

    @override
    def _get_declaration(self) -> FunctionDeclaration:
        item_schema = Schema(
            type=Type.OBJECT,
            properties={
                "content":
                Schema(
                    type=Type.STRING,
                    description="Imperative description of the task, e.g. 'Run tests'.",
                ),
                "activeForm":
                Schema(
                    type=Type.STRING,
                    description="Present-continuous form shown while active, e.g. 'Running tests'.",
                ),
                "status":
                Schema(
                    type=Type.STRING,
                    enum=["pending", "in_progress", "completed"],
                    description="Item status. Keep at most one item 'in_progress'.",
                ),
            },
            required=["content", "activeForm", "status"],
        )
        return FunctionDeclaration(
            name=self.name,
            description=DEFAULT_TODO_DESCRIPTION,
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "todos":
                    Schema(
                        type=Type.ARRAY,
                        items=item_schema,
                        description=("The complete, updated todo list. Replaces the previous list "
                                     "entirely. Send [] to clear the list."),
                    ),
                },
                required=["todos"],
            ),
        )

    @override
    async def process_request(
        self,
        *,
        tool_context: InvocationContext,
        llm_request: LlmRequest,
    ) -> None:
        """Register the declaration and inject behavioural guidance."""
        await super().process_request(tool_context=tool_context, llm_request=llm_request)
        llm_request.append_instructions([DEFAULT_TODO_PROMPT])

    @override
    async def _run_async_impl(
        self,
        *,
        tool_context: InvocationContext,
        args: dict[str, Any],
    ) -> Any:
        # 1. decode-guard: a missing field or explicit null is rejected so
        #    a dropped key cannot silently wipe the plan. The only valid
        #    clear gesture is an explicit empty array.
        if "todos" not in args:
            return {"error": "INVALID_ARGS: `todos` is required and must be an array (use [] to clear)"}
        raw_todos = args["todos"]
        if raw_todos is None:
            return {"error": "INVALID_ARGS: `todos` must be an array, got null (use [] to clear)"}
        if not isinstance(raw_todos, list):
            return {"error": "INVALID_ARGS: `todos` must be an array"}

        # 2. parse + hard-contract validation
        try:
            todos = [TodoItem.model_validate(x) for x in raw_todos]
        except (ValidationError, TypeError) as e:
            return {"error": f"INVALID_ARGS: each todo must have content/activeForm/status: {e}"}
        if (err := validate_todos(todos)) is not None:
            return {"error": f"INVALID_TODOS: {err}"}

        # 3. resolve branch + key (fall back to agent name for single-agent stability)
        branch = tool_context.branch or tool_context.agent_name or ""
        key = state_key(self._prefix, branch)

        # 4. read previous list for the diff
        old = get_todos(tool_context.session, branch, self._prefix)

        # 5. clear-on-all-done normalisation
        new = todos
        if self._clear_on_all_done and new and all(t.status == TodoStatus.COMPLETED for t in new):
            new = []

        # 6. persist. Writing through ``tool_context.state`` records a
        #    state delta that the framework commits with the function
        #    response event, so the list survives across runs.
        new_payload = [t.model_dump(mode="json", by_alias=True) for t in new]
        tool_context.state[key] = json.dumps(new_payload, ensure_ascii=False)

        # 7. assemble message: base nudge + read-only policy hooks
        message = self._default_nudge
        for hook in self._nudge_hooks:
            try:
                extra = hook(old, todos)
            except Exception as e:  # pylint: disable=broad-except
                logger.warning("TodoWriteTool nudge hook raised: %s", e)
                continue
            if extra:
                message = f"{message}\n\n{extra}"

        # 8. echo current + previous list for direct front-end consumption
        return {
            "message": message,
            "todos": new_payload,
            "oldTodos": [t.model_dump(mode="json", by_alias=True) for t in old] if old else None,
        }
