# DSL

DSL code generator 用于把业务工作流 JSON 描述转换为可运行的 tRPC-Agent-Python 工程。  
生成代码基于 trpc_agent_dsl.graph（见 [graph.md](./graph.md)），适合“DSL 声明 + 自动生成 + 二次开发”的协作方式。

## 命令行用法

执行入口：

```bash
python -m trpc_agent_dsl.codegen workflow.json [options]
```

常用参数如下：

- workflow.json：DSL JSON 文件路径
- --dsl-text：直接传入 DSL JSON 文本（不提供 workflow.json 时使用）
- -o, --output-dir：输出目录（默认 <workflow_stem>）
- --overwrite：允许写入非空目录

示例：

```bash
# 输出到同目录下 <workflow_stem> 目录
python -m trpc_agent_dsl.codegen workflow.json

# 指定输出目录并覆盖
python -m trpc_agent_dsl.codegen workflow.json -o examples/dsl/my_graph --overwrite

# 不提供文件，直接传 DSL JSON 文本
python -m trpc_agent_dsl.codegen --dsl-text "$(cat workflow.json)" -o examples/dsl/my_graph --overwrite
```

## 生成项目结构

基础结构如下：

```bash
.
├── README.md
├── .env
├── requirements.txt
├── run_agent.py
├── workflow.json
└── agent
    ├── __init__.py
    ├── agent.py
    ├── callbacks.py
    ├── config.py
    ├── nodes.py
    ├── prompts.py
    ├── state.py
    └── tools.py
```

文件职责：

- workflow.json：输入 DSL 的拷贝
- run_agent.py：本地交互入口
- agent/agent.py：Graph 组装（`add_node` / `add_agent_node` / `add_edge` / `add_conditional_edges`）
- agent/nodes.py：节点函数、路由函数、input_mapper 映射函数
- agent/state.py：WorkflowState 和结构化输出模型（如有）
- agent/config.py：模型与连接参数构造（例如 `env:OPENAI_API_KEY` 解析）
- agent/tools.py：MCP / memory_search / knowledge_search 工具构造
- agent/prompts.py：instruction 常量
- agent/callbacks.py：回调扩展点（默认空实现）

## DSL 结构总览

DSL 定义见：[trpc_agent_sdk/dsl/codegen/dsl_schema.json](../../../trpc_agent_sdk/dsl/codegen/dsl_schema.json)。

## 当前支持的 node_type（Python code generator）

虽然 schema 中定义了更多类型，但当前 Python code generator 可稳定生成的节点类型为：

- builtin.start
- builtin.llmagent
- builtin.end
- builtin.transform
- builtin.code
- builtin.mcp
- builtin.knowledge_search
- builtin.set_state
- builtin.user_approval
- custom.*（会生成带 TODO 的占位函数）

对应的 Graph API：

- builtin.llmagent -> graph.add_agent_node(...)
- builtin.mcp -> graph.add_mcp_node(...)
- builtin.knowledge_search -> graph.add_knowledge_node(...)
- builtin.code -> graph.add_code_node(...)
- 其他节点 -> graph.add_node(...)

## builtin.llmagent 常用配置

builtin.llmagent.config 建议优先关注这些字段：

- model_spec：模型配置（provider / model_name / api_key / base_url / headers）
- instruction：系统提示词，支持 {{...}} 模板引用
- temperature / max_tokens / top_p：生成参数
- output_format：text 或 json；当 json 时需要 schema
- tools：统一工具定义（当前主要支持 mcp / knowledge_search / memory_search）
- mcp_tools：兼容历史 MCP 写法
- user_message：覆盖该节点发送给模型的 user message，支持模板
- skills / executor：Agent Skills 相关配置

当 output_format.type = "json" 时，生成器会在 agent/state.py 中产出对应 Pydantic 模型，并在 LlmAgent(..., output_schema=...) 中挂载。

## 表达式与模板语法

### 1. expr（transform / conditional / end / set_state）

conditional_edges[*].condition.cases[*].predicate.expression 与 builtin.transform.config.expr 使用 CEL 风格子集，支持：

- 引用：state.*、input.output_parsed.*、input.output_text、nodes.<id>.output_parsed.*、nodes.<id>.output_text
- 运算：&&、||、三元 a ? b : c
- 函数：size()、string()、int()、double()、has()
- 字符串包含：xxx.contains("...")

限制：

- 使用 input.* 的表达式时，要求当前节点“恰好一个上游节点”
- builtin.end.config.expr 是更受限子集（只支持 state.* 和 nodes.* 引用）
- builtin.set_state.config.assignments[*].expr 也是受限子集（不支持三元/contains/has/size 等）

### 2. 模板字符串 {{...}}

以下字段支持模板引用：

- builtin.llmagent.config.instruction
- builtin.llmagent.config.user_message
- builtin.knowledge_search.config.query

模板引用同样可使用 state.* / input.* / nodes.*。  
生成器会把它们编译为 Python 取值逻辑；对 user_message 会自动生成 input_mapper，并把映射值写入子状态的 `STATE_KEY_USER_INPUT`。

## DSL 到生成代码示例

下面用现有样例说明“DSL 如何映射为代码”。

### 示例 1：条件路由（分类分流）

DSL（节选，来自 [examples/dsl/classifier_mcp/workflow.json](../../../examples/dsl/classifier_mcp/workflow.json)）：

```json
{
  "from": "classifier",
  "condition": {
    "cases": [
      {
        "predicate": {
          "expression": "input.output_parsed.classification == \"math_simple\"",
          "format": "cel"
        },
        "target": "simple_math_agent"
      }
    ]
  }
}
```

生成代码（节选，来自 examples/dsl/classifier_mcp/agent/nodes.py 和 agent/agent.py）：

```python
def route_by_classification(state: WorkflowState) -> str:
    if state[STATE_KEY_NODE_RESPONSES]['classifier']['classification'] == "math_simple":
        return 'simple_math_agent'
    raise ValueError('No conditional case matched for route from classifier')

graph.add_conditional_edges(NODE_ID_CLASSIFIER, route_by_classification)
```

### 示例 2：user_message 覆盖与 input_mapper

DSL（节选，来自 [examples/dsl/user_message_override/workflow.json](../../../examples/dsl/user_message_override/workflow.json)）：

```json
{
  "id": "echo_agent",
  "node_type": "builtin.llmagent",
  "config": {
    "user_message": "{{input.output_parsed.overridden_user_message}}"
  }
}
```

生成代码（节选）：

```python
def map_input_echo_agent(state: WorkflowState) -> dict[str, Any]:
    child_state = dict(state)
    child_state[STATE_KEY_USER_INPUT] = str(
        state[STATE_KEY_NODE_RESPONSES]['build_message']['overridden_user_message']
    )
    return child_state

graph.add_agent_node(
    NODE_ID_ECHO_AGENT,
    _create_echo_agent(),
    input_mapper=map_input_echo_agent,
    config=NodeConfig(name=NODE_ID_ECHO_AGENT, description='Echo agent'),
)
```

### 示例 3：builtin.mcp 节点映射

DSL（节选，来自 [examples/dsl/mcp_node/workflow.json](../../../examples/dsl/mcp_node/workflow.json)）：

```json
{
  "id": "mcp_weather",
  "node_type": "builtin.mcp",
  "config": {
    "mcp": {
      "transport": "sse",
      "server_url": "http://.../mcp"
    },
    "function": "add"
  }
}
```

生成代码（节选）：

```python
graph.add_mcp_node(
    NODE_ID_MCP_WEATHER,
    create_mcp_toolset_mcp_weather(),
    selected_tool_name='add',
    req_src_node='prepare_request',
    config=NodeConfig(name=NODE_ID_MCP_WEATHER, description='Call MCP add'),
)
```

## 常见注意事项

- `builtin.llmagent.config.model_spec.provider` 当前仅支持 `openai`
- `knowledge_search.connector.type` 当前仅支持 `trag`
- `builtin.code.config.executor_type` 当前仅支持 `local`
- `builtin.user_approval` 只能通过 `config.routing` 决策后续节点，不能再配置该节点的显式 `edges` / `conditional_edges`
- `state_variables` 不能与内置状态键冲突（如 `user_input`、`node_responses` 等）
- schema 中部分字段即使可写，当前 code generator 也可能尚未映射到生成代码；建议优先参考 `examples/dsl/*/workflow.json`

## 推荐阅读顺序

1. 先看 [examples/dsl/README.md](../../../examples/dsl/README.md)
2. 再看一个最小样例，如 [examples/dsl/memory_agent/workflow.json](../../../examples/dsl/memory_agent/workflow.json)
3. 看分支路由样例：[examples/dsl/classifier_mcp/workflow.json](../../../examples/dsl/classifier_mcp/workflow.json)
4. 看 RAG 编排样例：[examples/dsl/knowledge_search/workflow.json](../../../examples/dsl/knowledge_search/workflow.json)
5. 对照同目录下 `agent/*.py` 查看“DSL -> 代码”的最终落地
