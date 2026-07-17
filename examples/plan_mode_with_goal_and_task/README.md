# Plan Mode + Goal + Task 示例

演示 **Plan Mode**、**Goal**、**Task** 三种能力的组合使用：先规划并人工审批，再用 Goal 锁定会话完成契约，用 Task 看板跟踪实施步骤。

## 它解决什么问题

| 能力 | 职责 |
| --- | --- |
| **Plan Mode** | 调研 → 起草计划 → 人工审批 → 解锁写工具 |
| **Goal** | 会话级持久目标；active 期间不允许过早收尾 |
| **Task** | 批准后按 id 增量更新任务看板，支持依赖编排 |

三者分工：Plan 管「设计文档 + 写操作 gate」，Task 管步骤分解，Goal 管「整件事是否做完」。

## 目录结构

```
plan_mode_with_goal_and_task/
├── agent/
│   ├── agent.py        # orchestrator：FileToolSet + SpawnSubAgentTool + TaskToolSet + setup_plan + setup_goal
│   ├── prompts.py      # 组合 workflow 的 system instruction
│   └── __init__.py
├── static/
│   └── index.html      # AG-UI 浏览器 Demo（计划 + 目标 + 任务三栏侧板）
├── run_agent_with_agui.py
├── .env
└── README.md
```

## 架构

```
orchestrator (LlmAgent)
├── FileToolSet              # Read/Grep/Glob 始终可用；Write/Edit/Bash 计划批准后解锁
├── SpawnSubAgentTool        # Explore / Plan 只读子 agent
├── TaskToolSet              # task_create / task_update / task_get / task_list（gate 期间被拦截）
├── PlanToolSet (setup_plan) # enter_plan_mode / update_plan_content / exit_plan_mode / ask_user_question
└── GoalToolSet (setup_goal) # create_goal / get_goal / update_goal + enforcement callbacks
```

典型流程：

1. 用户描述多步需求（或 UI 切换 Plan 模式）
2. 进入 Plan Mode → 只读调研 → 撰写计划 → `exit_plan_mode` 等待审批
3. 审批通过后 → `create_goal` 设定会话目标 → `task_create` 分解实施步骤
4. 逐步执行 Write / Bash，`task_update` 更新进度
5. 全部完成后 `update_goal(complete)`

> Plan gate 激活期间，`task_create` / `task_update` / `create_goal` / `update_goal` 会被 `PLAN_MODE_GATE` 拦截（见 `DEFAULT_WRITE_TOOL_NAMES`）。

## 前置条件

```bash
cd trpc-agent-python
python3 -m venv .venv && source .venv/bin/activate
pip3 install -e '.[ag-ui]'

# 配置 examples/plan_mode_with_goal_and_task/.env
TRPC_AGENT_API_KEY=<你的 key>
TRPC_AGENT_BASE_URL=<可选>
TRPC_AGENT_MODEL_NAME=<可选，默认 gpt-4.1-mini>
```

## 运行

```bash
cd examples/plan_mode_with_goal_and_task
python3 run_agent_with_agui.py
```

启动后打开 <http://127.0.0.1:18091/>（默认端口 **18091**，与 `plan_mode` 示例的 18090 区分）。

## 浏览器 Demo 说明

侧板实时展示三块 session state（均来自 AG-UI `STATE_SNAPSHOT`）：

| 面板 | state key | 说明 |
| --- | --- | --- |
| 当前计划 | `plan:orchestrator` | 计划状态、目标、正文、澄清问答 |
| 会话目标 | `goal:orchestrator` | Goal 的 objective / status |
| 任务看板 | `tasks:orchestrator` | TaskStore（id、subject、status、blockedBy） |

HITL 交互与 [plan_mode](../plan_mode/) 相同：`enter_plan_mode` / `exit_plan_mode` / `ask_user_question` 卡片 + UI Plan 模式自动进入。

建议试用 prompt：

```
帮我参考 QQ 音乐生成一个类似的前端项目
```

切换 **Plan** 模式后发送，可跳过 `enter_plan_mode` 确认，直接进入规划。

## 相关文档

- [Plan Mode](../../docs/mkdocs/zh/plan.md)
- [Goal 工具](../../docs/mkdocs/zh/goal.md)
- [Task 工具族](../../docs/mkdocs/zh/tool_task.md)
