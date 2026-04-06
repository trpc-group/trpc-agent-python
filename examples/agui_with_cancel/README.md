# AG-UI Agent Cancel 示例

本示例演示如何通过 AG-UI 协议部署支持 Cancel 的 Agent 服务。当客户端关闭 SSE 连接时，服务端自动检测断开并触发协作式取消，Agent 在检查点处停止执行，保存部分响应和会话状态。

## 关键特性

- **AG-UI 协议接入**：通过 `AgUiAgent` + `AgUiService` 暴露标准 AG-UI 端点
- **SSE 断开自动 Cancel**：客户端关闭连接时，服务端自动触发 `cancel_run()` 取消 Agent 执行
- **部分结果保存**：Cancel 触发后，LLM 正在流式输出的内容和已完成的工具调用结果将保存到会话中
- **会话可恢复**：取消后继续在同一 `session_id` 发送请求，Agent 能感知到取消上下文并继续对话
- **前后端闭环验证**：Python 服务端 + Node.js 客户端，开箱可验证 Cancel 行为

## Agent 层级结构说明

本例是单 Agent 示例，不涉及多 Agent 分层路由：

```text
weather_agent_with_cancel (LlmAgent)
├── model: OpenAIModel
├── tools:
│   └── get_weather_report(city)
└── exposed via AgUiAgent at /weather_agent
    └── cancel_wait_timeout: 3.0s
```

关键文件：

- [examples/agui_with_cancel/run_server.py](./run_server.py)：服务启动入口
- [examples/agui_with_cancel/_agui_runner.py](./_agui_runner.py)：AG-UI Runner 组装与服务注册（含 cancel_wait_timeout 配置）
- [examples/agui_with_cancel/agent/agent.py](./agent/agent.py)：Agent 定义
- [examples/agui_with_cancel/agent/tools.py](./agent/tools.py)：天气工具（含 2 秒延时模拟慢调用）
- [examples/agui_with_cancel/client_js/main.js](./client_js/main.js)：JavaScript 客户端（收到 5 个文本 chunk 后 abort）
- [examples/agui_with_cancel/.env](./.env)：环境变量配置

## 关键代码解释

这一节用于快速定位"服务注册、Cancel 配置、客户端中断"三条关键链路。

### 1) 服务注册与 Cancel 配置（`run_server.py` + `_agui_runner.py`）

- `run_server.py` 中创建 `AguiRunner` 并调用 `run(host, port)`
- `_agui_runner.py` 中：
  - 创建 `AgUiAgent(trpc_agent=root_agent, cancel_wait_timeout=3.0, ...)`
  - `cancel_wait_timeout` 控制等待 Cancel 操作完成的超时时间
  - `agui_service.add_agent("/weather_agent", agui_agent)` 注册路由

### 2) Cancel 触发机制

```
客户端 abort() → SSE 连接断开 → 服务端 asyncio.CancelledError
→ AgUiAgent.cancel_run() → cancel.cancel_run(app_name, user_id, session_id)
→ Agent 在检查点处停止 → 保存部分响应到会话
```

### 3) 客户端中断（`client_js/main.js`）

- 客户端发送请求后接收 SSE 事件流
- 收到 5 个文本 chunk 后调用 `agent.abort()` 关闭连接
- 服务端检测到断开后自动触发 Cancel

### 4) Cancel 配置说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `cancel_wait_timeout` | 3.0 | 等待 Cancel 操作完成的超时时间（秒）。如果此值配置不当，Cancel 操作可能无法成功执行，导致流式文本无法保存到会话中。 |

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
pip3 install -e '.[ag-ui]'
```

### 环境变量要求

在 [examples/agui_with_cancel/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

1) 启动服务端：

```bash
cd examples/agui_with_cancel
python3 run_server.py
```

2) 在新终端运行客户端：

```bash
cd examples/agui_with_cancel/client_js
npm install
node main.js
```

## 运行结果（预期）

### 服务端输出

```text
[2026-04-02 16:26:52][INFO][trpc_agent_sdk][trpc_agent_sdk/server/ag_ui/_core/_session_manager.py:93][56989] Initialized SessionManager - timeout: 1200s, cleanup: 300s, max/user: unlimited, memory: enabled
INFO:     Started server process [56989]
INFO:     Waiting for application startup.
[2026-04-02 16:26:52][INFO][trpc_agent_sdk][examples/agui_with_cancel/_agui_runner.py:57][56989] TRPC AG-UI Server (with cancel) starting up.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:18080 (Press CTRL+C to quit)
[2026-04-02 16:27:07][INFO][trpc_agent_sdk][trpc_agent_sdk/server/ag_ui/_plugin/_service.py:137][56989] accept_header: /weather_agent
INFO:     127.0.0.1:58704 - "POST /weather_agent HTTP/1.1" 200 OK
[2026-04-02 16:27:07][INFO][trpc_agent_sdk][trpc_agent_sdk/server/ag_ui/_core/_session_manager.py:141][56989] Created new session: agui_cancel_demo:4928cecb-aa4b-4bb5-8540-2763b22f493a
[2026-04-02 16:27:08][INFO][trpc_agent_sdk][trpc_agent_sdk/server/ag_ui/_core/_event_translator.py:297][56989] 📤 TEXT_MESSAGE_START: {"type":"TEXT_MESSAGE_START","timestamp":1775118428422,"raw_event":null,"message_id":"be910d19-d824-4c8a-a8c6-caefe6ada3ef","role":"assistant","name":null}
[2026-04-02 16:27:08][INFO][trpc_agent_sdk][trpc_agent_sdk/server/ag_ui/_plugin/_utils.py:54][56989] Connection cancelled for thread 4928cecb-aa4b-4bb5-8540-2763b22f493a
[2026-04-02 16:27:08][INFO][trpc_agent_sdk][trpc_agent_sdk/cancel/_cancel.py:91][56989] Run marked for cancellation (app_name: agui_cancel_demo)(user: thread_user_4928cecb-aa4b-4bb5-8540-2763b22f493a)(session: 4928cecb-aa4b-4bb5-8540-2763b22f493a)
[2026-04-02 16:27:08][INFO][trpc_agent_sdk][trpc_agent_sdk/cancel/_cancel.py:218][56989] Cancelling run for session 4928cecb-aa4b-4bb5-8540-2763b22f493a
[2026-04-02 16:27:08][INFO][trpc_agent_sdk][trpc_agent_sdk/runners.py:369][56989] Run for session 4928cecb-aa4b-4bb5-8540-2763b22f493a was cancelled
[2026-04-02 16:27:08][INFO][trpc_agent_sdk][trpc_agent_sdk/server/ag_ui/_core/_event_translator.py:95][56989] Handling AgentCancelledEvent: Run for session 4928cecb-aa4b-4bb5-8540-2763b22f493a was cancelled
[2026-04-02 16:27:08][WARNING][trpc_agent_sdk][trpc_agent_sdk/server/ag_ui/_core/_event_translator.py:612][56989] 🚨 Force-closing unterminated streaming message: be910d19-d824-4c8a-a8c6-caefe6ada3ef
```

### 客户端输出

```text

⚙️  Run started: fd02067a-c54c-4b81-9f6e-efff7c841949
🤖 Assistant: Hello! I'm your friendly and professional **

⏸️  Aborting run after receiving 5 text chunks...
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **Cancel 触发正确**：客户端 abort 后，服务端检测到连接断开并触发 Cancel
- **部分内容保存正确**：Cancel 前的流式文本被保存到会话历史
- **服务端无异常退出**：Cancel 后服务端正常运行，可继续处理新请求
- **端到端验证通过**：从 Node 客户端 abort 到 Python 服务端 Cancel 全链路正常

## 适用场景建议

- 需要验证 AG-UI 协议下 SSE 断开触发 Cancel 的行为：适合使用本示例
- 需要验证 Cancel 后部分响应保存到会话的正确性：适合使用本示例
- 仅验证本地单 Agent Cancel（不涉及 AG-UI 协议）：建议使用 `examples/llmagent_with_cancel`
- 仅验证 AG-UI 基本功能（不涉及 Cancel）：建议使用 `examples/agui`
