# 自定义 Langchain RAG 组件示例

本示例演示如何基于 `LangchainKnowledge` 自定义 RAG 管线中的核心组件（Document Loader、Text Splitter、Retriever），并验证自定义组件在文档加载、文本分割、语义检索三条链路上的正确性。

## 关键特性

- **自定义 Document Loader**：继承 `BaseLoader`，按行加载文件内容，支持同步与异步两种模式
- **自定义 Text Splitter**：继承 `BaseDocumentTransformer`，按指定分隔符分割文本，保留原始元数据
- **自定义 Retriever**：继承 `BaseRetriever`，基于关键词匹配返回 Top-K 文档，并支持 `from_documents` 工厂方法
- **向量检索集成**：使用 `HuggingFaceEmbeddings`（`BAAI/bge-small-en-v1.5`）+ `InMemoryVectorStore` 完成 Embedding 索引与相似度检索
- **三组独立 Demo 覆盖**：同一程序内依次运行 Custom Loader / Custom Splitter / Custom Retriever 三个场景

## 组件层级结构说明

本例是纯 Knowledge 组件示例，不涉及 LlmAgent 或多 Agent 路由，核心结构如下：

```text
LangchainKnowledge (x3 实例)
├── Demo 1: Custom Document Loader
│   ├── document_loader: CustomDocumentLoader (按行读取文件)
│   ├── document_transformer: RecursiveCharacterTextSplitter
│   ├── embedder: HuggingFaceEmbeddings (BAAI/bge-small-en-v1.5)
│   └── vectorstore: InMemoryVectorStore
├── Demo 2: Custom Text Splitter
│   ├── document_loader: TextLoader
│   ├── document_transformer: CustomTextSplitter (按 \n 分割)
│   ├── embedder: HuggingFaceEmbeddings (BAAI/bge-small-en-v1.5)
│   └── vectorstore: InMemoryVectorStore
└── Demo 3: Custom Retriever
    ├── retriever: ToyRetriever (关键词匹配, Top-K)
    └── prompt_template: PromptTemplate("{query}")
```

关键文件：

- [examples/knowledge_with_custom_components/agent/agent.py](./agent/agent.py)：构建三组 `LangchainKnowledge` 实例，分别挂载不同的自定义组件
- [examples/knowledge_with_custom_components/agent/tools.py](./agent/tools.py)：`CustomDocumentLoader`、`CustomTextSplitter`、`ToyRetriever` 的实现
- [examples/knowledge_with_custom_components/agent/prompts.py](./agent/prompts.py)：三组 Demo 的提示词模板
- [examples/knowledge_with_custom_components/agent/config.py](./agent/config.py)：Embedding 模型名称、测试数据文件路径与内容
- [examples/knowledge_with_custom_components/run_agent.py](./run_agent.py)：测试入口，依次执行 3 个 Demo 并输出检索结果

## 关键代码解释

这一节用于快速定位"自定义 Loader、自定义 Splitter、自定义 Retriever"三条核心链路。

### 1) 自定义 Document Loader（`agent/tools.py` — `CustomDocumentLoader`）

- 继承 `BaseLoader`，在 `lazy_load` 中逐行读取文件，每行生成一个 `Document`（携带 `line_number` 和 `source` 元数据）
- 可选实现 `alazy_load`，使用 `aiofiles` 提供原生异步支持；未安装时自动回退到父类默认实现
- 在 `agent.py` 的 `create_document_loader_knowledge()` 中，将该 Loader 与 `RecursiveCharacterTextSplitter` + `InMemoryVectorStore` 组装成完整 RAG 管线

### 2) 自定义 Text Splitter（`agent/tools.py` — `CustomTextSplitter`）

- 继承 `BaseDocumentTransformer`，在 `transform_documents` 中按指定 `separator` 分割文档内容
- 分割后为每个 chunk 保留原始元数据，并附加 `chunk_index` 和 `original_doc_id`
- 在 `agent.py` 的 `create_text_splitter_knowledge()` 中，搭配标准 `TextLoader` 使用

### 3) 自定义 Retriever（`agent/tools.py` — `ToyRetriever`）

- 继承 `BaseRetriever`，在 `_get_relevant_documents` 中通过关键词包含匹配返回 Top-K 文档
- 实现 `from_documents` 类方法，支持与 VectorStore 配合使用时的工厂创建模式
- 在 `agent.py` 的 `create_retriever_knowledge()` 中，直接传入预构造的 `Document` 列表，不依赖 Embedding 模型

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

> **注意**：本示例依赖 `langchain-text-splitters`、`langchain-community`、`langchain-huggingface` 等包，
> 必须使用 `pip3 install -e ".[knowledge]"` 安装 knowledge 可选依赖，否则会报 `ModuleNotFoundError`。

### 环境变量要求

在 [examples/knowledge_with_custom_components/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/knowledge_with_custom_components
python3 run_agent.py
```

## 运行结果（实测）

```text
==================================================
Demo 1: Custom Document Loader
==================================================
Warning: You are sending unauthenticated requests to the HF Hub. Please set a HF_TOKEN to enable higher rate limits and faster downloads.
Loading weights: 100%|██████████████████████████████████████████████████████████████| 199/199 [00:00<00:00, 6858.73it/s]
BertModel LOAD REPORT from: BAAI/bge-small-en-v1.5
Key                     | Status     |  |
------------------------+------------+--+-
embeddings.position_ids | UNEXPECTED |  |

Notes:
- UNEXPECTED:   can be ignored when loading from different task/architecture; not ok if you expect identical arch.
📝 Query: beijing
🤖 Result: {'status': 'success', 'report': 'content: beijing: cloudy'}
----------------------------------------
==================================================
Demo 2: Custom Text Splitter
==================================================
Loading weights: 100%|██████████████████████████████████████████████████████████████| 199/199 [00:00<00:00, 14694.83it/s]
BertModel LOAD REPORT from: BAAI/bge-small-en-v1.5
Key                     | Status     |  |
------------------------+------------+--+-
embeddings.position_ids | UNEXPECTED |  |

Notes:
- UNEXPECTED:   can be ignored when loading from different task/architecture; not ok if you expect identical arch.
📝 Query: beijing
🤖 Result: {'status': 'success', 'report': 'content: beijing: cloudy'}
----------------------------------------
==================================================
Demo 3: Custom Retriever
==================================================
📝 Query: Shenzhen
🤖 Result: {'status': 'success', 'report': 'content: Shenzhen: sunny'}
----------------------------------------
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **Loader 链路正确**：`CustomDocumentLoader` 逐行加载测试文件后，经 `RecursiveCharacterTextSplitter` 分割并入库，查询 `beijing` 成功返回 `beijing: cloudy`
- **Splitter 链路正确**：`CustomTextSplitter` 按 `\n` 分割文档后入库，同样查询 `beijing` 返回 `beijing: cloudy`，说明自定义分割逻辑生效
- **Retriever 链路正确**：`ToyRetriever` 基于关键词匹配，查询 `Shenzhen` 正确返回 `Shenzhen: sunny`，无需 Embedding 模型参与
- **组件替换透明**：三组 Demo 均通过 `LangchainKnowledge` 统一接口调用 `search`，验证了自定义组件可无缝替换默认实现

## 适用场景建议

- 需要自定义文档加载逻辑（如从数据库、API 加载）：参考 Demo 1 的 `CustomDocumentLoader`
- 需要自定义文本分割策略（如按段落、按标记分割）：参考 Demo 2 的 `CustomTextSplitter`
- 需要自定义检索逻辑（如关键词匹配、混合检索）：参考 Demo 3 的 `ToyRetriever`
- 快速验证 `LangchainKnowledge` 组件替换是否正常工作：适合使用本示例

## 参考文档

- [How to create a custom Document Loader](https://python.langchain.com/docs/how_to/document_loader_custom/)
- [how_to/#custom](https://python.langchain.com/docs/how_to/#custom)
- [Custom Embeddings](https://python.langchain.com/docs/how_to/custom_embeddings/)
- [How to create a custom Retriever](https://python.langchain.com/docs/how_to/custom_retriever/)
