## Task Tool Family (Structured Task Board)

`TaskToolSet` exposes four tools — `task_create`, `task_update`, `task_get`, `task_list` — aligned with Claude Code v2.1.142+ structured Task capabilities. Unlike `TodoWriteTool`'s full-list replace, the Task family uses **incremental updates by server-assigned `id`**: creation returns an id; later `task_update` patches status, fields, or dependency edges locally.

The entire board is serialized as a **single JSON blob** in `tool_context.state["tasks[:<branch>]"]`, surviving across turns. `highwatermark` records the highest id ever assigned; soft-deleted tasks (`status: deleted`) **never reuse ids**.

### Features

- **Incremental updates**: `task_create` assigns ids; `task_update` patches by `taskId` without resending the whole board
- **Dependency orchestration**: `addBlockedBy` / `removeBlockedBy` (and `addBlocks` / `removeBlocks`) maintain bidirectional edges; upstream `completed` removes ids from downstream `blockedBy` and returns `unblocked`
- **Token optimization**: `task_list` returns summaries only (omits `description`); use `task_get` for full detail
- **Hard contract validation**: non-empty `subject`, valid status, existing dependency refs, **acyclic** graph (`detect_cycle`), default **at most one `in_progress`** (`enforce_single_in_progress`, can disable)
- **Concurrency safety**: `_TaskToolBase` wraps load → mutate → save in `task_store_lock` (per session + branch), compatible with `parallel_tool_calls=True`
- **Auto prompt injection**: `DEFAULT_TASK_PROMPT` injected once when multiple tools are mounted

### TaskToolSet Constructor Parameters

| Parameter | Type | Default | Description |
|------|------|--------|------|
| `state_key_prefix` | `str` | `"tasks"` | State key prefix; do not use `temp:` |
| `enforce_single_in_progress` | `bool` | `True` | Reject a second `in_progress` when one already exists |
| `inject_prompt` | `bool` | `True` | Inject `DEFAULT_TASK_PROMPT` into system instruction |

### LLM Parameters for the Four Tools

**`task_create`**

| Parameter | Required | Description |
|------|------|------|
| `subject` | Yes | Short imperative title |
| `description` | No | Free-text detail |
| `activeForm` | No | In-progress label |
| `metadata` | No | Extension key-value map |

Returns `{task: {id, subject}, message}`.

**`task_update`**

| Parameter | Required | Description |
|------|------|------|
| `taskId` | Yes | Task id to update |
| `status` | No | `pending` / `in_progress` / `completed` / `deleted` |
| `subject` / `description` / `activeForm` / `owner` / `metadata` | No | Scalar field patches |
| `addBlockedBy` / `removeBlockedBy` | No | Upstream dependency id lists |
| `addBlocks` / `removeBlocks` | No | Downstream blocked-id lists |

Returns `{task, unblocked, message}`; `unblocked` lists pending task ids unblocked by this completion.

**`task_get`**: `taskId` (required) → full record including `description`.

**`task_list`**: optional `includeDeleted`; returns `{tasks, stats}` with summaries (no `description`).

**Common error codes**: `INVALID_ARGS`, `INVALID_DEPENDENCY`, `INVALID_STATUS`, `NOT_FOUND`.

### Usage

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import TaskToolSet

agent = LlmAgent(
    name="task_planner",
    model=OpenAIModel(model_name="...", api_key="...", base_url="..."),
    instruction="Use task_create / task_update to maintain the board for multi-step projects.",
    tools=[TaskToolSet()],
    # With parallel_tool_calls=True, concurrent task tools on the same board are serialized by task_store_lock
)
```

Read back the persisted board (REST / audit / demo wrap-up):

```python
from trpc_agent_sdk.tools import get_task_store, render_task_list

store = get_task_store(session, branch=agent.name)
print(render_task_list(store))
# ✅ #1 completed
# 🔄 #2 in progress
# ⬜ #3 pending (blocked by: 2)
```

### Dependency and Unblock Example

```text
#1 Design schema
 ├──→ #2 Implement API ──→ #3 Unit tests
 └──→ #4 Write docs

#1 completed  →  unblocked: ['2', '4']
#2 completed  →  unblocked: ['3']
```

### Task Tool Family Best Practices

- **Separate planning from execution**: `task_create` + `addBlockedBy` first, then `in_progress` → `completed` item by item
- **Do not invent ids**: use only ids returned by `task_create`
- **Parallel calls**: with `parallel_tool_calls=True`, concurrent `task_create` / `task_update` on the same board are serialized by the lock; different `branch` values still run in parallel
- **Pick TodoWrite or Task**: long boards + dependencies → Task; short checklists → TodoWrite

### Task Tool Family Complete Examples

| Example | Description |
| --- | --- |
| [examples/task_tools](../../../examples/task_tools/) | Multi-turn dialog: dependency graph, step-by-step completion, `get_task_store` across turns |
| [examples/task_tools_parallel](../../../examples/task_tools_parallel/) | Validates `parallel_tool_calls` + `task_store_lock` (Phase 1–2 needs no API key) |

---
