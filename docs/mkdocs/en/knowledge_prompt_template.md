# Prompt Templates

Prompt Templates serve as the bridge between user input and Large Language Models (LLMs). They organize raw user input and related parameters into model-understandable instructions according to predefined template structures, guiding the model to generate more accurate and coherent responses within specific contexts. In RAG scenarios, Prompt Templates are particularly important — through well-designed templates, retrieved knowledge fragments can be effectively integrated with user questions, significantly improving the quality of model responses.

Below is an introduction to some commonly used template components:

- [PromptTemplate (StringPromptTemplate)](#prompttemplate-stringprompttemplate)
- [ChatPromptTemplate](#chatprompttemplate)
- [MessagesPlaceholder](#messagesplaceholder)

For more component usage details, see [Langchain Prompt Templates](https://python.langchain.com/docs/concepts/prompt_templates/).

## Installation

The `Prompt Templates` related packages are located in the `langchain-core` package, which is a sub-dependency of `langchain`.

After installing the trpc-python-agent framework, the related dependencies are automatically installed, so no further installation is required.

## PromptTemplate (StringPromptTemplate)

### Usage

1. Create a `PromptTemplate` object

`PromptTemplate` is used to format a single string, typically for simple inputs. It concatenates retrieval results with user questions through `{context}` and `{query}` placeholders.

```python
from langchain_core.prompts import PromptTemplate

prompt = PromptTemplate.from_template(
    "Please answer the user's question based on the following retrieved context.\n"
    "Context: {context}\n"
    "Question: {query}\n"
    "Answer:"
)
```

2. Construct a `LangchainKnowledge` object based on this prompt object

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

### Reference

- [PromptTemplate](https://python.langchain.com/docs/concepts/prompt_templates/)


## ChatPromptTemplate

### Usage

1. Create a `ChatPromptTemplate` object

`ChatPromptTemplate` is used to format a list of messages, composed of a list of templates. It supports `system` / `user` role separation, allowing the model to clearly distinguish between system instructions and user input.

```python
from langchain_core.prompts import ChatPromptTemplate

prompt = ChatPromptTemplate([
    ("system", "You are a knowledge Q&A assistant. Please answer questions based on the provided context. If the context does not contain relevant information, please state so honestly."),
    ("user", "Context: {context}\n\nQuestion: {query}"),
])
```

2. Construct a `LangchainKnowledge` object based on this prompt object

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

### Reference

- [ChatPromptTemplate](https://python.langchain.com/docs/concepts/prompt_templates/)

## MessagesPlaceholder

### Usage

1. Create a prompt object containing `MessagesPlaceholder`

`MessagesPlaceholder` is used to insert a list of messages at a specific position, suitable for multi-turn conversation scenarios that require preserving conversation history.

```python
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

prompt = ChatPromptTemplate([
    ("system", "You are a knowledge Q&A assistant. Please answer questions based on the provided context and conversation history."),
    MessagesPlaceholder("chat_history"),
    ("user", "Context: {context}\n\nQuestion: {query}"),
])
```

`MessagesPlaceholder("chat_history")` will be replaced with the actual conversation history message list at invocation time:

```python
from langchain_core.messages import HumanMessage, AIMessage

prompt.invoke({
    "chat_history": [
        HumanMessage(content="What is artificial intelligence?"),
        AIMessage(content="Artificial intelligence is a branch of computer science..."),
    ],
    "context": "Deep learning is a subfield of machine learning...",
    "query": "What is the relationship between deep learning and machine learning?",
})
```

2. Construct a `LangchainKnowledge` object based on this prompt object

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

### Reference

- [MessagesPlaceholder](https://python.langchain.com/docs/concepts/prompt_templates/)

## Complete Example

For a complete example, see: [examples/knowledge_with_prompt_template/run_agent.py](../../../examples/knowledge_with_prompt_template/run_agent.py)
