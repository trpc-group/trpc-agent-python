# MCP Tools 协议接入示例

本示例演示如何通过 `MCPToolset` 接入本地 MCP Server，并验证 Agent 对 MCP 工具的自动发现、调用与结果消费链路。

## 关键特性

- **协议化工具接入**：通过 MCP 协议连接工具服务，而非在 Agent 内硬编码工具
- **多 transport 支持**：内置 `stdio`、`sse`、`streamable-http` 三种接入模式
- **示例服务可独立运行**：`mcp_server.py` 提供天气查询与计算工具
- **端到端可观测**：日志展示“用户问题 -> MCP 工具调用 -> 工具返回 -> 生成回答”

## Agent 层级结构说明

本例是单 Agent + MCP Toolset 结构：

```text
mcp_assistant (LlmAgent)
├── model: OpenAIModel
└── tools:
    └── StdioMCPToolset (default)
        └── mcp_server.py
            ├── get_weather(location)
            └── calculate(operation, a, b)
```

关键文件：

- [examples/mcp_tools/run_agent.py](./run_agent.py)：示例入口，执行多轮问题
- [examples/mcp_tools/mcp_server.py](./mcp_server.py)：MCP 服务端工具定义
- [examples/mcp_tools/agent/agent.py](./agent/agent.py)：创建 `mcp_assistant`
- [examples/mcp_tools/agent/tools.py](./agent/tools.py)：三种 MCP 连接参数封装

## 关键代码解释

### 1) MCP Toolset 绑定

- 在 `agent/agent.py` 里默认使用 `StdioMCPToolset()`
- Agent 执行时由 MCP Toolset 拉取工具列表并转发调用

### 2) MCP Server 工具实现

- `mcp_server.py` 通过 `FastMCP` 暴露 `get_weather` 和 `calculate`
- 返回值被框架包装为工具响应并传回 LLM

### 3) 运行入口事件打印

- `run_agent.py` 中逐轮打印 `function_call` 和 `function_response`
- 便于确认 MCP 请求确实被触发且结果被正确消费

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

在 [examples/mcp_tools/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/mcp_tools
python3 run_agent.py
```

## 运行结果（实测）

```text
🆔 Session ID: 04b4022f...
📝 User: What's the weather like in Beijing?
🔧 [Invoke Tool: get_weather({'location': 'Beijing'})]
📊 [Tool Result: {'result': 'Sunny, 15°C, humidity 45%'}]
The weather in Beijing is sunny with a temperature of 15°C and humidity at 45%.
----------------------------------------
🆔 Session ID: 901952a7...
📝 User: Calculate 15 multiplied by 3.5
🔧 [Invoke Tool: calculate({'operation': 'multiply', 'a': 15, 'b': 3.5})]
📊 [Tool Result: {'result': '52.5'}]
The result of 15 multiplied by 3.5 is 52.5.
----------------------------------------
🆔 Session ID: 78e9f717...
📝 User: How is the weather in Shanghai?
🔧 [Invoke Tool: get_weather({'location': 'Shanghai'})]
📊 [Tool Result: {'result': 'Cloudy, 18°C, humidity 65%'}]
The weather in Shanghai is currently cloudy with a temperature of 18°C and 65% humidity.
----------------------------------------
🆔 Session ID: e2e59952...
📝 User: What is 100 divided by 4?
🔧 [Invoke Tool: calculate({'operation': 'divide', 'a': 100, 'b': 4})]
📊 [Tool Result: {'result': '25.0'}]
100 divided by 4 is 25.0.
----------------------------------------
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **工具路由正确**：天气问题命中 `get_weather`，计算问题命中 `calculate`
- **参数传递正确**：调用参数与用户问题一致（如 `multiply 15 * 3.5`、`divide 100 / 4`）
- **结果消费正确**：模型回答与工具返回值一致，形成自然语言输出
- **多轮稳定性正常**：连续 4 轮均完成“调用 -> 返回 -> 回答”闭环

## 特有说明

### MCP transport 切换说明

#### 1) stdio（默认）

- 当前默认模式
- 可直接运行 `python3 run_agent.py`
- `StdioMCPToolset` 通过子进程与 `mcp_server.py` 通信

#### 2) SSE

Step 1：在 `mcp_server.py` 启用：

```python
# app.run(transport="stdio")
app.run(transport="sse")
# app.run(transport="streamable-http")
```

Step 2：在 `agent/agent.py` 使用 `SseMCPToolset`。  
Step 3：先启动 `mcp_server.py`，再运行 `run_agent.py`。

#### 3) Streamable-HTTP

Step 1：在 `mcp_server.py` 启用：

```python
# app.run(transport="stdio")
# app.run(transport="sse")
app.run(transport="streamable-http")
```

Step 2：在 `agent/agent.py` 使用 `StreamableHttpMCPToolset`。  
Step 3：先启动 `mcp_server.py`，再运行 `run_agent.py`。

### FAQ：AnyIO cancel scope 报错

若出现 `Attempted to exit a cancel scope that isn't the current tasks's current cancel scope`：

- 确保示例结束前调用 `await runner.close()`
- 如仍出现，可在程序入口调用：

```python
from trpc_agent_sdk.tools import patch_mcp_cancel_scope_exit_issue

patch_mcp_cancel_scope_exit_issue()
```
