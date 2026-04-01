# Knowledge RAG Agent 示例

本示例展示如何在 trpc-agent 框架中集成 **RAG（Retrieval-Augmented Generation）** 能力，让 Agent 能够基于知识库文档进行检索增强的对话。

## 关键特性

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

本示例依赖 Langchain 社区组件和 HuggingFace 向量嵌入模型，需要额外安装：

```bash
pip3 install langchain-community langchain-huggingface sentence-transformers
```

| 依赖包 | 说明 |
|---|---|
| `langchain-community` | 提供 `TextLoader` 等文档加载器 |
| `langchain-huggingface` | 提供 `HuggingFaceEmbeddings` 向量嵌入模型接口 |
| `sentence-transformers` | HuggingFace 嵌入模型的底层依赖，用于加载和运行嵌入模型 |

> 首次运行时会自动从 HuggingFace Hub 下载 `BAAI/bge-small-en-v1.5` 嵌入模型，请确保网络可访问 huggingface.co。

3. 配置环境变量

在 `.env` 文件中设置使用 LLM 相关的变量（也可以通过export设置）:
- TRPC_AGENT_API_KEY
- TRPC_AGENT_BASE_URL
- TRPC_AGENT_MODEL_NAME

4. 运行示例

```bash
cd examples/knowledge_with_rag_agent/
python3 run_agent.py
```

## 核心组件说明

### 项目结构

```
knowledge_with_rag_agent/
├── .env                  # 环境变量配置（API Key、模型地址等）
├── README.md             # 项目说明
├── run_agent.py          # 主入口：初始化向量库并运行 Agent 对话
└── agent/
    ├── __init__.py
    ├── config.py          # 模型配置：从环境变量读取 LLM 连接参数
    ├── prompts.py         # Prompt 定义：Agent 指令 + RAG 检索模板
    ├── tools.py           # 工具定义：知识库构建 + 检索搜索工具
    └── agent.py           # Agent 定义：组装模型、工具、指令
```

### `agent/prompts.py` — Prompt 模板

| 变量 | 说明 |
|---|---|
| `INSTRUCTION` | Agent 的系统指令，定义 Agent 的对话风格和行为 |
| `RAG_PROMPT_TEMPLATE` | RAG 检索时的 Prompt 模板，使用 `{query}` 占位符传入用户问题 |
| `rag_prompt` | 基于模板生成的 `ChatPromptTemplate` 实例，供 `LangchainKnowledge` 使用 |

### `agent/tools.py` — 知识库与搜索工具

| 组件 | 说明 |
|---|---|
| `build_knowledge()` | 构建 RAG 知识链，组装以下 Langchain 组件并返回 `LangchainKnowledge` 实例 |
| `HuggingFaceEmbeddings` | 向量嵌入模型，使用 `BAAI/bge-small-en-v1.5` 将文本转换为向量表示 |
| `TextLoader` | 文档加载器，从文本文件加载原始文档内容 |
| `RecursiveCharacterTextSplitter` | 文档分割器，将长文档按指定 chunk_size 切分为小片段 |
| `InMemoryVectorStore` | 内存向量数据库，存储文档向量并支持相似度检索 |
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