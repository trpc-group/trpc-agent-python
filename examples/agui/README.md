# AG-UI Agent 示例

本示例演示如何将 `LlmAgent` 暴露为 AG-UI 协议服务，并通过 JavaScript 客户端消费 SSE 事件流，完成远程工具调用与文本回复。

## 关键特性

- **AG-UI 协议接入**：通过 `AgUiAgent` + `AgUiService` 暴露标准 AG-UI 端点
- **SSE 流式输出**：服务端持续推送 Tool Call 与文本事件给客户端
- **前后端闭环验证**：服务端触发工具、客户端接收工具事件并展示最终回答
- **会话自动管理**：请求到达后自动创建 session 并维护线程上下文
- **最小可运行示例**：Python 服务端 + Node.js 客户端，开箱可验证

## Agent 层级结构说明

本例是单 Agent 示例，不涉及多 Agent 分层路由：

```text
weather_agent (LlmAgent)
├── tool: get_weather(city)
└── exposed via AgUiAgent at /weather_agent
```

关键文件：

- [examples/agui/run_server.py](./run_server.py)：服务启动入口
- [examples/agui/_agui_runner.py](./_agui_runner.py)：AG-UI Runner 组装与服务注册
- [examples/agui/agent/agent.py](./agent/agent.py)：Agent 定义
- [examples/agui/agent/tools.py](./agent/tools.py)：天气工具
- [examples/agui/README.md](./README.md)：示例文档（当前文件）

## 关键代码解释

这一节用于快速定位“服务注册、事件转发、客户端消费”三条主链路。

### 1) 服务注册（`run_server.py` + `_agui_runner.py`）

- `run_server.py` 中创建 `AguiRunner` 并调用 `run(host, port)`
- `_agui_runner.py` 中：
  - 创建 `AgUiService(service_name, app=...)`
  - 创建 `AgUiAgent(trpc_agent=root_agent, ...)`
  - `agui_service.add_agent("/weather_agent", agui_agent)` 注册路由

### 2) 服务端事件流（AG-UI）

- 客户端 POST 到 `/weather_agent`
- `AgUiAgent.run(...)` 内部调用 `Runner.run_async(...)`
- `EventTranslator` 把 TRPC 事件翻译成 AG-UI 事件：
  - `TOOL_CALL_RESULT`
  - `TEXT_MESSAGE_START/CONTENT/END`
- `StreamingResponse` 以 SSE 形式推送到客户端

### 3) 客户端消费（`client_js`）

- 客户端发起 run 请求后接收事件流
- 按事件类型打印：
  - `Call Tool`
  - `Tool result`
  - `Assistant` 最终文本

## 环境与运行

### 环境要求

- Python 3.12
- Node.js 18+

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 环境变量要求

在 [examples/agui/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

1) 启动服务端：

```bash
cd examples/agui
python3 run_server.py
```

2) 在新终端运行客户端：

```bash
cd examples/agui/client_js
npm install
node main.js
```

## 运行结果（实测）

### 服务端输出

```text
[INFO] Initialized SessionManager - timeout: 1200s, cleanup: 300s, max/user: unlimited, memory: enabled
INFO: Uvicorn running on http://127.0.0.1:18080
...
[INFO] accept_header: /weather_agent
INFO: 127.0.0.1 - "POST /weather_agent HTTP/1.1" 200 OK
[INFO] Created new session: agui_demo:0005033e-169c-4d9d-b050-b3ef4cb2ba56
[INFO] AG-UI request: method=POST path=/weather_agent ...
[INFO] TOOL_CALL_RESULT: tool=get_weather, response={"temperature":"25°C","condition":"Sunny","humidity":"60%"}
[INFO] TEXT_MESSAGE_START ...
[INFO] TEXT_MESSAGE_CONTENT(Accumulated): The current weather in Beijing is sunny ...
[INFO] TEXT_MESSAGE_END ...
```

### 客户端输出

```text
Run started: 0564cde0-9a2b-4579-ae1f-36a081e3d9e7
🔧 Call Tool get_weather: {"city": "Beijing"}
✅ Tool result: {"temperature": "25°C", "condition": "Sunny", "humidity": "60%"}
🤖 Assistant: The current weather in Beijing is sunny with a temperature of 25°C and humidity at 60%. It's a pleasant day, so you might want to enjoy some outdoor activities!
⚙️  Run finished
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **服务端链路正确**：请求命中 `/weather_agent`，成功创建 session 并输出 AG-UI 事件序列
- **工具调用闭环正确**：服务端产生 `TOOL_CALL_RESULT`，客户端正确收到并渲染
- **文本流闭环正确**：`TEXT_MESSAGE_START/CONTENT/END` 完整出现，客户端拿到最终回答
- **端到端验证通过**：从 Node 客户端请求到 Python 服务端响应全链路正常

## 适用场景建议

- 需要把 `trpc-agent-sdk` 以 AG-UI 协议对接前端：适合使用本示例
- 需要验证 SSE 事件流与 tool call 事件展示：适合使用本示例
- 仅验证本地单 Agent 对话：建议使用 `examples/llmagent`
