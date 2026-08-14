# A2A Agent 示例（Standard Protocol over HTTP）

本示例演示如何通过标准 HTTP 运行 A2A 服务，并用远程客户端发起多轮对话。

## 功能说明

- 使用 SDK 内置的 `create_a2a_application()` 提供 A2A HTTP 服务（1.x 路由装配封装）
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

- 默认：纯 1.0 服务端
- `A2A_V03_COMPAT=1 python3 run_server.py`：1.0 服务端**同时接受 0.3 客户端**（开启 v0.3 compat）

服务地址：

- API：`http://127.0.0.1:18081`
- Agent Card（1.x）：`http://127.0.0.1:18081/.well-known/agent-card.json`
- Agent Card（0.3，仅 `A2A_V03_COMPAT=1` 时发布）：`http://127.0.0.1:18081/.well-known/agent.json`

### 4. 启动客户端

新开终端执行：

```bash
cd examples/a2a
python3 test_a2a.py
```

## 三种调用链路

同一个 example（`run_server.py` + `test_a2a.py`）通过两个环境变量覆盖三种协议组合：服务端 `A2A_V03_COMPAT`（同时接受 0.3 客户端），客户端 `A2A_FORCE_V03`（强制打 0.3 报文）。

| 场景 | 服务端命令 | 客户端命令 |
|---|---|---|
| **1.0 → 1.0**（默认）| `python3 run_server.py` | `python3 test_a2a.py` |
| **0.3 客户端 → 1.0** | `A2A_V03_COMPAT=1 python3 run_server.py` | 旧版 0.3 客户端 |
| **1.0 → 0.3 服务端** | 旧版 0.3 服务端 | `A2A_FORCE_V03=1 python3 test_a2a.py` |

### 场景 1：1.0 客户端 → 1.0 服务端（默认）

```bash
# 终端 A：1.0 服务端
python3 run_server.py
# 终端 B：1.0 客户端
python3 test_a2a.py
```

### 场景 2：0.3 客户端 → 1.0 服务端

服务端开 compat（同时接受 1.0 和 0.3 客户端，并发布 `/.well-known/agent.json`），**此时** 0.3 客户端无需改动：

```bash
# 终端 A：1.0 服务端 + v0.3 compat
A2A_V03_COMPAT=1 python3 run_server.py
# 终端 B：旧版（v0.3）客户端，例如旧版 trpc-agent-python 的 test_a2a.py
cd old_version/trpc-agent-python/examples/a2a
python3 test_a2a.py
```

服务端卡片会同时声明 `1.0` 和 `0.3` 接口，0.3 客户端能正确发现并调用。

### 场景 3：1.0 客户端 → 0.3 服务端

对端是旧 0.3 服务端、且卡片没法按默认路径用时（例如没填 url），客户端打开 `force_v0_3=True`（本示例用 `A2A_FORCE_V03=1`），强制走 0.3 报文。完整的 0.3 卡走默认即可。

```bash
# 终端 A：旧版（v0.3）服务端
cd old_version/trpc-agent-python/examples/a2a
python3 run_server.py
# 终端 B：1.0 客户端，指定对端是 0.3
cd examples/a2a
A2A_FORCE_V03=1 python3 test_a2a.py
```

等价于在代码里：

```python
remote_agent = TrpcRemoteA2aAgent(
    name="weather_agent",
    agent_base_url="http://127.0.0.1:18081",
    force_v0_3=True,  # 明确对端是 0.3，强制旧报文
)
```

> 通常不用开：完整 1.0 / 0.3 卡跟卡片走即可。`force_v0_3=True` 用于你确认对端是 0.3、但卡不可用的情况（例如 0.3 空 url）。发现失败则初始化失败。

## 运行结果（实测）

### 服务端输出

```text
[2026-04-01 16:23:05][INFO][trpc_agent_sdk][trpc_agent_sdk/server/a2a/_agent_service.py:108][1706047] Initialized A2A Agent Service weather_agent_standard_service for weather_agent
Starting A2A server (standard protocol over HTTP)...
Listening on: http://127.0.0.1:18081
Agent card: http://127.0.0.1:18081/.well-known/agent-card.json
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
| `run_server.py` | A2A 服务端入口（Starlette + Uvicorn；`A2A_V03_COMPAT=1` 开 v0.3 compat） |
| `test_a2a.py` | A2A 客户端示例（3 轮对话；`A2A_FORCE_V03=1` 强制走 0.3 报文，不做协商） |
| `agent/agent.py` | Agent 定义（LlmAgent + 天气工具） |
| `agent/config.py` | 模型配置（从环境变量读取） |
| `agent/prompts.py` | Agent 提示词 |
| `agent/tools.py` | 天气查询工具（`get_weather_report`） |
| `.env` | 环境变量配置文件 |

## a2a 实现

参考： [trpc_agent_sdk/server/a2a/README.md](../../trpc_agent_sdk/server/a2a/README.md)
