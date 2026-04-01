# Knowledge VectorStore RAG Agent 示例

本示例展示如何在 trpc-agent 框架中使用不同的 **向量数据库（VectorStore）** 构建 RAG（Retrieval-Augmented Generation）Agent。支持以下三种向量数据库后端：

- **PGVector** — 基于 PostgreSQL 的向量扩展
- **Elasticsearch** — 基于 Elasticsearch 的向量检索
- **Tencent Cloud VectorDB** — 腾讯云向量数据库

## 关键特性

- **多向量数据库支持**：通过环境变量 `VECTORSTORE_TYPE` 切换不同的向量数据库后端
- **知识库检索增强**：Agent 在对话时自动调用知识库搜索工具，基于检索到的文档内容生成更准确的回答
- **Langchain 生态集成**：通过 `LangchainKnowledge` 无缝对接 Langchain 的 Document Loader、Text Splitter、Embeddings、VectorStore 等组件
- **工具自动调用**：Agent 根据用户问题自动决策是否需要调用 `simple_search` 工具检索知识库
- **流式输出**：支持流式输出 Agent 的推理过程、工具调用和最终回答

## 环境要求

Python版本: 3.10+（强烈建议使用3.12）

## 在trpc-agent-python框架代码下如何运行此代码示例

1. 下载trpc-agent-python代码并安装

```bash
git clone https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent
cd trpc-agent
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

2. 安装 RAG 相关依赖

根据选择的向量数据库后端安装对应依赖：

**PGVector：**

```bash
pip3 install langchain-community langchain-huggingface sentence-transformers langchain-postgres
```

**Elasticsearch：**

```bash
pip3 install langchain-community langchain-huggingface sentence-transformers langchain-elasticsearch
```

**Tencent Cloud VectorDB（腾讯云向量数据库）：**

```bash
pip3 install langchain-community tcvectordb
```

| 依赖包 | 说明 |
|---|---|
| `langchain-community` | 提供 `TextLoader` 等文档加载器及腾讯云向量数据库集成 |
| `langchain-huggingface` | 提供 `HuggingFaceEmbeddings` 向量嵌入模型接口（PGVector/Elasticsearch 使用） |
| `sentence-transformers` | HuggingFace 嵌入模型的底层依赖（PGVector/Elasticsearch 使用） |
| `langchain-postgres` | 提供 `PGVector` 向量数据库接口 |
| `langchain-elasticsearch` | 提供 `ElasticsearchStore` 向量数据库接口 |
| `tcvectordb` | 腾讯云向量数据库 SDK |

> 使用 PGVector/Elasticsearch 时，首次运行会自动从 HuggingFace Hub 下载 `BAAI/bge-small-en-v1.5` 嵌入模型，请确保网络可访问 huggingface

3. 配置环境变量

在 `.env` 文件中设置以下变量（也可以通过export设置）：

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

4. 准备知识库文档

默认使用项目下的 `test.txt` 作为知识库文档。你可以替换为自己的文档，或通过 `KNOWLEDGE_FILE` 环境变量指定路径。

示例测试文档：

```shell
echo "shenzhen weather: sunny
guangzhou weather: rain
shanghai weather: cloud" > test.txt
```

5. 运行示例

```bash
cd examples/knowledge_with_vectorstore/
python3 run_agent.py
```

## 核心组件说明

### 项目结构

```
knowledge_with_vectorstore/
├── .env                  # 环境变量配置（LLM、向量数据库连接参数等）
├── README.md             # 项目说明
├── test.txt              # 示例知识库文档
├── run_agent.py          # 主入口：初始化向量库并运行 Agent 对话
└── agent/
    ├── __init__.py
    ├── config.py          # 配置：从环境变量读取 LLM 和向量数据库连接参数
    ├── prompts.py         # Prompt 定义：Agent 指令 + RAG 检索模板
    ├── tools.py           # 工具定义：知识库构建 + 检索搜索工具
    └── agent.py           # Agent 定义：组装模型、工具、指令
```

### `agent/config.py` — 配置管理

| 函数 | 说明 |
|---|---|
| `get_model_config()` | 从环境变量读取 LLM 模型配置 |
| `get_vectorstore_type()` | 获取向量数据库类型（pgvector/elasticsearch/tencentvdb） |
| `get_pgvector_config()` | 获取 PGVector 连接配置 |
| `get_elasticsearch_config()` | 获取 Elasticsearch 连接配置 |
| `get_tencentvdb_config()` | 获取腾讯云向量数据库连接配置 |

### `agent/prompts.py` — Prompt 模板

| 变量 | 说明 |
|---|---|
| `INSTRUCTION` | Agent 的系统指令，定义 Agent 的对话风格和行为 |
| `RAG_PROMPT_TEMPLATE` | RAG 检索时的 Prompt 模板，使用 `{query}` 占位符传入用户问题 |
| `rag_prompt` | 基于模板生成的 `ChatPromptTemplate` 实例，供 `LangchainKnowledge` 使用 |

### `agent/tools.py` — 知识库与搜索工具

| 组件 | 说明 |
|---|---|
| `build_knowledge()` | 根据 `VECTORSTORE_TYPE` 构建对应的 RAG 知识链 |
| `_build_pgvector_knowledge()` | 使用 PGVector 构建知识链 |
| `_build_elasticsearch_knowledge()` | 使用 Elasticsearch 构建知识链 |
| `_build_tencentvdb_knowledge()` | 使用腾讯云向量数据库构建知识链 |
| `get_create_vectorstore_kwargs()` | 返回创建向量数据库时所需的额外参数 |
| `rag` | 模块级 `LangchainKnowledge` 实例，在模块加载时构建 |
| `simple_search(query)` | 搜索工具函数，作为 `FunctionTool` 注册到 Agent，执行知识库检索并返回最相关的文档 |

### `agent/agent.py` — Agent 组装

通过 `create_agent()` 将模型、指令和工具组装为一个 `LlmAgent`：
- **模型**：通过 `config.py` 从环境变量加载 OpenAI 兼容模型配置
- **指令**：使用 `prompts.py` 中定义的 `INSTRUCTION`
- **工具**：将 `simple_search` 包装为 `FunctionTool` 注册到 Agent

### `run_agent.py` — 运行入口

执行流程：
1. 加载 `.env` 环境变量
2. 调用 `rag.create_vectorstore_from_document()` 从文档构建向量数据库（加载文档 → 分片 → 向量化 → 存入向量库）
3. 创建 `Runner` 并发起对话
4. Agent 接收到用户问题后，自动调用 `simple_search` 工具检索知识库，结合检索结果生成回答

## 参考文档

- [LangChain PGVector](https://python.langchain.com/docs/integrations/vectorstores/pgvector/)
- [LangChain Elasticsearch](https://python.langchain.com/docs/integrations/vectorstores/elasticsearch/)
- [LangChain Tencent Cloud VectorDB](https://python.langchain.com/docs/integrations/vectorstores/tencentvectordb/)
- [腾讯云向量数据库](https://cloud.tencent.com/document/product/1709)
