# Goal 工具示例

演示 **Goal 工具族**（`create_goal` / `get_goal` / `update_goal`）：为会话设置一个持久目标，目标未完成前 Agent 应继续执行，而不是过早给出最终回复。示例同时挂载 `Bash` / `Write` / `Read` 完成真实的多步文件任务。

## 快速开始

```bash
# 在项目根目录安装
cd trpc-agent-python
python3 -m venv .venv && source .venv/bin/activate
pip3 install -e .

# 配置模型（examples/goal_tools/.env）
TRPC_AGENT_API_KEY=your-api-key
TRPC_AGENT_BASE_URL=your-base-url
TRPC_AGENT_MODEL_NAME=your-model-name

# 运行
cd examples/goal_tools
python3 run_agent.py
```

## 两个演示场景

脚本会**依次**跑两个独立 session：

| | Case 1 | Case 2 |
| --- | --- | --- |
| 谁设目标 | 模型调用 `create_goal` | 宿主调用 `start_goal()` 预注入 |
| 用户消息 | 普通多步任务（可提及 goal 能力） | 只描述要做的事，不提 goal 工具 |
| 典型流程 | `create_goal` → 写文件 → 验证 → `update_goal(complete)` | 写文件 → （可能被拦截）→ 补全 → `update_goal(complete)` |

只跑其中一个时，在 `run_agent.py` 的 `main()` 里注释掉对应 case。

## 日志说明

| 输出 | 含义 |
| --- | --- |
| `🔧 [Tool call]` | 模型发起的工具调用 |
| `📊 [Tool result]` | 工具返回摘要 |
| `💬 ...` | 工具返回里的 `message`（给模型看的提示） |
| `⚡ [Goal retry]` | 目标仍 active 时模型想提前收尾，被拦截并继续执行 |
| `🤖 Assistant:` | 本轮最终回复（正常应在 `update_goal(complete)` 之后） |
| `🎯 Persisted goal` | 从 session 读回的目标状态 |

终端较窄时，长参数的工具调用行可能折行，看起来像重复打印，以 `📊` 条数为准。

## 运行结果（实测摘录）

### Case 1：模型自己设 Goal

```text
🔧 [Tool call]   create_goal({...})
📊 [Tool result] created id=... status=active objective='...'
💬 Goal created and is now active. Keep working until it is genuinely met, then call update_goal('complete').

🔧 [Tool call]   Write({'path': 'mypkg/__init__.py', ...})
🔧 [Tool call]   Write({'path': 'mypkg/utils.py', ...})
🔧 [Tool call]   Write({'path': 'mypkg/README.md', ...})
...
🔧 [Tool call]   Bash({'command': 'ls -la mypkg/ && ... python -c "import mypkg; ..."'})
🔧 [Tool call]   update_goal({'status': 'complete'})

🤖 Assistant: 工具包 `mypkg/` 已搭建完成并验证通过 ✅

🎯 Persisted goal (read from session):
✅ Goal [complete]
```

本例未触发 `⚡ [Goal retry]`，说明模型在标记完成前没有过早收尾。

### Case 2：宿主预注入 Goal

```text
🎯 Goal pre-injected by host:
   objective: '在当前目录创建 notes/ 目录，其中包含两个文件：...'
   status:    active

🔧 [Tool call]   Write({'path': 'notes/summary.txt', ...})
  ⚡ [Goal retry] Premature final intercepted (attempt 1/3). Objective: '...'

🔧 [Tool call]   Write({'path': 'notes/example.py', ...})
  ⚡ [Goal retry] Premature final intercepted (attempt 2/3). Objective: '...'

🔧 [Tool call]   get_goal({})
🔧 [Tool call]   Bash({'command': 'cd notes && python example.py', ...})
🔧 [Tool call]   update_goal({'status': 'complete'})

🤖 Assistant: 已完成。创建了 `notes/` 目录，其中包含两个文件：...

🎯 Persisted goal (read from session):
✅ Goal [complete]
```

Case 2 里写完第一个文件后模型就想总结，**Goal enforcement 拦截了 2 次**，随后补写 `example.py`、运行验证，再 `update_goal(complete)`——这正是 Goal 能力的预期表现。

## 关键文件

- [`run_agent.py`](./run_agent.py) — 入口，驱动两个 case 并打印事件
- [`agent/agent.py`](./agent/agent.py) — 组装 Agent，调用 `setup_goal()` 挂载 Goal 能力
