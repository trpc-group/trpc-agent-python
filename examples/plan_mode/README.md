# Plan Mode 示例

演示 tRPC-Agent-Python 的 **Plan Mode**：用 `setup_plan()` 给 LLM Agent 装上「先规划、后实施」的工作流，并配合 `SpawnSubAgentTool`（Explore / Plan 两种子 agent 原型）完成代码探索与方案设计。

## 它解决什么问题

普通 coding agent 容易上来就改文件、边写边改方向。Plan Mode 把流程拆成两段：

1. **规划阶段** —— 模型只能用只读工具（Read / Grep / Glob）和子 agent（Explore / Plan）调研代码、撰写计划文档；写文件、改代码、执行命令等「副作用」工具被自动 gate，直到计划被人工批准。
2. **实施阶段** —— 计划通过后，写工具自动解锁，模型按照批准的计划落地实现，并可用 `todo_write` 跟踪进度。

规划期间需要人工介入的三个动作——`enter_plan_mode` / `exit_plan_mode` / `ask_user_question`——都是 `LongRunningFunctionTool`，运行会暂停等待人类响应。这种交互用浏览器页面承载比 CLI 更自然，所以本示例同时提供了一个 AG-UI 服务端和一张零依赖的静态页面。

## 目录结构

```
plan_mode/
├── agent/
│   ├── agent.py        # orchestrator agent 定义：FileToolSet + SpawnSubAgentTool + TodoWriteTool + setup_plan
│   ├── prompts.py      # system instruction
│   └── __init__.py
├── static/
│   └── index.html      # AG-UI 浏览器 Demo（单文件，无构建依赖）
├── run_agent_with_agui.py  # FastAPI + AG-UI 服务端，托管 agent 接口与静态页
├── .env                # 模型配置（TRPC_AGENT_API_KEY / BASE_URL / MODEL_NAME）
└── README.md
```

## 架构

```
orchestrator (LlmAgent + setup_plan)
├── FileToolSet         # Read/Grep/Glob 始终可用；Write/Edit/Bash 在计划批准后解锁
├── SpawnSubAgentTool(EXPLORE_AGENT, PLAN_AGENT)  # 只读调研与方案设计子 agent
├── TodoWriteTool       # 实施阶段用于跟踪进度
└── PlanToolSet         # enter_plan_mode / update_plan_content / exit_plan_mode / ask_user_question
```

- 计划文档持久化在**主 agent 的 session** 中（`state["plan"]`）。
- 被 spawn 出来的子 agent 只返回文本，不直接改动主 agent 的状态。

## 前置条件

```bash
# 1. 安装 SDK（含 AG-UI 依赖）
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .

# 2. 配置模型访问
# 复制并填写 examples/plan_mode/.env：
TRPC_AGENT_API_KEY=<你的 key>
TRPC_AGENT_BASE_URL=<可选，自定义 endpoint>
TRPC_AGENT_MODEL_NAME=<可选，默认 gpt-4.1-mini>
```

## 运行

```bash
cd examples/plan_mode

# 完整 agent + 基于 AG-UI 的浏览器页面（推荐）
python3 run_agent_with_agui.py

# 自定义监听地址 / 端口
python3 run_agent_with_agui.py --host 0.0.0.0 --port 8080
```

启动后控制台会打印三个地址：

```
Plan Mode AG-UI demo:  http://127.0.0.1:18090/
Agent endpoint:        http://127.0.0.1:18090/plan_agent
Health check:          http://127.0.0.1:18090/health
```

> 未设置 `TRPC_AGENT_API_KEY` 时服务仍能启动，但会打印警告，agent 调用会失败。

## 浏览器 Demo 说明

打开 <http://127.0.0.1:18090/> ——静态页面和 agent 接口（`/plan_agent`）由同一个 FastAPI 应用提供，因此页面可同源调用接口，无需任何 CORS 配置。

页面提供：

- **聊天面板** —— 与 orchestrator 对话。
- **实时计划面板** —— 根据 AG-UI 每次运行结束发送的 `STATE_SNAPSHOT` 事件更新（解析 `plan:orchestrator` 这个 session state key，见 `trpc_agent_sdk/plan_mode/_helpers.py:state_key`）。

三种 HITL 交互的页面表现：

| 模型动作 | 页面表现 | 用户操作后续跑格式 |
|---|---|---|
| `enter_plan_mode` | 展示「确认进入 Plan Mode」卡片 | 确认后进入 `exploring` 状态，开启写工具 gate |
| `exit_plan_mode` | 展示「通过 / 拒绝计划」卡片 | `{"role":"tool","toolCallId":...,"content":"{\"status\":\"approved\",...}"}` |
| `ask_user_question` | 展示问题与选项卡片 | 同上；问题文本/选项/`question_id` 取自状态快照中的 `askedQuestions` |

> `exit_plan_mode` 触发时本次运行会在没有 `TOOL_CALL_RESULT` 的情况下结束——这是 AG-UI 表示「长时间运行的工具调用被暂停」的方式。点击按钮续跑的报文格式，与任何宿主应用需满足的 `process_hitl_function_response` 期望格式一致。

静态页 `static/index.html` 是一个**无任何依赖的单文件**（无需构建、无需 npm install），可直接在浏览器打开。如需用 Node 驱动同一接口（例如写测试脚本），参考 [examples/agui/client_js](../agui/client_js) 中的 `@ag-ui/client` 写法。
