# Tool Filter & Callback 拦截处理示例

本示例演示如何基于框架的 `Filter` 和 `Callback` 机制，在工具调用前后进行拦截处理，实现日志打印、参数审计等扩展逻辑。

## 关键特性

- **Tool Filter 拦截链**：通过 `@register_tool_filter` 注册自定义 `BaseFilter`，在工具执行前后插入拦截逻辑
- **Callback 钩子**：使用 `before_tool_callback` / `after_tool_callback` 在工具调用前后获取上下文、工具实例、参数及返回值
- **Filter + Callback 组合**：`FunctionTool` 同时挂载 `filters_name` 与 Callback，展示两种拦截机制的协同工作
- **工具调用能力**：通过 `FunctionTool` 接入天气查询工具，验证拦截链不影响正常工具调用流程
- **流式事件处理**：通过 `runner.run_async(...)` 处理 partial/full event，并打印工具调用与工具返回

## Agent 层级结构说明

本例是单 Agent 示例，重点展示 Filter 与 Callback 的拦截链路：

```text
assistant (LlmAgent)
├── model: OpenAIModel
├── tools:
│   └── get_weather_report(city)  [filters_name=["tool_filter"]]
├── before_tool_callback: before_tool_callback()
├── after_tool_callback: after_tool_callback()
└── session: InMemorySessionService
```

关键文件：

- [examples/filter_with_tool/agent/agent.py](./agent/agent.py)：构建 `LlmAgent`，挂载工具并绑定 Filter 与 Callback
- [examples/filter_with_tool/agent/filter.py](./agent/filter.py)：`ToolFilter` 实现与 `before/after_tool_callback` 定义
- [examples/filter_with_tool/agent/tools.py](./agent/tools.py)：天气查询工具实现
- [examples/filter_with_tool/agent/prompts.py](./agent/prompts.py)：提示词模板
- [examples/filter_with_tool/agent/config.py](./agent/config.py)：环境变量读取
- [examples/filter_with_tool/run_agent.py](./run_agent.py)：测试入口，执行 2 轮对话

## 关键代码解释

这一节用于快速定位"Filter 注册、Callback 绑定、拦截链执行"三条核心链路。

### 1) Tool Filter 注册与实现（`agent/filter.py`）

- 使用 `@register_tool_filter("tool_filter")` 装饰器将 `ToolFilter` 注册到框架
- `ToolFilter` 继承 `BaseFilter`，在 `run` 方法中通过 `await handle()` 调用下一层处理，前后分别插入自定义逻辑
- 拦截链采用洋葱模型：`run 前置逻辑 → handle() → run 后置逻辑`

### 2) Callback 钩子定义与绑定（`agent/filter.py` + `agent/agent.py`）

- `before_tool_callback(context, tool, args, response)`：工具调用前触发，可用于参数校验、日志记录
- `after_tool_callback(context, tool, args, response)`：工具调用后触发，`response` 此时包含工具返回值
- 在 `LlmAgent` 构造时通过 `before_tool_callback` / `after_tool_callback` 参数绑定

### 3) FunctionTool 挂载 Filter（`agent/agent.py`）

- `FunctionTool(get_weather_report, filters_name=["tool_filter"])`：将工具与已注册的 Filter 关联
- 工具调用时执行顺序：`before_tool_callback → ToolFilter.run(前置) → 工具执行 → ToolFilter.run(后置) → after_tool_callback`

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

在 [examples/filter_with_tool/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/filter_with_tool
python3 run_agent.py
```

## 运行结果（实测）

```text
🆔 Session ID: fa2f1fd4...
📝 User: What's the weather like today?
🤖 Assistant: Could you please specify the city for which you'd like to know the weather?
----------------------------------------
🆔 Session ID: f19e7aeb...
📝 User: What's the current weather in Beijing?
🤖 Assistant: 
🔧 [Invoke Tool: get_weather_report({'city': 'Beijing'})]


==== run tool filter run start ===
@before_tool_callback context: <class 'trpc_agent_sdk.context._invocation_context.InvocationContext'>, tool: <class 'trpc_agent_sdk.tools._function_tool.FunctionTool'>, args: <class 'dict'>, response: <class 'NoneType'>
@after_tool_callback context: <class 'trpc_agent_sdk.context._invocation_context.InvocationContext'>, tool: <class 'trpc_agent_sdk.tools._function_tool.FunctionTool'>, args: <class 'dict'>, response: <class 'dict'>


==== run tool filter run end ===
📊 [Tool Result: {'temperature': '25°C', 'condition': 'Sunny', 'humidity': '60%'}]
The current weather in Beijing is sunny with a temperature of 25°C and humidity at 60%.
----------------------------------------
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **Filter 拦截正确**：第 2 轮调用工具时，`ToolFilter.run` 的前置/后置日志均正常输出
- **Callback 触发正确**：`before_tool_callback` 在工具执行前打印，`response` 为 `NoneType`；`after_tool_callback` 在工具执行后打印，`response` 为 `dict`
- **拦截链不影响工具调用**：工具正常返回天气数据，Agent 正确消费结果并生成回答
- **无工具调用时不触发**：第 1 轮未触发工具调用，Filter 和 Callback 均未执行，符合预期

## 适用场景建议

- 验证 Tool Filter 拦截链与 Callback 钩子的协同工作：适合使用本示例
- 需要在工具调用前后插入日志、审计、参数校验等扩展逻辑：适合参考本示例
- 需要测试 Model 级别的 Filter 拦截：建议使用 `examples/filter_with_model`
- 需要测试 Agent 级别的 Filter 拦截：建议使用 `examples/filter_with_agent`
