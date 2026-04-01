# Langchain Knowledge 使用文档

## 概述

Langchain Knowledge 是 tRPC-Agent 框架中的知识管理系统，支持Langchain生态，为 Agent 提供检索增强生成（Retrieval-Augmented Generation, RAG）能力。用户仅需声明RAG组件类型: 向量嵌入模型，向量数据库等，即可实现基本RAG流程。

### 使用模式

Knowledge 系统的使用遵循以下模式：

1. **创建 Knowledge**：选择并配置 RAG 组件（向量存储、Embedder、文档加载器等）
2. **加载文档**：调用 `create_vectorstore_from_document` 从文档源构建向量数据库
4. **集成到 Agent**：将搜索工具添加到 Agent 的 `tools` 列表中
5. **Agent 调用**：Agent 在对话过程中自动调用知识搜索工具获取上下文

这种模式提供了：

- **语义检索**：支持相似度搜索（similarity）、带阈值的相似度搜索（similarity_score_threshold）和最大边际相关性（MMR）
- **LangChain 生态兼容**：无缝对接 LangChain 的 VectorStore、Embeddings、Retriever 等组件
- **重排序能力**：支持向量数据库检索后通过 Retriever 进行重排序
- **元数据过滤**：通过 `KnowledgeFilterExpr` 支持静态过滤和 Agent 智能动态过滤
- **可扩展架构**：基于 `KnowledgeBase` 抽象基类，支持自定义知识后端

### Agent 集成方式

Knowledge 系统与 Agent 的集成支持两种方式：

- **搜索工具集成（推荐）**：使用 `LangchainKnowledgeSearchTool` 创建搜索工具，直接传给 Agent 的 `tools` 参数
- **智能过滤搜索**：使用 `AgenticLangchainKnowledgeSearchTool` 创建支持动态过滤的搜索工具，Agent 可根据用户查询自动构建过滤条件

## 安装方式

- **版本兼容性**: 本模块支持 LangChain 0.3.x 和 1.x.x 版本，模块中采用try/except方式进行兼容处理。更多信息可以参考[Langchain 版本兼容性说明](#langchain-版本兼容性)

### 依赖要求

在 `pyproject.toml` 中配置：

```toml
dependencies = [
    "langchain>=0.3.0",
    "langchain-core>=0.3.0",
    "langchain-text-splitters>=0.3.0",
]
```

## 适用场景

Langchain Knowledge 支持四种使用模式：
- 完整Langchain链：支持从Langchain框架无缝迁移，直接使用完整链运行对接trpc-agent框架

- 向量数据库检索方式：支持根据文档构建向量数据库，使用相关性等方式检索与Query相关的文档

- 检索器检索方式：支持根据文档构建检索器，使用相关性等方式检索与Query相关的文档

- 向量数据库检索以及检索器重排序：支持根据文档构建向量数据库，根据Query检索相关文档后利用检索器进行重排序

## 创建Langchain Knowledge

### 初始化组件说明
- `chain`: 用户自定义完整的Langchain链实现RAG流程。若非空，则忽略其他组件，执行当前完整链；若为空，则忽略当前chain组件

- `prompt_template`: Prompt模版，实现基于模版嵌入Query。若为空，则传递原始Query

- `document_loader`: 文档加载器，实现异步加载文档。若非空，需指定文件路径；若为空，则需在向量数据库或检索器初始化时指定文档

- `document_transformer`: 文档转换器，实现异步文档分片；若为空，则代表不分片

- `embedder`: 嵌入模型，实现将文档转换为对应向量；如向量数据库本身提供embedding能力，则可为空。

- `vectorstore`: 向量数据库，实现根据文档构建数据库并支持检索文档。向量数据库和检索器不能同时为空，否则无法实现检索。

- `retriever`: 检索器，实现根据文档构建数据库并支持检索文档，也可以支持重排序，例如：BM25Retrievr等。向量数据库和检索器不能同时为空，否则无法实现检索。当同时使用`vectorstore`和`retriever`时，`retriever`用于对`vectorstore`的检索结果进行重排序，这时要求`retriever`具备`from_documents`接口。

组件使用说明见[tRPC-Python-Agent 框架中使用 LangChain RAG 组件](./langchain_components_guide.md)

### 核心方法详解

`search`方法是Langchain Knowledge的核心方法， 该方法实现了：
1. 获取对话上下文，可注入到Query中实现强化Query
2. 根据声明的RAG组件类型， 检索得到相关文档
3. 将得到的相关文档转为trpc_agent 框架支持的数据类型

`create_vectorstore_from_document`方法提供了从文档创建向量数据库的能力，包括：
文档加载 - 文档分片（可选）- 向量化 - 存入向量数据库

### 检索方式说明

`SearchType` 定义了三种检索方式，可在创建搜索工具时指定：

| 检索方式 | 枚举值 | 说明 |
|----------|--------|------|
| `SIMILARITY` | `"similarity"` | 纯相似度检索，返回最相似的 K 个文档 |
| `SIMILARITY_SCORE_THRESHOLD` | `"similarity_score_threshold"` | 带相关性分数的相似度检索，结果包含分数 |
| `MAX_MARGINAL_RELEVANCE` | `"mmr"` | 最大边际相关性，在相关性和多样性之间取平衡 |

## 核心组件概述

### 模块结构

Knowledge 系统采用分层架构，核心接口定义在 `trpc_agent` 中，具体实现在 `trpc_agent_ecosystem` 中：

```
trpc_agent/knowledge/                    # 核心接口层
├── _knowledge.py                       # KnowledgeBase 抽象基类、SearchRequest/SearchResult 数据模型
└── _filter_expr.py                     # KnowledgeFilterExpr 统一过滤表达式

trpc_agent_ecosystem/knowledge/          # 实现层
├── langchain_knowledge.py              # LangchainKnowledge —— 基于 LangChain 生态的 RAG 实现
└── tools/
    └── langchain_knowledge_searchtool.py  # LangchainKnowledgeSearchTool / AgenticLangchainKnowledgeSearchTool
```

### KnowledgeBase 接口

`KnowledgeBase` 是所有知识后端的抽象基类，定义了统一的搜索接口：

```python
from trpc_agent_sdk.knowledge import KnowledgeBase, SearchRequest, SearchResult

class KnowledgeBase(ABC):
    async def search(self, ctx: AgentContext, req: SearchRequest) -> SearchResult:
        """执行语义搜索并返回最佳结果，这是 Agent 用于 RAG 的主要方法。"""

    def build_search_extra_params(self, filter_expr: KnowledgeFilterExpr | None) -> dict:
        """将统一过滤表达式转为后端特定参数。"""
```

框架提供了两种内置实现：
- **LangchainKnowledge**：对接 LangChain 生态，支持 LangChain 的 VectorStore、Retriever、Embeddings 等全套组件

### 搜索数据结构概述

#### SearchParams — 搜索参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `search_type` | `str` | `"similarity"` | 搜索方式：`similarity`、`similarity_score_threshold`、`mmr` |
| `top_p` | `float` | `0.8` | 概率累积阈值 |
| `rank_top_k` | `int` | `3` | 返回最相关的 K 个结果 |
| `rerank_threshold` | `float` | `0.3` | 重排序最小相关性分数阈值 |
| `default_score` | `float` | `0.0` | 默认相关性分数 |
| `generator_temperature` | `float` | `0.0` | 生成模型温度参数 |
| `generator_max_tokens` | `int` | `5000` | 生成模型最大输出 token 数 |
| `extra_params` | `dict` | `{}` | 后端特定的扩展参数 |

#### SearchRequest — 搜索请求

| 字段 | 类型 | 说明 |
|------|------|------|
| `query` | `Part` | 搜索查询内容 |
| `history` | `List[BaseMessage]` | 最近的对话消息，用作上下文 |
| `user_id` | `str` | 用户 ID，可用于个性化搜索 |
| `session_id` | `str` | 会话 ID，可用于会话特定上下文 |
| `params` | `SearchParams` | 搜索参数配置 |

#### SearchResult — 搜索结果

`SearchResult` 包含 `documents` 列表，每个 `SearchDocument` 包含：
- `document`：匹配的文档（LangChain `Document` 对象，含 `page_content` 和 `metadata`）
- `score`：相关性分数

## 过滤器

`KnowledgeFilterExpr` 提供了统一的过滤表达式模型，支持对搜索结果基于文档元数据进行精准过滤，可用于静态配置或由 Agent 动态生成。

### 支持的操作符

| 类别 | 操作符 | 说明 |
|------|--------|------|
| 比较操作符 | `eq`, `ne` | 等于、不等于 |
| 比较操作符 | `gt`, `gte`, `lt`, `lte` | 大于、大于等于、小于、小于等于 |
| 集合操作符 | `in`, `not in` | 在集合中、不在集合中 |
| 模糊操作符 | `like`, `not like` | 模糊匹配 |
| 范围操作符 | `between` | 区间范围（值为两元素列表） |
| 逻辑操作符 | `and`, `or` | 逻辑与、逻辑或（值为子条件列表，支持嵌套） |

### 过滤器示例

```python
from trpc_agent_sdk.knowledge import KnowledgeFilterExpr

# 简单条件：类别等于 "machine-learning"
simple_filter = KnowledgeFilterExpr.model_validate({
    "field": "metadata.category",
    "operator": "eq",
    "value": "machine-learning",
})

# 复合条件：状态为 active 且年份 >= 2024
compound_filter = KnowledgeFilterExpr.model_validate({
    "operator": "and",
    "value": [
        {"field": "metadata.status", "operator": "eq", "value": "active"},
        {"field": "metadata.year", "operator": "gte", "value": 2024},
    ],
})

# 嵌套条件：(类别为 AI 或 ML) 且状态为 published
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

## 搜索工具

框架提供了两种搜索工具，用于将知识库能力集成到 Agent 中。

### LangchainKnowledgeSearchTool

基础知识搜索工具，支持语义搜索和静态过滤，Agent 在对话中会自动调用该工具检索知识：

```python
from trpc_agent_sdk.server.knowledge.tools import LangchainKnowledgeSearchTool
from trpc_agent_sdk.server.knowledge.langchain_knowledge import SearchType

search_tool = LangchainKnowledgeSearchTool(
    rag=rag,                              # LangchainKnowledge 实例
    top_k=3,                              # 返回最相关的 K 个结果
    search_type=SearchType.SIMILARITY,    # 检索方式
    min_score=0.5,                        # 最低相关性分数过滤
)
```

### AgenticLangchainKnowledgeSearchTool

智能过滤搜索工具，在基础搜索能力之上增加了 Agent 动态过滤能力。Agent 可以根据用户查询自动构建 `KnowledgeFilterExpr` 过滤条件，实现基于元数据的精准搜索

动态过滤不需要你手动构造 `dynamic_filter`，只需将工具挂载到 Agent 上即可。LLM 会根据工具声明中的参数描述，在运行时自动决定是否生成过滤条件：

**第一步：创建工具并挂载到 Agent**

```python
from trpc_agent_sdk.agents.llm_agent import LlmAgent
from trpc_agent_sdk.knowledge import KnowledgeFilterExpr
from trpc_agent_sdk.server.knowledge.tools import AgenticLangchainKnowledgeSearchTool
from trpc_agent_sdk.server.knowledge.langchain_knowledge import SearchType

# 可选：静态过滤条件，始终生效
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
    knowledge_filter=static_filter,       # 可选，不传则完全由 LLM 动态决定过滤条件
)

agent = LlmAgent(
    name="knowledge_agent",
    model=model,
    instruction="你是一个知识库助手，请根据用户问题搜索相关文档并回答。",
    tools=[agentic_search_tool],
)
```

**第二步：运行时 LLM 自动生成动态过滤**

当用户提问时，LLM 会根据问题语义自动判断是否需要 `dynamic_filter`。例如：

| 用户提问 | LLM 生成的工具调用 |
|---|---|
| "介绍一下深度学习" | `{"query": "深度学习"}` — 无需动态过滤，仅静态过滤生效 |
| "帮我找2024年发表的论文" | `{"query": "论文", "dynamic_filter": {"field": "metadata.year", "operator": "eq", "value": 2024}}` — LLM 自动提取年份构建过滤 |
| "查找 active 状态的英文文档" | `{"query": "英文文档", "dynamic_filter": {"operator": "and", "value": [{"field": "metadata.status", "operator": "eq", "value": "active"}, {"field": "metadata.language", "operator": "eq", "value": "en"}]}}` — LLM 组合多个条件 |

**第三步：框架自动合并静态与动态过滤**

如果同时配置了 `knowledge_filter`（静态）且 LLM 传入了 `dynamic_filter`（动态），框架会自动通过 AND 逻辑合并。以上述第二个例子为例，最终生效的过滤条件等价于：

```json
{
    "operator": "and",
    "value": [
        {"field": "metadata.category", "operator": "eq", "value": "machine-learning"},
        {"field": "metadata.year", "operator": "eq", "value": 2024}
    ]
}
```

### 搜索工具配置选项

两种搜索工具都支持以下配置选项：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `rag` | `LangchainKnowledge` | 必选 | Knowledge 实例 |
| `top_k` | `int` | `3` | 返回最相关的 K 个结果 |
| `search_type` | `SearchType` | `SIMILARITY` | 检索方式 |
| `min_score` | `float` | `0.0` | 最低相关性分数，低于此分数的文档将被过滤 |
| `knowledge_filter` | `KnowledgeFilterExpr` | `None` | 静态元数据过滤条件 |
| `filters_name` | `list[str]` | `None` | 关联的 Filter 名称列表 |
| `filters` | `list[BaseFilter]` | `None` | 关联的 Filter 实例列表 |


## 与 Agent 集成

### 方式一：使用搜索工具（推荐）

使用 `LangchainKnowledgeSearchTool` 或 `AgenticLangchainKnowledgeSearchTool` 直接作为 Agent 工具，无需手动编写搜索函数：

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

#  Prompt 模板
INSTRUCTION = "You are a helpful assistant. Be conversational and remember our previous exchanges."

RAG_PROMPT_TEMPLATE = """Answer the question gently:
    Query: {query}
    """
rag_prompt = ChatPromptTemplate.from_template(RAG_PROMPT_TEMPLATE)

#  模型配置（从环境变量读取）
api_key = os.getenv('TRPC_AGENT_API_KEY', '')
base_url = os.getenv('TRPC_AGENT_BASE_URL', '')
model_name = os.getenv('TRPC_AGENT_MODEL_NAME', '')

model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=base_url)

#  构建 Knowledge
def build_knowledge():
    """构建 RAG Knowledge"""
    # Embedder
    embedder = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")
    # VectorStore
    vectorstore = InMemoryVectorStore(embedder)
    # Document Loader：将文本写入临时文件后加载
    text_content = ("人工智能（Artificial Intelligence，简称AI）是计算机科学的一个分支，"
                    "它企图了解智能的实质，并生产出一种新的能以人类智能相似的方式做出反应的智能机器。")
    tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="w", encoding="utf-8")
    tmp_file.write(text_content)
    tmp_file.flush()
    tmp_file.close()
    text_loader = TextLoader(tmp_file.name, encoding="utf-8")
    # Document Transformer：chunk_size设置为10是因为测试文本较短，实际使用时需要根据文本长度调整
    text_splitter = RecursiveCharacterTextSplitter(separators=["\n"], chunk_size=10, chunk_overlap=0)

    # 组装 LangchainKnowledge
    rag = LangchainKnowledge(
        prompt_template=rag_prompt,
        document_loader=text_loader,
        document_transformer=text_splitter,
        embedder=embedder,
        vectorstore=vectorstore,
    )
    return rag

rag = build_knowledge()

#  创建 LangchainKnowledgeSearchTool 并传给 Agent
search_tool = LangchainKnowledgeSearchTool(rag, top_k=1, search_type=SearchType.SIMILARITY)
# 或使用智能过滤搜索工具
# AgenticLangchainKnowledgeSearchTool(rag, top_k=5, min_score=0.5),
root_agent = LlmAgent(
    name="rag_agent",
    description="A helpful assistant for conversation with RAG knowledge",
    model=model,
    instruction=INSTRUCTION,
    tools=[search_tool],  # 直接使用 LangchainKnowledgeSearchTool，无需手动封装搜索函数
)
```

完整示例见[examples/knowledge_with_searchtool_rag_agent/run_agent.py](../../examples/knowledge_with_searchtool_rag_agent/run_agent.py)

### 方式二：自定义函数工具

将 `simple_search` 方法封装为 `FunctionTool`，适合需要自定义搜索逻辑或结果处理的场景：


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
    # 使用 TextLoader：将文本写入临时文件后加载
    text_content = ("人工智能（Artificial Intelligence，简称AI）是计算机科学的一个分支，"
                    "它企图了解智能的实质，并生产出一种新的能以人类智能相似的方式做出反应的智能机器。")
    tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="w", encoding="utf-8")
    tmp_file.write(text_content)
    tmp_file.flush()
    tmp_file.close()
    text_loader = TextLoader(tmp_file.name, encoding="utf-8")
    # 这里由于测试文本较短，所以chunk_size设置为10，实际使用时需要根据文本长度调整
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

# 构建 simple_search 方法
async def simple_search(query: str):
    """Search the knowledge base for relevant documents"""
    # metadata 可用于存储元数据
    metadata = {
        'assistant_name': 'test',  # Agent Name, 可用于上下文
        'runnable_config': {},  # Langchain中的Runnable配置
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
        tools=[FunctionTool(simple_search)], # simple_search 方法封装为FunctionTool
    )
    return agent


root_agent = create_agent()
```

完整示例见：[examples/knowledge_with_rag_agent/run_agent.py](../../examples/knowledge_with_rag_agent/run_agent.py)

## 自定义 Knowledge 后端

通过继承 `KnowledgeBase` 抽象基类，可以实现自定义的知识后端。只需实现 `search` 方法即可与框架的搜索工具无缝集成：

```python
from trpc_agent_sdk.knowledge import KnowledgeBase, SearchRequest, SearchResult
from trpc_agent_sdk.context import AgentContext

class MyKnowledge(KnowledgeBase):
    async def search(self, ctx: AgentContext, req: SearchRequest) -> SearchResult:
        # 自定义检索逻辑
        ...
```

框架内置的 `TragKnowledge` 就是一个自定义后端的例子，它继承 `LangchainKnowledge` 并对接 TRAG 向量检索服务，替换了向量数据库检索逻辑，同时复用了 Prompt 构建和 Retriever 重排序等能力。

## LangChain 版本兼容性

### 主要变化

#### 1. Text Splitters 导入路径变化

**LangChain 0.3.x:**

```python
from langchain.text_splitter import RecursiveCharacterTextSplitter
```

**LangChain 1.x.x:**

```python
from langchain_text_splitters import RecursiveCharacterTextSplitter
```

**兼容性写法:**

```python
try:
    # langchain v1.x.x版本导入方式
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    # langchain v0.3.x版本导入方式
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
# Chain 已被弃用，推荐使用 Runnable
```

**兼容性处理:**

在 `langchain_knowledge.py` 中已经处理了这个兼容性：

```python
try:
    from langchain_core.runnables import Runnable as Chain
except ImportError:
    from langchain.chains.base import Chain
```

### 示例代码兼容性

所有示例代码都已更新为兼容两个版本。例如：

```python
# Compatible imports for LangChain 0.3.x and 1.x.x
try:
    # langchain v1.x.x版本导入方式
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    # langchain v0.3.x版本导入方式
    from langchain.text_splitter import RecursiveCharacterTextSplitter

from langchain_community.document_loaders import TextLoader
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_huggingface import HuggingFaceEmbeddings
```

### 升级建议

#### 从 LangChain 0.3.x 升级到 1.x.x

1. **更新依赖:**

```bash
pip install --upgrade langchain langchain-core langchain-text-splitters
```

2. **代码无需修改:**
   - 所有示例代码已经兼容两个版本
   - `LangchainKnowledge` 类自动处理版本差异

3. **验证升级:**

```bash
python -c "import langchain; print(langchain.__version__)"
```

#### 保持在 LangChain 0.3.x

如果需要保持在 0.3.x 版本：

```bash
pip install "langchain>=0.3.0,<1.0.0" "langchain-core>=0.3.0,<1.0.0"
```

### 最佳实践

1. **使用 langchain-core 的稳定 API:**
   - `langchain_core.prompts`
   - `langchain_core.documents`
   - `langchain_core.vectorstores`
   - `langchain_core.retrievers`

2. **Text Splitters 使用兼容导入:**

```python
try:
    # langchain v1.x.x版本导入方式
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    # langchain v0.3.x版本导入方式
    from langchain.text_splitter import RecursiveCharacterTextSplitter
```

3. **避免直接使用 Chain:**
   - 在新代码中优先使用 `Runnable`
   - `LangchainKnowledge` 已经处理了这个兼容性

### 常见问题

#### Q: 如何检查当前使用的 LangChain 版本？

```python
import langchain
print(f"LangChain version: {langchain.__version__}")
```

#### Q: 升级后示例代码无法运行？

确保安装了所有必要的子包：

```bash
pip install langchain-core langchain-text-splitters langchain-community
```

#### Q: 是否需要修改现有代码？

不需要。所有示例代码和 `LangchainKnowledge` 类都已经处理了版本兼容性。

### 参考资源

- [LangChain 官方迁移指南](https://docs.langchain.com/oss/python/migrate/langchain-v1)
- [LangChain Core 文档](https://python.langchain.com/docs/langchain_core/)
- [LangChain Text Splitters 文档](https://python.langchain.com/docs/modules/data_connection/document_transformers/)

## 更多内容

- [Prompt 模板](./knowledge_prompt_template.md) - RAG 检索的 Prompt 模板配置
- [文档加载器](./knowledge_document_loader.md) - 文件、目录、URL 等知识来源加载配置
- [Text Splitter](./knowledge_text_splitter.md) - 文本分割器配置
- [向量存储](./knowledge_vectorstore.md) - 配置各种向量数据库后端
- [Embedder](./knowledge_embedder.md) - 文本向量化模型配置
- [Retriever](./knowledge_retrievers.md) - 检索器配置与重排序
