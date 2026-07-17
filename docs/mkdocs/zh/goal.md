## Goal 工具族（持久会话目标）

`GoalToolSet` 暴露三个工具——`create_goal`、`get_goal`、`update_goal`——对齐 Claude Code 的 **Session Goal** 能力。与 `TodoWriteTool`（多行待办）和 `TaskToolSet`（多任务看板）不同，Goal 在每个 session branch 上**至多只有一个**持久目标：在目标为 `active` 期间，模型给出「看起来像最终答案」的文本**不算完成**——必须继续执行，或显式调用 `update_goal('complete' | 'blocked')` 收尾。

目标序列化为**单个 JSON blob**（`GoalRecord`）写入 `tool_context.state["goal[:<branch>]"]`，跨 `Runner.run_async` 调用存活。除三个模型工具外，完整能力还需通过 `setup_goal()` 挂载一对 **enforcement callbacks**（`before_model` / `after_model`），在**同一次 invocation 内**拦截过早的最终回复并自动重试。

### 功能特性

- **单目标契约**：每个 branch 一个 `GoalRecord`（`objective` + 三态 `active` / `complete` / `blocked`）；`complete` / `blocked` 为**不可逆**终态
- **跨轮持久化**：随 function-response 的 state delta 落库；**勿用** `temp:` 前缀
- **子 Agent 隔离**：state key 追加 `:<branch>`，父 / 子 Agent 各自维护独立目标
- **强制收尾（enforcement）**：目标 `active` 时，`after_model` 检测「无 tool call、有可见文本、非 partial」的过早 final，抑制该回复并在同 invocation 内 re-run；`before_model` 注入 user-role nudge
- **fail-open 预算**：`max_retries`（默认 3）次拦截后放行最终回复，避免无限循环；计数器存在 invocation 级 `agent_context.metadata`，不持久化
- **双入口创建**：
  - **模型侧**：`create_goal(objective=...)` —— LLM 判断多步任务后自主创建
  - **宿主侧**：`start_goal(session_service, ...)` —— 应用层在首轮前写入 session，模型无需调用 `create_goal`
- **Prompt 引导分层**：`DEFAULT_GUIDANCE` 在目标 active 时经 `before_model` 注入 system instruction（`inject_guidance=True`）；硬约束由 store 校验 + callback 共同保证
- **并发安全**：`_GoalToolBase` 在 load → mutate → save 外包 `goal_store_lock`（按 session + branch），兼容 `parallel_tool_calls=True`

### 与 Todo / Task 的关系

| 维度 | `TodoWriteTool` | `TaskToolSet` | Goal 工具族 |
| --- | --- | --- | --- |
| 粒度 | 多行待办清单 | 多任务看板 + 依赖 | **单个**会话目标 |
| 更新方式 | 整表替换 | 按 `taskId` 增量 | `create_goal` / `update_goal` |
| 未完成能否收尾 | Prompt 引导 | Prompt 引导 | **callback 强制拦截** |
| state key | `todos[:branch]` | `tasks[:branch]` | `goal[:branch]` |
| 典型用途 | 步骤可视、短清单 | 长看板、依赖编排 | 整件事是否算做完 |

> Todo / Task 管「步骤分解」，Goal 管「整体完成契约」。可组合使用，但避免让模型同时混用过多规划工具。

### GoalOptions 构造参数

通过 `setup_goal(agent, GoalOptions(...))` 配置：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `state_key_prefix` | `str` | `"goal"` | state key 前缀；勿使用 `temp:` |
| `inject_guidance` | `bool` | `True` | 是否在 `before_model` 向 system instruction 注入 `DEFAULT_GUIDANCE` |
| `guidance` | `str` | `DEFAULT_GUIDANCE` | 注入的长文案（含串行调用 goal 工具等约定） |
| `max_retries` | `int` | `3` | 同 invocation 内拦截过早 final 的预算；耗尽后 fail-open |
| `nudge_template` | `str` | `DEFAULT_NUDGE` | 拦截后以 user-role 追加的提醒模板（支持 `{attempt}` / `{max_retries}` / `{objective}`） |
| `on_retry` | `Callable[[RetryEvent], None]` | `None` | 每次拦截或预算耗尽时的可观测回调 |

仅挂载模型工具、不要 enforcement 时，可直接 `tools=[GoalToolSet()]`，但不具备「未完成不许收尾」能力。

### 三个工具的 LLM 参数概要

**`create_goal`**

| 参数 | 必填 | 说明 |
|------|------|------|
| `objective` | 是 | 完成标准——「done」具体指什么 |

成功返回 `{message, goal}`；若已有 `active` 目标返回 `{error: "INVALID_STATE: ..."}`。

**`get_goal`**

无参数。有目标时返回 `{message, goal}`；无目标时返回 `{message: "No session goal is set."}`。

**`update_goal`**

| 参数 | 必填 | 说明 |
|------|------|------|
| `status` | 是 | `complete`（目标已达成）或 `blocked`（同一阻塞条件反复出现、无用户输入无法继续） |

成功返回 `{message, goal}`；无 active 目标或已是终态时返回 `{error: "INVALID_STATE: ..."}`。

**`GoalRecord` 字段**（JSON 使用 camelCase 别名持久化）：

| 字段 | 说明 |
|------|------|
| `id` | 服务端分配的 uuid |
| `objective` | 完成标准文本 |
| `status` | `active` / `complete` / `blocked` |
| `createdAtUnix` / `updatedAtUnix` | 创建 / 最后更新时间（unix 秒） |
| `terminalAtUnix` | 进入终态的时间（可选） |

### enforcement 工作流程

```text
模型输出 final 文本（无 tool call，goal 仍 active）
        ↓
after_model 判定为 premature final
        ↓
抑制该 final（不提交为答案），retry_count += 1
before_model 注入 nudge，同 invocation 继续 agent loop
        ↓
retry_count >= max_retries → fail-open，on_retry(reason="exhausted")
```

拦截条件（`_is_premature_final`）：非 partial、无 error、content 含可见文本，且**不含** `function_call` / `function_response`。

### 使用方式

推荐一行挂载工具 + callbacks：

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools.goal_tools import GoalOptions, RetryEvent, setup_goal

def on_retry(event: RetryEvent) -> None:
    if event.reason == "blocked":
        print(f"拦截过早收尾 (attempt {event.attempt_number}/{event.max_retries})")

agent = LlmAgent(
    name="goal_agent",
    model=OpenAIModel(model_name="...", api_key="...", base_url="..."),
    instruction="多步工程任务请用 goal 工具跟踪完成状态。",
    tools=[...],  # 业务工具，如 BashTool / WriteTool
)
setup_goal(agent, GoalOptions(max_retries=3, on_retry=on_retry))
```

宿主在首轮前预注入目标（模型不调用 `create_goal`）：

```python
from trpc_agent_sdk.tools.goal_tools import start_goal

goal = await start_goal(
    session_service,
    app_name="my_app",
    user_id="user_1",
    session_id=session_id,
    objective="在当前目录创建 notes/ 并写入 summary.txt 与 example.py",
    agent_name=agent.name,  # 与 LlmAgent.name 一致，用于 branch 隔离
)
```

读回持久化目标（REST / 审计 / demo 收尾）：

```python
from trpc_agent_sdk.tools.goal_tools import get_goal_record, render_goal

goal = get_goal_record(session, branch=agent.name)
print(render_goal(goal))
# ✅ Goal [complete]
#    objective: ...
#    created:   1782893110
#    terminal:  1782893116
```

### Goal 工具族最佳实践

- **用 `setup_goal` 而非只挂 `GoalToolSet`**：只有 callbacks 才能实现「active 期间不许 final」
- **模型侧 vs 宿主侧**：Slash command、`/goal`、配置驱动任务用 `start_goal()`；让模型自主判断多步任务时用 `create_goal`
- **一次响应只调一个 goal 工具**：`DEFAULT_GUIDANCE` 要求串行语义；不要同轮 `create_goal` + `update_goal`
- **`blocked` 慎用**：仅当同一阻塞条件跨多次尝试仍无法推进、且需要用户输入或外部状态变化时使用；不要因为任务难、慢或不完整就标记 blocked
- **可观测性**：通过 `on_retry` 记录 `⚡ Premature final intercepted` 与预算耗尽，便于调优 prompt 或 `max_retries`
- **与 Todo / Task 分工**：Todo / Task 展示步骤与依赖；Goal 约束「整件事是否已真正完成」

### Goal 工具族完整示例

| 示例 | 说明 |
| --- | --- |
| [examples/goal_tools](../../../examples/goal_tools/) | Case 1：模型 `create_goal`；Case 2：宿主 `start_goal` 预注入；演示 enforcement 拦截与 `update_goal(complete)` |
