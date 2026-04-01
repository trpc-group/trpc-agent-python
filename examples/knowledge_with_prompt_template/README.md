# Knowledge with Prompt Template 示例

本示例展示了三种 Prompt Template 在 RAG 知识库中的用法，帮助理解不同模版类型的适用场景。

## 关键特性

- **PromptTemplate（StringPromptTemplate）**：格式化单个字符串，适用于简单的输入场景
- **ChatPromptTemplate**：格式化消息列表，支持 system/user 角色分离，适用于需要明确角色指令的场景
- **MessagesPlaceholder**：在特定位置插入消息列表，适用于需要保留对话历史的多轮对话场景
- **LangchainKnowledge 集成**：每种 Prompt Template 均通过 `LangchainKnowledge` 构建完整的 RAG 管道
- **LangchainKnowledgeSearchTool**：将知识检索封装为 Agent 可调用的标准工具

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

本示例还依赖 Langchain 社区组件和 HuggingFace 向量嵌入模型，需要额外安装：

```bash
pip3 install langchain-community langchain-huggingface sentence-transformers
```

| 依赖包 | 说明 |
|---|---|
| `langchain-community` | 提供 `TextLoader` 等文档加载器 |
| `langchain-huggingface` | 提供 `HuggingFaceEmbeddings` 向量嵌入模型接口 |
| `sentence-transformers` | HuggingFace 嵌入模型的底层依赖，用于加载和运行嵌入模型 |


2. 运行此代码示例
在 `.env` 文件中设置使用 LLM 相关的变量（也可以通过export设置）:
- TRPC_AGENT_API_KEY
- TRPC_AGENT_BASE_URL
- TRPC_AGENT_MODEL_NAME

然后运行下面的命令：

```bash
cd examples/knowledge_with_prompt_template/
python3 run_agent.py
```
