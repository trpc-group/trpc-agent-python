# TeamAgent + Claude 成员示例

本示例演示如何在 Team 场景中把 `ClaudeAgent` 作为成员代理接入，由 Leader 进行任务委派并汇总结果。

## 关键特性

- **异构团队编排**：Leader 使用 `LlmAgent`，成员使用 `ClaudeAgent`
- **委派链路清晰**：通过 `delegate_to_member` 把天气任务派发给 `weather_expert`
- **成员工具执行**：Claude 成员调用 `mcp__weather_expert_tools__get_weather` 返回结构化结果
- **资源生命周期管理**：示例覆盖代理进程启动、会话结束、线程退出和环境清理

## Agent 层级结构说明

```text
assistant_team (Leader, LlmAgent)
└── weather_expert (ClaudeAgent member)
    └── tool: mcp__weather_expert_tools__get_weather
```

关键文件：

- [examples/team_member_agent_claude/agent/agent.py](./agent/agent.py)
- [examples/team_member_agent_claude/agent/prompts.py](./agent/prompts.py)
- [examples/team_member_agent_claude/agent/tools.py](./agent/tools.py)
- [examples/team_member_agent_claude/run_agent.py](./run_agent.py)

## 关键代码解释

### 1) Leader 委派成员

- Leader 收到用户请求后调用 `delegate_to_member`
- 委派载荷中包含 `member_name` 与具体任务文本

### 2) Claude 成员执行工具

- `weather_expert` 调用 MCP 天气工具获取城市天气
- 工具结果回传后由成员生成答复，再由 Leader 汇总给用户

### 3) Claude 环境清理

- 运行日志包含 proxy 进程启动、event loop thread 生命周期和最终清理
- 结束时出现 `Cleaned up Claude environment`，说明清理流程完整

## 环境与运行

### 环境要求

- Python 3.12

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .[agent-claude]
```

### 环境变量要求

在 [examples/team_member_agent_claude/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/team_member_agent_claude
python3 run_agent.py
```

## 运行结果（实测）

```text
[Turn 1] User: What's the weather in Beijing?
[assistant_team] Tool: delegate_to_member, Args: {'member_name': 'weather_expert', 'task': "What's the current weather in Beijing?"}
[weather_expert] Tool: mcp__weather_expert_tools__get_weather, Args: {'city': 'Beijing'}
[weather_expert] Tool Response: {'result': 'Beijing: Sunny, 25C, humidity 45%'}
The current weather in Beijing is sunny with a temperature of 25°C and humidity at 45%.

[Turn 2] User: How about Shanghai?
[assistant_team] Tool: delegate_to_member, Args: {'member_name': 'weather_expert', 'task': "What's the current weather in Shanghai?"}
[weather_expert] Tool: mcp__weather_expert_tools__get_weather, Args: {'city': 'Shanghai'}
[weather_expert] Tool Response: {'result': 'Shanghai: Cloudy, 28C, humidity 65%'}
The current weather in Shanghai is cloudy with a temperature of 28°C and humidity at 65%.

... ClaudeAgent event loop thread stopped
... Subprocess terminated successfully.
Cleaned up Claude environment
Demo completed!
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **委派机制正确**：两轮请求都由 `assistant_team` 成功委派给 `weather_expert`
- **成员工具调用正确**：每轮都触发 `mcp__weather_expert_tools__get_weather`，参数与城市一致
- **结果闭环完整**：成员返回天气数据后，Leader 成功生成最终答复
- **生命周期管理正常**：日志显示线程与子进程都被正常回收

## 适用场景建议

- 需要把专用模型代理（如 Claude 成员）嵌入 Team 编排的场景
- 需要“Leader 统筹 + 专家成员执行”职责分离的场景
- 关注运行期资源管理（进程/线程/会话清理）稳定性的场景
