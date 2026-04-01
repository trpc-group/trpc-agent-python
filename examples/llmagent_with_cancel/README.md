# LLM Agent 取消能力示例

本示例演示如何基于 `LlmAgent` 验证运行中断能力，并覆盖“流式输出中取消”和“工具执行中取消”两类核心场景。

## 关键特性

- **运行中取消能力**：支持在 `Runner.run_async(...)` 执行过程中请求取消
- **双场景测试覆盖**：分别验证 LLM 流式阶段取消与 Tool 执行阶段取消
- **取消后会话可恢复**：取消后继续在同一 `session_id` 发送下一条请求，验证上下文可继续使用
- **不完整工具调用清理**：工具执行中取消时，保留已完成结果并清理未完成调用
- **可观测取消日志**：输出 `Run marked for cancellation`、`Run ... was cancelled`、`Cancellation requested: True`

## Agent 层级结构说明

本例是单 Agent 示例，不涉及多 Agent 分层路由：

```text
weather_agent (LlmAgent)
├── model: OpenAIModel
├── tools:
│   └── get_weather_report(city)
└── session: InMemorySessionService (同一场景复用 session_id)
```

关键文件：

- `examples/llmagent_with_cancel/agent/agent.py`：构建带天气工具的 `LlmAgent`
- `examples/llmagent_with_cancel/agent/tools.py`：天气工具实现（含延时与执行日志）
- `examples/llmagent_with_cancel/run_agent.py`：取消测试主流程（两个场景）
- `examples/llmagent_with_cancel/agent/config.py`：环境变量读取

## 关键代码解释

这一节用于快速定位“取消触发点、取消时机、取消后验证”三条链路。

### 1) 场景 1：LLM 流式阶段取消（`run_agent.py`）

- 通过 `event_count_callback` 统计事件
- 收到第 10 个事件后触发取消请求
- 预期行为：本轮返回 `AgentCancelledEvent`，下一轮同会话可继续回答

### 2) 场景 2：工具执行阶段取消（`run_agent.py`）

- 通过 `tool_call_callback` 在检测到 `function_call` 时触发取消
- 取消请求发送后，可能出现 `cancel wait timeout`（执行仍在收尾）
- 预期行为：已完成的工具结果可见，未完成调用被清理，下一轮仍可继续对话

### 3) 取消事件处理（`run_agent.py`）

- 在事件流中识别 `AgentCancelledEvent`
- 输出统一取消提示：`Run was cancelled`
- 不中断进程，继续后续 Query 验证会话状态

## 环境与运行

### 环境要求

- Python 3.10+（强烈建议 3.12）

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 环境变量要求

在 `examples/llmagent_with_cancel/.env` 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/llmagent_with_cancel
python3 run_agent.py
```

## 运行结果（实测）

以下输出来自你提供的终端日志：

```text
================================================================================
🎯 Agent Cancellation Demo
================================================================================

📋 Scenario 1: Cancel During LLM Streaming
--------------------------------------------------------------------------------
🆔 Session ID: 51c6a1dc...
📝 User Query 1: Introduce yourself, what can you do.

⏳ Waiting for first 10 events...
🤖 Assistant: Hello! I'm your friendly weather assistant, here to provide you with real-time weather information for any city you're curious
⏳ [Received 10 events, triggering cancellation...]
 about. Whether
⏸️  Requesting cancellation after 10 events...
[2026-04-01 17:38:45][INFO][trpc_agent_sdk][trpc_agent_sdk/cancel/_cancel.py:91][1881658] Run marked for cancellation (app_name: weather_agent_cancel_demo)(user: demo_user)(session: 51c6a1dc-1feb-4a2e-8ed1-764febd9c1a3)
 you're planning[2026-04-01 17:38:45][INFO][trpc_agent_sdk][trpc_agent_sdk/cancel/_cancel.py:218][1881658] Cancelling run for session 51c6a1dc-1feb-4a2e-8ed1-764febd9c1a3
[2026-04-01 17:38:45][INFO][trpc_agent_sdk][trpc_agent_sdk/runners.py:369][1881658] Run for session 51c6a1dc-1feb-4a2e-8ed1-764febd9c1a3 was cancelled

❌ Run was cancelled: Run for session 51c6a1dc-1feb-4a2e-8ed1-764febd9c1a3 was cancelled

[2026-04-01 17:38:45][INFO][trpc_agent_sdk][trpc_agent_sdk/runners.py:145][1881658] Cancel completed for user_id demo_user, session 51c6a1dc-1feb-4a2e-8ed1-764febd9c1a3
✓ Cancellation requested: True

💡 Result: The partial response was saved to session with cancellation message

📝 User Query 2: what happens?

🤖 Assistant: If you cancel the execution, any ongoing tasks (like fetching weather data) will be stopped immediately, and I won't provide the final response for that request. You can then start a new query or ask for something else. Let me know how I can assist you!
💡 Result: Agent can still respond with session context maintained
--------------------------------------------------------------------------------

📋 Scenario 2: Cancel During Tool Execution
--------------------------------------------------------------------------------
🆔 Session ID: ebf25584...
📝 User Query 1: What's the current weather in Shanghai and Beijing?

⏳ Waiting for tool call to be detected...
🤖 Assistant:
🔧 [Invoke Tool: get_weather_report({'city': 'Shanghai'})]
⏳ [Tool call detected...]

🔧 [Invoke Tool: get_weather_report({'city': 'Beijing'})]
⏳ [Tool call detected...]
[Tool executing: fetching weather for Shanghai...]

⏸️  Tool call detected! Requesting cancellation during tool execution...
[2026-04-01 17:38:54][INFO][trpc_agent_sdk][trpc_agent_sdk/cancel/_cancel.py:91][1881658] Run marked for cancellation (app_name: weather_agent_cancel_demo)(user: demo_user)(session: ebf25584-4598-4dc8-849e-28bbb10da22c)
[2026-04-01 17:38:55][WARNING][trpc_agent_sdk][trpc_agent_sdk/runners.py:147][1881658] Cancel wait timeout (1.0s) reached for user_id demo_user, session ebf25584-4598-4dc8-849e-28bbb10da22c. The execution may still be running.
✓ Cancellation requested: True
[Tool executing: weather for Shanghai fetched]
[Tool completed: got result for Shanghai]
📊 [Tool Result: {'city': 'Shanghai', 'temperature': '20°C', 'condition': 'Sunny', 'humidity': '80%'}]
[2026-04-01 17:38:56][INFO][trpc_agent_sdk][trpc_agent_sdk/cancel/_cancel.py:218][1881658] Cancelling run for session ebf25584-4598-4dc8-849e-28bbb10da22c
[2026-04-01 17:38:56][INFO][trpc_agent_sdk][trpc_agent_sdk/runners.py:369][1881658] Run for session ebf25584-4598-4dc8-849e-28bbb10da22c was cancelled

❌ Run was cancelled: Run for session ebf25584-4598-4dc8-849e-28bbb10da22c was cancelled

💡 Result: Incomplete function calls were cleaned up from session

📝 User Query 2: what happens?

🤖 Assistant: It seems there was an interruption or cancellation during the execution of the weather query for Beijing.

Here's the weather information I successfully retrieved for Shanghai:
- **City**: Shanghai
- **Temperature**: 20°C
- **Condition**: Sunny
- **Humidity**: 80%

Would you like me to try fetching the weather for Beijing again?
💡 Result: Agent can still respond with session context maintained
--------------------------------------------------------------------------------

================================================================================
✅ Demo completed!
================================================================================
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **场景 1 符合预期**：流式阶段触发取消成功，出现 `Run was cancelled`，后续同会话可继续问答
- **场景 2 符合预期**：工具阶段取消成功，出现 timeout 警告后最终取消；已完成工具结果保留，未完成调用被清理
- **会话连续性符合预期**：两种场景在第二个 Query 中都能延续“取消上下文”进行回答
- **日志质量符合预期**：本次输出中未出现 `Failed to detach context` 异常

## 适用场景建议

- 需要验证“用户中断”能力（Stop 按钮/取消请求）：适合使用本示例
- 需要验证“工具执行中取消”的一致性与清理策略：适合使用本示例
- 需要验证普通单 Agent 工具调用主链路：建议使用 `examples/llmagent`
