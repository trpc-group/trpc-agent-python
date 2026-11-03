# Tool Search

当 Agent 拥有大量工具时，把全部工具定义放入每次模型请求会增加输入
Token、影响工具选择，也会降低模型缓存的命中率。`ToolSearchToolSet` 只暴露
常驻工具和 `tool_search`，让模型按需发现并加载当前会话需要的工具。

## 核心概念

- **Tool**：模型可以调用的函数，例如 `get_weather`。通常使用
  `FunctionTool(get_weather)` 包装普通 Python 函数。
- **ToolSearchToolSet**：管理工具目录、执行搜索并控制工具何时对模型可用的
  ToolSet。将它放入 `LlmAgent.tools` 即可启用 Tool Search。
- **Preset Tool（常驻工具）**：通过 `preset_tools` 配置，完整 Schema
  始终直接提供给模型，不需要先搜索。适合计算器、当前时间等少量高频工具。
  `preset_tools` 不是必填项；常驻工具也可以直接放入 `LlmAgent.tools`。
- **Deferred Tool（延迟工具）**：通过 `deferred_tools` 配置，初始只在目录中
  可被搜索，完整 Schema 不会立即提供给模型。这里的“延迟”是指延迟暴露给
  模型，不是延迟执行或后台任务。
- **Toolbox（工具箱）**：通过 `toolboxes` 配置的一组静态 Deferred Tool。
  每个 Toolbox 有名称和描述，可用来按业务域组织工具，例如 `weather`、
  `filesystem`。Toolbox 中的工具仍然是延迟工具。
- **Namespace（命名空间）**：Toolbox 的名称，用于限定搜索范围，减少不同
  业务域中相似工具的干扰。它不是静态工具调用名称的一部分。
- **Catalog（工具目录）**：提供给模型的轻量工具索引，主要包含 Namespace、
  工具名称和描述，不等于完整工具 Schema。
- **发现/加载**：`tool_search` 找到某个 Deferred Tool 后，框架记录该工具
  已发现。Native 模式下一轮会把它的完整 Schema 提供给模型；Dispatch
  模式会在搜索结果中返回 Schema，并允许 `call_tool` 调用。
- **Session State**：持久化已发现工具名称的会话状态，不保存 Tool 对象或
  Schema。它使同一 Agent 分支的后续轮次能够复用已经发现的工具。

`deferred_tools` 与 `toolboxes` 都用于配置静态延迟工具，区别仅在于是否分组：

```python
tool_search = ToolSearchToolSet(
    # 不分组，属于默认空 Namespace
    deferred_tools=[
        FunctionTool(search_document),
    ],
    # 按业务域分组，可以通过 namespace="weather" 限定搜索
    toolboxes=[
        ToolSearchToolbox(
            name="weather",
            description="天气查询和预报",
            tools=[
                FunctionTool(get_weather),
                FunctionTool(get_forecast),
            ],
        ),
    ],
)
```

如果工具数量较少且不需要按业务域搜索，使用 `deferred_tools` 即可；工具数量
较多、存在多个业务域或相似工具时，建议使用 `toolboxes` 分组。

## 基本用法

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.tools import ToolSearchToolbox
from trpc_agent_sdk.tools import ToolSearchToolSet
from trpc_agent_sdk.tools.tool_search import TOOL_SEARCH_PLACEHOLDER

tool_search = ToolSearchToolSet(
    preset_tools=[FunctionTool(calculator)],
    toolboxes=[
        ToolSearchToolbox(
            name="weather",
            description="天气查询和预报",
            tools=[
                FunctionTool(get_weather),
                FunctionTool(get_forecast),
            ],
        ),
    ],
)

agent = LlmAgent(
    name="assistant",
    model=model,
    instruction=(
        "调用延迟工具前先使用 tool_search。\n\n"
        f"{TOOL_SEARCH_PLACEHOLDER}"
    ),
    tools=[tool_search],
)
```

`preset_tools` 始终直接提供给模型；`deferred_tools` 和 `toolboxes` 中的工具
只有被 `tool_search` 找到后才可调用。已发现的工具名称保存在 Session
State 中，同一 Agent 分支的后续轮次可以继续使用。

## 调用模式

`ToolSearchOptions.invocation_mode` 支持两种模式：

- `InvocationMode.NATIVE`（默认）：发现后将工具定义直接加入下一次模型请求，
  模型按原工具名称调用，适合工具数量适中的场景。
- `InvocationMode.DISPATCH`：模型统一通过 `call_tool(tool_name, params)` 调用
  已发现工具，模型请求中的工具定义数量保持稳定，适合大规模目录和重视
  Prompt Cache 稳定性的场景。

Dispatch 模式不会允许绕过搜索调用未加载的工具。权限过滤也会同时作用于
目录、搜索结果和实际调用。Native 模式下如果模型直接调用尚未加载的工具，
框架会返回专用提示，要求先通过 `tool_search` 加载再重试；该提示工具本身
不会暴露给模型。对于已经从 MCP 最新工具列表下线、但模型仍持有旧名称的
工具，也会返回相同提示。

## 检索和目录注入

默认检索不需要额外依赖，按名称、描述和命名空间进行关键词匹配。关键词前加
`+` 表示必须命中，例如 `["+weather", "forecast"]`。

模型调用 `tool_search` 时可以使用以下参数：

- `queries`：关键词或自然语言查询列表，例如 `["天气预报"]`。
- `tool_names`：需要精确加载的工具名称列表。名称通常来自目录或上一次搜索的
  `additional_candidates`。
- `namespace`：可选的 Toolbox Namespace，只在指定业务域内搜索。
- `max_results`：本次最多加载的匹配数量，请求上限为 10；其余匹配会通过
  `additional_candidates` 返回名称，模型可以随后精确加载。

`queries` 适合“不知道具体工具名称”的场景，`tool_names` 适合“已经知道准确
名称”的场景。例如：

```json
{
  "queries": ["查询未来天气"],
  "namespace": "weather",
  "max_results": 3
}
```

命名空间只用于限定搜索范围，不是静态工具名称的一部分。调用 `tool_names`
时应复制目录展示的准确名称，例如使用 `"get_forecast"`，不要使用
`"weather.get_forecast"`。为兼容模型偶发的错误推断，框架仅在命名空间和
工具名都能准确匹配时容错接受后一种写法。

目录默认替换系统指令中的 `{deferred_tools_section}`；没有该占位符时会追加到
系统指令。设置 `catalog_in_description=True` 可改为写入 `tool_search` 描述。

如需语义检索，可向 `ToolSearchToolSet` 传入
`SemanticToolIndex(embedder)`。Embedder 可实现异步 `aembed_query` /
`aembed_documents`，或同步 `embed_query` / `embed_documents`。设置
`embedding_fail_open=True` 后，Embedding 服务失败时自动降级到关键词检索。

`SemanticToolIndex(embedder, vector_store=...)` 支持注入
`ToolSearchVectorStore`，默认使用 `InMemoryToolSearchVectorStore`。自定义
VectorStore 需要实现 `upsert`、`delete` 和 `search`，方法可以是同步或异步。

Embedder 可以继续直接返回向量；如果服务提供 Token 用量，也可以返回
`(向量, usage)`，其中 usage 支持 `prompt_tokens` / `total_tokens` 或
`prompt_token_count` / `total_token_count`。每次模型请求会自动开始新的统计
周期；当前轮结果通过 `tool_search.get_embedding_usage(ctx)` 读取，传入
`reset=True` 可提前清零。

## 主要配置项

- `max_results`：默认搜索结果数量。
- `permission_filter`：根据当前 `InvocationContext` 返回每个延迟工具是否
  可见和可调用，用于租户、用户或 Agent 级权限控制。
- `catalog_in_description`：是否把 Catalog 放入 `tool_search` 的工具描述；
  默认放入系统指令。
- `invocation_mode`：选择 Native 或 Dispatch 调用模式。
- `embedding_fail_open`：语义检索失败时是否降级到关键词检索。
- `state_key_prefix`：Session State 中已发现工具记录的键前缀，用于避免与
  其他框架状态冲突。
- `strict_validation`：是否严格校验重复 Namespace、重复工具和无效配置。

## 配置容错

默认 `ToolSearchOptions(strict_validation=True)`，空 Namespace、重复
Namespace 或重复工具会在初始化时直接报错。动态组装大量工具时可以设置：

```python
ToolSearchOptions(strict_validation=False)
```

容错模式会记录 Warning，并合并重复静态 Namespace、保留工具的第一次归属，
跳过空 Toolbox、无效工具和发生冲突的 MCP Namespace。保留名称冲突、
同名工具 Declaration 不一致以及非法 `max_results` 仍然报错，因为这些问题
无法安全恢复。

## 动态 MCP 工具

使用 `ToolSearchMCPToolbox` 包装动态 ToolSet：

```python
from trpc_agent_sdk.tools import ToolSearchMCPToolbox

tool_search = ToolSearchToolSet(
    mcp_toolboxes=[
        ToolSearchMCPToolbox(
            server_name="github",
            description="GitHub MCP 工具",
            toolset=mcp_toolset,
        ),
    ],
)
```

`ToolSearchMCPToolbox` 与静态 `ToolSearchToolbox` 的区别是：静态 Toolbox
直接持有确定的工具列表；MCP Toolbox 持有一个动态 ToolSet，每次构建工具
快照时可以从 MCP 服务获取最新工具声明。

动态工具会以 `mcp__github__<tool_name>` 暴露，避免多个服务重名。关闭
`ToolSearchToolSet` 时会同步关闭其动态 ToolSet。框架会比较动态工具的声明
指纹：声明变化或工具下线时清理旧语义向量，未变化的工具继续复用已有索引；
并发刷新时还会阻止过期声明重新写入索引。

这里的**声明指纹**是根据工具名称、描述和参数 Schema 计算的标识，用于判断
MCP 工具定义是否变化；它不是认证凭证，也不会发送给模型。

完整可运行代码见
[`examples/tool_search`](../../../examples/tool_search/)。
