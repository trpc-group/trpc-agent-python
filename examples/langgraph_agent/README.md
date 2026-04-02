# LangGraph Agent 基础能力示例

本示例演示如何基于 `LangGraphAgent` 快速构建一个计算器助手，并验证 `LangGraph 图构建 + Tool Calling + 流式事件处理` 的核心链路是否正常工作。

## 关键特性

- **LangGraph 集成**：通过 `LangGraphAgent` 将 LangGraph 构建的状态图接入 trpc_agent_sdk 框架
- **装饰器驱动节点声明**：使用 `@langgraph_llm_node` 标记 LLM 节点、`@langgraph_tool_node` 标记工具节点，框架自动完成事件采集
- **工具调用能力**：通过 LangChain `@tool` + `ToolNode` 接入计算工具函数，支持加减乘除四则运算
- **流式事件处理**：通过 `runner.run_async(...)` 处理 partial/full event，并打印工具调用与工具返回
- **多轮对话验证**：同一 session 内覆盖"自我介绍 + 乘法计算 + 基于上轮结果除法 + 结束寒暄"四轮典型问答
- **子图扩展支持**：可选开启 subgraph 模式，验证父图路由 + 子图计算的嵌套图结构

## Agent 层级结构说明

本例是单 Agent 示例（可选扩展为父图 + 子图模式），默认不涉及多 Agent 分层路由：

```text
simple_langgraph_agent (LangGraphAgent)
├── graph: StateGraph
│   ├── chatbot (langgraph_llm_node) ── model.bind_tools([calculate])
│   ├── tools (ToolNode) ── [calculate]
│   └── edges: START → chatbot → (tools_condition) → tools → chatbot / END
├── instruction: 友好对话 + 四则运算
└── session: InMemorySessionService
```

关键文件：

- [examples/langgraph_agent/agent/agent.py](./agent/agent.py)：构建 `StateGraph`，定义 LLM 节点与工具节点，组装 `LangGraphAgent`
- [examples/langgraph_agent/agent/tools.py](./agent/tools.py)：计算工具实现（加减乘除）
- [examples/langgraph_agent/agent/config.py](./agent/config.py)：环境变量读取
- [examples/langgraph_agent/run_agent.py](./run_agent.py)：测试入口，执行 4 轮对话

## 关键代码解释

这一节用于快速定位"图构建、工具调用、事件输出"三条核心链路。

### 1) LangGraph 图构建与 Agent 组装（`agent/agent.py`）

- 使用 `StateGraph(State)` 定义状态图，`State` 包含 `messages` 列表
- 通过 `init_chat_model` 初始化 LLM，使用 `model.bind_tools([calculate])` 绑定工具
- 使用 `@langgraph_llm_node` 装饰 `chatbot` 节点，使框架能自动采集 LLM 事件
- 通过 `ToolNode(tools=tools)` 创建工具执行节点，配合 `tools_condition` 实现条件路由
- 最终通过 `LangGraphAgent(name=..., graph=..., instruction=...)` 组装为 trpc_agent_sdk Agent

### 2) 工具定义与装饰器（`agent/tools.py`）

- 使用 LangChain `@tool` 装饰器声明工具函数签名与文档
- 使用 `@langgraph_tool_node` 装饰器使框架能追踪工具调用事件
- `calculate(operation, a, b)` 支持 `add`、`subtract`、`multiply`、`divide` 四种运算

### 3) 流式事件处理与多轮对话（`run_agent.py`）

- 使用 `runner.run_async(...)` 消费事件流
- `event.partial=True` 时打印文本分片
- 完整事件中区分并打印：
  - `function_call`（工具调用）
  - `function_response`（工具返回）
- 同一 `session_id` 下连续执行 4 轮查询，验证上下文记忆能力

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

在 [examples/langgraph_agent/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/langgraph_agent
python3 run_agent.py
```

## 运行结果（实测）

```text
============================================================
LangGraph Agent Demo
============================================================

User: Hello, who are you?
Assistant: Hello! I'm your helpful Assistant, here to assist you with friendly conversations, answer your questions, and perform calculations if needed. How can I help you today?

User: Please calculate 15 multiply 23.
Assistant:
[Invoke Tool: calculate({'operation': 'multiply', 'a': 15, 'b': 23})]
[Tool Result: {'result': 'Calculation result: 15.0 multiply 23.0 = 345.0'}]
The result of multiplying 15 by 23 is **345**. Let me know if you need help with anything else!

User: Now divide the result by 5.
Assistant:
[Invoke Tool: calculate({'operation': 'divide', 'a': 345, 'b': 5})]
[Tool Result: {'result': 'Calculation result: 345.0 divide 5.0 = 69.0'}]
The result of dividing 345 by 5 is **69**. Let me know if you'd like to perform any other calculations!

User: Thank you!
Assistant: You're welcome! If you have any more questions or need further assistance, feel free to ask. Have a great day! 😊

============================================================
Demo completed!
============================================================
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **工具路由正确**：计算请求正确调用 `calculate`，非计算请求直接由 LLM 回复
- **工具参数正确**：第 2 轮中 `operation='multiply'`、`a=15`、`b=23` 符合用户意图；第 3 轮中自动引用上轮结果 `a=345`
- **工具结果被正确消费**：回复内容与工具返回数据一致，并能组织为可读答案
- **多轮上下文保持**：第 3 轮 "divide the result" 正确引用了第 2 轮的计算结果 345，证明 session 内记忆有效
- **能力覆盖完整**：4 轮测试分别覆盖"自我介绍、乘法运算、上下文除法、结束寒暄"四类场景

## 适用场景建议

- 快速验证 LangGraph + trpc_agent_sdk 集成主链路：适合使用本示例
- 验证 `@langgraph_llm_node` / `@langgraph_tool_node` 装饰器的事件采集：适合使用本示例
- 需要测试子图（subgraph）嵌套与流式事件：取消 `agent.py` 和 `run_agent.py` 中相关注释即可开启
- 需要测试多 Agent 分层路由：建议使用其他多 Agent 示例
