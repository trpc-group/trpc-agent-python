# LangChain 工具集成示例

本示例展示如何将 LangChain 的 Tavily 搜索工具封装为 `FunctionTool`，集成到 trpc-agent 中使用，验证第三方工具链接入的完整链路。

## 关键特性

- **LangChain 工具封装**：将 `langchain-tavily` 的 `TavilySearch` 包装为异步函数，再通过 `FunctionTool` 注册到 Agent
- **实时搜索能力**：借助 Tavily 搜索引擎，Agent 可回答需要实时或最新信息的问题
- **会话状态管理**：使用 `InMemorySessionService` 创建会话并注入 `user_name` 状态
- **流式事件处理**：通过 `runner.run_async(...)` 消费事件流，打印工具调用与工具返回

## Agent 层级结构说明

本例是单 Agent 示例，不涉及多 Agent 分层路由：

```text
langchain_tavily_agent (LlmAgent)
├── model: OpenAIModel
├── tools:
│   └── tavily_search(query, max_results)
└── session: InMemorySessionService (state 注入 user_name)
```

关键文件：

- [examples/langchain_tools/agent/agent.py](./agent/agent.py)：构建 `LlmAgent`、挂载 Tavily 搜索工具
- [examples/langchain_tools/agent/tools.py](./agent/tools.py)：Tavily 搜索工具实现，封装 `TavilySearch`
- [examples/langchain_tools/agent/prompts.py](./agent/prompts.py)：提示词模板
- [examples/langchain_tools/agent/config.py](./agent/config.py)：环境变量读取（LLM 配置 + Tavily API Key）
- [examples/langchain_tools/run_agent.py](./run_agent.py)：测试入口，执行搜索查询

## 关键代码解释

这一节用于快速定位"工具封装、Agent 组装、事件输出"三条核心链路。

### 1) LangChain 工具封装（`agent/tools.py`）

- 导入 `langchain_tavily.TavilySearch`，在异步函数 `tavily_search` 中调用 `tool.ainvoke(query)`
- 对返回结果做归一化处理（兼容 dict 和 list 两种响应格式），统一输出 `status`、`query`、`result_count`、`results`
- 异常时返回 `{"status": "error", "error_message": ...}`，保证 Agent 不会因工具异常崩溃

### 2) Agent 组装（`agent/agent.py`）

- 使用 `LlmAgent` 组装搜索助手，通过 `FunctionTool(tavily_search)` 挂载工具
- 提示词指引 Agent 在用户提出需要实时信息的问题时调用 `tavily_search`

### 3) 流式事件处理与可观测输出（`run_agent.py`）

- 使用 `runner.run_async(...)` 消费事件流
- `event.partial=True` 时打印文本分片
- 完整事件中区分并打印：
  - `function_call`（工具调用）
  - `function_response`（工具返回）

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
pip3 install langchain-tavily
```

### 环境变量要求

在 [examples/langchain_tools/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`
- `TAVILY_API_KEY`（Tavily 搜索的 API Key，可在 https://tavily.com 获取）

### 运行命令

```bash
cd examples/langchain_tools
python3 run_agent.py
```

## 运行结果（实测）

```text
🆔 Session ID: 95ea1edd...
📝 User: Search for today's major news in the AI field
🤖 Assistant:
🔧 [Invoke Tool: tavily_search({'query': "today's major news in AI field", 'max_results': 5})]
📊 [Tool Result: {'status': 'success', 'query': "today's major news in AI field", 'result_count': 5, 'results': [...]}]
Here are today's major developments in the AI field:

1. **OpenAI Announces New Model**: OpenAI released its latest model with improved reasoning capabilities...
2. **Google DeepMind Research Breakthrough**: A new paper demonstrates advances in protein structure prediction...
3. **AI Regulation Update**: The EU published new guidelines for AI governance...

Let me know if you'd like more details on any of these topics!
----------------------------------------
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **工具路由正确**：搜索类问题正确调用 `tavily_search`
- **工具参数正确**：`query` 与 `max_results` 参数符合用户意图
- **工具结果被正确消费**：Agent 根据搜索返回的结果进行归纳总结，输出可读答案
- **LangChain 封装有效**：`TavilySearch` 被成功封装为 `FunctionTool`，与 trpc-agent 无缝集成

说明：该示例每轮使用新的 `session_id`，主要验证的是 LangChain 工具集成与调用链路，不强调跨轮记忆一致性。

## 适用场景建议

- 快速验证 LangChain 第三方工具集成到 trpc-agent 的链路：适合使用本示例
- 验证实时搜索能力（Tavily）+ Agent 工具调用完整流程：适合使用本示例
- 需要测试多 Agent 分层路由：建议使用 `examples/graph` 等多 Agent 示例
