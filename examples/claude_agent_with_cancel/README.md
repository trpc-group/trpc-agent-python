# ClaudeAgent 取消功能示例

本示例演示 ClaudeAgent 的取消功能，展示如何Cancel正在运行的Agent。

## 功能说明

本示例展示了 ClaudeAgent 的取消机制，包含两个真实场景:
- **流式响应期间取消**: 在 ClaudeAgent 流式输出响应时触发 Cancel
- **工具执行期间取消**: 在 ClaudeAgent 工具执行过程中触发 Cancel

## 环境要求

Python版本: 3.10+(强烈建议使用3.12)

## 运行方法

1. 下载并安装 trpc-agent-python

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .[agent-claude]
```

2. 在 `.env` 文件中设置环境变量(也可以通过export设置):
   - TRPC_AGENT_API_KEY
   - TRPC_AGENT_BASE_URL
   - TRPC_AGENT_MODEL_NAME

3. 运行示例:

```bash
cd examples/claude_agent_with_cancel/
python3 run_agent.py
```

## 预期行为

输出如下所示:

```bash
================================================================================
🎯 ClaudeAgent Cancellation Demo
================================================================================

[2026-01-13 14:24:10][INFO][trpc_agent_sdk][trpc_agent_ecosystem/agents/claude/_setup.py:222][1388253] Proxy server proxy process started (PID: 1389619)
[2026-01-13 14:24:10][INFO][trpc_agent_sdk][trpc_agent_ecosystem/agents/claude/_setup.py:239][1388253] Proxy server is ready at http://0.0.0.0:8082
📋 Scenario 1: Cancel During Streaming
--------------------------------------------------------------------------------
🆔 Session ID: 2c0b370c...
📝 User Query 1: Introduce yourself, what can you do.

⏳ Waiting for first 10 events...
🤖 Assistant: Hello! I'm a professional weather query assistant, here to help you with all your weather-related needs. Here
⏳ [Received 10 events, triggering cancellation...]
's what I
⏸️  Requesting cancellation after 10 events...
[2026-01-13 14:24:15][INFO][trpc_agent_sdk][trpc_agent_sdk/cancel/_cancel.py:98][1388253] Run marked for cancellation (app_name: claude_agent_cancel_demo)(user: demo_user)(session: 2c0b370c-af5f-4bc9-a4bd-784ea97fd197)
[2026-01-13 14:24:15][INFO][trpc_agent_sdk][trpc_agent_sdk/cancel/_cancel.py:215][1388253] Cancelling run for session 2c0b370c-af5f-4bc9-a4bd-784ea97fd197
[2026-01-13 14:24:15][INFO][trpc_agent_sdk][trpc_agent_sdk/runners.py:351][1388253] Run for session 2c0b370c-af5f-4bc9-a4bd-784ea97fd197 was cancelled

⏳ [Received 11 events, triggering cancellation...]

❌ Run was cancelled: Run for session 2c0b370c-af5f-4bc9-a4bd-784ea97fd197 was cancelled

[2026-01-13 14:24:15][INFO][trpc_agent_sdk][trpc_agent_sdk/runners.py:147][1388253] Cancel completed for user_id demo_user, session 2c0b370c-af5f-4bc9-a4bd-784ea97fd197
✓ Cancellation requested: True

💡 Result: The partial response was saved to session with cancellation message

📝 User Query 2: what happens?

🤖 Assistant: It seems like you might have canceled the previous interaction or want to know what happens next. 

If you're asking about the weather query assistant, I can provide you with weather forecasts, current conditions, and other weather-related information for any location you specify. Just let me know the city or region you're interested in, and I'll fetch the details for you!

If you meant something else, please clarify, and I'll be happy to assist.
💡 Result: Agent can still respond with session context maintained
--------------------------------------------------------------------------------

📋 Scenario 2: Cancel During Tool Execution
--------------------------------------------------------------------------------
🆔 Session ID: 4e9a24b5...
📝 User Query 1: What's the current weather in Shanghai and Beijing?

⏳ Waiting for tool call to be detected...
🤖 Assistant: 
🔧 [Tool Call: mcp__claude_weather_agent_with_cancel_tools__get_weather_report({'city': 'Shanghai'})]
⏳ [Tool call detected...]

⏸️  Tool call detected! Requesting cancellation during tool execution...
[2026-01-13 14:24:29][INFO][trpc_agent_sdk][trpc_agent_sdk/cancel/_cancel.py:98][1388253] Run marked for cancellation (app_name: claude_agent_cancel_demo)(user: demo_user)(session: 4e9a24b5-10c1-4066-90fd-d42f32e6a2e9)
[2026-01-13 14:24:29][INFO][trpc_agent_sdk][trpc_agent_sdk/cancel/_cancel.py:215][1388253] Cancelling run for session 4e9a24b5-10c1-4066-90fd-d42f32e6a2e9
Task exception was never retrieved
future: <Task finished name='Task-41' coro=<<async_generator_athrow without __name__>()> exception=ProcessError('Command failed with exit code 143 (exit code: 143)\nError output: Check stderr output for details')>
Traceback (most recent call last):
  File "/data/work/ai/trpc-agent-dev/trpc-agent-dev2/venv/lib64/python3.12/site-packages/claude_agent_sdk/_internal/transport/subprocess_cli.py", line 626, in _read_messages_impl
    raise self._exit_error
claude_agent_sdk._errors.ProcessError: Command failed with exit code 143 (exit code: 143)
Error output: Check stderr output for details
[2026-01-13 14:24:29][INFO][trpc_agent_sdk][trpc_agent_sdk/runners.py:351][1388253] Run for session 4e9a24b5-10c1-4066-90fd-d42f32e6a2e9 was cancelled

❌ Run was cancelled: Run for session 4e9a24b5-10c1-4066-90fd-d42f32e6a2e9 was cancelled

[2026-01-13 14:24:29][INFO][trpc_agent_sdk][trpc_agent_sdk/runners.py:147][1388253] Cancel completed for user_id demo_user, session 4e9a24b5-10c1-4066-90fd-d42f32e6a2e9
✓ Cancellation requested: True

💡 Result: Incomplete function calls were cleaned up from session

📝 User Query 2: what happens?

🤖 Assistant: It seems there was an issue retrieving the weather information for Shanghai and Beijing. The previous attempt was canceled, possibly due to a timeout or an interruption.

Would you like me to try fetching the weather information for Shanghai and Beijing again? Let me know!
💡 Result: Agent can still respond with session context maintained
--------------------------------------------------------------------------------


================================================================================
✅ Demo completed!
================================================================================
[2026-01-13 14:24:36][INFO][trpc_agent_sdk][trpc_agent_ecosystem/agents/claude/_setup.py:275][1388253] Terminating proxy process (PID: 1389619)...
[2026-01-13 14:24:36][INFO][trpc_agent_sdk][trpc_agent_ecosystem/agents/claude/_setup.py:287][1388253] Subprocess terminated successfully.
🧹 Claude environment cleaned up
```
