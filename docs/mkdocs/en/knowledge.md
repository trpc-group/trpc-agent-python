# Langchain Knowledge Documentation

## Overview

Langchain Knowledge is the knowledge management system within the tRPC-Agent framework. It supports the Langchain ecosystem and provides Retrieval-Augmented Generation (RAG) capabilities for Agents. Users only need to declare the RAG component types — such as vector embedding models and vector databases — to implement a basic RAG pipeline.

### Usage Pattern

The Knowledge system follows this usage pattern:

1. **Create Knowledge**: Select and configure RAG components (vector store, embedder, document loader, etc.)
2. **Load Documents**: Call `create_vectorstore_from_document` to build a vector database from document sources
4. **Integrate with Agent**: Add the search tool to the Agent's `tools` list
5. **Agent Invocation**: The Agent automatically invokes the knowledge search tool to retrieve context during conversations

This pattern provides:

- **Semantic Retrieval**: Supports similarity search, similarity search with score threshold (similarity_score_threshold), and Maximum Marginal Relevance (MMR)
- **LangChain Ecosystem Compatibility**: Seamless integration with LangChain's VectorStore, Embeddings, Retriever, and other components
- **Reranking Capability**: Supports reranking retrieved results from the vector database via a Retriever
- **Metadata Filtering**: Supports static filtering and Agent-driven dynamic filtering through `KnowledgeFilterExpr`
- **Extensible Architecture**: Based on the `KnowledgeBase` abstract base class, supports custom knowledge backends

### Agent Integration Methods

The Knowledge system supports two methods of integration with Agents:

- **Search Tool Integration (Recommended)**: Use `LangchainKnowledgeSearchTool` to create a search tool and pass it directly to the Agent's `tools` parameter
- **Agentic Filtered Search**: Use `AgenticLangchainKnowledgeSearchTool` to create a search tool with dynamic filtering capability, where the Agent can automatically construct filter conditions based on user queries

## Installation

- **Version Compatibility**: This module supports LangChain 0.3.x and 1.x.x versions, using try/except for compatibility handling within the module. For more information, refer to [LangChain Version Compatibility](#langchain-version-compatibility)

### Dependency Requirements

Configure in `pyproject.toml`:

```toml
dependencies = [
    "langchain>=0.3.0",
    "langchain-core>=0.3.0",
    "langchain-text-splitters>=0.3.0",
]
```

## Use Cases

Langchain Knowledge supports four usage modes:
- Full Langchain chain: Supports seamless migration from the Langchain framework, directly using a full chain to integrate with the trpc-agent framework

- Vector store retrieval: Supports building a vector database from documents and retrieving query-relevant documents using relevance-based methods

- Retriever-based retrieval: Supports building a retriever from documents and retrieving query-relevant documents using relevance-based methods

- Vector store retrieval with retriever reranking: Supports building a vector database from documents, retrieving relevant documents based on the query, and then reranking results using a retriever

## Creating Langchain Knowledge

### Component Initialization

- `chain`: A user-defined full Langchain chain implementing the RAG pipeline. If non-empty, other components are ignored and the full chain is executed; if empty, the chain component is ignored

- `prompt_template`: A prompt template for embedding the query into a template. If empty, the raw query is passed through

- `document_loader`: A document loader for asynchronous document loading. If non-empty, a file path must be specified; if empty, documents must be specified during vector store or retriever initialization

- `document_transformer`: A document transformer for asynchronous document chunking; if empty, no chunking is performed

- `embedder`: An embedding model for converting documents to corresponding vectors; can be empty if the vector store itself provides embedding capability.

- `vectorstore`: A vector store for building a database from documents and supporting document retrieval. The vector store and retriever cannot both be empty, otherwise retrieval is not possible.

- `retriever`: A retriever for building a database from documents and supporting document retrieval; it can also support reranking, e.g., BM25Retriever. The vector store and retriever cannot both be empty, otherwise retrieval is not possible. When both `vectorstore` and `retriever` are used simultaneously, the `retriever` is used to rerank the retrieval results from the `vectorstore`, which requires the `retriever` to have a `from_documents` interface.

For detailed usage of each component, see: [Document Loader](./knowledge_document_loader.md), [Text Splitter](./knowledge_text_splitter.md), [Embedder](./knowledge_embedder.md), [VectorStore](./knowledge_vectorstore.md), [Retrievers](./knowledge_retrievers.md), [Prompt Template](./knowledge_prompt_template.md), [Custom Components](./knowledge_custom_components.md)

### Core Method Details

The `search` method is the core method of Langchain Knowledge. It implements:
1. Retrieving conversation context, which can be injected into the query to enhance it
2. Retrieving relevant documents based on the declared RAG component types
3. Converting the retrieved documents into data types supported by the trpc_agent framework

The `create_vectorstore_from_document` method provides the capability to create a vector database from documents, including:
Document loading - Document chunking (optional) - Vectorization - Storage into the vector database

### Search Type Description

`SearchType` defines three search methods that can be specified when creating a search tool:

| Search Type | Enum Value | Description |
|-------------|------------|-------------|
| `SIMILARITY` | `"similarity"` | Pure similarity search, returns the top K most similar documents |
| `SIMILARITY_SCORE_THRESHOLD` | `"similarity_score_threshold"` | Similarity search with relevance scores, results include scores |
| `MAX_MARGINAL_RELEVANCE` | `"mmr"` | Maximum Marginal Relevance, balances between relevance and diversity |

## Core Component Overview

### Module Structure

The Knowledge system uses a layered architecture, with core interfaces defined in `trpc_agent` and concrete implementations in `trpc_agent_ecosystem`:

```
trpc_agent_sdk/knowledge/                    # Core interface layer
├── _knowledge.py                       # KnowledgeBase abstract base class, SearchRequest/SearchResult data models
└── _filter_expr.py                     # KnowledgeFilterExpr unified filter expression

trpc_agent_sdk/server/knowledge/          # Implementation layer
├── langchain_knowledge.py              # LangchainKnowledge — RAG implementation based on LangChain ecosystem
└── tools/
    └── langchain_knowledge_searchtool.py  # LangchainKnowledgeSearchTool / AgenticLangchainKnowledgeSearchTool
```

### KnowledgeBase Interface

`KnowledgeBase` is the abstract base class for all knowledge backends, defining a unified search interface:

```python
from trpc_agent_sdk.knowledge import KnowledgeBase, SearchRequest, SearchResult

class KnowledgeBase(ABC):
    async def search(self, ctx: AgentContext, req: SearchRequest) -> SearchResult:
        """Perform semantic search and return the best results. This is the primary method for Agent RAG."""

    def build_search_extra_params(self, filter_expr: KnowledgeFilterExpr | None) -> dict:
        """Convert the unified filter expression to backend-specific parameters."""
```

The framework provides two built-in implementations:
- **LangchainKnowledge**: Integrates with the LangChain ecosystem, supporting LangChain's full suite of components including VectorStore, Retriever, Embeddings, etc.

### Search Data Structures Overview

#### SearchParams — Search Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `search_type` | `str` | `"similarity"` | Search method: `similarity`, `similarity_score_threshold`, `mmr` |
| `top_p` | `float` | `0.8` | Cumulative probability threshold |
| `rank_top_k` | `int` | `3` | Return the top K most relevant results |
| `rerank_threshold` | `float` | `0.3` | Minimum relevance score threshold for reranking |
| `default_score` | `float` | `0.0` | Default relevance score |
| `generator_temperature` | `float` | `0.0` | Generation model temperature parameter |
| `generator_max_tokens` | `int` | `5000` | Maximum output tokens for the generation model |
| `extra_params` | `dict` | `{}` | Backend-specific extension parameters |

#### SearchRequest — Search Request

| Field | Type | Description |
|-------|------|-------------|
| `query` | `Part` | Search query content |
| `history` | `List[BaseMessage]` | Recent conversation messages, used as context |
| `user_id` | `str` | User ID, can be used for personalized search |
| `session_id` | `str` | Session ID, can be used for session-specific context |
| `params` | `SearchParams` | Search parameter configuration |

#### SearchResult — Search Result

`SearchResult` contains a `documents` list, where each `SearchDocument` includes:
- `document`: The matched document (LangChain `Document` object, containing `page_content` and `metadata`)
- `score`: Relevance score

## Filters

`KnowledgeFilterExpr` provides a unified filter expression model that supports precise filtering of search results based on document metadata. It can be used for static configuration or dynamically generated by the Agent.

### Supported Operators

| Category | Operator | Description |
|----------|----------|-------------|
| Comparison Operators | `eq`, `ne` | Equal to, Not equal to |
| Comparison Operators | `gt`, `gte`, `lt`, `lte` | Greater than, Greater than or equal to, Less than, Less than or equal to |
| Set Operators | `in`, `not in` | In set, Not in set |
| Fuzzy Operators | `like`, `not like` | Fuzzy match |
| Range Operators | `between` | Range interval (value is a two-element list) |
| Logical Operators | `and`, `or` | Logical AND, Logical OR (value is a list of sub-conditions, supports nesting) |

### Filter Examples

```python
from trpc_agent_sdk.knowledge import KnowledgeFilterExpr

# Simple condition: category equals "machine-learning"
simple_filter = KnowledgeFilterExpr.model_validate({
    "field": "metadata.category",
    "operator": "eq",
    "value": "machine-learning",
})

# Compound condition: status is active AND year >= 2024
compound_filter = KnowledgeFilterExpr.model_validate({
    "operator": "and",
    "value": [
        {"field": "metadata.status", "operator": "eq", "value": "active"},
        {"field": "metadata.year", "operator": "gte", "value": 2024},
    ],
})

# Nested condition: (category is AI OR ML) AND status is published
nested_filter = KnowledgeFilterExpr.model_validate({
    "operator": "and",
    "value": [
        {
            "operator": "or",
            "value": [
                {"field": "metadata.category", "operator": "eq", "value": "AI"},
                {"field": "metadata.category", "operator": "eq", "value": "ML"},
            ],
        },
        {"field": "metadata.status", "operator": "eq", "value": "published"},
    ],
})
```

## Search Tools

The framework provides two search tools for integrating knowledge base capabilities into Agents.

### LangchainKnowledgeSearchTool

A basic knowledge search tool that supports semantic search and static filtering. The Agent automatically invokes this tool to retrieve knowledge during conversations:

```python
from trpc_agent_sdk.server.knowledge.tools import LangchainKnowledgeSearchTool
from trpc_agent_sdk.server.knowledge.langchain_knowledge import SearchType

search_tool = LangchainKnowledgeSearchTool(
    rag=rag,                              # LangchainKnowledge instance
    top_k=3,                              # Return top K most relevant results
    search_type=SearchType.SIMILARITY,    # Search method
    min_score=0.5,                        # Minimum relevance score filter
)
```

### AgenticLangchainKnowledgeSearchTool

An agentic filtered search tool that adds dynamic filtering capabilities on top of the basic search. The Agent can automatically construct `KnowledgeFilterExpr` filter conditions based on user queries for precise metadata-based search.

Dynamic filtering does not require you to manually construct `dynamic_filter` — simply mount the tool on the Agent. The LLM will automatically decide whether to generate filter conditions at runtime based on the parameter descriptions in the tool declaration:

**Step 1: Create the tool and mount it on the Agent**

```python
from trpc_agent_sdk.agents.llm_agent import LlmAgent
from trpc_agent_sdk.knowledge import KnowledgeFilterExpr
from trpc_agent_sdk.server.knowledge.tools import AgenticLangchainKnowledgeSearchTool
from trpc_agent_sdk.server.knowledge.langchain_knowledge import SearchType

# Optional: static filter condition, always in effect
static_filter = KnowledgeFilterExpr.model_validate({
    "field": "metadata.category",
    "operator": "eq",
    "value": "machine-learning",
})

agentic_search_tool = AgenticLangchainKnowledgeSearchTool(
    rag=rag,
    top_k=5,
    search_type=SearchType.SIMILARITY,
    min_score=0.5,
    knowledge_filter=static_filter,       # Optional; if omitted, filter conditions are entirely determined by the LLM dynamically
)

agent = LlmAgent(
    name="knowledge_agent",
    model=model,
    instruction="You are a knowledge base assistant. Search relevant documents based on user questions and provide answers.",
    tools=[agentic_search_tool],
)
```

**Step 2: LLM automatically generates dynamic filters at runtime**

When a user asks a question, the LLM automatically determines whether `dynamic_filter` is needed based on the query semantics. For example:

| User Query | LLM-Generated Tool Call |
|---|---|
| "Introduce deep learning" | `{"query": "deep learning"}` — No dynamic filter needed; only static filter applies |
| "Find papers published in 2024" | `{"query": "papers", "dynamic_filter": {"field": "metadata.year", "operator": "eq", "value": 2024}}` — LLM automatically extracts the year to build a filter |
| "Find active English documents" | `{"query": "English documents", "dynamic_filter": {"operator": "and", "value": [{"field": "metadata.status", "operator": "eq", "value": "active"}, {"field": "metadata.language", "operator": "eq", "value": "en"}]}}` — LLM combines multiple conditions |

**Step 3: Framework automatically merges static and dynamic filters**

If both `knowledge_filter` (static) and `dynamic_filter` (dynamic, passed by the LLM) are configured, the framework automatically merges them using AND logic. Using the second example above, the effective filter condition is equivalent to:

```json
{
    "operator": "and",
    "value": [
        {"field": "metadata.category", "operator": "eq", "value": "machine-learning"},
        {"field": "metadata.year", "operator": "eq", "value": 2024}
    ]
}
```

### Search Tool Configuration Options

Both search tools support the following configuration options:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `rag` | `LangchainKnowledge` | Required | Knowledge instance |
| `top_k` | `int` | `3` | Return the top K most relevant results |
| `search_type` | `SearchType` | `SIMILARITY` | Search method |
| `min_score` | `float` | `0.0` | Minimum relevance score; documents below this score are filtered out |
| `knowledge_filter` | `KnowledgeFilterExpr` | `None` | Static metadata filter condition |
| `filters_name` | `list[str]` | `None` | List of associated Filter names |
| `filters` | `list[BaseFilter]` | `None` | List of associated Filter instances |


## Integration with Agent

### Method 1: Using Search Tools (Recommended)

Use `LangchainKnowledgeSearchTool` or `AgenticLangchainKnowledgeSearchTool` directly as Agent tools, without the need to manually write search functions:

```python
import os
import tempfile

from langchain_community.document_loaders import TextLoader
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_huggingface import HuggingFaceEmbeddings
try:
    from langchain.text_splitter import RecursiveCharacterTextSplitter
except ModuleNotFoundError:
    from langchain_text_splitters import RecursiveCharacterTextSplitter

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.server.knowledge.langchain_knowledge import (
    LangchainKnowledge,
    SearchType,
)
from trpc_agent_sdk.server.knowledge.tools import LangchainKnowledgeSearchTool

#  Prompt Template 
INSTRUCTION = "You are a helpful assistant. Be conversational and remember our previous exchanges."

RAG_PROMPT_TEMPLATE = """Answer the question gently:
    Query: {query}
    """
rag_prompt = ChatPromptTemplate.from_template(RAG_PROMPT_TEMPLATE)

#  Model Configuration (read from environment variables) 
api_key = os.getenv('TRPC_AGENT_API_KEY', '')
base_url = os.getenv('TRPC_AGENT_BASE_URL', '')
model_name = os.getenv('TRPC_AGENT_MODEL_NAME', '')

model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=base_url)

#  Build Knowledge
def build_knowledge():
    """Build RAG Knowledge"""
    # Embedder
    embedder = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")
    # VectorStore
    vectorstore = InMemoryVectorStore(embedder)
    # Document Loader: write text to a temporary file and then load it
    text_content = ("人工智能（Artificial Intelligence，简称AI）是计算机科学的一个分支，"
                    "它企图了解智能的实质，并生产出一种新的能以人类智能相似的方式做出反应的智能机器。")
    tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="w", encoding="utf-8")
    tmp_file.write(text_content)
    tmp_file.flush()
    tmp_file.close()
    text_loader = TextLoader(tmp_file.name, encoding="utf-8")
    # Document Transformer: chunk_size is set to 10 because the test text is short; adjust according to actual text length in practice
    text_splitter = RecursiveCharacterTextSplitter(separators=["\n"], chunk_size=10, chunk_overlap=0)

    # Assemble LangchainKnowledge
    rag = LangchainKnowledge(
        prompt_template=rag_prompt,
        document_loader=text_loader,
        document_transformer=text_splitter,
        embedder=embedder,
        vectorstore=vectorstore,
    )
    return rag

rag = build_knowledge()

#  Create LangchainKnowledgeSearchTool and pass it to the Agent
search_tool = LangchainKnowledgeSearchTool(rag, top_k=1, search_type=SearchType.SIMILARITY)
# Or use the agentic filtered search tool
# AgenticLangchainKnowledgeSearchTool(rag, top_k=5, min_score=0.5),
root_agent = LlmAgent(
    name="rag_agent",
    description="A helpful assistant for conversation with RAG knowledge",
    model=model,
    instruction=INSTRUCTION,
    tools=[search_tool],  # Use LangchainKnowledgeSearchTool directly, no need to manually wrap search functions
)
```

For the complete example, see [examples/knowledge_with_searchtool_rag_agent/run_agent.py](../../../examples/knowledge_with_searchtool_rag_agent/run_agent.py)

### Method 2: Custom Function Tool

Wrap the `simple_search` method as a `FunctionTool`, suitable for scenarios that require custom search logic or result processing:


```python
import tempfile

from langchain_community.document_loaders import TextLoader
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_huggingface import HuggingFaceEmbeddings
try:
    from langchain.text_splitter import RecursiveCharacterTextSplitter
except ModuleNotFoundError:
    from langchain_text_splitters import RecursiveCharacterTextSplitter

from trpc_agent_sdk.context import new_agent_context
from trpc_agent_sdk.knowledge import SearchRequest, SearchResult
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.server.knowledge.langchain_knowledge import LangchainKnowledge

from .prompts import rag_prompt


def build_knowledge():
    """Build the RAG knowledge chain"""
    embedder = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")
    vectorstore = InMemoryVectorStore(embedder)
    # Use TextLoader: write text to a temporary file and then load it
    text_content = ("人工智能（Artificial Intelligence，简称AI）是计算机科学的一个分支，"
                    "它企图了解智能的实质，并生产出一种新的能以人类智能相似的方式做出反应的智能机器。")
    tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="w", encoding="utf-8")
    tmp_file.write(text_content)
    tmp_file.flush()
    tmp_file.close()
    text_loader = TextLoader(tmp_file.name, encoding="utf-8")
    # chunk_size is set to 10 because the test text is short; adjust according to actual text length in practice
    text_splitter = RecursiveCharacterTextSplitter(separators=["\n"], chunk_size=10, chunk_overlap=0)

    rag = LangchainKnowledge(
        prompt_template=rag_prompt,
        document_loader=text_loader,
        document_transformer=text_splitter,
        embedder=embedder,
        vectorstore=vectorstore,
    )
    return rag


rag = build_knowledge()

# Build the simple_search method
async def simple_search(query: str):
    """Search the knowledge base for relevant documents"""
    # metadata can be used to store metadata
    metadata = {
        'assistant_name': 'test',  # Agent Name, can be used for context
        'runnable_config': {},  # Runnable configuration in Langchain
    }
    ctx = new_agent_context(timeout=3000, metadata=metadata)
    sr: SearchRequest = SearchRequest()
    sr.query = Part.from_text(text=query)
    search_result: SearchResult = await rag.search(ctx, sr)
    if len(search_result.documents) == 0:
        return {"status": "failed", "report": "No documents found"}

    best_doc = search_result.documents[0].document
    return {"status": "success", "report": f"content: {best_doc.page_content}"}
```

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

from .prompts import INSTRUCTION
from .tools import simple_search
from .config import get_model_config


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent() -> LlmAgent:
    """ Create an agent"""
    agent = LlmAgent(
        name="rag_agent",
        description="A helpful assistant for conversation, ",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[FunctionTool(simple_search)], # Wrap simple_search as a FunctionTool
    )
    return agent


root_agent = create_agent()
```

For the complete example, see: [examples/knowledge_with_rag_agent/run_agent.py](../../../examples/knowledge_with_rag_agent/run_agent.py)

## Custom Knowledge Backend

By inheriting the `KnowledgeBase` abstract base class, you can implement a custom knowledge backend. Simply implement the `search` method to seamlessly integrate with the framework's search tools:

```python
from trpc_agent_sdk.knowledge import KnowledgeBase, SearchRequest, SearchResult
from trpc_agent_sdk.context import AgentContext

class MyKnowledge(KnowledgeBase):
    async def search(self, ctx: AgentContext, req: SearchRequest) -> SearchResult:
        # Custom retrieval logic
        ...
```

The built-in `TragKnowledge` is an example of a custom backend. It inherits from `LangchainKnowledge` and integrates with the TRAG vector retrieval service, replacing the vector database retrieval logic while reusing capabilities such as prompt construction and retriever reranking.

## LangChain Version Compatibility

### Major Changes

#### 1. Text Splitters Import Path Changes

**LangChain 0.3.x:**

```python
from langchain.text_splitter import RecursiveCharacterTextSplitter
```

**LangChain 1.x.x:**

```python
from langchain_text_splitters import RecursiveCharacterTextSplitter
```

**Compatible approach:**

```python
try:
    # Import for langchain v1.x.x
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    # Import for langchain v0.3.x
    from langchain.text_splitter import RecursiveCharacterTextSplitter
```

#### 2. Chain vs Runnable

**LangChain 0.3.x:**

```python
from langchain.chains.base import Chain
```

**LangChain 1.x.x:**

```python
from langchain_core.runnables import Runnable
# Chain is deprecated; Runnable is recommended
```

**Compatibility handling:**

This compatibility is already handled in `langchain_knowledge.py`:

```python
try:
    from langchain_core.runnables import Runnable as Chain
except ImportError:
    from langchain.chains.base import Chain
```

### Example Code Compatibility

All example code has been updated to be compatible with both versions. For example:

```python
# Compatible imports for LangChain 0.3.x and 1.x.x
try:
    # Import for langchain v1.x.x
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    # Import for langchain v0.3.x
    from langchain.text_splitter import RecursiveCharacterTextSplitter

from langchain_community.document_loaders import TextLoader
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_huggingface import HuggingFaceEmbeddings
```

### Upgrade Guide

#### Upgrading from LangChain 0.3.x to 1.x.x

1. **Update dependencies:**

```bash
pip install --upgrade langchain langchain-core langchain-text-splitters
```

2. **No code changes required:**
   - All example code is already compatible with both versions
   - The `LangchainKnowledge` class automatically handles version differences

3. **Verify the upgrade:**

```bash
python -c "import langchain; print(langchain.__version__)"
```

#### Staying on LangChain 0.3.x

If you need to stay on version 0.3.x:

```bash
pip install "langchain>=0.3.0,<1.0.0" "langchain-core>=0.3.0,<1.0.0"
```

### Best Practices

1. **Use stable APIs from langchain-core:**
   - `langchain_core.prompts`
   - `langchain_core.documents`
   - `langchain_core.vectorstores`
   - `langchain_core.retrievers`

2. **Use compatible imports for Text Splitters:**

```python
try:
    # Import for langchain v1.x.x
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    # Import for langchain v0.3.x
    from langchain.text_splitter import RecursiveCharacterTextSplitter
```

3. **Avoid using Chain directly:**
   - Prefer `Runnable` in new code
   - `LangchainKnowledge` already handles this compatibility

### FAQ

#### Q: How to check the current LangChain version?

```python
import langchain
print(f"LangChain version: {langchain.__version__}")
```

#### Q: Example code doesn't run after upgrading?

Make sure all necessary sub-packages are installed:

```bash
pip install langchain-core langchain-text-splitters langchain-community
```

#### Q: Do I need to modify existing code?

No. All example code and the `LangchainKnowledge` class already handle version compatibility.

### References

- [LangChain Official Migration Guide](https://docs.langchain.com/oss/python/migrate/langchain-v1)
- [LangChain Core Documentation](https://python.langchain.com/docs/langchain_core/)
- [LangChain Text Splitters Documentation](https://python.langchain.com/docs/modules/data_connection/document_transformers/)

## More

- [Prompt Template](./knowledge_prompt_template.md) - RAG retrieval prompt template configuration
- [Document Loader](./knowledge_document_loader.md) - Configuration for loading knowledge sources from files, directories, URLs, etc.
- [Text Splitter](./knowledge_text_splitter.md) - Text splitter configuration
- [Vector Store](./knowledge_vectorstore.md) - Configure various vector database backends
- [Embedder](./knowledge_embedder.md) - Text vectorization model configuration
- [Retriever](./knowledge_retrievers.md) - Retriever configuration and reranking
