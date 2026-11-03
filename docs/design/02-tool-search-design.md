# trpc-agent-python Tool Search 设计

> **结论摘要**：Tool Search 用 `ToolSearchToolSet` 把大规模工具目录拆成“常驻工具 + 搜索入口 + 按需加载工具”。它通过 Python 框架已有的 `ToolSet.get_tools()` 和 `BaseTool.process_request()` 生命周期接入每一轮模型请求，不引入独立 Plugin 系统。工具目录支持静态 Toolbox、动态 MCP ToolSet、关键词检索和可选语义检索；发现结果写入 Session State，并按 ToolSet、Agent/Branch 和工具名隔离。Native 模式在下一轮直接暴露已加载工具，Dispatch 模式始终只暴露 `tool_search` 与 `call_tool`，用于保持模型工具面稳定。

## 1. 背景与目标

当 Agent 配置几十到几百个工具时，如果每次模型请求都携带全部工具定义，会产生以下问题：

- 工具 Schema 占用大量输入 Token；
- 模型需要在过大的候选集中选择工具，误选概率增加；
- 不同请求使用的工具不同，模型工具面难以保持稳定；
- 动态 MCP Server 的工具列表可能持续变化；
- 多租户场景下，不同用户能使用的工具集合不同；
- Prompt Cache 容易因为工具定义变化而失效。

Tool Search 的核心思路是：**初始请求只提供少量常驻工具和一个 `tool_search` 入口，模型根据目录按需发现工具，后续再调用已发现的工具。**

本设计参考
[trpc-agent-go Tool Search Plugin](https://github.com/trpc-group/trpc-agent-go/tree/e73500ecabebfc373221b19c24a84e2158c1797f/plugin/toolsearch)
的功能，但不机械复制 Go Plugin 架构，而是使用 Python SDK 原有的 ToolSet、Tool、InvocationContext 和 Session State 能力实现。

### 1.1 目标

- **按需暴露工具**：未加载的 Deferred Tool 不进入初始模型工具列表。
- **保持简单接入**：作为普通 ToolSet 配置到 `LlmAgent.tools`。
- **支持大规模目录**：通过 Toolbox 命名空间组织工具。
- **支持动态来源**：每轮刷新 MCP ToolSet，并隔离不同 MCP Server 的工具名。
- **支持两种调用方式**：兼顾原生 Function Call 和固定工具面的 Dispatch 模式。
- **支持会话延续**：同一 Session 的后续 Agent Loop 可以继续使用已发现工具。
- **支持并发搜索**：并行 `tool_search` 调用不会覆盖彼此的发现状态。
- **支持权限控制**：禁止的工具不进入目录、搜索结果和实际调用。
- **无强制检索依赖**：默认关键词检索不引入额外包；语义检索按需注入 Embedder。
- **失败可降级**：Embedding 服务失败时可选择降级到关键词检索。

### 1.2 非目标

- 不实现新的 Runner Plugin 系统；
- 不替代 Tool 自身的 Filter、Callback 和权限控制；
- 不负责模型 Prompt Cache 的创建、计费和命中统计；
- 不内置持久化向量数据库；需要持久化时通过 `ToolSearchVectorStore` 接口注入；
- 不允许通过 Tool Search 绕过原始工具的参数 Schema、Filter 或 Callback；
- 不保证 Native 模式的工具列表始终固定，固定工具面由 Dispatch 模式提供。

## 2. 方案设计

### 2.1 设计原则与总体架构

**设计原则**

1. **使用现有扩展点**：动态工具集合由 `ToolSet.get_tools()` 管理，工具目录通过 `Tool.process_request()` 注入模型请求。
2. **每轮重新解析**：每次模型调用前重新构建只读 Snapshot，避免请求执行期间被动态来源修改。
3. **目录与执行分离**：Catalog 只告诉模型有哪些 Deferred Tool；工具必须经过 `tool_search` 后才可执行。
4. **权限前置**：权限过滤发生在目录渲染和搜索候选构建之前，禁止的工具不会泄漏给模型或远程 Embedder。
5. **Session 状态最小化**：只保存“哪个工具已经发现”，不保存 Tool 对象和 Schema。
6. **并发写入可交换**：每个已发现工具使用独立状态键，避免并行 Event Delta 相互覆盖。
7. **动态来源隔离命名**：MCP 工具统一使用 `mcp__<server>__<tool>`，避免跨 Server 和静态工具重名。
8. **可配置校验策略**：默认 Fail Fast；动态组装场景可启用 Go 风格容错，跳过或合并可安全恢复的配置。

**总体架构**

```text
┌──────────────────────────── LlmAgent / Agent Loop ───────────────────────────┐
│                                                                              │
│  ToolsProcessor                                                             │
│       │                                                                      │
│       ▼                                                                      │
│  ToolSearchToolSet.get_tools(ctx)                                            │
│       │                                                                      │
│       ├─ 静态 Preset / Deferred / Toolbox                                    │
│       ├─ 动态 MCP ToolSet ──► mcp__<server>__<tool>                           │
│       ├─ Permission Filter                                                   │
│       ├─ Session State：已发现工具                                            │
│       └─ 构建当前请求 Snapshot                                                │
│                 │                                                            │
│                 ├─ Native：preset + tool_search + loaded tools               │
│                 └─ Dispatch：preset + tool_search + call_tool                 │
│                                                                              │
│  tool_search.process_request()                                               │
│       └─ Catalog ──► system instruction 或 tool description                  │
│                                                                              │
│  模型调用 tool_search                                                        │
│       ├─ tool_names：精确加载                                                 │
│       ├─ queries：关键词 / 语义检索                                           │
│       └─ namespace：限定 Toolbox                                              │
│                 │                                                            │
│                 ▼                                                            │
│       InvocationContext.state：记录发现结果                                   │
│                 │                                                            │
│                 └─ 下一轮 Agent Loop 生效                                     │
└──────────────────────────────────────────────────────────────────────────────┘
```

一次 Native 模式调用流程：

```text
1. ToolsProcessor 调 ToolSearchToolSet.get_tools(ctx)
2. ToolSet 返回 preset tools + tool_search
3. tool_search.process_request() 注入 Deferred Tool Catalog
4. 模型调用 tool_search(tool_names=["get_forecast"])
5. tool_search 将 get_forecast 写入 InvocationContext.state
6. 工具结果 Event 持久化 State Delta
7. Agent Loop 进入下一轮模型请求
8. get_tools(ctx) 读取 Session State
9. ToolSet 返回 preset tools + tool_search + get_forecast
10. 模型直接调用 get_forecast
```

### 2.2 为什么使用 ToolSet，而不是 Plugin 或 Filter

Go 实现通过一个 Plugin 注册 `BeforeModel` 和 `BeforeTool`：

```text
BeforeModel：刷新目录、注入 Catalog、增加已加载工具
BeforeTool：拦截尚未加载的 Deferred Tool
```

Python 同样具备 Model/Tool Filter 的 before/after 生命周期，但动态控制本轮模型可见工具本身是 ToolSet 的职责：

```text
ToolSet.get_tools()       动态决定本轮有哪些工具
Tool.process_request()    修改模型请求、注入 Catalog
Tool Filter              已解析工具执行前后的校验和处理
```

未加载的 Native Tool 不会由 `get_tools()` 返回，因此不会进入模型请求的
Declaration。模型仍然异常调用该名称时，ToolsProcessor 会询问 ToolSet 的
`get_unresolved_tool()` 回退钩子；Tool Search 返回一个不带 Declaration 的
Guard Tool，给出“先搜索再重试”的提示。Dispatch 模式则由始终可见的
`call_tool` 显式检查加载状态。

因此本方案不增加 Tool Search 专用 Filter：

- ToolSet 管理工具可见性；
- 原始 Tool Filter/Callback 继续管理已加载工具的执行；
- Dispatch 的 `call_tool` 管理目标工具的加载状态和权限；
- Native 模式通过 ToolSet 回退钩子提示尚未加载的异常调用。

### 2.3 公共 API

核心入口：

```python
ToolSearchToolSet(
    preset_tools=None,
    deferred_tools=None,
    toolboxes=None,
    mcp_toolboxes=None,
    options=None,
    semantic_index=None,
    name="tool_search_tools",
)
```

各参数语义：

| 参数 | 作用 |
|---|---|
| `preset_tools` | 始终暴露给模型；不参与关键词或语义搜索 |
| `deferred_tools` | 无业务命名空间的延迟工具 |
| `toolboxes` | 按静态命名空间组织的延迟工具 |
| `mcp_toolboxes` | 每轮从动态 ToolSet 获取工具的 MCP 命名空间 |
| `options` | 搜索结果数、权限、Catalog 位置、调用模式和降级策略 |
| `semantic_index` | 可选语义索引；未配置时使用关键词检索 |
| `name` | ToolSet 名称，同时参与 Session State 隔离 |

`Toolbox`：

```python
Toolbox(
    name="weather",
    description="天气查询和预报",
    tools=[get_weather, get_forecast],
)
```

`MCPToolbox`：

```python
MCPToolbox(
    server_name="github",
    description="GitHub 仓库操作",
    toolset=github_mcp_toolset,
)
```

`ToolSearchOptions`：

```python
ToolSearchOptions(
    max_results=5,
    permission_filter=None,
    catalog_in_description=False,
    invocation_mode=InvocationMode.NATIVE,
    embedding_fail_open=False,
    state_key_prefix="tool_search",
    strict_validation=True,
)
```

公开导出同时提供带 Tool Search 前缀的别名，避免与其他模块类型冲突：

```python
from trpc_agent_sdk.tools import (
    SemanticToolIndex,
    ToolSearchInvocationMode,
    ToolSearchMCPToolbox,
    ToolSearchOptions,
    ToolSearchToolbox,
    ToolSearchToolSet,
)
```

### 2.4 Snapshot 与请求生命周期

`ToolSearchToolSet` 不长期维护一个可被所有请求修改的全局工具索引，而是在每次 `get_tools(ctx)` 时构建 `_Snapshot`：

```python
@dataclass(frozen=True)
class _Snapshot:
    entries: dict[str, _ToolEntry]
    canonical_names: dict[str, str]
    deferred_names: frozenset[str]
    namespace_tools: dict[str, frozenset[str]]
    namespace_descriptions: dict[str, str]
    namespace_order: tuple[str, ...]
```

Snapshot 包含：

- 工具名称到 Tool、Declaration、Namespace 的映射；
- 小写名称到标准名称的映射，用于大小写不敏感的精确加载；
- Deferred Tool 集合；
- 每个 Namespace 的成员集合；
- Toolbox 描述和渲染顺序。

Snapshot 只服务当前模型请求和由该请求产生的 `tool_search` / `call_tool`。动态 MCP 下一轮变化时会创建新的 Snapshot，不修改已经进入执行阶段的 Snapshot。

这保证：

- 当前模型看到的目录与当前 `tool_search` 使用的搜索集合一致；
- 当前 `call_tool` 使用的 Schema 与搜索结果一致；
- 动态来源变化不会在单次模型请求中造成目录和执行对象不一致。

### 2.5 Preset、Deferred 与 Toolbox

工具分为三类：

```text
Preset Tool
  ├─ 每轮直接暴露
  ├─ 可以通过精确 tool_names 返回
  └─ 不进入 query / embedding 候选集

Default Deferred Tool
  ├─ 初始不暴露
  ├─ 没有业务 Namespace
  └─ 可通过 tool_names 或全局 queries 加载

Toolbox Deferred Tool
  ├─ 初始不暴露
  ├─ 属于一个业务 Namespace
  └─ 可按 Namespace 限定搜索范围
```

静态工具名在同一个 `ToolSearchToolSet` 内必须全局唯一。Namespace 只是搜索范围，不是静态工具名的一部分：

```text
正确：tool_names=["get_forecast"], namespace="weather"
不推荐：tool_names=["weather.get_forecast"]
```

考虑到模型可能从分组目录错误推断出 `namespace.tool`，实现提供有限容错：仅当 Namespace 和工具归属都匹配时，`weather.get_forecast` 才会被解析为 `get_forecast`；错误的 `network.get_forecast` 不会匹配。

### 2.6 Catalog 注入

Catalog 默认通过占位符注入系统指令：

```python
TOOL_SEARCH_PLACEHOLDER = "{deferred_tools_section}"
```

处理顺序：

```text
1. system instruction 包含占位符
   └─ 用 Catalog 替换第一个占位符

2. 没有占位符，但 Catalog 非空
   └─ 追加到 system instruction

3. catalog_in_description=True
   ├─ Catalog 写入 tool_search description
   └─ 从 system instruction 移除字面占位符
```

Toolbox Catalog 示例：

```text
<toolbox-catalog>
Deferred tools are not callable until loaded with tool_search;
after loading, call it directly.
- namespace=weather (天气查询和预报);
  exact tool_names=["get_forecast", "get_weather"]
Namespace filters the search scope and must not be prepended to a tool name.
</toolbox-catalog>
```

如果只有无 Namespace 的 Deferred Tool，则使用更紧凑的兼容格式：

```text
<available-deferred-tools>
read_file
write_file
</available-deferred-tools>
```

权限过滤在 Catalog 渲染前完成，禁止的工具不会出现在目录中。

### 2.7 搜索路由

`tool_search` 接收四个字段：

```text
tool_names   精确工具名列表
queries      关键词或语义查询列表
namespace    Toolbox 搜索范围
max_results  本次最多加载的结果数
```

路由优先级：

```text
tool_names 非空
  └─ 大小写不敏感的精确加载，跨 Namespace

namespace 非空且 queries 为空
  └─ 按名称列出该 Namespace 的工具

queries 非空且配置 SemanticToolIndex
  └─ 语义检索

queries 非空且未配置 SemanticToolIndex
  └─ 关键词检索

其他
  └─ 返回空结果
```

未知 Namespace 返回结构化结果和有效 Namespace Catalog，便于模型重试：

```json
{
  "status": "unknown_namespace",
  "message": "Namespace 'missing' is not in the tool catalog.",
  "namespace": "missing",
  "catalog": [
    {
      "name": "weather",
      "description": "天气查询和预报"
    }
  ]
}
```

模型请求的 `max_results` 上限为 10。超过有效上限的匹配不会直接丢弃，而是以名称列表放入 `additional_candidates`，模型可以随后通过 `tool_names` 精确加载。

结果中的每个重复匹配会标记 `already_loaded=true`。顶层 `status` 同时区分
“全部首次加载”“部分已经加载”和“全部已经加载”，并按 Native/Dispatch
模式分别提示直接调用或通过 `call_tool` 调用，减少模型重复搜索。

### 2.8 关键词检索

关键词检索不依赖外部组件，索引内容包括：

- Tool 名称；
- CamelCase / 下划线拆分后的名称片段；
- Tool Description；
- Toolbox Description，用于 Namespace Bias。

名称解析示例：

```text
getWeatherForecast  → get / weather / forecast
get_weather         → get / weather
mcp__github__search → mcp / github / search
```

Query 使用 Unicode 感知的分隔规则；空格、逗号、中英文标点都会拆分为独立 Term。

匹配分数：

| 条件 | 分数 |
|---|---:|
| 整个 Query 精确等于工具名 | 1000 |
| 复合 Term 命中工具名 | 10 |
| Term 精确命中名称片段 | 10 |
| Term 命中 Description | 3 |
| 仅命中完整名称文本 | 3 |

`+term` 表示必须命中：

```python
queries=["+read content"]
```

只有同时满足 `read` 的工具才进入候选，然后再使用全部 Term 排序。包含 `_` 或 `-` 的 Required Term 会继续拆成原子 Term。

多个 Query 独立评分，同一工具保留最佳分数；最终排序规则：

```text
分数降序
  → 名称片段数量升序
  → 工具名字典序
```

未指定 Namespace 时会搜索所有 Deferred Tool，并根据 Toolbox Description 的命中情况增加 Namespace Bias，使业务域描述更相关的候选获得额外分数。

### 2.9 语义检索

语义检索通过注入 `SemanticToolIndex` 开启：

```python
tool_search = ToolSearchToolSet(
    deferred_tools=tools,
    semantic_index=SemanticToolIndex(
        embedder,
        vector_store=my_vector_store,
    ),
)
```

Embedder 支持以下任意一组接口：

```text
异步：aembed_documents / aembed_query
同步：embed_documents / embed_query
```

同步方法通过 `asyncio.to_thread()` 执行，避免阻塞 Agent Event Loop。Embedder
可以直接返回向量以保持兼容，也可以返回 `(向量, usage)`：

```python
return vectors, {
    "prompt_tokens": 120,
    "total_tokens": 120,
}
```

每个工具转换成如下文档：

```text
Tool: get_forecast
Description: Get a multi-day weather forecast
Declaration: <完整 FunctionDeclaration JSON>
```

该文本计算 SHA-256 Fingerprint。`SemanticToolIndex` 保存：

```text
tool_name → fingerprint
tool_name → vector（由 ToolSearchVectorStore 管理）
```

Declaration 未变化时复用向量；名称、描述或 Schema 变化后生成新的 Fingerprint 并重新 Embedding。

VectorStore 接口包含 `upsert(name, vector)`、`delete(names)` 和
`search(query_vector, candidate_names, limit)`，支持同步或异步实现。默认使用
`InMemoryToolSearchVectorStore`；应用可以注入远程或持久化实现。

检索过程：

```text
权限过滤后的候选工具
  → 补齐缺失向量
  → Embedding Query
  → 计算余弦相似度
  → 每个 Query 独立排序
  → 按每个工具的最佳 Rank 合并
  → max_results 切分
```

禁止的工具在构建语义文档前已经移除，因此名称、描述和 Schema 不会发送给远程 Embedding 服务。

`embedding_fail_open=True` 时，Embedding 或向量计算失败会记录 Warning 并降级到关键词检索；默认 `False`，失败直接返回给调用方。

每次模型请求开始时，`tool_search.process_request()` 都会创建新的 Usage
累计周期；当前轮次内累加 Embedder 返回的 `prompt_tokens` /
`total_tokens`。应用可通过
`tool_search.get_embedding_usage(ctx, reset=False)` 获取当前轮快照，或传入
`reset=True` 提前清零。

MCP 工具声明变化或下线时主动清理旧向量，并在并发刷新期间校验声明指纹，避免过期 Snapshot 重新写入旧向量。

### 2.10 Native 与 Dispatch 模式

**Native 模式**

```python
ToolSearchOptions(
    invocation_mode=InvocationMode.NATIVE,
)
```

工具面变化：

```text
初始：preset + tool_search
发现后：preset + tool_search + loaded tool A
继续发现：preset + tool_search + loaded tool A + loaded tool B
```

Native 模式使用 `InvocationContext.state` 记录已经发现的工具名称。模型每次
请求前，`get_tools(ctx)` 都会读取这些名称，在最新 Snapshot 中重新查找工具、
检查权限，并将仍然有效的完整 Tool Schema 加入本轮 `tools` 列表。State
本身不保存 Tool 对象或 Schema。

Session 历史只保存 Function Call 的名称、实参和执行结果，不保存当轮
`LlmRequest.tools` 中的完整工具声明。因此，即使历史中出现过某个工具，
框架仍需要通过 State 确定本轮应重新声明哪些工具。可以将它理解为：

```text
Session Events：帮助模型理解“之前调用过什么”
Session State：帮助框架确定“本轮需要声明哪些工具”
```

优点：

- 模型使用标准 Function Call 直接调用真实工具；
- 工具参数 Schema 由框架正常提供；
- 原始 Tool Filter、Callback、Streaming 和 Long Running 行为保持自然。

代价：

- 每次发现新工具后模型工具列表会变化；
- 大量已加载工具仍会逐步增加 Token；
- Prompt Cache 可能因为工具面变化而失效。

如果模型在 Native 模式下直接调用尚未发现的 Deferred Tool，
`ToolsProcessor` 会通过 ToolSet 的 `get_unresolved_tool()` 回退钩子取得一个
不带 Declaration 的 Guard Tool，并返回“先使用 `tool_search` 加载后重试”的
专用提示。对于已经从 MCP 最新非空列表下线、但模型仍持有旧名称的工具，
框架也会根据 `mcp__<server>__<tool>` 和已注册 Namespace 返回相同提示。
Guard Tool 不会进入模型请求的工具声明。

**Dispatch 模式**

```python
ToolSearchOptions(
    invocation_mode=InvocationMode.DISPATCH,
)
```

工具面保持为：

```text
preset + tool_search + call_tool
```

`tool_search` 结果额外返回标准化后的 `input_schema`：

```json
{
  "name": "get_forecast",
  "description": "Get a multi-day weather forecast",
  "input_schema": {
    "type": "object",
    "properties": {
      "city": {
        "type": "string"
      }
    }
  }
}
```

模型随后调用：

```json
{
  "tool_name": "get_forecast",
  "params": {
    "city": "Shenzhen",
    "days": 3
  }
}
```

`call_tool` 依次检查：

```text
tool_name 是否为空
  → params 是否为对象
  → 工具是否存在
  → 当前调用方是否有权限
  → 工具是否已通过 tool_search 加载
  → 调用原始 Tool
```

Dispatch 模式不会使用 State 向模型注入真实工具 Schema。真实工具的
`input_schema` 由 `tool_search` 作为 Function Response 返回；模型读取该
响应后，通过固定声明的 `call_tool(tool_name, params)` 发起调用。

该模式下，State 只保存“工具已通过搜索加载”的名称，作为 `call_tool` 的
搜索前置门禁，确保模型不能仅靠猜测工具名称绕过 Namespace、Top-K 和搜索
结果直接调用。它不等同于权限：调用时仍会根据最新 Snapshot 单独执行权限
检查。

```text
tool_search 返回 name + description + input_schema
             ↓
State 记录 discovered_tools:<name> = true
             ↓
call_tool 检查权限和 discovered 状态
             ↓
执行最新 Snapshot 中的真实 Tool
```

如果产品希望 `call_tool` 支持按准确名称直接分发，也可以取消该 State 门禁，
只保留工具存在性和权限检查；但这会将 Dispatch 从“搜索后调用”改为“可直接
调用的统一分发器”，不再保证模型调用前看过目标工具的最新 Schema。

Dispatch 的优点是 Deferred Tool 工具面固定，更适合大目录和重视 Prompt Cache 稳定性的场景。

Progress Streaming Tool 在 Dispatch 中会被完整消费，并按顺序聚合为 `{"chunks": [...]}` 单次返回；Native 模式仍走框架原生 Progress Streaming 路径。

### 2.11 Session State 与并发

发现状态写入 `InvocationContext.state`，状态键格式：

```text
<state_key_prefix>:<toolset_name>:<branch_or_agent>:discovered_tools:<tool_name>
```

示例：

```text
tool_search:tool_search_tools:weather_agent:discovered_tools:get_forecast = true
```

隔离维度：

| 维度 | 作用 |
|---|---|
| `state_key_prefix` | 避免与其他框架状态冲突 |
| `toolset_name` | 同一 Agent 中多个 Tool Search 实例相互隔离 |
| `branch_or_agent` | 多 Agent / Graph Branch 之间相互隔离 |
| `tool_name` | 每个工具独立写入，支持并发合并 |

使用“每个工具一个状态键”，而不是一个 JSON 工具列表，是为了让并行搜索的 Event Delta 可交换：

```text
并行搜索 A：...:tool_a = true
并行搜索 B：...:tool_b = true

无论 Event 以什么顺序持久化，最终两个键都存在。
```

状态通过正常工具结果 Event 的 State Delta 持久化，不在 Tool Search 内直接调用 Session Service。这样遵循框架统一的 Event/State 提交流程。

状态只保存工具名称，不保存 Tool 对象。下一轮 `get_tools()` 会在最新 Snapshot 中重新解析名称：

- 静态工具仍存在且有权限：继续暴露；
- MCP 工具已从最新非空列表删除：不再暴露；
- 权限被收回：不再暴露；
- 名称不存在：忽略旧状态。

### 2.12 权限模型

权限回调签名：

```python
ToolPermissionFilter = Callable[
    [InvocationContext, Sequence[str]],
    dict[str, bool],
]
```

一次请求对全部 Deferred Tool 批量计算权限：

```python
def permissions(ctx, names):
    return {
        name: can_use(ctx.user_id, name)
        for name in names
    }
```

没有明确返回 `True` 的工具按禁止处理。权限作用于：

```text
Catalog 渲染
  → 精确 tool_names
  → 关键词候选
  → 语义检索候选
  → Native 已加载工具暴露
  → Dispatch call_tool 实际执行
```

Preset Tool 不由 Tool Search Permission Filter 管理；如果 Preset Tool 需要权限控制，应使用原始 Tool Filter、Agent Callback 或应用自己的授权层。

### 2.13 动态 MCP ToolSet

MCP Toolbox 每次 `get_tools(ctx)` 都调用内部动态 ToolSet：

```text
ToolSearchToolSet.get_tools()
  → MCPToolbox.toolset.get_tools(ctx)
  → 获取当前 MCP 工具
  → 重命名
  → 写入当前 Snapshot
```

重命名规则：

```text
原名称：read_file
Server：github
新名称：mcp__github__read_file
```

原因是模型 Function Call 只有工具名，没有独立的 MCP Server 字段。多个 Server 都可能暴露 `read_file` 或 `search`，因此必须形成全局唯一名称。

重命名代理保留：

- 原始 Declaration 和参数 Schema；
- Tool Filter；
- `is_streaming` / `is_progress_streaming`；
- `api_variant`；
- `skip_summarization`；
- Long Running 类型标记；
- 实际调用委托给原始 Tool。

每个 MCP Namespace 使用独立 `asyncio.Lock` 串行化“获取列表 + 更新缓存”，避免并发请求以相反顺序提交快照。

每次非空列表还会计算工具声明指纹：

- 新增工具在首次语义搜索时生成向量；
- 声明发生变化时清理旧向量，下一次搜索重新生成；
- 从非空列表中删除的工具同时从当前 Snapshot 和语义索引移除；
- Embedding 完成前再次校验最新指纹，防止并发旧 Snapshot 复活已下线或旧版本工具。

容错策略：

```text
动态列表非空
  └─ 替换最后一次成功快照

动态列表为空
  └─ 保留最后一次非空快照

动态列表抛异常且已有快照
  └─ 记录 Warning，使用最后快照

动态列表抛异常且没有快照
  └─ 向上抛出异常
```

空列表被视为临时不可用，而不是“Server 主动删除全部工具”。这是可用性优先的约定。

`ToolSearchToolSet.close()` 会关闭所有不同的动态 ToolSet，同一个实例被多个 MCP Toolbox 引用时只关闭一次。

### 2.14 配置校验与生命周期

以下名称是保留名称：

```text
tool_search
call_tool
```

默认 `strict_validation=True`，初始化时拒绝：

- Preset / Deferred / Toolbox 静态工具重名；
- 工具使用保留名称；
- 空 Toolbox 名称；
- 重复 Toolbox Namespace；
- 空 MCP Server Name；
- MCP Namespace 与静态 Toolbox Namespace 冲突；
- `max_results <= 0`。

设置 `strict_validation=False` 后采用 Go 风格容错：

- 空 Toolbox / MCP Server Name、无效工具和空 MCP ToolSet：记录 Warning 并跳过；
- 重复静态 Namespace：按注册顺序合并，首个非空 Description 生效；
- 同一工具注册到多个 Namespace：保留第一次归属并记录 Warning；
- MCP Namespace 与已有 Namespace 冲突：保留已有配置，跳过后者；
- 动态 MCP 列表包含相同声明的同名工具：保留第一项。

以下问题即使在容错模式下仍然报错，因为无法安全决定最终行为：

- 工具使用 `tool_search` / `call_tool` 保留名称；
- 同名工具或动态 MCP 工具的 Declaration 不一致；
- `max_results <= 0`；
- 动态工具与现有全局工具发生无法消解的名称冲突。

没有 Function Declaration 的动态工具不会进入 Snapshot，并记录 Warning，因为模型无法获得其调用 Schema。

工具与 ToolSet 生命周期：

```text
ToolSearchToolSet 由 Runner/Agent 生命周期管理
  ├─ 静态 Tool 不由 Tool Search 单独关闭
  └─ MCPToolbox.toolset 在 ToolSearchToolSet.close() 中关闭
```

## 3. 使用方式

### 3.1 Native 模式

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.tools import ToolSearchOptions
from trpc_agent_sdk.tools import ToolSearchToolbox
from trpc_agent_sdk.tools import ToolSearchToolSet
from trpc_agent_sdk.tools import TOOL_SEARCH_PLACEHOLDER

tool_search = ToolSearchToolSet(
    preset_tools=[
        FunctionTool(calculator),
    ],
    toolboxes=[
        ToolSearchToolbox(
            name="weather",
            description="天气查询和预报",
            tools=[
                FunctionTool(get_weather),
                FunctionTool(get_forecast),
            ],
        ),
        ToolSearchToolbox(
            name="billing",
            description="账单、支付和退款",
            tools=[
                FunctionTool(create_invoice),
                FunctionTool(refund_payment),
            ],
        ),
    ],
    options=ToolSearchOptions(),
)

agent = LlmAgent(
    name="assistant",
    model=model,
    instruction=(
        "调用延迟工具前先使用 tool_search。"
        "namespace 只用于限定搜索范围，不是工具名前缀。\n\n"
        f"{TOOL_SEARCH_PLACEHOLDER}"
    ),
    tools=[tool_search],
)
```

用户请求：

```text
查询深圳未来三天的天气。
```

模型调用流程：

```json
{
  "name": "tool_search",
  "arguments": {
    "tool_names": ["get_forecast"]
  }
}
```

下一轮：

```json
{
  "name": "get_forecast",
  "arguments": {
    "city": "深圳",
    "days": 3
  }
}
```

### 3.2 Dispatch 模式

```python
from trpc_agent_sdk.tools import ToolSearchInvocationMode

tool_search = ToolSearchToolSet(
    toolboxes=toolboxes,
    options=ToolSearchOptions(
        invocation_mode=ToolSearchInvocationMode.DISPATCH,
    ),
)
```

模型工具面始终保持：

```text
tool_search
call_tool
```

搜索后调用：

```json
{
  "name": "call_tool",
  "arguments": {
    "tool_name": "get_forecast",
    "params": {
      "city": "深圳",
      "days": 3
    }
  }
}
```

### 3.3 语义检索与失败降级

```python
tool_search = ToolSearchToolSet(
    deferred_tools=tools,
    semantic_index=SemanticToolIndex(embedder),
    options=ToolSearchOptions(
        embedding_fail_open=True,
    ),
)
```

如果用户查询：

```text
明天出门是否需要带伞？
```

语义检索可以将其匹配到 `get_forecast`。Embedding 服务不可用时，如果 `embedding_fail_open=True`，框架使用原始 Query 降级到关键词搜索；降级后可用性仍在，但召回准确率可能下降。

## 4. 测试设计

测试文件：

```text
tests/tools/tool_search/test_tool_search.py
```

覆盖范围：

| 分类 | 断言 |
|---|---|
| 关键词搜索 | Namespace、Required Term、精确名称、大小写、结果溢出、加载状态提示 |
| 名称容错 | 正确 `namespace.tool` 可解析，错误 Namespace 不匹配 |
| Agent Loop | ToolsProcessor 下一轮暴露已加载 Native Tool |
| Native 回退 | 未加载及已下线 MCP 工具返回先搜索再重试的专用提示 |
| Catalog | 占位符替换、准确名称提示、Namespace 说明 |
| 配置容错 | Namespace 合并、首次归属、MCP 冲突跳过、Declaration 冲突报错 |
| 权限 | 禁止工具不出现在搜索和已加载工具中 |
| Preset | 始终可见，不进入 Query 候选 |
| Dispatch | 固定工具面、Schema、未加载拦截、真实调用 |
| Streaming | Dispatch 消费 Progress Stream 并按顺序返回完整 `chunks` |
| MCP | 重命名、空列表缓存、异常缓存、关闭、Long Running 标记 |
| 语义搜索 | 相似度排序、Embedding 失败降级、自定义 VectorStore、Usage 累计 |
| 并发状态 | 并行搜索结果合并 |
| 多 Agent | Branch 状态隔离 |

相关回归测试还应包含：

```text
tests/tools/test_tool_adapter.py
tests/tools/test_default_toolset.py
tests/tools/test_base_tool.py
tests/tools/test_streaming_progress_tool.py
tests/agents/core/test_tools_processor.py
```

验证命令：

```bash
python -m yapf -i \
  trpc_agent_sdk/tools/tool_search/*.py \
  tests/tools/tool_search/test_tool_search.py \
  examples/tool_search/**/*.py

python -m pytest \
  tests/tools/tool_search/test_tool_search.py \
  tests/tools/test_tool_adapter.py \
  tests/tools/test_default_toolset.py \
  tests/tools/test_base_tool.py \
  tests/tools/test_streaming_progress_tool.py \
  tests/agents/core/test_tools_processor.py -q

python -m flake8 \
  trpc_agent_sdk/tools/tool_search \
  tests/tools/tool_search/test_tool_search.py \
  examples/tool_search
```

## 5. 与 Go Plugin 的对应关系

Python 和 Go 的用户可见核心行为保持一致，但接入结构不同：

| Go Plugin | Python 实现 |
|---|---|
| `BeforeModel` 刷新目录 | `ToolSearchToolSet.get_tools()` 构建 Snapshot |
| `BeforeModel` 注入 Catalog | `_ToolSearchTool.process_request()` |
| `BeforeModel` 添加已加载工具 | Native `get_tools()` 返回 Session 中已发现工具 |
| `BeforeTool` 拦截未加载工具 | Native 使用 ToolSet 回退 Guard；Dispatch 在 `call_tool` 中检查 |
| Plugin 内可变全局索引 | 每次模型请求构建只读 Snapshot |
| 一个 JSON 列表保存发现状态 | 每个工具独立 State Key |
| Session 级共享 | ToolSet + Agent/Branch 级隔离 |
| 框架 VectorStore | `ToolSearchVectorStore`，默认内存实现并支持注入 |
| Embedding Usage 统计 | 从 Embedder 返回值累计，可按 InvocationContext 读取 |
| Stream 聚合为 `chunks` | Dispatch 同样按顺序返回完整 `chunks` |

Python 使用 ToolSet 而不是 Plugin 的原因：

- Python 已有标准动态 ToolSet 生命周期；
- ToolsProcessor 每轮都会重新解析 ToolSet；
- BaseTool 的 `process_request()` 可以修改模型请求；
- Tool Filter 只在工具已解析并进入 `run_async()` 后执行，不能代替动态工具可见性；
- ToolSet 能同时解决模型声明和后续工具执行解析。

因此这是框架风格上的等价实现，不是对 Go Plugin 类型结构的直接翻译。

## 6. 已知边界与后续演进

当前已知边界：

1. **Native Prompt Cache 变化**：加载新工具后 Function Declaration 集合变化；需要固定工具面时应使用 Dispatch。
2. **Embedding Usage 独立统计**：Usage 可通过 Tool Search API 读取，但不会直接并入模型响应的 `usage_metadata`。
3. **MCP 空列表语义**：空列表按临时故障处理，不能表达“Server 主动清空全部工具”。

建议后续按优先级演进：

```text
P2  为动态 MCP 增加显式“确认空目录”语义
P3  评估将 Embedding Usage 合并到统一 Event Usage 的方案
```

不建议为形式对齐 Go 而新增独立 Plugin 系统；后续能力应继续围绕 ToolSet、Tool、Filter 和 InvocationContext 的现有边界演进。
