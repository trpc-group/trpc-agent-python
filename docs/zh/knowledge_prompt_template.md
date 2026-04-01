# Prompt Templates 提示模版

Prompt Templates（提示模版）是连接用户输入与大语言模型（LLM）之间的桥梁。它将用户的原始输入和相关参数按照预定义的模版结构组织为模型可理解的指令，从而引导模型在特定上下文中生成更精准、更连贯的响应。在 RAG 场景中，Prompt Templates 尤为重要——通过合理的模版设计，可以将检索到的知识片段与用户问题有效融合，显著提升模型的回答质量。

以下是一些常用模版组件的用法介绍：

- [PromptTemplate（StringPromptTemplate）](#prompttemplatestringprompttemplate)
- [ChatPromptTemplate](#chatprompttemplate)
- [MessagesPlaceholder](#messagesplaceholder)

更多组件使用说明详见 [Langchain Prompt Templates](https://python.langchain.com/docs/concepts/prompt_templates/)。

## 安装依赖

`Prompt Templates` 相关包位于 `langchain-core` 包中，该包是 `langchain` 的子依赖。

在安装了 trpc-python-agent 框架后，相关依赖会自动安装，因此无需进一步安装依赖。

## PromptTemplate（StringPromptTemplate）

### 使用

1. 创建 `PromptTemplate` 对象

`PromptTemplate` 用于格式化单个字符串，通常用于简单的输入。通过 `{context}` 和 `{query}` 占位符将检索结果与用户问题拼接。

```python
from langchain_core.prompts import PromptTemplate

prompt = PromptTemplate.from_template(
    "请根据以下检索到的上下文回答用户的问题。\n"
    "上下文：{context}\n"
    "问题：{query}\n"
    "回答："
)
```

2. 基于此 prompt 对象构造 `LangchainKnowledge` 对象

```python
from trpc_agent_sdk.server.knowledge.langchain_knowledge import LangchainKnowledge

rag = LangchainKnowledge(
    prompt_template=prompt,
    document_loader=text_loader,
    document_transformer=text_splitter,
    embedder=embedder,
    vectorstore=vectorstore,
)
```

### 参考文档

- [PromptTemplate](https://python.langchain.com/docs/concepts/prompt_templates/)


## ChatPromptTemplate

### 使用

1. 创建 `ChatPromptTemplate` 对象

`ChatPromptTemplate` 用于格式化消息列表，由模版列表组成。支持 `system` / `user` 角色分离，让模型明确系统指令与用户输入的边界。

```python
from langchain_core.prompts import ChatPromptTemplate

prompt = ChatPromptTemplate([
    ("system", "你是一个知识问答助手，请根据提供的上下文信息回答问题。如果上下文中没有相关信息，请如实说明。"),
    ("user", "上下文：{context}\n\n问题：{query}"),
])
```

2. 基于此 prompt 对象构造 `LangchainKnowledge` 对象

```python
from trpc_agent_sdk.server.knowledge.langchain_knowledge import LangchainKnowledge

rag = LangchainKnowledge(
    prompt_template=prompt,
    document_loader=text_loader,
    document_transformer=text_splitter,
    embedder=embedder,
    vectorstore=vectorstore,
)
```

### 参考文档

- [ChatPromptTemplate](https://python.langchain.com/docs/concepts/prompt_templates/)

## MessagesPlaceholder

### 使用

1. 创建包含 `MessagesPlaceholder` 的 prompt 对象

`MessagesPlaceholder` 用于在特定位置插入消息列表，适用于需要保留对话历史的多轮对话场景。

```python
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

prompt = ChatPromptTemplate([
    ("system", "你是一个知识问答助手，请根据提供的上下文信息和对话历史回答问题。"),
    MessagesPlaceholder("chat_history"),
    ("user", "上下文：{context}\n\n问题：{query}"),
])
```

`MessagesPlaceholder("chat_history")` 会在调用时被替换为实际的对话历史消息列表：

```python
from langchain_core.messages import HumanMessage, AIMessage

prompt.invoke({
    "chat_history": [
        HumanMessage(content="什么是人工智能？"),
        AIMessage(content="人工智能是计算机科学的一个分支..."),
    ],
    "context": "深度学习是机器学习的一个子领域...",
    "query": "深度学习和机器学习有什么关系？",
})
```

2. 基于此 prompt 对象构造 `LangchainKnowledge` 对象

```python
from trpc_agent_sdk.server.knowledge.langchain_knowledge import LangchainKnowledge

rag = LangchainKnowledge(
    prompt_template=prompt,
    document_loader=text_loader,
    document_transformer=text_splitter,
    embedder=embedder,
    vectorstore=vectorstore,
)
```

### 参考文档

- [MessagesPlaceholder](https://python.langchain.com/docs/concepts/prompt_templates/)

## 完整示例

完整示例见：[examples/knowledge_with_prompt_template/run_agent.py](../../examples/knowledge_with_prompt_template/run_agent.py)
