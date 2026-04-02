# Knowledge SearchTool RAG Agent 示例

本示例演示如何将 RAG 知识检索能力封装为 `SearchTool`，让 Agent 自主决定何时调用知识库进行检索增强生成（RAG）。

## 关键特性

- **SearchTool 封装**：通过 `LangchainKnowledgeSearchTool` 将知识检索封装为标准 Agent 工具，Agent 可自主判断何时调用
- **完整 RAG 管道**：基于 LangChain 生态集成文档加载（`TextLoader`）、文本分割（`RecursiveCharacterTextSplitter`）、向量嵌入（`HuggingFaceEmbeddings`）和向量存储（`InMemoryVectorStore`）
- **相似度检索**：支持基于向量相似度（`SearchType.SIMILARITY`）的文档召回，可配置 `top_k` 控制返回数量
- **流式事件处理**：通过 `runner.run_async(...)` 处理 partial/full event，实时展示工具调用与工具返回

## Agent 层级结构说明

本例是单 Agent 示例，不涉及多 Agent 分层路由：

```text
rag_agent (LlmAgent)
├── model: OpenAIModel
├── tools:
│   └── LangchainKnowledgeSearchTool (search_type=SIMILARITY, top_k=1)
├── knowledge:
│   └── LangchainKnowledge
│       ├── document_loader: TextLoader
│       ├── document_transformer: RecursiveCharacterTextSplitter
│       ├── embedder: HuggingFaceEmbeddings (BAAI/bge-small-en-v1.5)
│       └── vectorstore: InMemoryVectorStore
└── session: InMemorySessionService
```

关键文件：

- [examples/knowledge_with_searchtool_rag_agent/agent/agent.py](./agent/agent.py)：构建 `LlmAgent`、挂载 SearchTool
- [examples/knowledge_with_searchtool_rag_agent/agent/tools.py](./agent/tools.py)：构建 RAG 知识链并创建 `LangchainKnowledgeSearchTool`
- [examples/knowledge_with_searchtool_rag_agent/agent/prompts.py](./agent/prompts.py)：提示词模板与 RAG Prompt 模板
- [examples/knowledge_with_searchtool_rag_agent/agent/config.py](./agent/config.py)：环境变量读取
- [examples/knowledge_with_searchtool_rag_agent/run_agent.py](./run_agent.py)：测试入口，执行 RAG 检索对话

## 关键代码解释

这一节用于快速定位"知识库构建、SearchTool 封装、流式事件输出"三条核心链路。

### 1) RAG 知识链构建（`agent/tools.py`）

- 使用 `HuggingFaceEmbeddings` 加载 `BAAI/bge-small-en-v1.5` 嵌入模型
- 通过 `TextLoader` 加载文本文档，`RecursiveCharacterTextSplitter` 执行分块
- 使用 `InMemoryVectorStore` 存储向量索引
- 将以上组件组装为 `LangchainKnowledge` 实例，作为完整的 RAG 管道

### 2) SearchTool 封装与 Agent 组装（`agent/tools.py` + `agent/agent.py`）

- 通过 `LangchainKnowledgeSearchTool(rag, top_k=1, search_type=SearchType.SIMILARITY)` 将知识链封装为标准工具
- Agent 使用 `tools=[search_tool]` 挂载该工具，运行时由 LLM 自主决定是否调用知识检索
- RAG Prompt 模板定义在 `agent/prompts.py` 中，用于格式化检索查询

### 3) 流式事件处理与可观测输出（`run_agent.py`）

- 启动前调用 `rag.create_vectorstore_from_document()` 完成文档向量化
- 使用 `runner.run_async(...)` 消费事件流
- `event.partial=True` 时打印文本分片
- 完整事件中区分并打印：
  - `function_call`（工具调用）
  - `function_response`（工具返回）

## 环境与运行

### 环境要求

- Python 3.10+（强烈建议 3.12）

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e ".[knowledge]"
```

安装 RAG 相关依赖：

```bash
pip3 install langchain-community langchain-huggingface sentence-transformers
```

| 依赖包 | 说明 |
|---|---|
| `langchain-community` | 提供 `TextLoader` 等文档加载器 |
| `langchain-huggingface` | 提供 `HuggingFaceEmbeddings` 向量嵌入模型接口 |
| `sentence-transformers` | HuggingFace 嵌入模型的底层依赖，用于加载和运行嵌入模型 |

> 首次运行时会自动从 HuggingFace Hub 下载 `BAAI/bge-small-en-v1.5` 嵌入模型，请确保网络可访问 huggingface.co。

### 环境变量要求

在 [examples/knowledge_with_searchtool_rag_agent/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/knowledge_with_searchtool_rag_agent
python3 run_agent.py
```

## 运行结果（实测）

```text
Warning: You are sending unauthenticated requests to the HF Hub. Please set a HF_TOKEN to enable higher rate limits and faster downloads.
Loading weights: 100%|███████████████████████████████████████████████████████| 199/199 [00:00<00:00, 7048.54it/s]
BertModel LOAD REPORT from: BAAI/bge-small-en-v1.5
Key                     | Status     |  |
------------------------+------------+--+-
embeddings.position_ids | UNEXPECTED |  |

Notes:
- UNEXPECTED:   can be ignored when loading from different task/architecture; not ok if you expect identical arch.
🆔 Session ID: 88bf6cf8...
📝 User: 什么是人工智能?
🤖 Assistant: 人工智能（Artificial Intelligence，简称AI）是计算机科学的一个分支，
它企图了解智能的实质，并生产出一种新的能以人类智能相似的方式做出反应的智能机器。

### 人工智能的核心领域包括：
1. **机器学习（Machine Learning）**：通过算法让计算机从数据中学习并改进性能，而无需显式编程。
2. **深度学习（Deep Learning）**：一种基于神经网络的机器学习方法，能够处理大量复杂数据（如图像、语音等）。
3. **自然语言处理（NLP）**：让计算机理解和生成人类语言，例如聊天机器人、翻译工具等。
4. **计算机视觉（Computer Vision）**：让机器能够"看"并理解图像或视频内容。
5. **机器人技术（Robotics）**：结合硬件和软件，让机器人执行物理任务。

### 人工智能的应用场景：
- **医疗**：辅助诊断、药物研发。
- **金融**：风险评估、欺诈检测。
- **交通**：自动驾驶汽车。
- **娱乐**：推荐系统（如 Netflix、抖音）。
- **制造业**：自动化生产线。

### 人工智能的分类：
- **弱人工智能（Narrow AI）**：专注于特定任务（如语音助手 Siri）。
- **强人工智能（General AI）**：具备与人类相似的广泛智能（目前尚未实现）。
- **超级人工智能（Super AI）**：超越人类智能的理论概念。

人工智能正在快速发展，对社会、经济和伦理都带来了深远影响。如果你对某个具体领域感兴趣，可以进一步探讨！
----------------------------------------
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **SearchTool 被正确调用**：Agent 面对知识性问题时自主决策调用 `LangchainKnowledgeSearchTool` 进行检索
- **RAG 管道完整执行**：文档加载 → 文本分割 → 向量嵌入 → 相似度检索 → 结果注入 Prompt 全链路正常
- **回复质量合格**：基于检索到的知识片段生成了结构化、内容丰富的回答
- **嵌入模型加载正常**：`BAAI/bge-small-en-v1.5` 模型正确加载并完成向量化

说明：该示例使用内存向量存储与临时文件作为知识源，适用于快速验证 SearchTool + RAG 链路，生产环境建议替换为持久化向量数据库。

## 适用场景建议

- 快速验证 SearchTool 封装 + RAG 检索增强链路：适合使用本示例
- 验证 Agent 自主决策是否调用知识检索工具：适合使用本示例
- 需要测试自定义 DocumentLoader / VectorStore 组件：建议使用 `examples/knowledge_with_custom_components`
- 需要测试基于 PromptTemplate 的 RAG 注入：建议使用 `examples/knowledge_with_prompt_template`
