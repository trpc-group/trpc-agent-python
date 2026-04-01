# LLM Agent 基础能力示例

本示例演示如何基于 `LlmAgent` 快速构建一个天气助手，并验证 `Prompt + Tool Calling + Session` 的核心链路是否正常工作。

## 关键特性

- **工具调用能力**：通过 `FunctionTool` 接入天气工具函数，支持实时天气与多日预报查询
- **Prompt 模板注入**：在提示词中使用 `{user_name}`、`{user_city}` 占位符，结合 Session 状态动态生成上下文
- **会话状态管理**：使用 `InMemorySessionService` 保存每轮请求的状态，验证状态变量能被 Agent 读取
- **流式事件处理**：通过 `runner.run_async(...)` 处理 partial/full event，并打印工具调用与工具返回
- **多轮测试覆盖**：同一程序内覆盖“当前天气 + 多日预报 + 新城市天气”三类典型问法

## Agent 层级结构说明

本例是单 Agent 示例，不涉及多 Agent 分层路由：

```text
weather_agent (LlmAgent)
├── model: OpenAIModel
├── tools:
│   ├── get_weather_report(city)
│   └── get_weather_forecast(city, days)
└── session: InMemorySessionService (state 注入 user_name / user_city)
```

关键文件：

- `examples/llmagent/agent/agent.py`：构建 `LlmAgent`、挂载工具、设置生成参数
- `examples/llmagent/agent/tools.py`：天气查询工具实现
- `examples/llmagent/agent/prompts.py`：提示词模板
- `examples/llmagent/agent/config.py`：环境变量读取
- `examples/llmagent/run_agent.py`：测试入口，执行 3 轮对话

## 关键代码解释

这一节用于快速定位“工具调用、提示词注入、事件输出”三条核心链路。

### 1) Agent 组装与参数配置（`agent/agent.py`）

- 使用 `LlmAgent` 组装天气助手，挂载 `FunctionTool(get_weather_report)` 与 `FunctionTool(get_weather_forecast)`
- 通过 `GenerateContentConfig` 设置 `temperature`、`top_p`、`max_output_tokens`
- 使用统一的提示词模板 `INSTRUCTION`，由会话状态驱动动态注入变量

### 2) 提示词与会话变量（`agent/prompts.py` + `run_agent.py`）

- 提示词中使用 `{user_name}`、`{user_city}` 占位符
- 在 `run_agent.py` 中创建会话时写入 state（如 `user_name`、`user_city`）
- 运行时由框架完成状态到提示词变量的映射与渲染

### 3) 流式事件处理与可观测输出（`run_agent.py`）

- 使用 `runner.run_async(...)` 消费事件流
- `event.partial=True` 时打印文本分片
- 完整事件中区分并打印：
  - `function_call`（工具调用）
  - `function_response`（工具返回）

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

在 `examples/llmagent/.env` 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/llmagent
python3 run_agent.py
```

## 运行结果（实测）

```text
🆔 Session ID: 458a95e7...
📝 User: What's the weather like today?
🤖 Assistant:
🔧 [Invoke Tool:: get_weather_report({'city': 'Beijing'})]
📊 [Tool Result: {'temperature': '25°C', 'condition': 'Sunny', 'humidity': '60%'}]
The weather in Beijing today is sunny with a temperature of 25°C and humidity at 60%. It's a great day for outdoor activities, so you might want to enjoy the sunshine! Don't forget to stay hydrated and wear sunscreen if you're planning to be outside for long.
----------------------------------------
🆔 Session ID: 4280b436...
📝 User: What will the weather be like in Shanghai for the next three days?
🤖 Assistant:
🔧 [Invoke Tool:: get_weather_forecast({'city': 'Shanghai', 'days': 3})]
📊 [Tool Result: {'result': [{'date': '2024-01-01', 'temperature': '25°C', 'condition': 'Sunny'}, {'date': '2024-01-02', 'temperature': '23°C', 'condition': 'Cloudy'}, {'date': '2024-01-03', 'temperature': '20°C', 'condition': 'Light rain'}]}]
Here’s the weather forecast for Shanghai for the next three days:

1. **January 1, 2024**:
   - **Temperature**: 25°C
   - **Condition**: Sunny
   - **Suggestion**: Perfect day for outdoor activities. Don’t forget sunscreen!

2. **January 2, 2024**:
   - **Temperature**: 23°C
   - **Condition**: Cloudy
   - **Suggestion**: A bit cooler, but still pleasant. A light jacket might be useful.

3. **January 3, 2024**:
   - **Temperature**: 20°C
   - **Condition**: Light rain
   - **Suggestion**: Carry an umbrella and wear waterproof shoes if heading out.

Let me know if you need more details!
----------------------------------------
🆔 Session ID: 98c42e34...
📝 User: What's the current weather in Guangzhou?
🤖 Assistant:
🔧 [Invoke Tool:: get_weather_report({'city': 'Guangzhou'})]
📊 [Tool Result: {'temperature': '32°C', 'condition': 'Thunderstorm', 'humidity': '85%'}]
The current weather in Guangzhou is 32°C with a thunderstorm and 85% humidity.

**Suggestions:**
- Stay indoors if possible due to the thunderstorm.
- If you must go out, carry an umbrella and wear waterproof clothing.
- Be cautious of potential flooding or slippery roads.

Let me know if you'd like more details or a forecast for the coming days!
----------------------------------------
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **工具路由正确**：当前天气问题调用 `get_weather_report`，多日预报问题调用 `get_weather_forecast`
- **工具参数正确**：第 2 轮中 `days=3`、`city='Shanghai'` 等参数符合用户意图
- **工具结果被正确消费**：回复内容与工具返回数据一致，并能组织为可读答案
- **能力覆盖完整**：3 轮测试分别覆盖“当前天气、预测天气、跨城市查询”三类核心场景

说明：该示例每轮使用新的 `session_id`，因此主要验证的是工具调用与回复质量，不强调跨轮记忆一致性。

## 适用场景建议

- 快速验证单 Agent + Tool Calling 主链路：适合使用本示例
- 验证提示词模板变量注入（基于会话状态）：适合使用本示例
- 需要测试多 Agent 分支隔离行为：建议使用 `examples/llmagent_with_branch_filtering`
