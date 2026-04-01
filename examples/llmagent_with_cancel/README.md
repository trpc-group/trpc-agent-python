# LlmAgent 取消功能示例

本示例演示 LlmAgent 的取消功能，展示如何取消正在运行的智能体执行。

## 功能说明

本示例展示了 LlmAgent 的取消机制，包含两个真实场景:
- **LLM 流式响应期间取消**: 在模型流式输出响应时触发取消
- **工具执行期间取消**: 在工具执行过程中触发取消

## 环境要求

Python版本: 3.10+(强烈建议使用3.12)

## 运行方法

1. 下载并安装 trpc-agent-python

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

2. 在 `.env` 文件中设置环境变量(也可以通过export设置):
   - TRPC_AGENT_API_KEY
   - TRPC_AGENT_BASE_URL
   - TRPC_AGENT_MODEL_NAME

3. 运行示例:

```bash
cd examples/llmagent_with_cancel/
python3 run_agent.py
```

## 预期行为

本示例演示两个场景:

1. 场景1:在 LLM 流式响应时取消 → 保存部分响应和取消消息
2. 场景2:在工具执行时取消 → 清理未完成的函数调用，保存取消记录

输出如下所示:

```bash
================================================================================
🎯 Agent Cancellation Demo
================================================================================

📋 Scenario 1: Cancel During LLM Streaming
--------------------------------------------------------------------------------
🆔 Session ID: e4fea114...
📝 User Query 1: Introduce yourself, what can you do.

⏳ Waiting for first 10 events...
🤖 Assistant: Hello! I'm your friendly weather assistant, here to provide you with real-time weather information for any city you're curious
⏳ [Received 10 events, triggering cancellation...]
 about. Whether
⏸️  Requesting cancellation after 10 events...
[2026-01-13 14:21:03][INFO][trpc_agent][trpc_agent/cancel/_cancel.py:98][1369642] Run marked for cancellation (app_name: weather_agent_cancel_demo)(user: demo_user)(session: e4fea114-fa35-459e-89f5-cdf6cc4df8ab)
 you need[2026-01-13 14:21:03][INFO][trpc_agent][trpc_agent/cancel/_cancel.py:215][1369642] Cancelling run for session e4fea114-fa35-459e-89f5-cdf6cc4df8ab
[2026-01-13 14:21:03][INFO][trpc_agent][trpc_agent/runners.py:351][1369642] Run for session e4fea114-fa35-459e-89f5-cdf6cc4df8ab was cancelled

❌ Run was cancelled: Run for session e4fea114-fa35-459e-89f5-cdf6cc4df8ab was cancelled

[2026-01-13 14:21:03][INFO][trpc_agent][trpc_agent/runners.py:147][1369642] Cancel completed for user_id demo_user, session e4fea114-fa35-459e-89f5-cdf6cc4df8ab
✓ Cancellation requested: True

💡 Result: The partial response was saved to session with cancellation message

📝 User Query 2: what happens?

🤖 Assistant: If you cancel the agent's execution, any ongoing tasks or processes (like fetching weather data) will be stopped immediately. You'll be returned to the chat interface, and you can start a new query or ask for something else. Let me know how I can assist you! 
💡 Result: Agent can still respond with session context maintained
--------------------------------------------------------------------------------

📋 Scenario 2: Cancel During Tool Execution
--------------------------------------------------------------------------------
🆔 Session ID: 8bc1719c...
📝 User Query 1: What's the current weather in Shanghai and Beijing?

⏳ Waiting for tool call to be detected...
🤖 Assistant: 
🔧 [Invoke Tool: get_weather_report({'city': 'Shanghai'})]
⏳ [Tool call detected...]

🔧 [Invoke Tool: get_weather_report({'city': 'Beijing'})]
⏳ [Tool call detected...]
[Tool executing: fetching weather for Shanghai...]

⏸️  Tool call detected! Requesting cancellation during tool execution...
[2026-01-13 14:21:06][INFO][trpc_agent][trpc_agent/cancel/_cancel.py:98][1369642] Run marked for cancellation (app_name: weather_agent_cancel_demo)(user: demo_user)(session: 8bc1719c-25cf-427a-aad1-1f42b1b7c090)
[2026-01-13 14:21:07][WARNING][trpc_agent][trpc_agent/runners.py:149][1369642] Cancel wait timeout (1.0s) reached for user_id demo_user, session 8bc1719c-25cf-427a-aad1-1f42b1b7c090. The execution may still be running.
✓ Cancellation requested: True
[Tool executing: weather for Shanghai fetched]
[Tool completed: got result for Shanghai]
📊 [Tool Result: {'city': 'Shanghai', 'temperature': '20°C', 'condition': 'Sunny', 'humidity': '80%'}]
[2026-01-13 14:21:08][INFO][trpc_agent][trpc_agent/cancel/_cancel.py:215][1369642] Cancelling run for session 8bc1719c-25cf-427a-aad1-1f42b1b7c090
[2026-01-13 14:21:08][INFO][trpc_agent][trpc_agent/runners.py:351][1369642] Run for session 8bc1719c-25cf-427a-aad1-1f42b1b7c090 was cancelled

❌ Run was cancelled: Run for session 8bc1719c-25cf-427a-aad1-1f42b1b7c090 was cancelled


💡 Result: Incomplete function calls were cleaned up from session

📝 User Query 2: what happens?

🤖 Assistant: It seems like the weather query for Beijing was canceled or interrupted before it could be completed. 

Here's the weather information I successfully retrieved for **Shanghai**:
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
