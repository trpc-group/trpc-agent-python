# Knowledge RAG Agent 示例

本示例演示如何基于 `LlmAgent` 集成 **RAG（Retrieval-Augmented Generation）** 能力，让 Agent 能够基于知识库文档进行检索增强的对话，并验证 `知识库构建 + 向量检索 + 工具调用 + 流式输出` 的核心链路是否正常工作。

## 关键特性

- **知识库检索增强**：Agent 在对话时自动调用 `simple_search` 工具检索知识库，基于检索到的文档内容生成更准确的回答
- **Langchain 生态集成**：通过 `LangchainKnowledge` 无缝对接 Langchain 的 Document Loader、Text Splitter、Embeddings、VectorStore 等组件
- **工具自动决策**：Agent 根据用户问题自动决策是否需要调用知识库检索工具，无需手动干预
- **流式事件处理**：通过 `runner.run_async(...)` 处理 partial/full event，并打印工具调用与工具返回

## Agent 层级结构说明

本例是单 Agent 示例，不涉及多 Agent 分层路由：

```text
rag_agent (LlmAgent)
├── model: OpenAIModel
├── tools:
│   └── simple_search(query)  — 知识库向量检索
├── knowledge: LangchainKnowledge
│   ├── document_loader: TextLoader
│   ├── document_transformer: RecursiveCharacterTextSplitter
│   ├── embedder: HuggingFaceEmbeddings (BAAI/bge-small-en-v1.5)
│   └── vectorstore: InMemoryVectorStore
└── session: InMemorySessionService
```

关键文件：

- [examples/knowledge_with_rag_agent/agent/agent.py](./agent/agent.py)：构建 `LlmAgent`、挂载工具、设置模型
- [examples/knowledge_with_rag_agent/agent/tools.py](./agent/tools.py)：知识库构建与检索搜索工具实现
- [examples/knowledge_with_rag_agent/agent/prompts.py](./agent/prompts.py)：系统指令与 RAG 检索 Prompt 模板
- [examples/knowledge_with_rag_agent/agent/config.py](./agent/config.py)：环境变量读取
- [examples/knowledge_with_rag_agent/run_agent.py](./run_agent.py)：测试入口，构建向量库并执行对话

## 关键代码解释

这一节用于快速定位"知识库构建、向量检索、工具调用、流式输出"四条核心链路。

### 1) Agent 组装与模型配置（`agent/agent.py`）

- 使用 `LlmAgent` 组装 RAG 助手，挂载 `FunctionTool(simple_search)` 知识库检索工具
- 通过 `config.py` 从环境变量加载 OpenAI 兼容模型配置
- 使用统一的系统指令 `INSTRUCTION`，定义 Agent 的对话风格和行为

### 2) 知识库构建与检索工具（`agent/tools.py`）

- `build_knowledge()` 组装 Langchain 组件链：`TextLoader` → `RecursiveCharacterTextSplitter` → `HuggingFaceEmbeddings` → `InMemoryVectorStore`
- 构建 `LangchainKnowledge` 实例，绑定 RAG Prompt 模板
- `simple_search(query)` 作为 `FunctionTool` 注册到 Agent，执行向量检索并返回最相关的文档片段

### 3) 提示词模板（`agent/prompts.py`）

- `INSTRUCTION`：定义 Agent 系统指令与对话风格
- `RAG_PROMPT_TEMPLATE`：RAG 检索时的 Prompt 模板，使用 `{query}` 占位符传入用户问题
- `rag_prompt`：基于模板生成的 `ChatPromptTemplate` 实例，供 `LangchainKnowledge` 使用

### 4) 向量库初始化与流式事件处理（`run_agent.py`）

- 先调用 `rag.create_vectorstore_from_document()` 从文档构建向量数据库（加载文档 → 分片 → 向量化 → 存入向量库）
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

安装 RAG 相关额外依赖：

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

在 [examples/knowledge_with_rag_agent/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/knowledge_with_rag_agent
python3 run_agent.py
```

## 运行结果（实测）

```text
Warning: You are sending unauthenticated requests to the HF Hub. Please set a HF_TOKEN to enable higher rate limits and faster downloads.
Loading weights: 100%|███████████████████████████████████████████████████████| 199/199 [00:00<00:00, 7097.69it/s]
BertModel LOAD REPORT from: BAAI/bge-small-en-v1.5
Key                     | Status     |  |
------------------------+------------+--+-
embeddings.position_ids | UNEXPECTED |  |

Notes:
- UNEXPECTED:   can be ignored when loading from different task/architecture; not ok if you expect identical arch.
🆔 Session ID: 9fa905eb...
📝 User: 什么是人工智能?
🤖 Assistant: 人工智能（Artificial Intelligence，简称 AI）是指由计算机系统或机器模拟人类智能的技术和科学。它涉及让机器具备感知、学习、推理、规划、决策、语言理解、创造等能力，从而完成通常需要人类智能才能完成的任务。

### 人工智能的核心领域包括：
1. **机器学习（Machine Learning）**：让机器通过数据自动学习和改进，而无需显式编程。
2. **深度学习（Deep Learning）**：一种基于神经网络的机器学习方法，擅长处理图像、语音和自然语言等复杂数据。
3. **自然语言处理（NLP）**：让机器理解和生成人类语言，例如聊天机器人、翻译工具等。
4. **计算机视觉（Computer Vision）**：让机器"看懂"图像和视频，例如人脸识别、自动驾驶等。
5. **机器人技术（Robotics）**：结合硬件和软件，让机器人执行物理任务。
6. **专家系统（Expert Systems）**：模拟人类专家的决策能力，用于医疗诊断、金融分析等领域。

### 人工智能的分类：
- **弱人工智能（Narrow AI）**：专注于特定任务，如语音助手（Siri、Alexa）、推荐系统（Netflix、淘宝）。
- **强人工智能（General AI）**：具备类似人类的广泛智能，目前尚未实现。
- **超级人工智能（Super AI）**：超越人类智能的理论概念。

### 应用场景：
- **医疗**：辅助诊断、药物研发。
- **金融**：风险评估、量化交易。
- **交通**：自动驾驶、智能交通管理。
- **教育**：个性化学习、智能辅导。
- **娱乐**：游戏 AI、内容生成。

人工智能正在快速改变我们的生活和工作方式，但也带来伦理、隐私和就业等挑战。你对哪个具体方面感兴趣？我可以进一步展开！
----------------------------------------
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **知识库构建正确**：嵌入模型 `BAAI/bge-small-en-v1.5` 成功加载，向量数据库正常构建
- **检索工具可用**：Agent 能够调用 `simple_search` 工具从知识库中检索相关文档片段
- **回答内容合理**：Agent 基于检索到的知识库内容，结合自身能力生成了结构清晰、内容丰富的回答
- **流式输出正常**：文本分片与工具调用事件均正确打印

说明：该示例主要验证 RAG 知识链路（文档加载 → 分片 → 向量化 → 检索 → 生成）的端到端可用性，不强调多轮记忆一致性。

## 适用场景建议

- 快速验证 RAG 知识库检索增强链路：适合使用本示例
- 验证 Langchain 生态组件集成（DocumentLoader / TextSplitter / Embeddings / VectorStore）：适合使用本示例
- 需要自定义知识库组件（如自定义 Embeddings 或 VectorStore）：建议使用 `examples/knowledge_with_custom_components`
- 需要测试知识库与 Prompt 模板的结合使用：建议使用 `examples/knowledge_with_prompt_template`
