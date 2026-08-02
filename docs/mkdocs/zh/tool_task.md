## Task 工具族（结构化任务看板）

`TaskToolSet` 暴露四个工具——`task_create`、`task_update`、`task_get`、`task_list`——对齐 Claude Code v2.1.142+ 的结构化 Task 能力。与 `TodoWriteTool` 的整表替换不同，Task 工具族采用**按服务端分配的 `id` 增量更新**：创建时返回 id，后续用 `task_update` 局部修改状态、字段或依赖边。

整个看板序列化为**单个 JSON blob** 写入 `tool_context.state["tasks[:<branch>]"]`，跨轮存活；`highwatermark` 记录曾分配的最高 id，软删除（`status: deleted`）后**不会复用 id**。

### 功能特性

- **增量更新**：`task_create` 分配 id；`task_update` 按 `taskId` patch，无需重传整板
- **依赖编排**：`addBlockedBy` / `removeBlockedBy`（及 `addBlocks` / `removeBlocks`）维护双向边；上游 `completed` 时自动从下游 `blockedBy` 移除并返回 `unblocked`
- **Token 优化**：`task_list` 只返回摘要（省略 `description`）；完整详情用 `task_get`
- **硬契约校验**：`subject` 非空、状态合法、依赖存在、**无环**（`detect_cycle`）、默认**至多一个 `in_progress`**（`enforce_single_in_progress`，可关）
- **并发安全**：`_TaskToolBase` 在 load → mutate → save 外包 `task_store_lock`（按 session + branch），兼容 `parallel_tool_calls=True` 下同批并行调用
- **Prompt 自动注入**：`DEFAULT_TASK_PROMPT` 多工具挂载时只注入一次

### TaskToolSet 构造参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `state_key_prefix` | `str` | `"tasks"` | state key 前缀；勿使用 `temp:` |
| `enforce_single_in_progress` | `bool` | `True` | 设置某任务 `in_progress` 时，若已有其他 `in_progress` 则拒绝 |
| `inject_prompt` | `bool` | `True` | 是否向 system instruction 注入 `DEFAULT_TASK_PROMPT` |

### 四个工具的 LLM 参数概要

**`task_create`**

| 参数 | 必填 | 说明 |
|------|------|------|
| `subject` | 是 | 短标题（祈使句） |
| `description` | 否 | 自由文本详情 |
| `activeForm` | 否 | 进行时文案 |
| `metadata` | 否 | 扩展键值 |

返回 `{task: {id, subject}, message}`。

**`task_update`**

| 参数 | 必填 | 说明 |
|------|------|------|
| `taskId` | 是 | 要更新的任务 id |
| `status` | 否 | `pending` / `in_progress` / `completed` / `deleted` |
| `subject` / `description` / `activeForm` / `owner` / `metadata` | 否 | 标量字段 patch |
| `addBlockedBy` / `removeBlockedBy` | 否 | 上游依赖 id 列表 |
| `addBlocks` / `removeBlocks` | 否 | 下游阻塞 id 列表 |

返回 `{task, unblocked, message}`；`unblocked` 为因本次完成而解除阻塞的 pending 任务 id 列表。

**`task_get`**：`taskId`（必填）→ 含 `description` 的完整记录。

**`task_list`**：可选 `includeDeleted`；返回 `{tasks, stats}`，摘要不含 `description`。

**常见错误码**：`INVALID_ARGS`、`INVALID_DEPENDENCY`、`INVALID_STATUS`、`NOT_FOUND`。

### 使用方式

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import TaskToolSet

agent = LlmAgent(
    name="task_planner",
    model=OpenAIModel(model_name="...", api_key="...", base_url="..."),
    instruction="多步项目请用 task_create / task_update 维护看板。",
    tools=[TaskToolSet()],
    # parallel_tool_calls=True 时，同批多个 task 工具由 task_store_lock 保护 store 一致性
)
```

读回持久化看板（REST / 审计 / demo 收尾）：

```python
from trpc_agent_sdk.tools import get_task_store, render_task_list

store = get_task_store(session, branch=agent.name)
print(render_task_list(store))
# ✅ #1 已完成
# 🔄 #2 进行中
# ⬜ #3 待办 (blocked by: 2)
```

### 依赖与解锁示例

```text
#1 设计表结构
 ├──→ #2 实现 API ──→ #3 单元测试
 └──→ #4 编写文档

#1 completed  →  unblocked: ['2', '4']
#2 completed  →  unblocked: ['3']
```

### Task 工具族最佳实践

- **规划与执行分离**：先 `task_create` 建板并 `addBlockedBy`，再逐项 `in_progress` → `completed`
- **不要编造 id**：只使用 `task_create` 返回的 id
- **并行调用**：开启 `parallel_tool_calls=True` 时，同 board 上的并发 `task_create` / `task_update` 由锁串行化；不同 `branch` 仍并行
- **与 TodoWrite 二选一**：长板 + 依赖用 Task；短清单用 TodoWrite

### Task 工具族完整示例

| 示例 | 说明 |
| --- | --- |
| [examples/task_tools](../../../examples/task_tools/) | 多轮对话：依赖编排、逐项完成、跨轮 `get_task_store` 读回看板 |
| [examples/task_tools_parallel](../../../examples/task_tools_parallel/) | 验证 `parallel_tool_calls` 与 `task_store_lock`（Phase 1–2 无需 API Key） |

---
