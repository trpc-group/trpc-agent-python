# LangGraph Agent Human-in-the-Loop 示例

本示例演示如何基于 `LangGraphAgent` 构建一个需要人工审批的数据库操作助手，并验证 `LangGraph StateGraph + interrupt() + Command 路由` 的 Human-in-the-Loop 核心链路是否正常工作。

## 关键特性

- **图编排工作流**：通过 `StateGraph` 构建有向图，以显式节点和边定义执行流程，支持条件分支路由
- **Human-in-the-Loop**：使用 `interrupt()` 在工具调用后暂停图执行，等待人工审批决策，实现高风险操作的安全管控
- **Command 路由分支**：审批通过跳转 `approved_path` 执行操作，拒绝跳转 `rejected_path` 取消操作
- **工具调用能力**：通过 `@tool` + `@langgraph_tool_node` 双装饰器定义数据库操作工具，支持 delete/update/create
- **状态持久化**：使用 `InMemorySaver` 作为 Checkpointer，保存图执行状态以支持 `interrupt` 后恢复
- **流式事件处理**：通过 `runner.run_async(...)` 处理 partial/full event，区分工具调用、工具返回与长时间运行事件

## Agent 层级结构说明

本例是单 Agent 示例，使用 LangGraph 图编排替代传统的 LLM Agent 单步调用：

```text
human_in_loop_langgraph_agent (LangGraphAgent)
├── graph: StateGraph(State)
│   ├── chatbot 节点 (@langgraph_llm_node, LLM 绑定工具)
│   ├── tools 节点 (ToolNode, 执行工具调用)
│   ├── human_approval 节点 (interrupt() 暂停, Command 路由)
│   ├── approved_path 节点 (审批通过处理)
│   └── rejected_path 节点 (审批拒绝处理)
├── checkpointer: InMemorySaver (状态持久化)
└── instruction: INSTRUCTION (数据库管理助手提示词)
```

关键文件：

- [examples/langgraphagent_with_human_in_the_loop/agent/agent.py](./agent/agent.py)：`StateGraph` 图定义、节点构建、`LangGraphAgent` 创建
- [examples/langgraphagent_with_human_in_the_loop/agent/tools.py](./agent/tools.py)：数据库操作工具（`@tool` + `@langgraph_tool_node`）
- [examples/langgraphagent_with_human_in_the_loop/agent/prompts.py](./agent/prompts.py)：Agent 指令提示词
- [examples/langgraphagent_with_human_in_the_loop/agent/config.py](./agent/config.py)：环境变量读取
- [examples/langgraphagent_with_human_in_the_loop/run_agent.py](./run_agent.py)：测试入口，驱动执行与审批恢复

## 关键代码解释

这一节用于快速定位"图编排、人工审批、事件处理"三条核心链路。

### 1) StateGraph 组装与节点定义（`agent/agent.py`）

- 使用 `StateGraph(State)` 构建有向图，`State` 定义 `messages`、`task_description`、`approval_status` 三个状态字段
- `chatbot` 节点使用 `@langgraph_llm_node` 装饰，LLM 绑定 `execute_database_operation` 工具后进行意图识别
- 通过 `tools_condition` 条件边判断是否需要调用工具，调用则进入 `tools` → `human_approval` 链路
- 使用 `InMemorySaver` 作为 Checkpointer，保存图状态以支持中断后恢复

### 2) Human-in-the-Loop 审批机制（`agent/agent.py` - `human_approval` 节点）

- 工具执行完成后进入 `human_approval` 节点，调用 `interrupt(task_info)` 暂停图执行
- `task_info` 包含 `_node_name`、`question`、工具调用详情等信息，推送给审批者
- 审批者返回决策后，通过 `Command(goto=...)` 路由到 `approved_path` 或 `rejected_path`

### 3) 执行入口与审批恢复（`run_agent.py`）

- 使用 `runner.run_async(...)` 消费事件流，区分 `LongRunningEvent`（人工审批）与普通事件
- 捕获到 `LongRunningEvent` 后模拟人工审批，构造 `FunctionResponse` 携带审批决策
- 通过 `resume_content` 再次调用 `run_invocation` 恢复图执行

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

在 [examples/langgraphagent_with_human_in_the_loop/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/langgraphagent_with_human_in_the_loop
python3 run_agent.py
```

## 运行结果（实测）

```text
🔧 LangGraph Human-In-The-Loop Demo
============================================================
This demo shows how to handle human approval in LangGraph using interrupts.
============================================================

📝 User: I need to delete the production database 'user_data' for migration purposes. The details are: environment=prod, backup_created=true, reason=migration_to_new_system
🤖 Assistant: 
🔧 [Calling tool: execute_database_operation]
   Args: {'operation': 'delete', 'database': 'user_data', 'details': {'environment': 'prod', 'backup_created': True, 'reason': 'migration_to_new_system'}}
📊 [Tool result: {'result': "Database operation 'delete' on 'user_data' executed successfully with details: {'environment': 'prod', 'backup_created': True, 'reason': 'migration_to_new_system'}"}]

🔧 [Calling tool: human_approval]
   Args: {'_node_name': 'human_approval', 'question': 'Do you approve this database operation?'}
📊 [Tool result: {'_node_name': 'human_approval', 'question': 'Do you approve this database operation?'}]

🔄 [Long-running operation detected]
   Function: human_approval
   Response: {'_node_name': 'human_approval', 'question': 'Do you approve this database operation?'}
   ⏳ Waiting for human intervention...

👤 Human intervention simulation...
🤖 Assistant: human_approval: {'_node_name': 'human_approval', 'question': 'Do you approve this database operation?'}
   Human decision: approved

🔄 Resuming agent execution...
✅ Operation approved - executing...

✅ LangGraph Human-In-The-Loop Demo completed!
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **工具路由正确**：LLM 正确识别用户意图并调用 `execute_database_operation` 工具，参数 `operation='delete'`、`database='user_data'` 符合用户描述
- **审批链路正确**：工具执行后自动进入 `human_approval` 节点，触发 `interrupt()` 暂停并生成 `LongRunningEvent`
- **恢复机制正常**：人工审批返回 `approved` 后，通过 `Command` 路由至 `approved_path`，图正常恢复执行至 `END`
- **事件流完整**：事件流依次输出工具调用、工具返回、长时间运行事件、审批恢复，符合预期

说明：该示例通过模拟人工审批（硬编码 `approved`）验证完整链路，实际场景中审批决策应来自外部系统或用户交互。

## 适用场景建议

- 验证 LangGraph `interrupt()` + `Command` 实现 Human-in-the-Loop：适合使用本示例
- 需要在工具调用后加入人工审批环节的高风险操作场景：适合使用本示例
- 仅需验证单 Agent + Tool Calling 基础链路：建议使用 `examples/llmagent`
- 需要测试 LangGraph 多轮对话与取消能力：建议使用 `examples/langgraph_agent_with_cancel`
