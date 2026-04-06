# ClaudeAgent 基础能力示例

本示例演示如何基于 `ClaudeAgent` 快速构建一个天气查询助手，并验证 `Prompt + Tool Calling + Claude Proxy` 的核心链路是否正常工作。

## 关键特性

- **工具调用能力**：通过 `FunctionTool` 接入天气工具函数，支持城市天气查询
- **Claude 代理环境**：使用 `setup_claude_env` / `destroy_claude_env` 管理 Claude 代理服务，自动启停代理进程
- **流式事件处理**：通过 `runner.run_async(...)` 处理 partial/full event，并打印工具调用与工具返回
- **会话状态管理**：使用 `InMemorySessionService` 保存请求状态

## Agent 层级结构说明

本例是单 Agent 示例，不涉及多 Agent 分层路由：

```text
claude_weather_agent (ClaudeAgent)
├── model: OpenAIModel
├── tools:
│   └── get_weather(city)
└── session: InMemorySessionService
```

关键文件：

- [examples/claude_agent/agent/agent.py](./agent/agent.py)：构建 `ClaudeAgent`、挂载工具、设置模型
- [examples/claude_agent/agent/tools.py](./agent/tools.py)：天气查询工具实现
- [examples/claude_agent/agent/prompts.py](./agent/prompts.py)：提示词模板
- [examples/claude_agent/agent/config.py](./agent/config.py)：环境变量读取
- [examples/claude_agent/run_agent.py](./run_agent.py)：测试入口，执行 1 轮对话

## 关键代码解释

这一节用于快速定位"工具调用、Claude 代理环境、事件输出"三条核心链路。

### 1) Agent 组装与参数配置（`agent/agent.py`）

- 使用 `ClaudeAgent` 组装天气助手，挂载 `FunctionTool(get_weather)`
- 通过 `OpenAIModel` 配置模型连接信息（api_key、base_url、model_name）
- 使用提示词 `INSTRUCTION` 定义 Agent 角色行为

### 2) Claude 代理环境管理（`run_agent.py`）

- 通过 `setup_claude_env(proxy_host, proxy_port, claude_models)` 启动 Claude 代理服务
- 调用 `root_agent.initialize()` 完成 Agent 初始化
- 程序退出前通过 `destroy_claude_env()` 清理代理进程，避免端口泄漏

### 3) 流式事件处理与可观测输出（`run_agent.py`）

- 使用 `runner.run_async(...)` 消费事件流
- `event.partial=True` 时打印文本分片
- 完整事件中区分并打印：
  - `function_call`（工具调用）
  - `function_response`（工具返回）

## 环境与运行

### 环境要求

- Python 3.12

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e '.[agent-claude]'
```

### 环境变量要求

在 [examples/claude_agent/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/claude_agent
python3 run_agent.py
```

## 运行结果（实测）

```text
🆔 Session ID: 6424c221...
📝 User: What is the weather in Beijing?

🤖 Agent: 
🔧 [Tool Call: mcp__claude_weather_agent_tools__get_weather({"city": "Beijing"})]
📊 [Tool Result: {"result": "{'city': 'Beijing', 'temperature': '25C', 'condition': 'Sunny', 'humidity': '60%'}"}]
The current weather in Beijing is sunny with a temperature of 25°C and humidity at 60%.

================================================================================
Demo completed!
================================================================================
[2026-04-02 20:32:01][INFO][trpc_agent_sdk][trpc_agent_sdk/server/agents/claude/_setup.py:280][67774] Terminating proxy process (PID: 67832)...
[2026-04-02 20:32:01][INFO][trpc_agent_sdk][trpc_agent_sdk/server/agents/claude/_setup.py:292][67774] Subprocess terminated successfully.
🧹 Claude environment cleaned up
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **工具路由正确**：天气查询问题正确调用 `get_weather`，参数 `city='Beijing'` 符合用户意图
- **工具结果被正确消费**：回复内容与工具返回数据一致，并能组织为可读答案
- **Claude 代理环境正常**：代理进程正常启停，日志输出 `Terminating proxy process` 及 `Subprocess terminated successfully`

## 适用场景建议

- 快速验证 ClaudeAgent + Tool Calling 主链路：适合使用本示例
- 验证 Claude 代理环境的启停与生命周期管理：适合使用本示例
- 需要测试多轮对话或会话状态注入：建议使用 `examples/llmagent`
- 需要测试流式工具调用或取消能力：建议使用 `examples/claude_agent_with_cancel`
