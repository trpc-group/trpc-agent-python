# Model Filter 与 Callback 拦截示例

本示例演示如何使用框架的 `Filter` 与 `Callback` 能力，在 Model 调用前后进行拦截处理，实现对 LLM 请求/响应的可观测与干预。

## 关键特性

- **Model Filter 拦截**：通过 `@register_model_filter` 注册自定义 `ModelFilter`，在流式调用的 `start / event / end` 阶段插入日志，验证 Filter 链路的完整性
- **Before/After Model Callback**：通过 `before_model_callback` 和 `after_model_callback` 分别在 LLM 请求发出前、响应返回后执行自定义逻辑
- **工具调用能力**：通过 `FunctionTool` 接入天气工具函数，验证 Filter 和 Callback 在工具调用场景下的触发行为
- **流式事件处理**：通过 `runner.run_async(...)` 处理 partial/full event，并打印工具调用与工具返回
- **多轮测试覆盖**：覆盖"无工具调用的纯文本回复"与"触发工具调用的天气查询"两类场景

## Agent 层级结构说明

本例是单 Agent 示例，重点展示 Filter + Callback 的拦截机制：

```text
weather_agent (LlmAgent)
├── model: OpenAIModel (filters_name=["model_filter"])
├── tools:
│   └── get_weather_report(city)
├── before_model_callback: before_model_callback
├── after_model_callback: after_model_callback
└── session: InMemorySessionService (state 注入 user_name / user_city)
```

关键文件：

- [examples/filter_with_model/agent/agent.py](./agent/agent.py)：构建 `LlmAgent`、挂载工具、注册 Filter 与 Callback
- [examples/filter_with_model/agent/filter.py](./agent/filter.py)：`ModelFilter` 实现与 `before/after_model_callback` 定义
- [examples/filter_with_model/agent/tools.py](./agent/tools.py)：天气查询工具实现
- [examples/filter_with_model/agent/prompts.py](./agent/prompts.py)：提示词模板
- [examples/filter_with_model/agent/config.py](./agent/config.py)：环境变量读取
- [examples/filter_with_model/run_agent.py](./run_agent.py)：测试入口，执行 2 轮对话

## 关键代码解释

这一节用于快速定位"Filter 注册、Callback 注入、拦截触发"三条核心链路。

### 1) Model Filter 注册与流式拦截（`agent/filter.py`）

- 使用 `@register_model_filter("model_filter")` 装饰器将 `ModelFilter` 注册到框架
- 在 `run_stream` 方法中，通过 `handle()` 获取上游事件流，在 `start`、每个 `event`、`end` 阶段分别打印日志
- 通过 `event.is_continue` 判断流是否结束，实现对流式生命周期的完整拦截

### 2) Before/After Model Callback（`agent/filter.py` + `agent/agent.py`）

- `before_model_callback` 在每次 LLM 请求发送前触发，接收 `InvocationContext` 和 `LlmRequest`，可用于请求审计或修改
- `after_model_callback` 在每次 LLM 响应返回后触发，接收 `InvocationContext` 和 `LlmResponse`，可用于响应审计或过滤
- 在 `agent.py` 中通过 `LlmAgent` 的 `before_model_callback` / `after_model_callback` 参数完成挂载

### 3) Filter 与 Model 的绑定（`agent/agent.py`）

- 在 `OpenAIModel` 初始化时通过 `filters_name=["model_filter"]` 指定要应用的 Filter 名称
- 框架在运行时自动查找已注册的 Filter 并组装到调用链中

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

在 [examples/filter_with_model/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/filter_with_model
python3 run_agent.py
```

## 运行结果（实测）

```text
🆔 Session ID: 5e81eef1...
📝 User: What's the weather like today?
🤖 Assistant:

==== run model filter run_stream start ===
@before_model_callback context: <class 'trpc_agent_sdk.context._invocation_context.InvocationContext'>, llm_request: <class 'trpc_agent_sdk.models._llm_request.LlmRequest'>
@after_model_callback context: <class 'trpc_agent_sdk.context._invocation_context.InvocationContext'>, llm_response: <class 'trpc_agent_sdk.models._llm_response.LlmResponse'>

==== run model filter run_stream event ===
Could you please specify the city for which you'd like to know the weather?

==== run model filter run_stream end ===

----------------------------------------
🆔 Session ID: aa164824...
📝 User: What's the current weather in Beijing?
🤖 Assistant:

==== run model filter run_stream start ===
@before_model_callback context: <class 'trpc_agent_sdk.context._invocation_context.InvocationContext'>, llm_request: <class 'trpc_agent_sdk.models._llm_request.LlmRequest'>
@after_model_callback context: <class 'trpc_agent_sdk.context._invocation_context.InvocationContext'>, llm_response: <class 'trpc_agent_sdk.models._llm_response.LlmResponse'>

==== run model filter run_stream event ===

🔧 [Invoke Tool: get_weather_report({'city': 'Beijing'})]

==== run model filter run_stream end ===
📊 [Tool Result: {'temperature': '25°C', 'condition': 'Sunny', 'humidity': '60%'}]

==== run model filter run_stream start ===
@before_model_callback context: <class 'trpc_agent_sdk.context._invocation_context.InvocationContext'>, llm_request: <class 'trpc_agent_sdk.models._llm_request.LlmRequest'>
@after_model_callback context: <class 'trpc_agent_sdk.context._invocation_context.InvocationContext'>, llm_response: <class 'trpc_agent_sdk.models._llm_response.LlmResponse'>

==== run model filter run_stream event ===
The current weather in Beijing is sunny with a temperature of 25°C and humidity at 60%.

==== run model filter run_stream end ===

----------------------------------------
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **Filter 生命周期完整**：每次 Model 调用均触发 `run_stream start → event(s) → end` 的完整日志链路
- **Callback 触发正确**：`before_model_callback` 在请求前触发，`after_model_callback` 在每次流式响应事件后触发
- **工具调用场景验证**：第 2 轮触发工具调用后，Model 被二次调用以生成最终回复，Filter 和 Callback 在两次调用中均正确触发
- **纯文本场景验证**：第 1 轮未触发工具调用，Filter 和 Callback 在单次 Model 调用中正常工作

说明：该示例每轮使用新的 `session_id`，主要验证 Filter 与 Callback 的拦截能力，不强调跨轮记忆一致性。

## 适用场景建议

- 验证 Model Filter 的注册与流式拦截能力：适合使用本示例
- 验证 Before/After Model Callback 的触发时机：适合使用本示例
- 需要测试 Agent 级别的 Filter 拦截（而非 Model 级别）：建议使用 `examples/filter_with_agent`
