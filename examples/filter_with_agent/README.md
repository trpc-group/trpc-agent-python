# Filter + Callback 拦截处理示例

本示例演示如何基于框架的 `Filter` 和 `Callback` 能力，在 Agent 调用前后进行拦截处理，并验证 `Filter 链路 + before/after Callback + 流式事件` 的核心链路是否正常工作。

## 关键特性

- **Filter 拦截能力**：通过 `BaseFilter` + `@register_agent_filter` 注册 Agent 级别的 Filter，拦截 `run_stream` 事件流的启动、逐条事件、结束三个阶段
- **Callback 前后钩子**：通过 `before_agent_callback` / `after_agent_callback` 在 Agent 执行前后注入自定义逻辑
- **工具调用能力**：通过 `FunctionTool` 接入天气查询工具函数，支持实时天气查询
- **会话状态管理**：使用 `InMemorySessionService` 保存每轮请求的状态，验证状态变量能被 Agent 读取
- **流式事件处理**：通过 `runner.run_async(...)` 处理 partial/full event，并打印工具调用与工具返回

## Agent 层级结构说明

本例是单 Agent 示例，重点展示 Filter 与 Callback 的拦截机制：

```text
weather_agent (LlmAgent)
├── model: OpenAIModel
├── tools:
│   └── get_weather_report(city)
├── filters_name: ["agent_filter"]  ← AgentFilter (run_stream 拦截)
├── before_agent_callback            ← 执行前钩子
├── after_agent_callback             ← 执行后钩子
└── session: InMemorySessionService (state 注入 user_name / user_city)
```

关键文件：

- [examples/filter_with_agent/agent/agent.py](./agent/agent.py)：构建 `LlmAgent`、挂载工具、注册 Filter 与 Callback
- [examples/filter_with_agent/agent/filter.py](./agent/filter.py)：`AgentFilter` 实现与 `before/after_agent_callback` 定义
- [examples/filter_with_agent/agent/tools.py](./agent/tools.py)：天气查询工具实现
- [examples/filter_with_agent/agent/prompts.py](./agent/prompts.py)：提示词模板
- [examples/filter_with_agent/agent/config.py](./agent/config.py)：环境变量读取
- [examples/filter_with_agent/run_agent.py](./run_agent.py)：测试入口，执行 2 轮对话

## 关键代码解释

这一节用于快速定位"Filter 注册与拦截、Callback 前后钩子、事件输出"三条核心链路。

### 1) Filter 注册与拦截（`agent/filter.py`）

- 使用 `@register_agent_filter("agent_filter")` 将 `AgentFilter` 注册为命名 Filter
- 在 `run_stream` 中拦截事件流：打印 start → 逐条 yield event 并打印 → 打印 end
- 通过 `event.is_continue` 判断事件流是否提前终止

### 2) Callback 前后钩子（`agent/filter.py` + `agent/agent.py`）

- `before_agent_callback`：Agent 执行前触发，接收 `InvocationContext`，可用于鉴权、日志、参数改写等
- `after_agent_callback`：Agent 执行后触发，接收 `InvocationContext`，可用于结果审计、后处理等
- 在 `LlmAgent` 构造时通过 `before_agent_callback` / `after_agent_callback` 参数挂载

### 3) Agent 组装与参数配置（`agent/agent.py`）

- 使用 `LlmAgent` 组装天气助手，挂载 `FunctionTool(get_weather_report)`
- 通过 `filters_name=["agent_filter"]` 关联已注册的 Filter
- 同时设置 `before_agent_callback` 和 `after_agent_callback` 两个 Callback 钩子

## 环境与运行

### 环境要求

- Python 3.12

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 环境变量要求

在 [examples/filter_with_agent/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/filter_with_agent
python3 run_agent.py
```

## 运行结果（实测）

```text
🆔 Session ID: edf37bc9...
📝 User: What's the weather like today?
🤖 Assistant:

==== run agent filter run_stream start ===
@before_agent_callback context: <class 'trpc_agent_sdk.context._invocation_context.InvocationContext'>


==== run agent filter run_stream event ===
Could

==== run agent filter run_stream event ===
 you please specify

==== run agent filter run_stream event ===
 the city for

==== run agent filter run_stream event ===
 which you'd

==== run agent filter run_stream event ===
 like to know

==== run agent filter run_stream event ===
 the weather?

==== run agent filter run_stream event ===
@after_agent_callback context: <class 'trpc_agent_sdk.context._invocation_context.InvocationContext'>


==== run agent filter run_stream end ===

----------------------------------------
🆔 Session ID: faeee227...
📝 User: What's the current weather in Beijing?
🤖 Assistant: @before_agent_callback context: <class 'trpc_agent_sdk.context._invocation_context.InvocationContext'>


==== run agent filter run_stream start ===


==== run agent filter run_stream event ===

🔧 [Invoke Tool: get_weather_report({'city': 'Beijing'})]


==== run agent filter run_stream event ===
📊 [Tool Result: {'temperature': '25°C', 'condition': 'Sunny', 'humidity': '60%'}]


==== run agent filter run_stream event ===
The

==== run agent filter run_stream event ===
 current weather in

==== run agent filter run_stream event ===
 Beijing is sunny

==== run agent filter run_stream event ===
 with a temperature

==== run agent filter run_stream event ===
 of 25

==== run agent filter run_stream event ===
°C and

==== run agent filter run_stream event ===
 humidity

==== run agent filter run_stream event ===
 at 60

==== run agent filter run_stream event ===
%.

==== run agent filter run_stream event ===


==== run agent filter run_stream end ===
@after_agent_callback context: <class 'trpc_agent_sdk.context._invocation_context.InvocationContext'>

----------------------------------------
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **Filter 拦截正确**：每轮请求均输出 `run_stream start` → 逐条 `run_stream event` → `run_stream end`，事件流完整
- **Callback 触发正确**：`before_agent_callback` 在 Agent 执行前触发，`after_agent_callback` 在 Agent 执行后触发，均正确接收 `InvocationContext`
- **工具路由正确**：第 1 轮未指定城市时 Agent 主动追问，第 2 轮正确调用 `get_weather_report` 查询北京天气
- **工具结果被正确消费**：回复内容与工具返回数据一致，并能组织为可读答案

说明：该示例每轮使用新的 `session_id`，因此主要验证的是 Filter 拦截与 Callback 钩子的执行顺序，不强调跨轮记忆一致性。

## 适用场景建议

- 快速验证 Filter 拦截 + Callback 前后钩子的执行链路：适合使用本示例
- 需要在 Agent 调用前后增加鉴权、日志、审计等横切逻辑：适合参考本示例
- 验证单 Agent + Tool Calling 主链路：建议使用 `examples/llmagent`
- 需要测试多 Agent 分支隔离行为：建议使用 `examples/llmagent_with_branch_filtering`
