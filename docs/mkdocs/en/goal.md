## Goal Tool Family (Persistent Session Goal)

`GoalToolSet` exposes three tools — `create_goal`, `get_goal`, `update_goal` — aligned with Claude Code **Session Goal** capabilities. Unlike `TodoWriteTool` (multi-item checklist) and `TaskToolSet` (multi-task board), Goal maintains **at most one** persistent objective per session branch: while status is `active`, a response that **looks like a final answer** does **not** mean the work is done — the model must keep working or explicitly call `update_goal('complete' | 'blocked')`.

The goal is serialized as a **single JSON blob** (`GoalRecord`) in `tool_context.state["goal[:<branch>]"]`, surviving across `Runner.run_async` calls. Beyond the three model tools, the full capability requires `setup_goal()` to mount **enforcement callbacks** (`before_model` / `after_model`) that intercept premature final responses and re-run within the **same invocation**.

### Features

- **Single-goal contract**: one `GoalRecord` per branch (`objective` + three states `active` / `complete` / `blocked`); `complete` / `blocked` are **irreversible** terminal states
- **Cross-turn persistence**: persisted via function-response state deltas; **do not** use the `temp:` prefix
- **Sub-agent isolation**: state key appends `:<branch>`
- **Enforced completion**: while `active`, `after_model` detects premature finals (no tool call, visible text, non-partial), suppresses them, and re-runs in the same invocation; `before_model` injects a user-role nudge
- **Fail-open budget**: after `max_retries` (default 3) interceptions, the final response is allowed so the loop cannot spin forever; counters live in invocation-scoped `agent_context.metadata` and are not persisted
- **Two creation paths**:
  - **Model side**: `create_goal(objective=...)` — LLM creates after judging a multi-step task
  - **Host side**: `start_goal(session_service, ...)` — application writes the goal before the first turn; the model does not call `create_goal`
- **Layered prompt guidance**: `DEFAULT_GUIDANCE` injected into system instruction via `before_model` when `inject_guidance=True`; hard rules enforced by store validation + callbacks
- **Concurrency safety**: `_GoalToolBase` wraps load → mutate → save in `goal_store_lock` (per session + branch), compatible with `parallel_tool_calls=True`

### Relationship to Todo / Task

| Dimension | `TodoWriteTool` | `TaskToolSet` | Goal Tool Family |
| --- | --- | --- | --- |
| Granularity | Multi-item checklist | Multi-task board + deps | **Single** session objective |
| Update style | Full-list replace | Incremental by `taskId` | `create_goal` / `update_goal` |
| Can finish while incomplete? | Prompt guidance | Prompt guidance | **Callback enforcement** |
| State key | `todos[:branch]` | `tasks[:branch]` | `goal[:branch]` |
| Typical use | Step visibility, short lists | Long boards, dependencies | Whether the whole job is truly done |

> Todo / Task handle **step decomposition**; Goal handles the **overall completion contract**. They can be combined, but avoid mounting too many planning tools at once.

### GoalOptions Constructor Parameters

Configure via `setup_goal(agent, GoalOptions(...))`:

| Parameter | Type | Default | Description |
|------|------|--------|------|
| `state_key_prefix` | `str` | `"goal"` | State key prefix; do not use `temp:` |
| `inject_guidance` | `bool` | `True` | Inject `DEFAULT_GUIDANCE` into system instruction in `before_model` |
| `guidance` | `str` | `DEFAULT_GUIDANCE` | Long guidance text (serial goal-tool calls, etc.) |
| `max_retries` | `int` | `3` | Same-invocation budget for intercepting premature finals; fail-open when exhausted |
| `nudge_template` | `str` | `DEFAULT_NUDGE` | User-role reminder after interception; supports `{attempt}` / `{max_retries}` / `{objective}` |
| `on_retry` | `Callable[[RetryEvent], None]` | `None` | Observability callback on each interception or budget exhaustion |

Mounting only `GoalToolSet()` without enforcement gives model-facing tools but **not** "no final while active".

### LLM Parameters for the Three Tools

**`create_goal`**

| Parameter | Required | Description |
|------|------|------|
| `objective` | Yes | Completion criteria — what "done" concretely means |

Success: `{message, goal}`; if an `active` goal already exists: `{error: "INVALID_STATE: ..."}`.

**`get_goal`**

No parameters. With a goal: `{message, goal}`; without: `{message: "No session goal is set."}`.

**`update_goal`**

| Parameter | Required | Description |
|------|------|------|
| `status` | Yes | `complete` (objective met) or `blocked` (same blocker repeats; cannot proceed without user input) |

Success: `{message, goal}`; no active goal or already terminal: `{error: "INVALID_STATE: ..."}`.

**`GoalRecord` fields** (persisted with camelCase JSON aliases):

| Field | Description |
|------|------|
| `id` | Server-assigned uuid |
| `objective` | Completion criteria text |
| `status` | `active` / `complete` / `blocked` |
| `createdAtUnix` / `updatedAtUnix` | Created / last-updated time (unix seconds) |
| `terminalAtUnix` | Time entered a terminal state (optional) |

### Enforcement Workflow

```text
Model outputs final text (no tool call, goal still active)
        ↓
after_model classifies as premature final
        ↓
Suppress final (not committed as answer), retry_count += 1
before_model injects nudge, same invocation continues agent loop
        ↓
retry_count >= max_retries → fail-open, on_retry(reason="exhausted")
```

Interception condition (`_is_premature_final`): non-partial, no error, visible text in content, and **no** `function_call` / `function_response`.

### Usage

Recommended one-line mount of tools + callbacks:

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools.goal_tools import GoalOptions, RetryEvent, setup_goal

def on_retry(event: RetryEvent) -> None:
    if event.reason == "blocked":
        print(f"Premature final intercepted (attempt {event.attempt_number}/{event.max_retries})")

agent = LlmAgent(
    name="goal_agent",
    model=OpenAIModel(model_name="...", api_key="...", base_url="..."),
    instruction="Use goal tools to track completion for multi-step engineering tasks.",
    tools=[...],  # Business tools, e.g. BashTool / WriteTool
)
setup_goal(agent, GoalOptions(max_retries=3, on_retry=on_retry))
```

Host pre-injects a goal before the first turn (model does not call `create_goal`):

```python
from trpc_agent_sdk.tools.goal_tools import start_goal

goal = await start_goal(
    session_service,
    app_name="my_app",
    user_id="user_1",
    session_id=session_id,
    objective="Create notes/ with summary.txt and example.py in the current directory",
    agent_name=agent.name,  # Match LlmAgent.name for branch isolation
)
```

Read back the persisted goal (REST / audit / demo wrap-up):

```python
from trpc_agent_sdk.tools.goal_tools import get_goal_record, render_goal

goal = get_goal_record(session, branch=agent.name)
print(render_goal(goal))
# ✅ Goal [complete]
#    objective: ...
#    created:   1782893110
#    terminal:  1782893116
```

### Goal Tool Family Best Practices

- **Use `setup_goal`, not `GoalToolSet` alone**: only callbacks enforce "no final while active"
- **Model vs host**: slash commands, `/goal`, config-driven tasks → `start_goal()`; let the model judge multi-step work → `create_goal`
- **One goal tool per response**: `DEFAULT_GUIDANCE` requires serial semantics; do not call `create_goal` and `update_goal` in the same turn
- **Use `blocked` sparingly**: only when the same blocker repeats across attempts and user input or external state change is required; do not mark blocked because work is hard, slow, or incomplete
- **Observability**: use `on_retry` to log premature-final interceptions and budget exhaustion when tuning prompt or `max_retries`
- **Division of labor with Todo / Task**: Todo / Task show steps and dependencies; Goal constrains whether the whole job is truly finished

### Goal Tool Family Complete Example

| Example | Description |
| --- | --- |
| [examples/goal_tools](../../../examples/goal_tools/) | Case 1: model `create_goal`; Case 2: host `start_goal` pre-injection; demonstrates enforcement interception and `update_goal(complete)` |
