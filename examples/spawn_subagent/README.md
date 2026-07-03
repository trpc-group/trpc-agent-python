# SpawnSubAgentTool 使用示例

演示 `SpawnSubAgentTool` 的用法——将复杂子任务派发给标准化的短期专家 Agent 处理。

## 三种接入方式

- **零配置**：`SpawnSubAgentTool()` — 仅 `default` 子 Agent（中性任务执行者，继承主 Agent 工具）
- **代码定义**：`SpawnSubAgentTool(agents=[security_auditor, EXPLORE_AGENT])` — 代码中定义子 Agent
- **MD 文件定义**：`SpawnSubAgentTool(agent_paths=[".trpc_agents/"])` — 从 Markdown 文件加载子 Agent

无论哪种方式，子 Agent 的 instruction、工具集、模型都被锁定——LLM 只能选择 `subagent_type` 和写 `prompt`，无法在调用时改写子 Agent 的角色。

## 运行

```bash
# 零配置（仅 default 子 Agent）
python run_agent.py

# 代码定义子 Agent（security-auditor + Explore + Plan）
python run_agent.py --mode code

# MD 文件定义子 Agent
python run_agent.py --mode md
```

## 结构

```
coding_assistant (LlmAgent)
├── tools: ReadTool, GlobTool, GrepTool
├── tools: SpawnSubAgentTool
│   ├── default（内置，默认注册，继承主 Agent 工具）
│   ├── Explore / Plan（内置，按需注册，只读工具集）
│   └── security-auditor（代码或 MD 自定义）
└── sample_repo/（共享微型示例代码库）
```

## 关键代码

```python
from trpc_agent_sdk.agents.sub_agent import EXPLORE_AGENT, PLAN_AGENT
from trpc_agent_sdk.tools import SpawnSubAgentTool

# 零配置 —— 仅 default 子 Agent
SpawnSubAgentTool()

# 追加内置子 Agent
SpawnSubAgentTool(agents=[EXPLORE_AGENT, PLAN_AGENT])

# 从 MD 文件加载
SpawnSubAgentTool(agent_paths=[".trpc_agents/"])
```

## 适用场景

- 有预定义的标准化专家角色（安全审计、代码探索、架构规划），且每次派发都是独立的一次性任务
- 需要非开发者通过 MD 文件维护专家定义，但专家角色本身需要被锁定
- 需要 instruction/tools 锁定的安全护栏，防止 LLM 在调用时越权改写子 Agent 角色
