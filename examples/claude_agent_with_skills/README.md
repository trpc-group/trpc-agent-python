# Claude Agent 使用 Skill 能力示例

本示例演示如何基于 `ClaudeAgent` 结合 Skill 机制构建一个旅游规划助手，并验证 `Skill 发现 + Skill 调用 + Tool Calling` 的核心链路是否正常工作。

## 关键特性

- **Skill 能力注入**：通过 `ClaudeAgentOptions` 配置 `setting_sources`，支持从用户级（`~/.claude/skills`）和项目级（`cwd/.claude/skills`）两个路径自动发现 Skill
- **Skill 调用链路**：Claude Agent SDK 以 Tool 调用方式驱动 Skill 执行，需在 `allowed_tools` 中显式配置 `"Skill"`
- **工具调用能力**：通过 `FunctionTool` 接入 `get_current_date` 工具，为 Skill 提供实时日期信息
- **Prompt 模板注入**：在提示词中定义 Agent 角色与行为约束，引导 Agent 正确选择并调用 Skill
- **流式事件处理**：通过 `runner.run_async(...)` 处理 partial/full event，并打印工具调用与工具返回

## Agent 层级结构说明

本例是单 Agent 示例，通过 Skill 扩展 Agent 能力，不涉及多 Agent 分层路由：

```text
travel_planner (ClaudeAgent)
├── model: OpenAIModel
├── tools:
│   └── get_current_date()
├── skills (via setting_sources):
│   ├── user: ~/.claude/skills/*
│   └── project: cwd/.claude/skills/traver_helper/SKILL.md
├── claude_agent_options:
│   ├── cwd: examples/claude_agent_with_skills
│   ├── setting_sources: ["user", "project"]
│   └── allowed_tools: ["Skill"]
└── session: InMemorySessionService
```

关键文件：

- [examples/claude_agent_with_skills/agent/agent.py](./agent/agent.py)：构建 `ClaudeAgent`、配置 Skill 数据源与 Tool、设置生成参数
- [examples/claude_agent_with_skills/agent/tools.py](./agent/tools.py)：`get_current_date` 工具实现
- [examples/claude_agent_with_skills/agent/prompts.py](./agent/prompts.py)：提示词模板
- [examples/claude_agent_with_skills/agent/config.py](./agent/config.py)：环境变量读取
- [examples/claude_agent_with_skills/run_agent.py](./run_agent.py)：测试入口，执行旅游规划对话
- [examples/claude_agent_with_skills/.claude/skills/traver_helper/SKILL.md](./.claude/skills/traver_helper/SKILL.md)：旅游规划 Skill 定义

## 关键代码解释

这一节用于快速定位"Skill 配置、工具调用、事件输出"三条核心链路。

### 1) Agent 组装与 Skill 配置（`agent/agent.py`）

- 使用 `ClaudeAgent` 组装旅游规划助手，挂载 `FunctionTool(get_current_date)`
- 通过 `ClaudeAgentOptions` 配置 Skill 发现路径：
  - `cwd`：设置为示例目录，用于定位项目级 Skill
  - `setting_sources=["user", "project"]`：同时从用户级和项目级路径加载 Skill
  - `allowed_tools=["Skill"]`：显式允许 Skill 工具调用
- 通过 `GenerateContentConfig` 设置 `temperature`、`top_p`、`max_output_tokens`

### 2) Skill 定义（`.claude/skills/traver_helper/SKILL.md`）

- Skill 以 Markdown 文件定义，包含 YAML Front Matter（`name`、`description`）和详细工作流程
- 工作流程涵盖信息收集、搜索工具调用、生成完整旅游方案等步骤
- Claude Agent SDK 根据 `description` 自动匹配用户意图并触发对应 Skill

### 3) 流式事件处理与可观测输出（`run_agent.py`）

- 使用 `runner.run_async(...)` 消费事件流
- `event.partial=True` 时打印文本分片
- 完整事件中区分并打印：
  - `function_call`（工具调用 / Skill 调用）
  - `function_response`（工具返回）

## 环境与运行

### 环境要求

- Python 3.10+（强烈建议 3.12）

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e '.[agent-claude]'
```

### Skill 前置配置

1. 在项目目录或根目录（`~`）创建 `.claude/skills` 目录：
   - **用户级**（跨项目）：`~/.claude/skills/`
   - **项目级**（当前项目）：`<cwd>/.claude/skills/`
2. 在 `skills` 目录下创建 Skill 子目录（如 `traver_helper`），并在其中编写 `SKILL.md`
3. Skill 格式参考：[Claude Agent SDK Skills 文档](https://platform.claude.com/docs/zh-CN/agents-and-tools/agent-skills/overview#skill)

### 环境变量要求

在 [examples/claude_agent_with_skills/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/claude_agent_with_skills
python3 run_agent.py
```

## 运行结果（实测）

```text
[2026-04-02 20:49:23][INFO][trpc_agent_sdk][trpc_agent_sdk/server/agents/claude/_setup.py:227][93216] Proxy server proxy process started (PID: 93246)
[2026-04-02 20:49:23][INFO][trpc_agent_sdk][trpc_agent_sdk/server/agents/claude/_setup.py:244][93216] Proxy server is ready at http://0.0.0.0:8082
[2026-04-02 20:49:23][INFO][trpc_agent_sdk][trpc_agent_sdk/server/agents/claude/_runtime.py:27][93216] ClaudeAgent event loop thread started
🆔 Session ID: 1ae0cd79...
📝 User: Help me create a travel itinerary for Beijing
🤖 Assistant:
🔧 [Invoke Tool:: Skill({'skill': 'traver_helper', 'args': 'destination=Beijing'})]
📊 [Tool Result: {'result': 'Launching skill: traver_helper'}]
The travel planning assistant is now generating a comprehensive itinerary for your trip to Beijing. Please hold on while I gather all the necessary details for your travel plan.
----------------------------------------
[2026-04-02 20:49:32][INFO][trpc_agent_sdk][trpc_agent_sdk/server/agents/claude/_runtime.py:39][93216] ClaudeAgent event loop thread stopped
[2026-04-02 20:49:32][INFO][trpc_agent_sdk][trpc_agent_sdk/server/agents/claude/_runtime.py:62][93216] ClaudeAgent thread terminated successfully
[2026-04-02 20:49:32][INFO][trpc_agent_sdk][trpc_agent_sdk/server/agents/claude/_setup.py:294][93216] Proxy process already stopped.
🧹 Claude environment cleaned up
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **Skill 发现正确**：Agent 成功从项目级 `.claude/skills/traver_helper/SKILL.md` 加载了旅游规划 Skill
- **Skill 路由正确**：用户提出旅游规划需求时，Agent 自动匹配并调用 `traver_helper` Skill
- **工具参数正确**：Skill 调用时传递了 `destination=Beijing` 参数，符合用户意图
- **Skill 结果被正确消费**：回复内容表明 Skill 已启动并开始生成旅游方案

说明：该示例使用单轮对话验证 Skill 发现与调用链路，主要验证的是 Skill 配置、匹配与调用能力。

## 适用场景建议

- 快速验证 Claude Agent + Skill 调用主链路：适合使用本示例
- 验证用户级/项目级 Skill 的发现与加载机制：适合使用本示例
- 需要测试普通 Tool Calling（不含 Skill）：建议使用 `examples/claude_agent`
- 需要测试 LlmAgent + Tool Calling：建议使用 `examples/llmagent`
