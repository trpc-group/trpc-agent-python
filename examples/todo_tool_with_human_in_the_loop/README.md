# TodoWriteTool + Human-in-the-Loop 示例

本示例在 [TodoWriteTool 任务清单示例](../todo_tool/README.md) 之上，演示 **Human-in-the-Loop（人机协同）** 与 **真实文件执行** 的组合流程：

1. Agent 提交多步计划 → `request_todo_plan_approval` 触发 `LongRunningEvent` 暂停；
2. 人工审阅 / 修改计划（demo 中模拟为**追加一条 todo**）→ 通过 `FunctionResponse` 恢复；
3. Agent 调用 `todo_write` 持久化清单，再用 `Bash` / `Write` / `Read` 逐步执行；
4. 清单跨轮保存在 session state，每轮结束后 CLI 读回并渲染 ASCII checklist。

Demo 场景：在当前工作目录搭建 `demo/` 静态站点（目录结构 + HTML + CSS + JS），审批阶段人工追加「生成 README 文件」任务。

## 关键特性

- **计划审批门禁**：`request_todo_plan_approval` 经 `LongRunningFunctionTool` 包装，返回 `pending_approval` 后触发 `LongRunningEvent`，Agent 暂停等待人工介入。
- **审批时可改计划**：人工可在 `FunctionResponse.response` 中修改 `todos` 列表（本 demo 自动追加「生成 README 文件」），Agent 恢复后按**更新后的清单**执行。
- **审批后持久化 + 逐步执行**：恢复后先 `todo_write`，再 `Bash` / `Write` / `Read` 落地文件；工具成功后才标记 `completed`。
- **文件执行能力**：挂载 `BashTool`、`WriteTool`、`ReadTool`，工作目录默认为 `run_agent.py` 的当前目录。
- **继承 TodoWrite 能力**：整表替换、硬契约校验、NudgeHook、跨轮持久化等与 `todo_tool` 示例一致。
- **启动清理**：每次运行前删除已有 `demo/` 目录，便于重复验证。

## Agent 层级结构

```text
todo_planner (LlmAgent)
├── model: OpenAIModel
├── instruction: 工程助手人设（DEFAULT_TODO_PROMPT 由 TodoWriteTool 自动注入）
├── tools:
│   ├── request_todo_plan_approval (LongRunningFunctionTool)  ← 计划审批
│   ├── TodoWriteTool(clear_on_all_done=False, nudge_hooks=[...])  ← 清单持久化
│   ├── BashTool(cwd=work_dir)   ← mkdir 等 shell 操作
│   ├── WriteTool(cwd=work_dir)  ← 写 HTML / CSS / JS / README
│   └── ReadTool(cwd=work_dir)   ← 读回验证
└── session: InMemorySessionService（单一 session）
```

关键文件：

- [agent/tools.py](./agent/tools.py)：`request_todo_plan_approval`（校验 + 返回 `preview`）
- [agent/agent.py](./agent/agent.py)：组装 Agent 与全部工具
- [agent/prompts.py](./agent/prompts.py)：Agent 人设 instruction
- [agent/config.py](./agent/config.py)：LLM 环境变量
- [run_agent.py](./run_agent.py)：驱动对话、捕获 HITL 事件、模拟人工改计划、恢复执行

## 关键代码解释

### 1) 审批工具（`agent/tools.py`）

```python
async def request_todo_plan_approval(todos: list, summary: str = "") -> dict:
    # 与 todo_write 相同的 validate_todos 硬契约
    return {
        "status": "pending_approval",
        "todos": [...],
        "preview": render_todos(items),  # ASCII checklist，供 CLI / 前端展示
        ...
    }
```

- 不写入 session state，仅提交待审计划。
- `preview` 可直接展示给审批人。

### 2) 人工改计划并恢复（`run_agent.py` · `_build_approval_resume`）

本 demo **不在终端等待真实输入**，而是在审批回调里模拟人工编辑计划：

```python
todos = list(response_data.get("todos") or [])
todos.append({
    "content": "生成 README 文件",
    "activeForm": "正在生成 README 文件",
    "status": "pending",
})
response_data["status"] = "approved"
response_data["todos"] = todos  # 把修改后的完整列表回填给 Agent
```

Agent 恢复后会看到 `message` 里说明「已追加 README todo」，并应用更新后的 `todos` 调用 `todo_write`。

生产环境 / AG-UI 前端：捕获 `LongRunningEvent`，让用户在前端增删改 todo 后，构造同样的 `FunctionResponse` 提交即可。参见 [llmagent_with_human_in_the_loop](../llmagent_with_human_in_the_loop/README.md)。

### 3) HITL 事件捕获（`run_agent.py` · `_consume_run`）

```python
async for event in runner.run_async(...):
    if isinstance(event, LongRunningEvent):
        # 展示 function_call.args 与 preview，Agent 暂停
        captured = event

# 构造 FunctionResponse 恢复
resume_content = Content(role="user", parts=[
    Part(function_response=FunctionResponse(
        id=event.function_response.id,
        name=event.function_response.name,
        response=response_data,  # status=approved + 修改后的 todos
    ))
])
await runner.run_async(..., new_message=resume_content)
```

### 4) 文件工具（`agent/agent.py`）

```python
cwd = work_dir or os.getcwd()
bash_tool = BashTool(cwd=cwd)
write_tool = WriteTool(cwd=cwd)
read_tool = ReadTool(cwd=cwd)
```

工具名必须为 `Bash` / `Write` / `Read`（区分大小写），由框架 schema 暴露给模型。

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

### 环境变量

在 [examples/todo_tool_with_human_in_the_loop/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/todo_tool_with_human_in_the_loop
python3 run_agent.py
```

运行后会在**当前目录**生成 `demo/` 项目；再次运行会先清理旧目录。

可在 [run_agent.py](./run_agent.py) 的 `TURNS` 中扩展更多轮次（`(label, query)` 二元组）。

## 运行结果（示意）

```text
🧹 Cleaned previous .../demo
🆔 Session ID: 57780a8a... (shared across all turns)
📂 Work dir: .../todo_tool_with_human_in_the_loop

========== 搭建静态站点 ==========
📝 User: 请帮我在当前目录搭建一个 demo 静态站点...
🔧 [Invoke Tool: request_todo_plan_approval({...})]

🔄 [Long-running operation detected — waiting for human approval]
   Proposed checklist:
     ⬜ 创建 demo/、demo/css/、demo/js/ 目录结构
     ⬜ 创建 demo/index.html ...
     ⬜ 创建 demo/css/style.css ...
     ⬜ 创建 demo/js/app.js ...

👤 [Human approval with plan edit]
   Decision: approved by demo_user
   Edit: added todo → 生成 README 文件
   Updated checklist:
     ⬜ 创建 demo/ ...
     ...
     ⬜ 生成 README 文件          ← 人工追加

🔄 Resuming agent after human approval...
🔧 [Invoke Tool: todo_write({...})]
🔧 [Invoke Tool: Bash({'command': 'mkdir -p demo/css demo/js'})]
🔧 [Invoke Tool: Write({'path': 'demo/index.html', ...})]
...
📋 Persisted checklist:
✅ 创建 demo/ ...
✅ 生成 README 文件
----------------------------------------
```

## 适用场景

| 需求 | 建议 |
|------|------|
| 执行前需人工确认 / 修改计划 | 复用 `request_todo_plan_approval` + `FunctionResponse` 改 `todos` |
| 计划 + 文件操作 + 进度跟踪 | 本示例（TodoWrite + File Tools + HITL） |
| AG-UI / REST 接入审批 UI | 捕获 `LongRunningEvent`，前端回填 `FunctionResponse` |
| 仅需 TodoWrite，无审批 | [todo_tool](../todo_tool/README.md) |
| 仅需 HITL 机制演示 | [llmagent_with_human_in_the_loop](../llmagent_with_human_in_the_loop/README.md) |

相关文档：[human_in_the_loop.md](../../docs/mkdocs/zh/human_in_the_loop.md) · [tool.md（File Tools）](../../docs/mkdocs/zh/tool.md)
