# Knowledge with Prompt Template 示例

本示例展示三种 Prompt Template 在 RAG 知识库中的用法，帮助理解不同模版类型（`PromptTemplate` / `ChatPromptTemplate` / `MessagesPlaceholder`）的适用场景与接入方式。

## 关键特性

- **PromptTemplate（StringPromptTemplate）**：格式化单个字符串，适用于简单输入场景
- **ChatPromptTemplate**：格式化消息列表，支持 system/user 角色分离，适用于需要明确角色指令的场景
- **MessagesPlaceholder**：在特定位置插入消息列表，适用于需要保留对话历史的多轮对话场景
- **LangchainKnowledge 集成**：每种 Prompt Template 均通过 `LangchainKnowledge` 构建完整的 RAG 管道
- **LangchainKnowledgeSearchTool**：将知识检索封装为 Agent 可调用的标准工具
- **向量化重试机制**：向量数据库创建支持指数退避重试，增强稳定性

## Agent 层级结构说明

本例是单 Agent 示例，不涉及多 Agent 分层路由。每种 Prompt Template 类型共用同一 Agent 结构：

```text
rag_agent_{template_type} (LlmAgent)
├── model: OpenAIModel
├── tools:
│   └── LangchainKnowledgeSearchTool (top_k=1, similarity search)
└── session: InMemorySessionService
```

关键文件：

- [examples/knowledge_with_prompt_template/agent/agent.py](./agent/agent.py)：构建 `LlmAgent`、挂载 SearchTool
- [examples/knowledge_with_prompt_template/agent/tools.py](./agent/tools.py)：构建 RAG 知识库与 `LangchainKnowledgeSearchTool`
- [examples/knowledge_with_prompt_template/agent/prompts.py](./agent/prompts.py)：三种 Prompt Template 定义
- [examples/knowledge_with_prompt_template/agent/config.py](./agent/config.py)：环境变量读取
- [examples/knowledge_with_prompt_template/run_agent.py](./run_agent.py)：测试入口，依次运行三种模版示例

## 关键代码解释

这一节用于快速定位"Agent/RAG 管道组装、Prompt Template 构建、流式事件处理"三条核心链路。

### 1) Agent 组装与 RAG 管道构建（`agent/agent.py` + `agent/tools.py`）

- 使用 `LlmAgent` 组装 RAG 知识问答助手，挂载 `LangchainKnowledgeSearchTool` 作为检索工具
- 通过 `HuggingFaceEmbeddings` + `InMemoryVectorStore` + `TextLoader` + `RecursiveCharacterTextSplitter` 构建完整 RAG 管道
- 将 RAG 管道封装为 `LangchainKnowledgeSearchTool(rag, top_k=1, search_type=SearchType.SIMILARITY)`，供 Agent 调用

### 2) 三种 Prompt Template 定义（`agent/prompts.py`）

- **PromptTemplate**：使用 `PromptTemplate.from_template(...)` 格式化单个字符串，包含 `{context}` 和 `{query}` 占位符
- **ChatPromptTemplate**：通过 `(role, template)` 元组列表构建消息序列，支持 system/user 角色分离
- **MessagesPlaceholder**：在 `ChatPromptTemplate` 中插入 `MessagesPlaceholder("chat_history")`，适用于多轮对话历史注入

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
pip3 install -e ".[knowledge]"
```

本示例还依赖 Langchain 社区组件和 HuggingFace 向量嵌入模型，需要额外安装：

```bash
pip3 install langchain-community langchain-huggingface sentence-transformers
```

| 依赖包 | 说明 |
|---|---|
| `langchain-community` | 提供 `TextLoader` 等文档加载器 |
| `langchain-huggingface` | 提供 `HuggingFaceEmbeddings` 向量嵌入模型接口 |
| `sentence-transformers` | HuggingFace 嵌入模型的底层依赖，用于加载和运行嵌入模型 |

### 环境变量要求

在 [examples/knowledge_with_prompt_template/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/knowledge_with_prompt_template
python3 run_agent.py
```

## 运行结果（实测）

```text
🚀 Knowledge with Prompt Template 示例
   本示例展示三种 Prompt Template 在 RAG 知识库中的用法


============================================================
📋 示例：PromptTemplate（StringPromptTemplate）— 格式化单个字符串
   Prompt Template 类型：string_prompt
============================================================
🆔 Session ID: 5d9dc47b...
📝 User: 什么是人工智能？
🤖 Assistant: 人工智能（Artificial Intelligence，简称 AI）是指由计算机系统或机器模拟人类智能的技术和科学。
它旨在使机器能够执行通常需要人类智能的任务，例如学习、推理、问题解决、感知、语言理解和决策等。
...
----------------------------------------

============================================================
📋 示例：ChatPromptTemplate — 格式化消息列表
   Prompt Template 类型：chat_prompt
============================================================
🆔 Session ID: 395f4d14...
📝 User: 深度学习和机器学习有什么关系？
🤖 Assistant: 深度学习和机器学习是密切相关的概念，深度学习实际上是机器学习的一个子领域。
...
----------------------------------------

============================================================
📋 示例：MessagesPlaceholder — 支持对话历史的消息模版
   Prompt Template 类型：messages_prompt
============================================================
🆔 Session ID: 8eb961e8...
📝 User: 人工智能有哪些研究领域？
🤖 Assistant: 人工智能（AI）是一个广泛且快速发展的领域，涵盖了许多研究方向和子领域。
主要包括：机器学习、计算机视觉、自然语言处理、机器人学、知识表示与推理等。
...
----------------------------------------

============================================================
✅ 所有示例运行完成
============================================================
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **三种 Template 均可正常工作**：PromptTemplate、ChatPromptTemplate、MessagesPlaceholder 三种模版类型均成功构建 RAG 管道并返回有效回答
- **知识检索准确**：回答内容与知识库文本（人工智能定义、研究领域、深度学习与机器学习关系）高度一致
- **SearchTool 调用正常**：Agent 能正确调用 `LangchainKnowledgeSearchTool` 进行知识检索
- **流式输出完整**：三轮测试均能完成流式文本输出

说明：该示例每轮使用新的 `session_id`，每种 Template 类型独立运行，主要验证的是不同 Prompt Template 对 RAG 管道格式化效果的差异。

## 适用场景建议

- 了解不同 Prompt Template 类型在 RAG 场景中的用法差异：适合使用本示例
- 快速验证 LangchainKnowledge + SearchTool 主链路：适合使用本示例
- 需要自定义文档加载器或向量存储组件：建议使用 `examples/knowledge_with_custom_components`
- 需要测试完整 RAG Agent 多轮对话能力：建议使用 `examples/knowledge_with_rag_agent`
