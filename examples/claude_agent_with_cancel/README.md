# ClaudeAgent 取消功能示例

本示例演示如何基于 `ClaudeAgent` 构建一个支持取消的天气助手，并验证在流式响应和工具执行两个阶段触发 Cancel 后，Agent 能否正确中断并保持会话状态可用。

## 关键特性

- **流式响应期间取消**：在 ClaudeAgent 流式输出过程中，接收到指定数量的事件后触发 Cancel，验证部分响应能被正确保存
- **工具执行期间取消**：在工具函数尚未返回时触发 Cancel，验证未完成的 `function_call` 能被正确清理
- **会话状态保持**：取消后继续提问，验证 Session 上下文未被破坏，Agent 仍能正常响应
- **事件驱动同步**：使用 `asyncio.Event` 精确控制取消时机，实现可复现的取消场景
- **多场景覆盖**：同一程序内覆盖"流式取消 + 工具执行取消"两类核心场景，每个场景各含 2 轮对话

## Agent 层级结构说明

本例是单 Agent 示例，不涉及多 Agent 分层路由：

```text
claude_weather_agent_with_cancel (ClaudeAgent)
├── model: OpenAIModel
├── tools:
│   └── get_weather_report(city)  # 内置 2s 延迟模拟慢 API
└── session: InMemorySessionService (enable_session=True)
```

关键文件：

- [examples/claude_agent_with_cancel/agent/agent.py](./agent/agent.py)：构建 `ClaudeAgent`、挂载工具、启用 Session
- [examples/claude_agent_with_cancel/agent/tools.py](./agent/tools.py)：天气查询工具实现（含 2s 延迟模拟慢调用）
- [examples/claude_agent_with_cancel/agent/prompts.py](./agent/prompts.py)：提示词模板
- [examples/claude_agent_with_cancel/agent/config.py](./agent/config.py)：环境变量读取
- [examples/claude_agent_with_cancel/run_agent.py](./run_agent.py)：测试入口，执行 2 个取消场景

## 关键代码解释

这一节用于快速定位"取消触发、事件同步、会话恢复"三条核心链路。

### 1) Agent 组装与 Claude 环境初始化（`agent/agent.py` + `run_agent.py`）

- 使用 `ClaudeAgent` 组装天气助手，挂载 `FunctionTool(get_weather_report)`
- 通过 `setup_claude_env()` 启动 Claude 代理服务（指定 `proxy_host`、`proxy_port`、`claude_models`）
- 设置 `enable_session=True`，由框架管理对话历史

### 2) 流式取消同步机制（`run_agent.py` — Scenario 1）

- 使用 `asyncio.Event` (`event_threshold_reached`) 在接收到第 10 个事件时触发
- `event_count_callback` 在每个事件到达时递增计数，达到阈值后 `set()` 信号
- 主协程 `await event_threshold_reached.wait()` 等待信号后，调用 `runner.cancel_run_async()` 发起取消

### 3) 工具执行取消同步机制（`run_agent.py` — Scenario 2）

- 使用 `asyncio.Event` (`tool_call_detected`) 在检测到 `function_call` 事件时触发
- `tool_call_callback` 在 `function_call` 到达时 `set()` 信号
- 工具内 `asyncio.sleep(2)` 模拟慢 API 调用，为取消留出时间窗口
- 主协程等待信号后立即调用 `runner.cancel_run_async()` 取消正在执行的工具

### 4) 取消后会话恢复验证

- 每个场景的第 2 轮对话发送 `"what happens?"`，验证 Agent 能读取之前的 Session 上下文
- 使用 `AgentCancelledEvent` 检测取消事件并优雅退出事件循环

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

在 [examples/claude_agent_with_cancel/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/claude_agent_with_cancel
python3 run_agent.py
```

## 运行结果（实测）

```text
📋 Scenario 1: Cancel During Streaming
--------------------------------------------------------------------------------
🆔 Session ID: 619ffc64...
📝 User Query 1: Introduce yourself, what can you do.

⏳ Waiting for first 10 events...
🤖 Assistant: Hello! I'm a professional weather query assistant, here to help you with all your weather-related questions. I can
⏳ [Received 10 events, triggering cancellation...]
 provide weather forecasts
⏸️  Requesting cancellation after 10 events...
✓ Cancellation requested: True

❌ Run was cancelled: Run for session 619ffc64-... was cancelled

💡 Result: The partial response was saved to session with cancellation message

📝 User Query 2: what happens?

🤖 Assistant: It seems like you might have canceled or interrupted the previous action. Could you clarify what you'd like to know or if you have a specific question about the weather? I'm here to assist you!
💡 Result: Agent can still respond with session context maintained
--------------------------------------------------------------------------------

📋 Scenario 2: Cancel During Tool Execution
--------------------------------------------------------------------------------
🆔 Session ID: 55e1da0a...
📝 User Query 1: What's the current weather in Shanghai and Beijing?

⏳ Waiting for tool call to be detected...
🤖 Assistant:
🔧 [Tool Call: mcp__claude_weather_agent_with_cancel_tools__get_weather_report({'city': 'Shanghai'})]
⏳ [Tool call detected...]

⏸️  Tool call detected! Requesting cancellation during tool execution...
✓ Cancellation requested: True

❌ Run was cancelled: Run for session 55e1da0a-... was cancelled

💡 Result: Incomplete function calls were cleaned up from session

📝 User Query 2: what happens?

🤖 Assistant: It seems there was an issue retrieving the weather information for Shanghai and Beijing. The weather agent execution was canceled, so the data wasn't fetched.

Would you like me to try again to get the current weather for Shanghai and Beijing?
💡 Result: Agent can still respond with session context maintained
--------------------------------------------------------------------------------

================================================================================
✅ Demo completed!
================================================================================
🧹 Claude environment cleaned up
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **流式取消生效**：Scenario 1 中接收 10 个事件后成功触发取消，Agent 停止输出，部分响应被保存至 Session
- **工具执行取消生效**：Scenario 2 中检测到 `function_call` 后立即取消，未完成的工具调用被正确清理
- **会话状态未被破坏**：两个场景的第 2 轮对话中 Agent 均能理解"上一轮被取消了"，并给出合理回复
- **取消机制可复现**：通过 `asyncio.Event` 同步取消时机，两个场景均能稳定触发取消

## 适用场景建议

- 验证 ClaudeAgent 取消机制是否正常工作：适合使用本示例
- 验证取消后 Session 状态是否保持完整：适合使用本示例
- 需要测试 LlmAgent 基础 Tool Calling 能力：建议使用 `examples/llmagent`
- 需要测试 LanggraphAgent 取消功能：建议使用 `examples/langgraph_agent_with_cancel`
