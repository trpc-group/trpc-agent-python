# Task 工具族（任务看板）示例

本示例演示框架内置的 **Task 工具族**（`task_create` / `task_update` / `task_get` / `task_list`），对齐 Claude Code v2.1.142+ 的结构化 Task 能力。与 `TodoWriteTool` 的「整表替换」不同，Task 工具族采用**按 `taskId` 增量更新**模型：任务由服务端分配 `id`，支持 `blockedBy` / `blocks` 依赖编排。任务看板存放在**会话级 state**（key 前缀 `tasks`，**不用** `temp:`），因此可以**跨轮（跨 `Runner.run_async` 调用）存活**。

## 关键特性

- **增量更新语义**：`task_create` 创建任务并返回服务端分配的 `id`；`task_update` 按 `taskId` 局部 patch 状态 / 字段 / 依赖，不必重传整表。
- **依赖编排**：`task_update` 的 `addBlockedBy` / `removeBlockedBy`（及 `addBlocks` / `removeBlocks`）维护双向依赖边；上游任务 `completed` 时自动从下游 `blockedBy` 中移除并报告 `unblocked`。
- **Token 优化**：`task_list` 只返回摘要（`id` / `subject` / `status` / `owner` / `blockedBy`），**刻意省略 `description`**；需要完整详情用 `task_get`。
- **会话级持久化**：整个看板序列化为单个 JSON blob 写入 `tool_context.state["tasks[:<branch>]"]`，随 function-response 事件的 state delta 自动落库，**跨轮存活**。注意：框架会剥离 `temp:` 前缀的 state，因此默认使用无前缀的 `tasks`。
- **子 Agent 隔离**：state key 追加 `:<branch>`，不同 branch（父 / 子 Agent）各自维护独立看板；branch 为空时回退到 agent 名。
- **硬契约校验（代码强制）**：`subject` 非空、状态合法、依赖引用存在、**依赖无环**（`detect_cycle`）、默认**至多一个 `in_progress`**（`enforce_single_in_progress`，可关）；违反返回 `INVALID_ARGS` / `INVALID_DEPENDENCY` / `INVALID_STATUS` / `NOT_FOUND`。
- **ID 不重用**：`highwatermark` 记录曾分配过的最高 id，软删除（`status: deleted`）后也不会复用。
- **Prompt 引导分层**：使用时机与状态机建议放在 `DEFAULT_TASK_PROMPT`，由工具 `process_request` 自动注入（多工具挂载只注入一次），与硬契约清晰分层。

## 与 `TodoWriteTool` 的关系

| 维度 | `todo_write` | Task 工具族 |
| --- | --- | --- |
| 工具数量 | 1 | 4 |
| 更新方式 | 整表替换 | 按 `taskId` 增量 |
| 单项标识 | `content`（唯一键） | `id`（服务端分配） |
| 依赖 | 无 | `blockedBy` / `blocks` |
| state key | `todos[:branch]` | `tasks[:branch]` |
| 适用场景 | 短清单、token 不敏感 | 长任务板、多 Agent、依赖编排 |

> 建议二选一挂载；同时挂载易让模型混用。

## Agent 层级结构说明

本例只有一个 `LlmAgent`，挂载 `TaskToolSet` 与文件/Shell 执行工具；`root_agent` 指向 `task_planner`：

```text
task_planner (LlmAgent)
├── model: OpenAIModel
├── instruction: 工程助手人设（DEFAULT_TASK_PROMPT 由工具 process_request 自动注入）
├── tools:
│   ├── TaskToolSet()  → task_create / task_update / task_get / task_list
│   ├── BashTool(cwd=work_dir)
│   ├── WriteTool(cwd=work_dir)
│   └── ReadTool(cwd=work_dir)
└── session: InMemorySessionService（单一 session 跨轮复用）
```

关键文件：

- [examples/task_tools/agent/agent.py](./agent/agent.py)：构建 `task_planner`，挂载 `TaskToolSet`
- [examples/task_tools/agent/prompts.py](./agent/prompts.py)：规划型人设 instruction（`DEFAULT_TASK_PROMPT` 由 `process_request` 自动追加）
- [examples/task_tools/agent/config.py](./agent/config.py)：环境变量读取（LLM 凭据）
- [examples/task_tools/run_agent.py](./run_agent.py)：测试入口，在**同一个 session** 内驱动两轮静态站点搭建/优化；`task_update` 将状态设为 `in_progress` / `completed` 时实时渲染看板，每轮结束后用 `get_task_store` 读回持久化看板

## 关键代码解释

### 1) 挂载与配置（`agent/agent.py`）

- `TaskToolSet()` 即可直接用，工具名 `task_create` / `task_update` / `task_get` / `task_list`（snake_case，满足部分 provider 的 `^[a-zA-Z0-9_-]+$` 命名约束）。
- 构造参数：
  - `state_key_prefix`（默认 `tasks`）：状态 key 前缀；勿用 `temp:`，该前缀不会被 SessionService 持久化。
  - `enforce_single_in_progress`（默认 `True`）：设置某任务 `in_progress` 时若已有其他 `in_progress` 则拒绝。
  - `inject_prompt`（默认 `True`）：把 `DEFAULT_TASK_PROMPT` 注入 system instruction（多工具挂载只注入一次）。

### 2) 跨轮持久化与 CLI 渲染（`run_agent.py`）

- 所有轮次共用同一个 `session_id`，每轮一次 `runner.run_async`。
- 工具把看板写进 `tasks:<branch>`，随事件 state delta 落库。
- **实时看板**：每次 `task_update` 成功且状态变为 `in_progress` 或 `completed` 时，demo 从 session 读回 `get_task_store` 并打印 `📋 Current task board:`（仅改依赖边的 `task_update` 不触发）。
- **轮末看板**：每轮结束后再次用 `get_task_store(session, branch=agent.name)` 读回持久化结果，打印 `📋 Persisted task board:`，证明跨轮存活。
- 渲染符号与 `render_task_list` 一致：
  - `✅` 已完成（completed）
  - `🔄` 进行中（in_progress，显示 `activeForm`）
  - `⬜` 待办（pending），并标注 `blocked by: <ids>`

### 3) 硬契约 vs Prompt 引导

- **硬契约（代码强制）**：`subject` 非空、状态合法、依赖存在且无环、至多一个 `in_progress`；违反返回明确错误码。
- **Prompt 引导（鼓励不强制）**：`DEFAULT_TASK_PROMPT` 在挂载工具时经 `process_request` 自动追加到 system instruction。
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

在 [examples/task_tools/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/task_tools
python3 run_agent.py
```

## Demo 状态转换流程

`run_agent.py` 在**同一个 session** 内驱动 2 轮对话：Turn 1 用 `task_create` 规划静态站点并逐项执行；Turn 2 追加 CSS/JS 相关任务并更新 README。典型工具链：

```text
Turn 1 搭建静态站点
  task_create ×3 → task_update addBlockedBy → task_update in_progress
    → Bash / Write 执行 → task_update completed（每步 in_progress / completed 后打印 Current task board）

Turn 2 优化静态站点
  task_create ×4（id 从 #4 起）→ task_update 依赖与状态 → Bash / Write 执行
    → task_update completed（轮末 Persisted task board 含 #1–#7）
```

## 运行结果（示意）

```text
🆔 Session ID: 71bb460a... (shared across all turns)
📂 Work dir: /path/to/examples/task_tools

========== 搭建静态站点 ==========
📝 User: 请帮我在当前目录搭建 demo 静态站点 ...
🔧 [Invoke Tool: task_create({'subject': '创建 demo/ 及子目录 css/、js/', ...})]
📊 [Tool Result: created id=1 subject='创建 demo/ 及子目录 css/、js/']
🔧 [Invoke Tool: task_update({'taskId': '2', 'addBlockedBy': ['1']})]
📊 [Tool Result: updated id=2 status=pending]
🔧 [Invoke Tool: task_update({'taskId': '1', 'status': 'in_progress'})]
📊 [Tool Result: updated id=1 status=in_progress]

📋 Current task board:
   🔄 #1 创建 demo/ 及子目录 css/、js/
   ⬜ #2 创建 demo/index.html (blocked by: 1)
   ⬜ #3 创建 demo/README.md (blocked by: 1)

🔧 [Invoke Tool: task_update({'taskId': '1', 'status': 'completed'})]
📊 [Tool Result: updated id=1 status=completed unblocked=['2', '3']]

📋 Current task board:
   ✅ #1 创建 demo/ 及子目录 css/、js/
   ⬜ #2 创建 demo/index.html
   ⬜ #3 创建 demo/README.md

📋 Persisted task board:
✅ #1 创建 demo/ 及子目录 css/、js/
✅ #2 创建 demo/index.html
✅ #3 创建 demo/README.md
----------------------------------------

========== 优化静态站点 ==========
🔧 [Invoke Tool: task_create({'subject': '创建 demo/css/style.css', ...})]
📊 [Tool Result: created id=4 subject='创建 demo/css/style.css']
... (Turn 2 追加 #4–#7，id 延续 Turn 1)

📋 Persisted task board:
✅ #1 创建 demo/ 及子目录 css/、js/
✅ #2 创建 demo/index.html
✅ #3 创建 demo/README.md
✅ #4 创建 demo/css/style.css
✅ #5 创建 demo/js/app.js
✅ #6 更新 demo/index.html
✅ #7 更新 demo/README.md
----------------------------------------
```

## 适用场景建议

- 长任务板、需要**显式依赖编排**或跨多轮跟踪：用 `TaskToolSet`。
- 需要服务端 / REST / 审计读取当前看板：调用 `get_task_store(session, branch)`。

## 相关示例

- [task_tools_parallel](../task_tools_parallel/) — 验证 `parallel_tool_calls=True` 与 `task_store_lock` 下的并行 `task_create` / `task_update`（Phase 1–2 无需 API Key）。
