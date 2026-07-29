## TodoWriteTool (Task Checklist Tool)

`TodoWriteTool` is the framework's built-in **structured task checklist tool**, aligned with Claude Code / DeepAgents `TodoWrite` semantics: the model sends the **complete, updated list** in a single `todo_write` call; the tool validates it, fully replaces the previous list, and persists it to session-level state so plans and progress survive across `Runner.run_async` invocations.

Best for **fewer steps, no explicit dependency edges, and simple implementation**. If you need server-assigned ids, incremental `taskId` patches, or `blockedBy` / `blocks` dependency orchestration, use the [Task Tool Family](./tool_task.md) instead.

### Features

- **Full-list replace**: each call passes the complete `todos` array; the new list **fully overwrites** the old one (no smart merge). The only valid way to clear is an explicit `todos: []`
- **Session-level persistence**: the checklist is serialized to JSON in `tool_context.state["todos[:<branch>]"]` (default prefix `todos`; **do not** use `temp:` — that prefix is stripped by `BaseSessionService` and is not persisted)
- **Sub-agent isolation**: the state key appends `:<branch>` so parent / child agents maintain separate lists
- **Hard contract validation (code-enforced)**: non-empty `content` / `activeForm`, at most one `in_progress`, globally unique `content`; violations return `INVALID_ARGS` / `INVALID_TODOS`
- **Layered prompt guidance**: `DEFAULT_TODO_PROMPT` is auto-injected into the system instruction via `process_request`, separate from hard contracts
- **Structured diff in responses**: on success returns `{message, todos, oldTodos}` for front-end / CLI rendering
- **Optional policy hooks**: read-only `nudge_hooks` can append strategy hints to `message` (must not modify the list)
- **Auto-clear when all done**: with `clear_on_all_done=True` (default), an all-`completed` list is persisted as empty to avoid stale accumulation

### TodoWriteTool Parameters

| Parameter | Type | Default | Description |
|------|------|--------|------|
| `state_key_prefix` | `str` | `"todos"` | State key prefix; do not use `temp:` |
| `clear_on_all_done` | `bool` | `True` | Clear persisted list when all items are `completed` |
| `default_nudge` | `str` | built-in text | Base hint appended on every successful response |
| `nudge_hooks` | `Optional[List[NudgeHook]]` | `None` | Read-only policy hook list |
| `filters_name` / `filters` | — | `None` | Filters forwarded to `BaseTool` |

**LLM call parameters** (`todo_write`):

| Parameter | Type | Required | Description |
|------|------|------|------|
| `todos` | `array` | Yes | Full list; each item has `content` (imperative), `activeForm` (in-progress label), `status` (`pending` / `in_progress` / `completed`) |

**Successful response fields**:

| Field | Type | Description |
|------|------|------|
| `message` | `str` | Base nudge + hook-appended text |
| `todos` | `array` | Persisted current list |
| `oldTodos` | `array \| null` | Previous list (`null` on first write) |

### Usage

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import TodoWriteTool

agent = LlmAgent(
    name="todo_planner",
    model=OpenAIModel(model_name="...", api_key="...", base_url="..."),
    instruction="You are a planning assistant; use todo_write for multi-step tasks.",
    tools=[TodoWriteTool()],
)
```

Read back the persisted checklist (REST / audit):

```python
from trpc_agent_sdk.tools import get_todos, render_todos

todos = get_todos(session, branch=agent.name)
print(render_todos(todos))  # ✅ / 🔄 / ⬜ plain-text checklist
```

### TodoWriteTool vs Task Tool Family

| Dimension | `TodoWriteTool` | `TaskToolSet` |
| --- | --- | --- |
| Tool count | 1 (`todo_write`) | 4 (`task_create` / `task_update` / `task_get` / `task_list`) |
| Update style | Full-list replace | Incremental patch by `taskId` |
| Item identity | `content` (unique key) | `id` (server-assigned) |
| Dependencies | None | `blockedBy` / `blocks`; upstream `completed` auto-unblocks |
| State key | `todos[:branch]` | `tasks[:branch]` |
| Parallel tool calls | Full-list overwrite, natural last-write-wins | `task_store_lock` serializes RMW |

> **Mount one or the other**; mounting both tends to confuse the model.

### TodoWriteTool Complete Example

See [examples/todo_tool/run_agent.py](../../../examples/todo_tool/run_agent.py): multiple turns in one session — plan → complete items step by step — with `get_todos` reading back the persisted list after each turn.

---
