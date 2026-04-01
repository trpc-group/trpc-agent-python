# Knowledge SearchTool RAG Agent 示例

本示例展示了如何将 RAG 知识检索能力封装为 SearchTool，让 Agent 自主决定何时调用知识库进行检索增强生成。

## 关键特性

- **SearchTool 封装**：通过 `LangchainKnowledgeSearchTool` 将知识检索封装为标准 Agent 工具，Agent 可自主判断何时调用
- **完整 RAG 管道**：基于 LangChain 生态集成文档加载（TextLoader）、文本分割（RecursiveCharacterTextSplitter）、向量嵌入（HuggingFaceEmbeddings）和向量存储（InMemoryVectorStore）
- **相似度检索**：支持基于向量相似度（`SearchType.SIMILARITY`）的文档召回，可配置 `top_k` 控制返回数量
- **流式响应输出**：实时展示 Agent 推理过程，包括工具调用参数和返回结果的完整链路

## 环境要求
Python版本: 3.10+（强烈建议使用3.12）

## 在trpc-agent-python框架代码下如何运行此代码示例

1. 下载trpc-agent-python代码并安装

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
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


3. 运行此代码示例

在 `.env` 文件中设置使用 LLM 相关的变量（也可以通过export设置）:
- TRPC_AGENT_API_KEY
- TRPC_AGENT_BASE_URL
- TRPC_AGENT_MODEL_NAME

然后运行下面的命令：

```bash
cd examples/knowledge_with_searchtool_rag_agent/
python3 run_agent.py
```
