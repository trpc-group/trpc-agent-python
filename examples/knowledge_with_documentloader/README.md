# Knowledge DocumentLoader 示例

本示例展示如何在 trpc-agent 框架中使用不同的 **DocumentLoader（文档加载器）** 组件加载多种格式的数据源，并结合 RAG 能力让 Agent 基于知识库进行检索增强的对话。

## 关键特性

- **多种文档加载器**：支持 `TextLoader`（纯文本）、`PyPDFLoader`（PDF）、`UnstructuredMarkdownLoader`（Markdown）三种加载器，通过环境变量一键切换
- **知识库检索增强**：Agent 在对话时自动调用知识库搜索工具，基于检索到的文档内容生成更准确的回答
- **Langchain 生态集成**：通过 `LangchainKnowledge` 无缝对接 Langchain 的 Document Loader、Text Splitter、Embeddings、VectorStore 等组件
- **工具自动调用**：Agent 根据用户问题自动决策是否需要调用 `simple_search` 工具检索知识库

## 环境要求

Python版本: 3.10+（强烈建议使用3.12）

## 在trpc-agent-python框架代码下如何运行此代码示例

1. 下载trpc-agent-python代码并安装

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

2. 安装 DocumentLoader 相关依赖

本示例依赖 Langchain 社区组件和 HuggingFace 向量嵌入模型，需要额外安装：

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

3. 配置环境变量

在 `.env` 文件中设置使用 LLM 相关的变量（也可以通过export设置）:
- TRPC_AGENT_API_KEY
- TRPC_AGENT_BASE_URL
- TRPC_AGENT_MODEL_NAME

设置文档加载器类型（可选，默认为 `text`）：
- DOCUMENT_LOADER_TYPE: 可选值 `text`、`pdf`、`markdown`
- DOCUMENT_PDF_PATH: 当使用 `pdf` 类型时，需要设置 PDF 文件路径

4. 运行示例

```bash
cd examples/knowledge_with_documentloader/
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

## 核心组件说明

### 项目结构

```
knowledge_with_documentloader/
├── .env                  # 环境变量配置（API Key、模型地址、加载器类型等）
├── README.md             # 项目说明
├── run_agent.py          # 主入口：初始化向量库并运行 Agent 对话
└── agent/
    ├── __init__.py
    ├── config.py          # 模型配置：从环境变量读取 LLM 连接参数
    ├── prompts.py         # Prompt 定义：Agent 指令 + RAG 检索模板
    ├── tools.py           # 工具定义：三种 DocumentLoader + 知识库构建 + 检索搜索工具
    └── agent.py           # Agent 定义：组装模型、工具、指令
```

### `agent/tools.py` — DocumentLoader 与知识库

| 组件 | 说明 |
|---|---|
| `LOADER_TYPE` | 从环境变量 `DOCUMENT_LOADER_TYPE` 读取，决定使用哪种 DocumentLoader |
| `_create_text_loader()` | 使用 `TextLoader` 加载纯文本文件，将示例文本写入临时文件后加载 |
| `_create_pypdf_loader()` | 使用 `PyPDFLoader` 加载 PDF 文件，从环境变量 `DOCUMENT_PDF_PATH` 读取文件路径 |
| `_create_unstructured_markdown_loader()` | 使用 `UnstructuredMarkdownLoader` 加载 Markdown 文件，将示例内容写入临时文件后加载 |
| `build_knowledge()` | 根据 `LOADER_TYPE` 选择对应的 DocumentLoader，组装 RAG 知识链并返回 `LangchainKnowledge` 实例 |
| `simple_search(query)` | 搜索工具函数，作为 `FunctionTool` 注册到 Agent，执行知识库检索并返回最相关的文档 |

### `agent/prompts.py` — Prompt 模板

| 变量 | 说明 |
|---|---|
| `INSTRUCTION` | Agent 的系统指令，定义 Agent 的对话风格和行为 |
| `RAG_PROMPT_TEMPLATE` | RAG 检索时的 Prompt 模板，使用 `{query}` 占位符传入用户问题 |
| `rag_prompt` | 基于模板生成的 `ChatPromptTemplate` 实例，供 `LangchainKnowledge` 使用 |

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
