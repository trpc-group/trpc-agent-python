# DSL Graph 分类路由 + MCP 工具调用示例

本示例演示如何基于 `GraphAgent` + DSL 代码生成，构建一个**分类路由型多 Agent 工作流**，并通过 MCP（SSE 传输）接入远程计算器工具，验证 `Classifier → 条件路由 → Worker Agent + MCP Tool Calling` 的核心链路。

## 关键特性

- **分类路由能力**：Classifier Agent 将用户输入分为 `math_simple`（加减法）与 `math_complex`（乘除法/复合运算），通过条件边路由到不同 Worker Agent
- **MCP 工具调用**：Simple Math Agent 和 Complex Math Agent 均通过 SSE 传输连接远程 MCP Calculator 服务，由模型自主调用工具完成计算
- **结构化输出**：Classifier Agent 使用 `output_schema`（Pydantic BaseModel）约束输出为 JSON 格式，包含 `classification` 和 `reason` 字段
- **DSL 代码生成**：工作流结构由 `workflow.json` 描述，通过 `python -m trpc_agent_dsl.codegen` 生成 Agent 代码骨架
- **流式事件处理**：通过 `runner.run_async(...)` 消费事件流，打印节点生命周期、工具调用、模型执行等全链路可观测信息

## Agent 层级结构说明

本例为多 Agent 分类路由工作流，由 Classifier 根据分类结果条件路由至两个 Worker Agent：

```text
classifier_mcp_example (GraphAgent)
├── start (function node) ─────────────────────── 入口节点
├── classifier (LlmAgent) ─────────────────────── 分类 Agent，输出 JSON 分类结果
│   └── output_schema: {classification, reason}
├── [条件路由] classification == "math_simple" ──► simple_math_agent
│              classification == "math_complex" ──► complex_math_agent
├── simple_math_agent (LlmAgent) ──────────────── 简单数学 Agent（加减法）
│   └── mcp_tools: calculator_sse (SSE transport)
├── complex_math_agent (LlmAgent) ─────────────── 复杂数学 Agent（乘除法）
│   └── mcp_tools: calculator_sse (SSE transport)
├── simple_end (function node) ────────────────── Simple 分支终止
└── complex_end (function node) ───────────────── Complex 分支终止
```

关键文件：

- `examples/dsl/classifier_mcp/agent/agent.py`：构建 `GraphAgent`，组装节点、边与条件路由
- `examples/dsl/classifier_mcp/agent/nodes.py`：节点函数（start/end）与路由函数 `route_func1`
- `examples/dsl/classifier_mcp/agent/state.py`：工作流 State 定义与 Classifier 输出 Schema
- `examples/dsl/classifier_mcp/agent/prompts.py`：各 Agent 提示词
- `examples/dsl/classifier_mcp/agent/tools.py`：MCP 工具创建
- `examples/dsl/classifier_mcp/agent/config.py`：环境变量读取与模型实例化
- `examples/dsl/classifier_mcp/run_agent.py`：交互式测试入口
- `examples/dsl/classifier_mcp/workflow.json`：DSL 工作流描述文件

## 关键代码解释

这一节用于快速定位"分类路由、MCP 工具调用、事件输出"三条核心链路。

### 1) 工作流组装与条件路由（`agent/agent.py` + `agent/nodes.py`）

- 使用 `StateGraph` 组装节点，通过 `add_agent_node` 挂载 3 个 `LlmAgent`（Classifier / Simple / Complex）
- Classifier 输出通过 `output_schema=Llmagent1OutputModel` 约束为结构化 JSON
- 通过 `add_conditional_edges(classifier, route_func1)` 实现分类结果到目标节点的条件路由
- `route_func1` 从 `state[STATE_KEY_NODE_RESPONSES]` 读取 Classifier 的 `classification` 字段完成路由判断

### 2) MCP 工具接入（`agent/tools.py` + `workflow.json`）

- `workflow.json` 中为 `simple_math_agent` 和 `complex_math_agent` 配置了 `mcp_tools`
- 使用 SSE 传输协议连接远程 MCP Calculator 服务
- 工具由 MCP 服务端动态注册，Agent 根据 Instruction 自主决定是否调用

### 3) 流式事件处理与可观测输出（`run_agent.py`）

- 使用 `runner.run_async(...)` 消费事件流
- 通过 `NodeExecutionMetadata.from_event(event)` 打印节点 start/done/error 生命周期
- 通过 `ToolExecutionMetadata.from_event(event)` 打印工具调用参数与返回结果
- 通过 `ModelExecutionMetadata.from_event(event)` 打印模型执行状态
- `event.partial=True` 时打印流式文本分片

## 环境与运行

### 环境要求

- Python 3.10+（强烈建议 3.12）

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 环境变量要求

在 `examples/dsl/classifier_mcp/.env` 中配置（或通过 `export`）：

- `MODEL1_NAME` / `MODEL1_API_KEY` / `MODEL1_BASE_URL`（Classifier Agent 模型）
- `MODEL2_NAME` / `MODEL2_API_KEY` / `MODEL2_BASE_URL`（Simple Math Agent 模型）
- `MODEL3_NAME` / `MODEL3_API_KEY` / `MODEL3_BASE_URL`（Complex Math Agent 模型）
- `MCP1_SERVER_URL`（Simple Math Agent MCP 服务地址）
- `MCP2_SERVER_URL`（Complex Math Agent MCP 服务地址）

### 运行命令

```bash
cd examples/dsl/classifier_mcp
python3 run_agent.py
```

## 运行结果（实测）

```text
Starting graph: classifier_mcp_example
Interactive mode. Type 'exit' to quit, 'new' for new session.
You: hello
Assistant:
  [Node start] node_type=function, node_id=start, node_description=Start
  [Node done ] node_type=function, node_id=start, node_description=Start
  [Node start] node_type=agent, node_id=classifier, node_description=Classifier Agent
  [classifier] {"classification": "math_simple", "reason": "The input 'hello' does not contain any mathematical operations, so it defaults to the simpler category."}
  [Node done ] node_type=agent, node_id=classifier, node_description=Classifier Agent
  [Node start] node_type=agent, node_id=simple_math_agent, node_description=Simple Math Agent
  [simple_math_agent] Hello! How can I assist you with your math questions today? Whether it's addition or subtraction, feel free to ask!
  [Node done ] node_type=agent, node_id=simple_math_agent, node_description=Simple Math Agent
  [Node start] node_type=function, node_id=simple_end, node_description=Simple Math End
  [Node done ] node_type=function, node_id=simple_end, node_description=Simple Math End
  Hello! How can I assist you with your math questions today? Whether it's addition or subtraction, feel free to ask!
You: help me calculate 3 plus 3
Assistant:
  [Node start] node_type=function, node_id=start, node_description=Start
  [Node done ] node_type=function, node_id=start, node_description=Start
  [Node start] node_type=agent, node_id=classifier, node_description=Classifier Agent
  [classifier] {"classification": "math_simple", "reason": "The request 'help me calculate 3 plus 3' involves a simple addition operation."}
  [Node done ] node_type=agent, node_id=classifier, node_description=Classifier Agent
  [Node start] node_type=agent, node_id=simple_math_agent, node_description=Simple Math Agent
  [simple_math_agent] [Function call] add({'a': 3, 'b': 3})
  [simple_math_agent] [Function result] {'result': '{"result":6}'}
  [simple_math_agent] The result of 3 plus 3 is 6. Let me know if you need help with anything else!
  [Node done ] node_type=agent, node_id=simple_math_agent, node_description=Simple Math Agent
  [Node start] node_type=function, node_id=simple_end, node_description=Simple Math End
  [Node done ] node_type=function, node_id=simple_end, node_description=Simple Math End
  The result of 3 plus 3 is 6. Let me know if you need help with anything else!
You: quit
Goodbye!
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **分类路由正确**：`hello` 和加法请求均被分类为 `math_simple`，路由至 Simple Math Agent
- **MCP 工具调用正确**：加法请求触发了 MCP Calculator 的 `add` 工具，参数 `{'a': 3, 'b': 3}` 符合用户意图
- **工具结果被正确消费**：Agent 将工具返回的 `{"result":6}` 组织为自然语言回复
- **节点生命周期完整**：每个节点均有 `start` / `done` 事件，执行顺序符合图结构定义
- **条件路由链路打通**：Classifier → 条件判断 → Worker Agent → End 全链路执行正常

## 适用场景建议

- 验证 DSL 代码生成 + GraphAgent 分类路由主链路：适合使用本示例
- 验证 MCP 工具（SSE 传输）接入与调用：适合使用本示例
- 验证结构化输出（output_schema）驱动条件路由：适合使用本示例
- 需要测试单 Agent + Tool Calling 基础能力：建议使用 `examples/llmagent`
