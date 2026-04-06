# A2A Agent 示例（Standard Protocol over HTTP）

本示例演示如何通过标准 HTTP 运行 A2A 服务，并用远程客户端发起多轮对话。

## 功能说明

- 使用 `A2AStarletteApplication` 提供 A2A HTTP 服务
- 使用 `TrpcRemoteA2aAgent` 作为远程客户端
- 演示三轮会话上下文保持
- 演示工具调用（`get_weather_report`）

## 环境要求

- Python 3.12
- 已安装项目依赖

## 运行步骤

### 1. 安装依赖

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e '.[a2a]'
pip3 install a2a-sdk python-dotenv
```

### 2. 配置环境变量

在 [examples/a2a/.env](./.env) 中设置（也可通过 export）：

```bash
TRPC_AGENT_API_KEY=...
TRPC_AGENT_BASE_URL=...
TRPC_AGENT_MODEL_NAME=...
```

### 3. 启动服务端

```bash
cd examples/a2a
python3 run_server.py
```

服务地址：

- API：`http://127.0.0.1:18081`
- Agent Card：`http://127.0.0.1:18081/.well-known/agent.json`

### 4. 启动客户端

新开终端执行：

```bash
cd examples/a2a
python3 test_a2a.py
```

## 运行结果（实测）

### 服务端输出

```text
[2026-04-01 16:23:05][INFO][trpc_agent_sdk][trpc_agent_sdk/server/a2a/_agent_service.py:108][1706047] Initialized A2A Agent Service weather_agent_standard_service for weather_agent
Starting A2A server (standard protocol over HTTP)...
Listening on: http://127.0.0.1:18081
Agent card: http://127.0.0.1:18081/.well-known/agent.json
INFO:     Started server process [1706047]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:18081 (Press CTRL+C to quit)
INFO:     127.0.0.1:59090 - "GET /.well-known/agent-card.json HTTP/1.1" 200 OK
INFO:     127.0.0.1:59090 - "POST / HTTP/1.1" 200 OK
[2026-04-01 16:23:10][INFO][trpc_agent_sdk][trpc_agent_sdk/server/a2a/executor/_a2a_agent_executor.py:199][1706047] Execute request for user_id: A2A_USER_5efc4dc2-2877-4fc1-aecd-7f6fcbba38c7, session_id: 5efc4dc2-2877-4fc1-aecd-7f6fcbba38c7
INFO:     127.0.0.1:59090 - "POST / HTTP/1.1" 200 OK
[2026-04-01 16:23:12][INFO][trpc_agent_sdk][trpc_agent_sdk/server/a2a/executor/_a2a_agent_executor.py:199][1706047] Execute request for user_id: A2A_USER_5efc4dc2-2877-4fc1-aecd-7f6fcbba38c7, session_id: 5efc4dc2-2877-4fc1-aecd-7f6fcbba38c7
INFO:     127.0.0.1:59090 - "POST / HTTP/1.1" 200 OK
[2026-04-01 16:23:15][INFO][trpc_agent_sdk][trpc_agent_sdk/server/a2a/executor/_a2a_agent_executor.py:199][1706047] Execute request for user_id: A2A_USER_5efc4dc2-2877-4fc1-aecd-7f6fcbba38c7, session_id: 5efc4dc2-2877-4fc1-aecd-7f6fcbba38c7
^CINFO:     Shutting down
INFO:     Waiting for application shutdown.
INFO:     Application shutdown complete.
INFO:     Finished server process [1706047]
```

### 客户端输出

```text
Remote A2A Agent Example (Standard Protocol over HTTP)
Note: Ensure the A2A server is running (python run_server.py)

============================================================
A2A Remote Agent Demo (Standard Protocol over HTTP)
============================================================

=== Turn 1/3 ===
Session ID: 5efc4dc2...
User Query: Hello, my name is Alice.

Remote Agent: Hello, Alice! How can I assist you with weather information today?

=== Turn 2/3 ===
Session ID: 5efc4dc2...
User Query: What's the weather in Beijing?

Remote Agent:
[Invoke Tool: get_weather_report({'city': 'Beijing'})]
[Tool Result: {'city': 'Beijing', 'temperature': '25C', 'condition': 'Sunny', 'humidity': '60%'}]
The weather in Beijing is currently sunny with a temperature of 25°C and humidity at 60%.

=== Turn 3/3 ===
Session ID: 5efc4dc2...
User Query: What's my name and what did I just ask?

Remote Agent: Your name is Alice, and you just asked about the weather in Beijing. The weather there is sunny with a temperature of 25°C and 60% humidity.

============================================================
Demo completed!
============================================================
```

## 文件说明

| 文件 | 说明 |
|---|---|
| `run_server.py` | A2A 服务端入口（Starlette + Uvicorn） |
| `test_a2a.py` | A2A 客户端示例（3 轮对话） |
| `agent/agent.py` | Agent 定义（LlmAgent + 天气工具） |
| `agent/config.py` | 模型配置（从环境变量读取） |
| `agent/prompts.py` | Agent 提示词 |
| `agent/tools.py` | 天气查询工具（`get_weather_report`） |
| `.env` | 环境变量配置文件 |

## a2a 实现

参考： [trpc_agent_sdk/server/a2a/README.md](../../trpc_agent_sdk/server/a2a/README.md)
