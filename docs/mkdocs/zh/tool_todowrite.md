## TodoWriteTool（任务清单工具）

`TodoWriteTool` 是框架内置的**结构化任务清单工具**，对齐 Claude Code / DeepAgents 的 `TodoWrite` 语义：模型通过单次 `todo_write` 调用发送**完整、更新后的清单**，工具校验后整体替换上一份清单，并将会话级 state 持久化，从而在多轮 `Runner.run_async` 之间保持计划与进度。

适合**步骤较少、无显式依赖边、希望实现简单**的场景。若需要服务端分配 id、按 `taskId` 增量 patch、或 `blockedBy` / `blocks` 依赖编排，请改用 [Task 工具族](./tool_task.md)。

### 功能特性

- **整表替换**：每次调用传入完整 `todos` 数组，新列表**完全覆盖**旧列表（不做智能 merge）；唯一合法的清空方式是显式传入 `todos: []`
- **会话级持久化**：清单序列化为 JSON 写入 `tool_context.state["todos[:<branch>]"]`（默认前缀 `todos`，**勿用** `temp:`——该前缀会被 `BaseSessionService` 剥离且不持久化）
- **子 Agent 隔离**：state key 追加 `:<branch>`，父 / 子 Agent 各自维护独立清单
- **硬契约校验（代码强制）**：`content` / `activeForm` 非空、至多一个 `in_progress`、`content` 全局唯一；违反时返回 `INVALID_ARGS` / `INVALID_TODOS`
- **Prompt 引导分层**：`DEFAULT_TODO_PROMPT` 经 `process_request` 自动注入 system instruction，描述使用时机与写法；与硬契约分离
- **响应带回 diff**：成功时返回 `{message, todos, oldTodos}`，便于前端 / CLI 直接渲染当前清单与变更
- **可选策略钩子**：`nudge_hooks` 只读回调，可在成功响应 `message` 末尾追加策略提示（不得修改清单）
- **全部完成后自动清空**：`clear_on_all_done=True`（默认）时，若传入列表全部为 `completed`，持久化为空列表，避免历史项堆积

### TodoWriteTool 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `state_key_prefix` | `str` | `"todos"` | state key 前缀；勿使用 `temp:` |
| `clear_on_all_done` | `bool` | `True` | 全部为 `completed` 时是否清空持久化列表 |
| `default_nudge` | `str` | 内置文案 | 每次成功响应的基础提示语 |
| `nudge_hooks` | `Optional[List[NudgeHook]]` | `None` | 只读策略钩子列表 |
| `filters_name` / `filters` | — | `None` | 透传给 `BaseTool` 的 Filter |

**LLM 调用参数**（`todo_write`）：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `todos` | `array` | 是 | 完整清单；每项含 `content`（祈使句）、`activeForm`（进行时）、`status`（`pending` / `in_progress` / `completed`） |

**成功响应字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `message` | `str` | 基础 nudge + 钩子追加文案 |
| `todos` | `array` | 持久化后的当前清单 |
| `oldTodos` | `array \| null` | 更新前的清单（首次写入为 `null`） |

### 使用方式

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import TodoWriteTool

agent = LlmAgent(
    name="todo_planner",
    model=OpenAIModel(model_name="...", api_key="...", base_url="..."),
    instruction="你是规划型助手，多步任务请用 todo_write 维护清单。",
    tools=[TodoWriteTool()],
)
```

服务端 / 审计读取当前清单：

```python
from trpc_agent_sdk.tools import get_todos, render_todos

todos = get_todos(session, branch=agent.name)
print(render_todos(todos))  # ✅ / 🔄 / ⬜ 纯文本 checklist
```

### TodoWriteTool 与 Task 工具族对比

| 维度 | `TodoWriteTool` | `TaskToolSet` |
| --- | --- | --- |
| 工具数量 | 1（`todo_write`） | 4（`task_create` / `task_update` / `task_get` / `task_list`） |
| 更新方式 | 整表替换 | 按 `taskId` 增量 patch |
| 单项标识 | `content`（唯一键） | `id`（服务端分配） |
| 依赖编排 | 无 | `blockedBy` / `blocks`，完成上游自动 unblock |
| state key | `todos[:branch]` | `tasks[:branch]` |
| 并行 tool 调用 | 整表覆盖，天然 last-write-wins | 内置 `task_store_lock` 串行化 RMW |

> **建议二选一挂载**；同时挂载易让模型混用两套语义。

### TodoWriteTool 完整示例

见 [examples/todo_tool/run_agent.py](../../../examples/todo_tool/run_agent.py)：同一 session 内多轮「规划 → 逐项完成」，每轮用 `get_todos` 读回持久化清单。

---
