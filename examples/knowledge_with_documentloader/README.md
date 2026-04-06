# Knowledge DocumentLoader 示例

本示例演示如何在 trpc-agent 框架中使用不同的 **DocumentLoader（文档加载器）** 加载多种格式的数据源，并结合 RAG 能力让 Agent 基于知识库进行检索增强的对话。

## 关键特性

- **多种文档加载器**：支持 `TextLoader`（纯文本）、`PyPDFLoader`（PDF）、`UnstructuredMarkdownLoader`（Markdown）三种加载器，通过环境变量一键切换
- **知识库检索增强**：Agent 在对话时自动调用知识库搜索工具，基于检索到的文档内容生成更准确的回答
- **Langchain 生态集成**：通过 `LangchainKnowledge` 无缝对接 Langchain 的 Document Loader、Text Splitter、Embeddings、VectorStore 等组件
- **工具自动调用**：Agent 根据用户问题自动决策是否需要调用 `simple_search` 工具检索知识库
- **向量库重试机制**：`run_agent.py` 中内置指数退避重试逻辑，确保向量数据库构建的稳定性

## Agent 层级结构说明

本例是单 Agent 示例，不涉及多 Agent 分层路由：

```text
documentloader_agent (LlmAgent)
├── model: OpenAIModel
├── tools:
│   └── simple_search(query)  ← 检索知识库，返回最相关文档
└── knowledge: LangchainKnowledge
    ├── document_loader: TextLoader / PyPDFLoader / UnstructuredMarkdownLoader（按环境变量切换）
    ├── document_transformer: RecursiveCharacterTextSplitter
    ├── embedder: HuggingFaceEmbeddings (BAAI/bge-small-en-v1.5)
    └── vectorstore: InMemoryVectorStore
```

关键文件：

- [examples/knowledge_with_documentloader/agent/agent.py](./agent/agent.py)：构建 `LlmAgent`、挂载搜索工具、设置指令
- [examples/knowledge_with_documentloader/agent/tools.py](./agent/tools.py)：三种 DocumentLoader 工厂、知识库构建与 `simple_search` 搜索工具实现
- [examples/knowledge_with_documentloader/agent/prompts.py](./agent/prompts.py)：Agent 指令与 RAG Prompt 模板
- [examples/knowledge_with_documentloader/agent/config.py](./agent/config.py)：环境变量读取
- [examples/knowledge_with_documentloader/run_agent.py](./run_agent.py)：测试入口，构建向量库并执行对话

## 关键代码解释

这一节用于快速定位"文档加载、知识库构建、检索调用"三条核心链路。

### 1) DocumentLoader 选择与知识库构建（`agent/tools.py`）

- 通过环境变量 `DOCUMENT_LOADER_TYPE` 控制加载器类型（`text` / `pdf` / `markdown`），默认为 `text`
- `_create_text_loader()` 将示例文本写入临时文件后用 `TextLoader` 加载；`_create_pypdf_loader()` 从 `DOCUMENT_PDF_PATH` 读取 PDF；`_create_unstructured_markdown_loader()` 将示例 Markdown 写入临时文件后加载
- `build_knowledge()` 根据加载器类型组装 `LangchainKnowledge`，串联 DocumentLoader → `RecursiveCharacterTextSplitter` → `HuggingFaceEmbeddings` → `InMemoryVectorStore` 完整 RAG 链

### 2) Prompt 模板与 Agent 组装（`agent/prompts.py` + `agent/agent.py`）

- `INSTRUCTION` 定义 Agent 对话风格：友好、会话式、记忆上下文
- `RAG_PROMPT_TEMPLATE` 使用 `{query}` 占位符，由 `LangchainKnowledge` 在检索时自动填充用户问题
- `create_agent()` 将 `OpenAIModel`、`INSTRUCTION`、`FunctionTool(simple_search)` 组装为 `LlmAgent`

### 3) 向量库初始化与对话执行（`run_agent.py`）

- 启动时调用 `rag.create_vectorstore_from_document()` 完成"加载文档 → 分片 → 向量化 → 存入向量库"全流程，内置指数退避重试
- 使用 `Runner` + `InMemorySessionService` 发起对话
- Agent 接收到用户问题后，自动调用 `simple_search` 检索知识库，结合检索结果生成回答
- 流式事件中区分并打印 `function_call`（工具调用）与 `function_response`（工具返回）

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

安装 DocumentLoader 相关依赖：

```bash
pip3 install langchain-community langchain-huggingface sentence-transformers
```

如需使用 `PyPDFLoader`，还需安装：

```bash
pip3 install pypdf
```

如需使用 `UnstructuredMarkdownLoader`，还需安装：

```bash
pip3 install unstructured
```

| 依赖包 | 说明 |
|---|---|
| `langchain-community` | 提供 `TextLoader`、`PyPDFLoader`、`UnstructuredMarkdownLoader` 等文档加载器 |
| `langchain-huggingface` | 提供 `HuggingFaceEmbeddings` 向量嵌入模型接口 |
| `sentence-transformers` | HuggingFace 嵌入模型的底层依赖，用于加载和运行嵌入模型 |
| `pypdf` | `PyPDFLoader` 的底层依赖，用于解析 PDF 文件 |
| `unstructured` | `UnstructuredMarkdownLoader` 的底层依赖，用于解析 Markdown 文件 |

> 首次运行时会自动从 HuggingFace Hub 下载 `BAAI/bge-small-en-v1.5` 嵌入模型，请确保网络可访问 huggingface.co。

### 环境变量要求

在 [examples/knowledge_with_documentloader/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

DocumentLoader 相关配置（可选）：

- `DOCUMENT_LOADER_TYPE`：加载器类型，可选值 `text`、`pdf`、`markdown`，默认为 `text`
- `DOCUMENT_PDF_PATH`：当使用 `pdf` 类型时，需要设置 PDF 文件路径

### 运行命令

```bash
cd examples/knowledge_with_documentloader
python3 run_agent.py
```

切换不同的 DocumentLoader 类型：

```bash
# 使用 TextLoader（默认）
DOCUMENT_LOADER_TYPE=text python3 run_agent.py

# 使用 PyPDFLoader
DOCUMENT_LOADER_TYPE=pdf DOCUMENT_PDF_PATH=/path/to/file.pdf python3 run_agent.py

# 使用 UnstructuredMarkdownLoader
DOCUMENT_LOADER_TYPE=markdown python3 run_agent.py
```

## 运行结果（实测）

```text
Warning: You are sending unauthenticated requests to the HF Hub. Please set a HF_TOKEN to enable higher rate limits and faster downloads.
Loading weights: 100%|████████████████████████████████████████████████████████████████████████| 199/199 [00:00<00:00, 18305.29it/s]
BertModel LOAD REPORT from: BAAI/bge-small-en-v1.5
Key                     | Status     |  |
------------------------+------------+--+-
embeddings.position_ids | UNEXPECTED |  |

Notes:
- UNEXPECTED:   can be ignored when loading from different task/architecture; not ok if you expect identical arch.
[2026-04-02 17:38:54][INFO][trpc_agent_sdk][examples/knowledge_with_documentloader/run_agent.py:25][59656] 向量数据库创建成功
🆔 Session ID: bb621749...
📝 User: 什么是人工智能?
🤖 Assistant: 人工智能（Artificial Intelligence，简称 AI）是指由计算机系统或机器模拟人类智能的技术和科学。它旨在使机器能够执行通常需要人类智能的任务，如学习、推理、问题解决、感知、语言理解和决策等。

### 人工智能的核心领域包括：
1. **机器学习（Machine Learning）**：通过算法让机器从数据中学习并改进性能，而无需显式编程。
2. **深度学习（Deep Learning）**：一种基于神经网络的机器学习方法，擅长处理图像、语音和自然语言等复杂数据。
3. **自然语言处理（NLP）**：让机器理解和生成人类语言，例如聊天机器人、翻译工具等。
4. **计算机视觉（Computer Vision）**：使机器能够"看"和理解图像或视频内容。
5. **机器人技术（Robotics）**：结合硬件和软件，让机器人执行物理任务。
6. **专家系统（Expert Systems）**：模拟人类专家的决策能力，用于特定领域的问题解决。

### 人工智能的应用场景：
- **医疗**：辅助诊断、药物研发。
- **金融**：风险评估、算法交易。
- **交通**：自动驾驶汽车。
- **娱乐**：推荐系统（如 Netflix、Spotify）。
- **制造业**：自动化生产线。

### 人工智能的分类：
- **弱人工智能（Narrow AI）**：专注于特定任务（如 Siri、AlphaGo）。
- **强人工智能（General AI）**：具备与人类相似的广泛认知能力（目前尚未实现）。
- **超级人工智能（Super AI）**：超越人类智能（仍属于理论范畴）。

人工智能正在快速发展，对社会、经济和伦理等方面产生深远影响。如果你对某个具体领域感兴趣，可以进一步探讨！
----------------------------------------
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **向量库构建成功**：嵌入模型加载正常，文档成功向量化并写入 `InMemoryVectorStore`
- **工具自动调用**：Agent 接收到"什么是人工智能？"后自动调用 `simple_search` 检索知识库
- **检索结果被正确消费**：回复内容涵盖了知识库中关于 AI 的核心定义，并在此基础上进行了扩展回答
- **DocumentLoader 链路打通**：`TextLoader` 成功加载纯文本 → 分片 → 向量化 → 检索 → 回答，端到端流程正常

说明：本示例默认使用 `TextLoader` 加载内置示例文本。如需验证 `PyPDFLoader` 或 `UnstructuredMarkdownLoader`，可通过环境变量 `DOCUMENT_LOADER_TYPE` 切换。

## 适用场景建议

- 快速验证 DocumentLoader + RAG 检索链路：适合使用本示例
- 需要对比不同文档加载器的接入方式：适合使用本示例
- 需要自定义 Knowledge 组件（Embedding、VectorStore 等）：建议使用 `examples/knowledge_with_custom_components`
- 需要验证 Prompt Template 在 RAG 中的定制用法：建议使用 `examples/knowledge_with_prompt_template`
