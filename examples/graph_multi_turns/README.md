# Graph 多轮对话示例

本示例演示如何基于 `GraphAgent` 构建一个支持多轮对话的图工作流，并验证 `条件路由 + LLM/Agent 分支 + Session 多轮记忆` 的核心链路是否正常工作。

## 关键特性

- **条件路由分支**：通过 `decide` 节点根据用户输入前缀（`llm:` / `agent:`）选择不同执行分支
- **LLM 节点与 Agent 节点并存**：`llm_reply_node` 直接调用模型生成回复，`agent_reply_node` 委托子 Agent 处理
- **会话多轮记忆**：使用 `InMemorySessionService` 在同一 Session 内保持上下文，验证跨轮记忆一致性
- **自定义状态管理**：通过 `MultiTurnState` 定义 `route`、`query_text`、`context_note` 等字段，驱动图执行流程
- **节点生命周期可观测**：通过事件流打印节点启动/完成、模型调用等生命周期日志

## Agent 层级结构说明

本例采用 `GraphAgent` + 子 `LlmAgent` 的混合模式：

```text
graph_multi_turns (GraphAgent)
├── graph:
│   ├── decide (FunctionNode) — 根据前缀选择 llm / agent 分支
│   ├── llm_reply_node (LlmNode) — 直接调用 OpenAIModel 生成回复
│   ├── agent_reply_node (AgentNode) — 委托 branch_agent_worker 处理
│   │   └── branch_agent_worker (LlmAgent)
│   └── format_output (FunctionNode) — 格式化当前轮输出
└── session: InMemorySessionService (同一 session_id 跨轮复用)
```

关键文件：

- [examples/graph_multi_turns/agent/agent.py](./agent/agent.py)：构建 `GraphAgent`，组装图结构、创建模型与子 Agent
- [examples/graph_multi_turns/agent/nodes.py](./agent/nodes.py)：`decide_route`、`route_choice`、`format_output` 节点函数
- [examples/graph_multi_turns/agent/state.py](./agent/state.py)：`MultiTurnState` 自定义状态定义
- [examples/graph_multi_turns/agent/prompts.py](./agent/prompts.py)：LLM 节点与 Agent 节点的提示词
- [examples/graph_multi_turns/agent/callbacks.py](./agent/callbacks.py)：节点回调（占位扩展点）
- [examples/graph_multi_turns/agent/config.py](./agent/config.py)：环境变量读取
- [examples/graph_multi_turns/run_agent.py](./run_agent.py)：测试入口，执行 4 轮对话

## 关键代码解释

这一节用于快速定位"条件路由、分支执行、多轮记忆"三条核心链路。

### 1) 图结构组装与分支定义（`agent/agent.py`）

- 使用 `StateGraph(MultiTurnState)` 创建有状态图，注册 `decide`、`llm_reply_node`、`agent_reply_node`、`format_output` 四个节点
- 通过 `add_conditional_edges` 将 `decide` 节点的输出路由到 `llm_reply_node` 或 `agent_reply_node`
- `agent_reply_node` 使用 `StateMapper` 将 `query_text` 映射为子 Agent 的用户输入，并将子 Agent 回复合并回状态

### 2) 条件路由与状态驱动（`agent/nodes.py`）

- `decide_route` 节点读取 `STATE_KEY_USER_INPUT`，根据前缀 `llm:` / `agent:` 确定分支
- 将去除前缀后的查询文本写回 `query_text` 和 `STATE_KEY_USER_INPUT`，供下游节点消费
- `route_choice` 函数从状态中读取 `route` 字段，供 `add_conditional_edges` 调用

### 3) 多轮会话与事件流处理（`run_agent.py`）

- 在同一 `session_id` 下连续发送 4 轮请求，前 2 轮走 `llm` 分支、后 2 轮走 `agent` 分支
- 通过 `runner.run_async(...)` 消费事件流，打印节点生命周期（`Node start` / `Node done`）、模型调用（`Model start` / `Model done`）等日志
- 从 Session 状态中读取 `STATE_KEY_LAST_RESPONSE` 获取格式化后的最终输出

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

在 [examples/graph_multi_turns/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/graph_multi_turns
python3 run_agent.py
```

## 运行结果（实测）

```text
============================================
Graph Multi-Turn Demo
Session: f4378878...
============================================
Turn 1/4
Input: llm: Define retrieval-augmented generation in one sentence.
--------------------------------------------
[Node start] node_type=function, node_name=decide
[node_execute:decide] return={'route': 'llm', 'query_text': 'Define retrieval-augmented generation in one sentence.', 'user_input': 'Define retrieval-augmented generation in one sentence.', 'context_note': 'user=demo_user session=f4378878-d8a8-4f6b-844d-cdb805c65c6d turn=1'}
[Node done ] node_type=function, node_name=decide
[Node start] node_type=llm, node_name=llm_reply_node
[Model start] deepseek-v3-local-II (llm_reply_node)
[llm_reply_node] Retrieval-augmented generation (RAG) enhances AI responses by retrieving relevant information from external sources before generating an answer.
[Model done ] deepseek-v3-local-II (llm_reply_node)
[Node done ] node_type=llm, node_name=llm_reply_node
[Node start] node_type=function, node_name=format_output
[node_execute:format_output] return.last_response_len=321
[Node done ] node_type=function, node_name=format_output
==============================
 Graph Multi-Turn Result
==============================

Branch: llm
Context: user=demo_user session=f4378878-d8a8-4f6b-844d-cdb805c65c6d turn=1

Retrieval-augmented generation (RAG) enhances AI responses by retrieving relevant information from external sources before generating an answer.
----------------------------------------
============================================
Turn 2/4
Input: llm: Summarize your previous answer in six words.
--------------------------------------------
[Node start] node_type=function, node_name=decide
[node_execute:decide] return={'route': 'llm', 'query_text': 'Summarize your previous answer in six words.', 'user_input': 'Summarize your previous answer in six words.', 'context_note': 'user=demo_user session=f4378878-d8a8-4f6b-844d-cdb805c65c6d turn=2'}
[Node done ] node_type=function, node_name=decide
[Node start] node_type=llm, node_name=llm_reply_node
[Model start] deepseek-v3-local-II (llm_reply_node)
[llm_reply_node] RAG retrieves then generates better answers.
[Model done ] deepseek-v3-local-II (llm_reply_node)
[Node done ] node_type=llm, node_name=llm_reply_node
[Node start] node_type=function, node_name=format_output
[node_execute:format_output] return.last_response_len=221
[Node done ] node_type=function, node_name=format_output
==============================
 Graph Multi-Turn Result
==============================

Branch: llm
Context: user=demo_user session=f4378878-d8a8-4f6b-844d-cdb805c65c6d turn=2

RAG retrieves then generates better answers.
----------------------------------------
============================================
Turn 3/4
Input: agent: What i ask? Reply as branch agent and then greet me.
--------------------------------------------
[Node start] node_type=function, node_name=decide
[node_execute:decide] return={'route': 'agent', 'query_text': 'What i ask? Reply as branch agent and then greet me.', 'user_input': 'What i ask? Reply as branch agent and then greet me.', 'context_note': 'user=demo_user session=f4378878-d8a8-4f6b-844d-cdb805c65c6d turn=3'}
[Node done ] node_type=function, node_name=decide
[Node start] node_type=agent, node_name=agent_reply_node
[branch_agent_worker] Agent branch: You asked about retrieval-augmented generation and its summary. Hello there! How can I assist you further?
[Node done ] node_type=agent, node_name=agent_reply_node
[Node start] node_type=function, node_name=format_output
[node_execute:format_output] return.last_response_len=299
[Node done ] node_type=function, node_name=format_output
==============================
 Graph Multi-Turn Result
==============================

Branch: agent
Context: user=demo_user session=f4378878-d8a8-4f6b-844d-cdb805c65c6d turn=3

Agent branch: You asked about retrieval-augmented generation and its summary. Hello there! How can I assist you further?
----------------------------------------
============================================
Turn 4/4
Input: agent: Summarize what i have asked you to do Do.
--------------------------------------------
[Node start] node_type=function, node_name=decide
[node_execute:decide] return={'route': 'agent', 'query_text': 'Summarize what i have asked you to do Do.', 'user_input': 'Summarize what i have asked you to do Do.', 'context_note': 'user=demo_user session=f4378878-d8a8-4f6b-844d-cdb805c65c6d turn=4'}
[Node done ] node_type=function, node_name=decide
[Node start] node_type=agent, node_name=agent_reply_node
[branch_agent_worker] Agent branch: You've asked me to define retrieval-augmented generation, summarize that definition in six words, and then summarize our conversation. Hello again!
[Node done ] node_type=agent, node_name=agent_reply_node
[Node start] node_type=function, node_name=format_output
[node_execute:format_output] return.last_response_len=340
[Node done ] node_type=function, node_name=format_output
==============================
 Graph Multi-Turn Result
==============================

Branch: agent
Context: user=demo_user session=f4378878-d8a8-4f6b-844d-cdb805c65c6d turn=4

Agent branch: You've asked me to define retrieval-augmented generation, summarize that definition in six words, and then summarize our conversation. Hello again!
----------------------------------------
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **条件路由正确**：前缀 `llm:` 的输入走 `llm_reply_node`，前缀 `agent:` 的输入走 `agent_reply_node`，路由无误
- **多轮记忆有效**：第 2 轮要求总结上一轮回答，模型成功基于上下文生成摘要（"RAG retrieves then generates better answers."）
- **跨分支记忆共享**：第 3 轮切换到 `agent` 分支后，子 Agent 仍能引用前两轮 `llm` 分支的对话内容
- **回复质量正常**：第 4 轮 Agent 准确总结了全部 3 轮的历史请求，说明会话状态在整个 Session 生命周期内持续可用
- **能力覆盖完整**：4 轮测试分别覆盖"LLM 直答、LLM 跨轮摘要、Agent 跨分支引用、Agent 全局总结"四类典型场景

## 适用场景建议

- 验证 Graph 条件路由与分支执行：适合使用本示例
- 验证同一 Session 下多轮对话记忆一致性：适合使用本示例
- 验证 LLM 节点与 Agent 节点的混合编排：适合使用本示例
- 需要测试单 Agent + Tool Calling 主链路：建议使用 `examples/llmagent`
- 需要测试单轮图执行（无跨轮记忆）：建议使用 `examples/graph`
