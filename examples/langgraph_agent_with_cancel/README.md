# LangGraphAgent 取消功能示例

本示例演示 LangGraphAgent 的取消功能，展示如何协作式地取消正在运行的 LangGraph 智能体执行。

## 功能说明

本示例展示了 LangGraphAgent 的取消机制，包含两个真实场景:
- **LLM 流式响应期间取消**: 在模型流式输出响应时触发取消
- **工具执行期间取消**: 在工具执行过程中触发取消

取消是协作式的，智能体会在下一个检查点停止，并保存部分进度和取消事件到会话历史中。

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
cd examples/langgraph_agent_with_cancel/
python3 run_agent.py
```

## 预期行为

本示例演示两个场景:

1. 场景1:在 LLM 流式响应时取消 → 保存部分响应和取消消息
2. 场景2:在工具执行时取消 → 清理未完成的函数调用，保存取消记录

输出如下所示:

```bash
================================================================================
🎯 LangGraph Agent Cancellation Demo
================================================================================

📋 Scenario 1: Cancel During LLM Streaming (LangGraph)
--------------------------------------------------------------------------------
🆔 Session ID: 4fab25f6...
📝 User Query 1: Please introduce yourself and explain what you can do in detail.

⏳ Waiting for first 10 events...
🤖 Assistant: Hello! I am your AI Assistant, designed to help you with a variety of tasks efficiently
⏳ [Received 10 events, triggering cancellation...]
 and professionally
⏸️  Requesting cancellation after 10 events...
[2026-01-13 14:23:19][INFO][trpc_agent][trpc_agent/cancel/_cancel.py:98][1382035] Run marked for cancellation (app_name: langgraph_calculator_cancel_demo)(user: demo_user)(session: 4fab25f6-1fc2-43de-81a8-4b61b9a375b8)

⏳ [Received 11 events, triggering cancellation...]
. Here[2026-01-13 14:23:19][INFO][trpc_agent][trpc_agent/cancel/_cancel.py:215][1382035] Cancelling run for session 4fab25f6-1fc2-43de-81a8-4b61b9a375b8
[2026-01-13 14:23:19][INFO][trpc_agent][trpc_agent/runners.py:351][1382035] Run for session 4fab25f6-1fc2-43de-81a8-4b61b9a375b8 was cancelled

⏳ [Received 12 events, triggering cancellation...]

❌ Run was cancelled: Run for session 4fab25f6-1fc2-43de-81a8-4b61b9a375b8 was cancelled

[2026-01-13 14:23:19][INFO][trpc_agent][trpc_agent/runners.py:147][1382035] Cancel completed for user_id demo_user, session 4fab25f6-1fc2-43de-81a8-4b61b9a375b8
✓ Cancellation requested: True

💡 Result: The partial response was saved to session with cancellation message

📝 User Query 2: what happened?

🤖 Assistant: It seems like your previous request was interrupted or cancelled before I could complete it. This can happen if you manually stopped the response or if there was a technical issue. 

Would you like me to reintroduce myself and explain what I can do, or is there something else you'd like assistance with? I'm here to help!
💡 Result: Agent can still respond with session context maintained
--------------------------------------------------------------------------------

📋 Scenario 2: Cancel During Tool Execution (LangGraph)
--------------------------------------------------------------------------------
🆔 Session ID: 1b20b6d1...
📝 User Query 1: Please calculate 123 multiply 456 and then analyze sales data with sample size 1000.

⏳ Waiting for tool call to be detected...
🤖 Assistant: 
🔧 [Invoke Tool: calculate({'operation': 'multiply', 'a': 123, 'b': 456})]
⏳ [Tool call detected...]

🔧 [Invoke Tool: analyze_data({'data_type': 'sales', 'sample_size': 1000})]
⏳ [Tool call detected...]

⏸️  Tool call detected! Requesting cancellation during tool execution...
[2026-01-13 14:23:21][INFO][trpc_agent][trpc_agent/cancel/_cancel.py:98][1382035] Run marked for cancellation (app_name: langgraph_calculator_cancel_demo)(user: demo_user)(session: 1b20b6d1-b21a-47ce-9598-1f06be96b835)
[Tool executing: calculating 123.0 multiply 456.0...]
[Tool completed: result = 56088.0]
[Tool executing: analyzing 1000 sales data points...]
[Tool completed: analysis done]
📊 [Tool Result: {'result': 'Calculation result: 123.0 multiply 456.0 = 56088.0'}]
📊 [Tool Result: {'result': 'Data Analysis Report:\n- Data Type: sales\n- Sample Size: 1000\n- Mean: 42.5\n- Median: 40.0\n- Std Dev: 15.3\n- Key Insight: Data shows positive trend'}]
[2026-01-13 14:23:21][INFO][trpc_agent][trpc_agent/cancel/_cancel.py:215][1382035] Cancelling run for session 1b20b6d1-b21a-47ce-9598-1f06be96b835
[2026-01-13 14:23:21][INFO][trpc_agent][trpc_agent/runners.py:351][1382035] Run for session 1b20b6d1-b21a-47ce-9598-1f06be96b835 was cancelled

❌ Run was cancelled: Run for session 1b20b6d1-b21a-47ce-9598-1f06be96b835 was cancelled

[2026-01-13 14:23:21][INFO][trpc_agent][trpc_agent/runners.py:147][1382035] Cancel completed for user_id demo_user, session 1b20b6d1-b21a-47ce-9598-1f06be96b835
✓ Cancellation requested: True

💡 Result: Incomplete function calls were cleaned up from session

📝 User Query 2: what happened?

🤖 Assistant: It seems like there was a cancellation during the execution of your request. Here's what was completed before the cancellation:

1. **Calculation**:  
   - \( 123 \times 456 = 56,088 \)

2. **Sales Data Analysis**:  
   - **Data Type**: Sales  
   - **Sample Size**: 1,000  
   - **Mean**: 42.5  
   - **Median**: 40.0  
   - **Standard Deviation**: 15.3  
   - **Key Insight**: The data shows a positive trend.

If you'd like to proceed with anything else or need further clarification, feel free to let me know!
💡 Result: Agent can still respond with session context maintained
--------------------------------------------------------------------------------


================================================================================
✅ Demo completed!
================================================================================
```
