# TodoWriteTool 任务清单示例

本示例演示框架内置的 `TodoWriteTool`，让 LLM Agent 把多步骤任务外显成一份**结构化、可持久化的待办清单**：先规划、再逐项执行、每完成一步翻转状态。清单存放在**会话级 state**（key 前缀 `todos`，**不用** `temp:`），因此可以**跨轮（跨 `Runner.run_async` 调用）存活**，Agent 能从上一轮停下的地方继续。

## 关键特性

- **整表替换语义**：模型每次调用 `todo_write` 都发送**完整的新列表**，整体替换旧列表，不做智能 merge（与 Claude Code / DeepAgents 路线一致，简单鲁棒）。
- **会话级持久化**：清单写入 `tool_context.state["todos[:<branch>]"]`，随 function-response 事件的 state delta 自动落库，**跨轮存活**，无需额外存储机制。注意：框架会剥离 `temp:` 前缀的 state，因此 TodoWrite 默认使用无前缀的 `todos`。
- **子 Agent 隔离**：state key 追加 `:<branch>`，不同 branch（父 / 子 Agent）各自维护独立清单，互不覆盖；branch 为空时回退到 agent 名。
- **硬契约校验（代码强制）**：`content` / `activeForm` 非空、**至多一个 `in_progress`**、`content` 全表唯一，违反则工具调用返回 `INVALID_TODOS` 错误。
- **防误删守卫**：缺失 `todos` 字段或 `todos: null` 一律报错；唯一合法的清空手势是显式空数组 `todos: []`，避免上游丢字段误清整张计划。
- **结构化回显**：返回 `{message, todos, oldTodos}` —— `message` 是给模型的 nudge，`todos/oldTodos` 供前端 / CLI 直接渲染当前列表与 diff，无需再查 session。
- **clear_on_all_done**：全部 `completed` 时默认清空列表，避免已完成项跨轮无限堆积（本 demo 显式设为 `False` 以便展示最终全完成态）。
- **NudgeHook 策略回调**：在持久化后、返回前调用的只读回调，返回的非空字符串追加进 message，可用于「预算告警 / 验证提醒 / 死循环检测」等策略而不改工具本体。
- **Prompt 引导分层**：风格建议（恰好一个 in_progress、完成立即标记、不要复述整张清单）放在 `DEFAULT_TODO_PROMPT`，与硬契约清晰分层。

## Agent 层级结构说明

本例只有一个 `LlmAgent`，挂载单个 `TodoWriteTool`；`root_agent` 指向 `todo_planner`：

```text
todo_planner (LlmAgent)
├── model: OpenAIModel
├── instruction: 规划型人设（DEFAULT_TODO_PROMPT 由工具 process_request 自动注入）
├── tools:
│   └── TodoWriteTool(clear_on_all_done=False, nudge_hooks=[_all_done_nudge_hook])
└── session: InMemorySessionService（单一 session 跨轮复用）
```

关键文件：

- [examples/todo_tool/agent/agent.py](./agent/agent.py)：构建 `todo_planner`，挂载 `TodoWriteTool` 并演示自定义只读 `NudgeHook`
- [examples/todo_tool/agent/prompts.py](./agent/prompts.py)：规划型人设 instruction（`DEFAULT_TODO_PROMPT` 由 `TodoWriteTool.process_request` 自动追加）
- [examples/todo_tool/agent/config.py](./agent/config.py)：环境变量读取（LLM 凭据）
- [examples/todo_tool/run_agent.py](./run_agent.py)：测试入口，在**同一个 session** 内驱动「规划 → 逐项完成」多轮对话，并在每轮后用 `get_todos` 读回持久化清单渲染成 ASCII checklist

## 关键代码解释

### 1) 挂载与配置（`agent/agent.py`）

- `TodoWriteTool()` 即可直接用，工具名默认 `todo_write`（snake_case，满足部分 provider 的 `^[a-zA-Z0-9_-]+$` 命名约束）。
- 构造参数：
  - `clear_on_all_done`（默认 `True`）：全部完成时清空列表；本 demo 设为 `False` 以便看到最终「全部 ✅」的清单。
  - `state_key_prefix`（默认 `todos`）：状态 key 前缀；勿用 `temp:`，该前缀不会被 SessionService 持久化。
  - `default_nudge`：每次成功响应追加的基础提醒。
  - `nudge_hooks`：只读策略回调列表。

### 2) 只读 NudgeHook（`_all_done_nudge_hook`）

```python
def _all_done_nudge_hook(old, new):
    if len(new) < 3:
        return None
    if not all(item.status == TodoStatus.COMPLETED for item in new):
        return None
    return "Reminder: all tasks are marked completed. ..."
```

- 签名为 `(old: list[TodoItem], new: list[TodoItem]) -> Optional[str]`，在持久化后、返回前被调用。
- 返回的非空字符串会追加进工具响应的 `message`，让模型看到。
- 约定 **只读**：不可修改清单；本例与 Go `examples/todo` 对齐——当清单 ≥3 项且全部 `completed` 时，提醒模型在收尾前简要总结结果。

### 3) 跨轮持久化与读回（`run_agent.py`）

- 所有轮次共用同一个 `session_id`，每轮一次 `runner.run_async`。
- 工具把清单写进 `todos:<branch>`，随事件 state delta 落库。
- 每轮结束后用 `get_todos(session, branch=agent.name)` 把**持久化后的清单**读回来，证明它跨轮存活，再用 `render_todos` 渲染：
  - `✅` 已完成（completed）
  - `🔄` 进行中（in_progress，显示 `activeForm`）
  - `⬜` 待办（pending）

### 4) 硬契约 vs Prompt 引导

- **硬契约（代码强制）**：`validate_todos` —— 字段非空、至多一个 `in_progress`、`content` 唯一；违反返回 `INVALID_TODOS`。
- **防误删守卫**：缺字段 / `null` 报错，仅 `todos: []` 可清空。
- **Prompt 引导（鼓励不强制）**：`DEFAULT_TODO_PROMPT` 在挂载工具时经 `process_request` 自动追加到 system instruction。
- 原则：要强制就加 validator，不要把约束塞进 prompt，两层保持可区分。

## 环境与运行

### 环境要求

- Python 3.12

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 环境变量要求

在 [examples/todo_tool/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/todo_tool
python3 run_agent.py
```

## 运行结果（示意）

```text
🆔 Session ID: 1a2b3c4d... (shared across all turns)

========== 规划任务 ==========
📝 User: 请规划一个三步任务并用 todo_write 记录：1) 初始化项目骨架 2) 实现核心业务逻辑 3) 编写并跑通单元测试。先整体规划，把第一步设为进行中，其余为待办。
🤖 Assistant:
🔧 [Invoke Tool: todo_write({'todos': [{'content': '初始化项目骨架', 'activeForm': '正在初始化项目骨架', 'status': 'in_progress'}, {'content': '实现核心业务逻辑', 'activeForm': '正在实现核心业务逻辑', 'status': 'pending'}, {'content': '编写并跑通单元测试', 'activeForm': '正在编写单元测试', 'status': 'pending'}]})]
📊 [Tool Result: items=3 old_items=0 [in_progress:初始化项目骨架, pending:实现核心业务逻辑, pending:编写并跑通单元测试]]
我已经把任务拆成三步，现在开始第一步：初始化项目骨架。

📋 Persisted checklist:
🔄 正在初始化项目骨架
⬜ 实现核心业务逻辑
⬜ 编写并跑通单元测试
----------------------------------------

========== 完成第 1 步 ==========
📝 User: 第一步『初始化项目骨架』已经完成了，请更新清单并开始下一步。
🤖 Assistant:
🔧 [Invoke Tool: todo_write({'todos': [{'content': '初始化项目骨架', 'activeForm': '正在初始化项目骨架', 'status': 'completed'}, {'content': '实现核心业务逻辑', 'activeForm': '正在实现核心业务逻辑', 'status': 'in_progress'}, {'content': '编写并跑通单元测试', 'activeForm': '正在编写单元测试', 'status': 'pending'}]})]
📊 [Tool Result: items=3 old_items=3 [completed:初始化项目骨架, in_progress:实现核心业务逻辑, pending:编写并跑通单元测试]]
第一步已完成，现在进行第二步：实现核心业务逻辑。

📋 Persisted checklist:
✅ 初始化项目骨架
🔄 正在实现核心业务逻辑
⬜ 编写并跑通单元测试
----------------------------------------

... (第 2、3 步依次翻转，最终全部 ✅ 完成)
```

## 适用场景建议

- 复杂多步任务（代码生成、多文件改造、调研、部署）需要**规划外显 + 进度可视 + 可控收尾**：直接复用本示例。
- 需要把清单接入前端 / AG-UI 实时渲染：消费工具响应里的 `todos` / `oldTodos`（已是纯 JSON 结构）。
- 需要服务端 / REST / 审计读取当前清单：调用 `get_todos(session, branch)`。
- 需要在工具调用前后插入日志、审计、参数校验：把 `TodoWriteTool(filters_name=[...])` 与 `before_tool_callback` / `after_tool_callback` 组合使用。
- 需要「未完成项不允许收尾」的强约束：用 `LlmAgent` 的 `after_model_callback` / `before_model_callback` 实现 enforcer（参考实现方案文档的 Phase 2）。
