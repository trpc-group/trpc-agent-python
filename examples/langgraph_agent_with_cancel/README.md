# LangGraphAgent 取消功能示例

本示例演示如何基于 `LangGraphAgent` 构建一个支持协作式取消的计算与数据分析助手，并验证 `LLM 流式响应取消 + 工具执行取消 + 会话状态恢复` 的核心链路是否正常工作。

## 关键特性

- **协作式取消机制**：通过 `runner.cancel_run_async(...)` 在任意检查点触发取消，Agent 会在下一个检查点安全停止
- **LLM 流式响应取消**：在模型流式输出过程中触发取消，保存已生成的部分响应与取消事件到会话历史
- **工具执行期间取消**：在工具执行过程中触发取消，清理未完成的函数调用，保存取消记录
- **会话状态保持**：取消后会话上下文完整保留，后续查询可感知前次取消并正常回复
- **事件驱动同步**：使用 `asyncio.Event` 精确控制取消时机，分别在事件计数阈值和工具调用检测时触发

## Agent 层级结构说明

本例是单 Agent 示例，使用 LangGraph 构建带工具调用的计算图，不涉及多 Agent 分层路由：

```text
calculator_agent_with_cancel (LangGraphAgent)
├── graph: StateGraph
│   ├── node: chatbot (LLM + tools_condition)
│   └── node: tools (ToolNode)
│       ├── calculate(operation, a, b)
│       └── analyze_data(data_type, sample_size)
├── cancel: runner.cancel_run_async()
└── session: InMemorySessionService
```

关键文件：

- [examples/langgraph_agent_with_cancel/agent/agent.py](./agent/agent.py)：构建 `StateGraph`，定义 chatbot 节点与 ToolNode，编译为 `LangGraphAgent`
- [examples/langgraph_agent_with_cancel/agent/tools.py](./agent/tools.py)：`calculate` 与 `analyze_data` 工具实现，使用 `@langgraph_tool_node` 装饰器
- [examples/langgraph_agent_with_cancel/agent/config.py](./agent/config.py)：环境变量读取
- [examples/langgraph_agent_with_cancel/run_agent.py](./run_agent.py)：测试入口，执行 2 个取消场景各 2 轮对话

## 关键代码解释

这一节用于快速定位"图构建、取消触发、事件处理"三条核心链路。

### 1) LangGraph 图构建与 Agent 组装（`agent/agent.py`）

- 使用 `StateGraph(State)` 定义消息流图，`State` 中通过 `add_messages` 管理对话消息列表
- 使用 `@langgraph_llm_node` 装饰 chatbot 节点，使其支持取消检查点
- 通过 `tools_condition` 实现条件分支：有工具调用时进入 `ToolNode`，否则结束
- 最终通过 `LangGraphAgent` 封装编译后的图，配置 `instruction` 系统提示

### 2) 工具定义与取消支持（`agent/tools.py`）

- 使用 `@tool` + `@langgraph_tool_node` 双装饰器定义工具，使工具执行过程支持取消检查点
- `calculate`：支持加减乘除四则运算，模拟慢操作以便测试取消
- `analyze_data`：生成统计报告（均值、中位数、标准差），模拟长时间数据分析

### 3) 取消场景与事件同步（`run_agent.py`）

- **场景 1（流式取消）**：使用 `event_count_callback` 计数流式事件，达到 10 个事件后通过 `asyncio.Event` 通知主协程调用 `cancel_run_async`
- **场景 2（工具取消）**：使用 `tool_call_callback` 检测到工具调用事件后立即触发取消
- 每个场景包含 2 轮查询：第 1 轮触发取消，第 2 轮询问 "what happened?" 验证会话状态完整性
- 通过 `AgentCancelledEvent` 识别取消事件，区分正常结束与取消退出

## 环境与运行

### 环境要求

- Python 3.12

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 环境变量要求

在 [examples/langgraph_agent_with_cancel/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/langgraph_agent_with_cancel
python3 run_agent.py
```

## 运行结果（实测）

```text
================================================================================
🎯 LangGraph Agent Cancellation Demo
================================================================================

📋 Scenario 1: Cancel During LLM Streaming (LangGraph)
--------------------------------------------------------------------------------
🆔 Session ID: df05faed...
📝 User Query 1: Please introduce yourself and explain what you can do in detail.

⏳ Waiting for first 10 events...
🤖 Assistant: Hello! I'm your Assistant, here to help you with a variety of tasks. Here's a detailed
⏳ [Received 10 events, triggering cancellation...]
 overview
⏸️  Requesting cancellation after 10 events...
 of what I
❌ Run was cancelled: Run for session df05faed-e321-486c-8356-24c5e42354eb was cancelled

✓ Cancellation requested: True

💡 Result: The partial response was saved to session with cancellation message

📝 User Query 2: what happened?

🤖 Assistant: It seems like your previous request was interrupted or cancelled before I could complete my response. This can happen if you manually cancelled the action or if there was a technical issue.

If you'd like, I can still provide the introduction and explanation of what I can do. Just let me know!
💡 Result: Agent can still respond with session context maintained
--------------------------------------------------------------------------------

📋 Scenario 2: Cancel During Tool Execution (LangGraph)
--------------------------------------------------------------------------------
🆔 Session ID: f84edbc9...
📝 User Query 1: Please calculate 123 multiply 456 and then analyze sales data with sample size 1000.

⏳ Waiting for tool call to be detected...
🤖 Assistant:
🔧 [Invoke Tool: calculate({'operation': 'multiply', 'a': 123, 'b': 456})]
⏳ [Tool call detected...]

🔧 [Invoke Tool: analyze_data({'data_type': 'sales', 'sample_size': 1000})]
⏳ [Tool call detected...]

⏸️  Tool call detected! Requesting cancellation during tool execution...
[Tool executing: calculating 123.0 multiply 456.0...]
[Tool completed: result = 56088.0]
[Tool executing: analyzing 1000 sales data points...]
[Tool completed: analysis done]
📊 [Tool Result: {'result': 'Calculation result: 123.0 multiply 456.0 = 56088.0'}]
📊 [Tool Result: {'result': 'Data Analysis Report:\n- Data Type: sales\n- Sample Size: 1000\n- Mean: 42.5\n- Median: 40.0\n- Std Dev: 15.3\n- Key Insight: Data shows positive trend'}]

❌ Run was cancelled: Run for session f84edbc9-964d-4dc0-b63a-c8f6501f76a5 was cancelled

✓ Cancellation requested: True

💡 Result: Incomplete function calls were cleaned up from session

📝 User Query 2: what happened?

🤖 Assistant: It seems like the execution was cancelled by the user. Here's what was completed before the cancellation:

1. **Calculation**:
   - 123 × 456 = 56,088

2. **Data Analysis**:
   - **Data Type**: Sales
   - **Sample Size**: 1,000
   - **Mean**: 42.5
   - **Median**: 40.0
   - **Standard Deviation**: 15.3
   - **Key Insight**: The data shows a positive trend.

Let me know if you'd like to proceed with anything else!
💡 Result: Agent can still respond with session context maintained
--------------------------------------------------------------------------------


================================================================================
✅ Demo completed!
================================================================================
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **流式取消生效**：场景 1 中在接收到 10 个流式事件后成功触发取消，Agent 停止输出并记录取消事件
- **工具执行取消生效**：场景 2 中在工具调用检测后触发取消，已执行的工具结果被保存，未完成的调用被清理
- **会话状态完整**：两个场景中第 2 轮查询均能感知前次取消，Agent 正确描述了取消经过并可继续服务
- **取消协作正确**：`cancel_run_async` 返回 `True`，`AgentCancelledEvent` 正确触发，日志记录完整

说明：每个场景使用独立的 `session_id`，主要验证的是取消机制的正确性与会话状态的恢复能力。

## 适用场景建议

- 验证 LangGraphAgent 的协作式取消机制：适合使用本示例
- 验证取消后会话状态保持与恢复：适合使用本示例
- 需要测试普通 LlmAgent 的取消功能：建议使用 `examples/langgraph_agent`
- 需要测试 A2A 协议下的取消：建议使用 `examples/a2a_with_cancel`
