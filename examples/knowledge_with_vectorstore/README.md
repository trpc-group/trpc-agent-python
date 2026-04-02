# Knowledge VectorStore RAG Agent 示例

本示例演示如何基于 `LlmAgent` + `LangchainKnowledge` 构建一个 RAG（Retrieval-Augmented Generation）知识库问答助手，支持 PGVector、Elasticsearch、腾讯云向量数据库三种后端，验证 `文档加载 → 向量化 → 检索 → 生成回答` 的完整 RAG 链路。

## 关键特性

- **多向量数据库支持**：通过环境变量 `VECTORSTORE_TYPE` 一键切换 PGVector / Elasticsearch / 腾讯云向量数据库后端
- **Langchain 生态集成**：通过 `LangchainKnowledge` 无缝对接 Langchain 的 Document Loader、Text Splitter、Embeddings、VectorStore 等组件
- **知识库检索增强**：Agent 在对话时自动调用 `simple_search` 工具检索知识库，基于检索到的文档内容生成更准确的回答
- **流式事件处理**：通过 `runner.run_async(...)` 处理 partial/full event，并打印工具调用与工具返回

## Agent 层级结构说明

本例是单 Agent 示例，不涉及多 Agent 分层路由：

```text
rag_agent (LlmAgent)
├── model: OpenAIModel
├── tools:
│   └── simple_search(query) — 基于 LangchainKnowledge 检索向量库并返回最相关文档
├── knowledge: LangchainKnowledge
│   ├── document_loader: TextLoader
│   ├── document_transformer: RecursiveCharacterTextSplitter
│   ├── embedder: HuggingFaceEmbeddings / TencentVDB 内置
│   └── vectorstore: PGVector / ElasticsearchStore / TencentVectorDB
└── session: InMemorySessionService
```

关键文件：

- [examples/knowledge_with_vectorstore/agent/agent.py](./agent/agent.py)：构建 `LlmAgent`、挂载工具、设置指令
- [examples/knowledge_with_vectorstore/agent/tools.py](./agent/tools.py)：知识库构建 + 检索搜索工具实现
- [examples/knowledge_with_vectorstore/agent/prompts.py](./agent/prompts.py)：提示词模板与 RAG Prompt
- [examples/knowledge_with_vectorstore/agent/config.py](./agent/config.py)：环境变量读取（LLM 与向量数据库配置）
- [examples/knowledge_with_vectorstore/run_agent.py](./run_agent.py)：测试入口，构建向量库并执行对话

## 关键代码解释

这一节用于快速定位"知识库构建、检索工具、Agent 组装"三条核心链路。

### 1) 知识库构建与向量数据库切换（`agent/tools.py`）

- 通过 `_BUILDERS` 字典按 `VECTORSTORE_TYPE` 分发到 `_build_pgvector_knowledge()`、`_build_elasticsearch_knowledge()`、`_build_tencentvdb_knowledge()` 三个构建函数
- 每个构建函数使用 `TextLoader` 加载文档、`RecursiveCharacterTextSplitter` 切片、对应 Embeddings + VectorStore 组装为 `LangchainKnowledge` 实例
- 模块级变量 `rag = build_knowledge()` 在加载时即完成知识链构建

### 2) 检索工具与 RAG Prompt（`agent/tools.py` + `agent/prompts.py`）

- `simple_search(query)` 作为 `FunctionTool` 注册到 Agent，执行 `rag.search()` 返回最相关文档
- RAG Prompt 模板使用 `{query}` 占位符，由 `ChatPromptTemplate` 生成，传入 `LangchainKnowledge` 用于检索时的问题格式化
- Agent 根据用户问题自动决策是否需要调用 `simple_search` 工具

### 3) Agent 组装与运行入口（`agent/agent.py` + `run_agent.py`）

- `create_agent()` 将 `OpenAIModel`、`INSTRUCTION` 指令和 `FunctionTool(simple_search)` 组装为 `LlmAgent`
- `run_agent.py` 执行流程：加载 `.env` → 调用 `rag.create_vectorstore_from_document()` 构建向量库 → 创建 `Runner` 发起对话
- 使用 `runner.run_async(...)` 消费事件流，区分并打印 `function_call`（工具调用）与 `function_response`（工具返回）

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

根据选择的向量数据库后端安装 RAG 相关依赖：

**PGVector：**

```bash
pip3 install langchain-community langchain-huggingface sentence-transformers langchain-postgres
```

**Elasticsearch：**

```bash
pip3 install langchain-community langchain-huggingface sentence-transformers langchain-elasticsearch
```

**腾讯云向量数据库：**

```bash
pip3 install langchain-community tcvectordb
```

> 使用 PGVector / Elasticsearch 时，首次运行会自动从 HuggingFace Hub 下载 `BAAI/bge-small-en-v1.5` 嵌入模型，请确保网络可访问 HuggingFace。

### 环境变量要求

在 [examples/knowledge_with_vectorstore/.env](./.env) 中配置（或通过 `export`）：

**通用配置（必填）：**

- `TRPC_AGENT_API_KEY` — LLM API Key
- `TRPC_AGENT_BASE_URL` — LLM API 地址
- `TRPC_AGENT_MODEL_NAME` — LLM 模型名称
- `VECTORSTORE_TYPE` — 向量数据库类型：`pgvector` / `elasticsearch` / `tencentvdb`

**PGVector 配置：**

- `PGVECTOR_CONNECTION` — PostgreSQL 连接字符串，如 `postgresql+psycopg://langchain:langchain@X.X.X.X:5432/langchain`
- `PGVECTOR_COLLECTION_NAME` — 集合名称（默认 `my_docs`）

**Elasticsearch 配置：**

- `ES_URL` — Elasticsearch 地址，如 `https://X.X.X.X:9200`
- `ES_INDEX_NAME` — 索引名称（默认 `langchain_index`）
- `ES_API_KEY` — Elasticsearch API Key

**腾讯云向量数据库配置：**

- `TENCENT_VDB_URL` — 数据库连接地址，如 `http://10.0.X.X`
- `TENCENT_VDB_KEY` — 数据库密钥
- `TENCENT_VDB_USERNAME` — 用户名（默认 `root`）
- `TENCENT_VDB_DATABASE` — 数据库名（默认 `LangChainDatabase`）
- `TENCENT_VDB_COLLECTION` — 集合名（默认 `LangChainCollection`）
- `TENCENT_VDB_EMBEDDING` — 腾讯云嵌入模型（默认 `bge-base-zh`）

**可选配置：**

- `KNOWLEDGE_FILE` — 知识库文档路径（默认使用项目下的 `test.txt`）

### 运行命令

准备知识库文档（默认使用项目下的 `test.txt`，也可替换为自己的文档）：

```shell
echo "shenzhen weather: sunny
guangzhou weather: rain
shanghai weather: cloud" > test.txt
```

```bash
cd examples/knowledge_with_vectorstore
python3 run_agent.py
```

## 运行结果（实测）

```text
🆔 Session ID: a3f1b2c8...
📝 User: shenzhen weather
🤖 Assistant:
🔧 [调用工具: simple_search({'query': 'shenzhen weather'})]
📊 [工具结果: {'status': 'success', 'report': 'content: shenzhen weather: sunny'}]
Based on the information I found, the weather in Shenzhen is currently sunny. It's a great day to enjoy some outdoor activities! Let me know if you'd like to know about the weather in other cities.
----------------------------------------
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **工具路由正确**：Agent 收到天气相关问题后自动调用 `simple_search` 检索知识库
- **检索结果正确**：查询 "shenzhen weather" 返回了知识库中最匹配的文档 `shenzhen weather: sunny`
- **工具结果被正确消费**：回复内容与知识库检索结果一致，并组织为自然语言回答
- **RAG 链路完整**：从文档加载 → 切片 → 向量化 → 存入向量库 → 检索 → 生成回答，全流程通过

说明：该示例需要先通过 `rag.create_vectorstore_from_document()` 构建向量库，确保向量数据库服务已启动且配置正确。

## 适用场景建议

- 快速验证 RAG 知识库检索 + Agent 问答主链路：适合使用本示例
- 需要对比不同向量数据库后端（PGVector / Elasticsearch / 腾讯云）的接入差异：适合使用本示例
- 验证 Langchain 生态组件与 trpc-agent 的集成能力：适合使用本示例
- 需要测试自定义 Document Loader / Embeddings / Prompt 的灵活组合：建议使用 `examples/knowledge_with_custom_components`
