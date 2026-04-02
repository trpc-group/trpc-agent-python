# A2A Agent Cancel 示例

本示例演示如何通过 A2A 协议部署支持远程 Cancel 的 Agent 服务，并用客户端在 Agent 执行过程中发送取消请求。

## 关键特性

- **A2A 远程取消**：客户端通过 `runner.cancel_run_async()` 向远程 A2A 服务发送 `cancel_task` 请求
- **双场景测试覆盖**：分别验证 LLM 流式阶段取消与 Tool 执行阶段取消
- **取消后会话可恢复**：取消后继续在同一 `session_id` 发送下一条请求，验证上下文可继续使用
- **不完整工具调用清理**：工具执行中取消时，保留已完成结果并清理未完成调用
- **可配置超时**：服务端 `cancel_wait_timeout` 和客户端 `timeout` 分别配置

## 架构

```text
┌─────────────────────────────────────────────────┐
│                   客户端                         │
│  ┌───────────────────────────────────────────┐  │
│  │         TrpcRemoteA2aAgent                │  │
│  │     (连接远程 A2A 服务)                     │  │
│  └─────────────┬─────────────────────────────┘  │
│                │ A2A Protocol                   │
│                │ (支持 Cancel)                  │
└────────────────┼────────────────────────────────┘
                 │
                 │ HTTP
                 │
┌────────────────▼────────────────────────────────┐
│                   服务端                         │
│  ┌───────────────────────────────────────────┐  │
│  │      TrpcA2aAgentService                  │  │
│  │  ┌─────────────────────────────────────┐  │  │
│  │  │          LlmAgent                   │  │  │
│  │  │     (支持 Cancel 的 Agent)          │  │  │
│  │  └─────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

## Agent 层级结构说明

```text
weather_agent (LlmAgent) — 部署在 A2A 服务端
├── model: OpenAIModel
├── tools:
│   └── get_weather_report(city)  (含 2s 延时模拟慢接口)
└── A2A Service: TrpcA2aAgentService
    └── cancel_wait_timeout: 3.0s
```

关键文件：

- [examples/a2a_with_cancel/run_server.py](./run_server.py)：A2A 服务端入口（配置 cancel_wait_timeout）
- [examples/a2a_with_cancel/test_a2a_cancel.py](./test_a2a_cancel.py)：A2A 客户端取消测试（两个场景）
- [examples/a2a_with_cancel/agent/agent.py](./agent/agent.py)：Agent 定义（LlmAgent + 天气工具）
- [examples/a2a_with_cancel/agent/tools.py](./agent/tools.py)：天气查询工具（含延时与执行日志）
- [examples/a2a_with_cancel/agent/config.py](./agent/config.py)：模型配置（从环境变量读取）
- [examples/a2a_with_cancel/agent/prompts.py](./agent/prompts.py)：Agent 提示词
- [examples/a2a_with_cancel/.env](./.env)：环境变量配置文件

## 关键代码解释

### 1) 服务端：配置 cancel_wait_timeout

```python
executor_config = TrpcA2aAgentExecutorConfig(
    cancel_wait_timeout=3.0,  # 等待 Agent 取消完成的超时时间
)

a2a_svc = TrpcA2aAgentService(
    service_name="weather_agent_cancel_service",
    agent=root_agent,
    executor_config=executor_config,
)
```

### 2) 客户端场景 1：LLM 流式阶段取消

- 通过 `event_count_callback` 统计流式事件
- 收到第 10 个事件后触发取消请求
- 预期行为：本轮返回 `AgentCancelledEvent`，下一轮同会话可继续回答

### 3) 客户端场景 2：工具执行阶段取消

- 通过 `tool_call_callback` 在检测到 `function_call` 时触发取消
- 取消请求通过 A2A 协议的 `cancel_task` 发送到服务端
- 预期行为：已完成的工具结果可见，未完成调用被清理，下一轮仍可继续对话

### 4) Cancel 超时配置

| 配置位置 | 参数 | 默认值 | 说明 |
|----------|------|--------|------|
| 服务端 | `cancel_wait_timeout` | 1.0 | 服务端等待后端 Agent 取消完成的超时时间 |
| 客户端 | `timeout` | 1.0 | 客户端等待本端 RemoteA2aAgent 取消完成的超时时间 |

建议两者配置相同的超时时间。

## 环境与运行

### 环境要求

- Python 3.10+（建议 3.12）
- 已安装项目依赖

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e '.[a2a]'
```

### 环境变量要求

在 [examples/a2a_with_cancel/.env](./.env) 中设置（也可通过 export）：

```bash
TRPC_AGENT_API_KEY=...
TRPC_AGENT_BASE_URL=...
TRPC_AGENT_MODEL_NAME=...
```

### 运行步骤

#### 1. 启动服务端

```bash
cd examples/a2a_with_cancel
python3 run_server.py
```

服务地址：

- API：`http://127.0.0.1:18082`
- Agent Card：`http://127.0.0.1:18082/.well-known/agent.json`

#### 2. 启动客户端（新开终端）

```bash
cd examples/a2a_with_cancel
python3 test_a2a_cancel.py
```

## 运行结果（实测）

### 服务端输出

```text
[2026-04-02 15:19:26][INFO][trpc_agent_sdk][trpc_agent_sdk/server/a2a/_agent_service.py:108][66551] Initialized A2A Agent Service weather_agent_cancel_service for weather_agent
Starting A2A server with cancel support...
Listening on: http://127.0.0.1:18082
Agent card: http://127.0.0.1:18082/.well-known/agent.json
Cancel wait timeout: 3.0s
INFO:     Started server process [66551]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:18082 (Press CTRL+C to quit)
INFO:     127.0.0.1:61085 - "GET /.well-known/agent-card.json HTTP/1.1" 200 OK
INFO:     127.0.0.1:61085 - "POST / HTTP/1.1" 200 OK
[2026-04-02 15:21:16][INFO][trpc_agent_sdk][trpc_agent_sdk/server/a2a/executor/_a2a_agent_executor.py:199][66551] Execute request for user_id: A2A_USER_c6372394-5c01-494a-8ec7-423f1a9174db, session_id: c6372394-5c01-494a-8ec7-423f1a9174db
[2026-04-02 15:21:18][INFO][trpc_agent_sdk][trpc_agent_sdk/server/a2a/executor/_a2a_agent_executor.py:136][66551] Canceling task 532e4820-ca75-4559-ba3f-235587f0be1b using metadata: app_name=weather_agent_cancel_service, user_id=A2A_USER_c6372394-5c01-494a-8ec7-423f1a9174db, session_id=c6372394-5c01-494a-8ec7-423f1a9174db
[2026-04-02 15:21:18][INFO][trpc_agent_sdk][trpc_agent_sdk/cancel/_cancel.py:91][66551] Run marked for cancellation (app_name: weather_agent_cancel_service)(user: A2A_USER_c6372394-5c01-494a-8ec7-423f1a9174db)(session: c6372394-5c01-494a-8ec7-423f1a9174db)
[2026-04-02 15:21:18][INFO][trpc_agent_sdk][trpc_agent_sdk/cancel/_cancel.py:218][66551] Cancelling run for session c6372394-5c01-494a-8ec7-423f1a9174db
[2026-04-02 15:21:18][INFO][trpc_agent_sdk][trpc_agent_sdk/runners.py:369][66551] Run for session c6372394-5c01-494a-8ec7-423f1a9174db was cancelled
[2026-04-02 15:21:18][INFO][trpc_agent_sdk][trpc_agent_sdk/runners.py:145][66551] Cancel completed for user_id A2A_USER_c6372394-5c01-494a-8ec7-423f1a9174db, session c6372394-5c01-494a-8ec7-423f1a9174db
[2026-04-02 15:21:18][INFO][trpc_agent_sdk][trpc_agent_sdk/server/a2a/executor/_a2a_agent_executor.py:156][66551] Cancel requested for user_id=A2A_USER_c6372394-5c01-494a-8ec7-423f1a9174db, session_id=c6372394-5c01-494a-8ec7-423f1a9174db
Queue is closed. Event will not be dequeued.
INFO:     127.0.0.1:61092 - "POST / HTTP/1.1" 200 OK
INFO:     127.0.0.1:61092 - "POST / HTTP/1.1" 200 OK
[2026-04-02 15:21:18][INFO][trpc_agent_sdk][trpc_agent_sdk/server/a2a/executor/_a2a_agent_executor.py:199][66551] Execute request for user_id: A2A_USER_c6372394-5c01-494a-8ec7-423f1a9174db, session_id: c6372394-5c01-494a-8ec7-423f1a9174db
INFO:     127.0.0.1:61092 - "POST / HTTP/1.1" 200 OK
[2026-04-02 15:21:23][INFO][trpc_agent_sdk][trpc_agent_sdk/server/a2a/executor/_a2a_agent_executor.py:199][66551] Execute request for user_id: A2A_USER_7661a89d-ef9e-4c23-9d0d-2a2c990f756a, session_id: 7661a89d-ef9e-4c23-9d0d-2a2c990f756a
[Tool executing: fetching weather for Shanghai...]
[2026-04-02 15:21:25][INFO][trpc_agent_sdk][trpc_agent_sdk/server/a2a/executor/_a2a_agent_executor.py:136][66551] Canceling task 5fe5400f-c679-4bda-9340-40458fcd27eb using metadata: app_name=weather_agent_cancel_service, user_id=A2A_USER_7661a89d-ef9e-4c23-9d0d-2a2c990f756a, session_id=7661a89d-ef9e-4c23-9d0d-2a2c990f756a
[2026-04-02 15:21:25][INFO][trpc_agent_sdk][trpc_agent_sdk/cancel/_cancel.py:91][66551] Run marked for cancellation (app_name: weather_agent_cancel_service)(user: A2A_USER_7661a89d-ef9e-4c23-9d0d-2a2c990f756a)(session: 7661a89d-ef9e-4c23-9d0d-2a2c990f756a)
[Tool executing: weather for Shanghai fetched]
[Tool completed: got result for Shanghai]
[2026-04-02 15:21:27][INFO][trpc_agent_sdk][trpc_agent_sdk/cancel/_cancel.py:218][66551] Cancelling run for session 7661a89d-ef9e-4c23-9d0d-2a2c990f756a
[2026-04-02 15:21:27][INFO][trpc_agent_sdk][trpc_agent_sdk/runners.py:369][66551] Run for session 7661a89d-ef9e-4c23-9d0d-2a2c990f756a was cancelled
[2026-04-02 15:21:27][INFO][trpc_agent_sdk][trpc_agent_sdk/runners.py:145][66551] Cancel completed for user_id A2A_USER_7661a89d-ef9e-4c23-9d0d-2a2c990f756a, session 7661a89d-ef9e-4c23-9d0d-2a2c990f756a
[2026-04-02 15:21:27][INFO][trpc_agent_sdk][trpc_agent_sdk/server/a2a/executor/_a2a_agent_executor.py:156][66551] Cancel requested for user_id=A2A_USER_7661a89d-ef9e-4c23-9d0d-2a2c990f756a, session_id=7661a89d-ef9e-4c23-9d0d-2a2c990f756a
Queue is closed. Event will not be dequeued.
INFO:     127.0.0.1:61146 - "POST / HTTP/1.1" 200 OK
INFO:     127.0.0.1:61146 - "POST / HTTP/1.1" 200 OK
[2026-04-02 15:21:27][INFO][trpc_agent_sdk][trpc_agent_sdk/server/a2a/executor/_a2a_agent_executor.py:199][66551] Execute request for user_id: A2A_USER_7661a89d-ef9e-4c23-9d0d-2a2c990f756a, session_id: 7661a89d-ef9e-4c23-9d0d-2a2c990f756a
```

### 客户端输出

```text
Remote A2A Agent Cancel Example
Note: Ensure the A2A server is running (python run_server.py)

================================================================================
🎯 A2A Agent Cancellation Demo
================================================================================

📋 Scenario 1: Cancel During LLM Streaming (Remote A2A)
--------------------------------------------------------------------------------
🆔 Session ID: c6372394...
📝 User Query 1: Introduce yourself in detail, what can you do as a weather assistant.

⏳ Waiting for first 10 events...
🤖 Remote Agent: Hello! I'm your friendly **Weather Assistant**, here to provide you with accurate, real-time weather
⏳ [Received 10 events, triggering cancellation...]
 information and
⏸️  Requesting cancellation after 10 events...
[2026-04-02 15:21:18][INFO][trpc_agent_sdk][trpc_agent_sdk/cancel/_cancel.py:91][69331] Run marked for cancellation (app_name: a2a_cancel_demo)(user: demo_user)(session: c6372394-5c01-494a-8ec7-423f1a9174db)
[2026-04-02 15:21:18][INFO][trpc_agent_sdk][trpc_agent_sdk/server/a2a/_remote_a2a_agent.py:187][69331] Cancel event triggered during streaming wait
[2026-04-02 15:21:18][INFO][trpc_agent_sdk][trpc_agent_sdk/server/a2a/_remote_a2a_agent.py:283][69331] Remote A2A agent 'weather_agent' execution cancelled, sending cancel request to remote service
[2026-04-02 15:21:18][INFO][trpc_agent_sdk][trpc_agent_sdk/server/a2a/_remote_a2a_agent.py:294][69331] Successfully sent cancel request for session_id: c6372394-5c01-494a-8ec7-423f1a9174db
[2026-04-02 15:21:18][INFO][trpc_agent_sdk][trpc_agent_sdk/runners.py:369][69331] Run for session c6372394-5c01-494a-8ec7-423f1a9174db was cancelled

❌ Run was cancelled: Run cancelled while waiting for stream response

[2026-04-02 15:21:18][INFO][trpc_agent_sdk][trpc_agent_sdk/runners.py:145][69331] Cancel completed for user_id demo_user, session c6372394-5c01-494a-8ec7-423f1a9174db
✓ Cancellation requested: True

💡 Result: The partial response was saved to session with cancellation message

📝 User Query 2: what happens?

🤖 Remote Agent: It seems like you might have canceled or interrupted the action. If you were asking for weather information or any other assistance, feel free to let me know, and I'll be happy to help! 

For example, you can ask:
- "What's the weather like in New York today?"
- "Will it rain in Tokyo tomorrow?"
- "How's the temperature in London right now?"

Just let me know how I can assist you! 😊
💡 Result: Agent can still respond with session context maintained
--------------------------------------------------------------------------------

📋 Scenario 2: Cancel During Tool Execution (Remote A2A)
--------------------------------------------------------------------------------
🆔 Session ID: 7661a89d...
📝 User Query 1: What's the current weather in Shanghai and Beijing?

⏳ Waiting for tool call to be detected...
🤖 Remote Agent: 
🔧 [Invoke Tool: get_weather_report({'city': 'Shanghai'})]
⏳ [Tool call detected...]

🔧 [Invoke Tool: get_weather_report({'city': 'Beijing'})]
⏳ [Tool call detected...]

⏸️  Tool call detected! Requesting cancellation during tool execution...
[2026-04-02 15:21:25][INFO][trpc_agent_sdk][trpc_agent_sdk/cancel/_cancel.py:91][69331] Run marked for cancellation (app_name: a2a_cancel_demo)(user: demo_user)(session: 7661a89d-ef9e-4c23-9d0d-2a2c990f756a)
[2026-04-02 15:21:25][INFO][trpc_agent_sdk][trpc_agent_sdk/server/a2a/_remote_a2a_agent.py:187][69331] Cancel event triggered during streaming wait
[2026-04-02 15:21:25][INFO][trpc_agent_sdk][trpc_agent_sdk/server/a2a/_remote_a2a_agent.py:283][69331] Remote A2A agent 'weather_agent' execution cancelled, sending cancel request to remote service
[2026-04-02 15:21:27][INFO][trpc_agent_sdk][trpc_agent_sdk/server/a2a/_remote_a2a_agent.py:294][69331] Successfully sent cancel request for session_id: 7661a89d-ef9e-4c23-9d0d-2a2c990f756a
[2026-04-02 15:21:27][INFO][trpc_agent_sdk][trpc_agent_sdk/runners.py:369][69331] Run for session 7661a89d-ef9e-4c23-9d0d-2a2c990f756a was cancelled

❌ Run was cancelled: Run cancelled while waiting for stream response

[2026-04-02 15:21:27][INFO][trpc_agent_sdk][trpc_agent_sdk/runners.py:145][69331] Cancel completed for user_id demo_user, session 7661a89d-ef9e-4c23-9d0d-2a2c990f756a
✓ Cancellation requested: True

💡 Result: Incomplete function calls were cleaned up from session

📝 User Query 2: what happens?

🤖 Remote Agent: It seems there was an interruption or cancellation during the execution of the weather query for Beijing. However, I successfully retrieved the weather information for Shanghai:

- **Shanghai**:  
  - **Temperature**: 28°C  
  - **Condition**: Cloudy  
  - **Humidity**: 70%  

Would you like me to try fetching the weather for Beijing again?
💡 Result: Agent can still respond with session context maintained
--------------------------------------------------------------------------------


================================================================================
✅ Demo completed!
================================================================================
```


## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **场景 1 符合预期**：LLM 流式阶段通过 A2A 远程取消成功，出现 `Run was cancelled`，后续同会话可继续问答
- **场景 2 符合预期**：工具执行阶段通过 A2A 远程取消成功，已完成工具结果保留，未完成调用被清理
- **会话连续性符合预期**：两种场景在第二个 Query 中都能延续"取消上下文"进行回答
- **A2A 协议 Cancel 链路畅通**：客户端 `cancel_run_async` → A2A `cancel_task` → 服务端 Agent 取消

## 文件说明

| 文件 | 说明 |
|---|---|
| `run_server.py` | A2A 服务端入口（配置 cancel_wait_timeout） |
| `test_a2a_cancel.py` | A2A 客户端取消示例（两个场景） |
| `agent/agent.py` | Agent 定义（LlmAgent + 天气工具） |
| `agent/config.py` | 模型配置（从环境变量读取） |
| `agent/prompts.py` | Agent 提示词 |
| `agent/tools.py` | 天气查询工具（含 2s 延时模拟慢接口） |
| `.env` | 环境变量配置文件 |

## 适用场景建议

- 需要验证 A2A 协议远程取消能力：适合使用本示例
- 需要验证 `cancel_wait_timeout` 配置效果：适合使用本示例
- 需要验证基本 A2A 服务（不含 Cancel）：建议使用 `examples/a2a`
- 需要验证单机 Agent 取消能力：建议使用 `examples/llmagent_with_cancel`
