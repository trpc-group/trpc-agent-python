# LiteLLM 统一模型后端示例

本示例演示如何使用 `LiteLLMModel` 作为统一模型后端，通过 `provider/model` 字符串一套调用方式接入任意 LLM 提供商，并以天气助手场景验证 `LiteLLM + Tool Calling + 多模型轮询` 的核心链路。

## 关键特性

- **统一模型接口**：通过 `LiteLLMModel` 封装 LiteLLM，以 `provider/model` 格式（如 `openai/gpt-5.1`、`openai/claude-4-5-sonnet-20250929`）统一调用不同提供商
- **多模型轮询测试**：在 `MODELS_TO_RUN` 列表中配置多个模型，依次创建 Agent 并执行相同查询，便于对比各模型的工具调用与回复质量
- **工具调用能力**：通过 `FunctionTool` 接入天气工具函数，支持实时天气查询
- **Prompt 模板注入**：在提示词中使用 `{user_city}` 占位符，结合 Session 状态动态生成默认城市上下文
- **会话状态管理**：使用 `InMemorySessionService` 保存每轮请求的状态，验证状态变量能被 Agent 读取
- **流式事件处理**：通过 `runner.run_async(...)` 处理 partial/full event，并打印工具调用与工具返回

## Agent 层级结构说明

本例是单 Agent 示例，不涉及多 Agent 分层路由；核心亮点是用 `LiteLLMModel` 替代 `OpenAIModel`，实现多模型统一调用：

```text
weather_agent (LlmAgent)
├── model: LiteLLMModel (provider/model 格式，如 openai/gpt-5.1)
├── tools:
│   └── get_weather_report(city)
└── session: InMemorySessionService (state 注入 user_name / user_city)
```

关键文件：

- [examples/litellm/agent/agent.py](./agent/agent.py)：构建 `LiteLLMModel` 与 `LlmAgent`、挂载工具
- [examples/litellm/agent/tools.py](./agent/tools.py)：天气查询工具实现
- [examples/litellm/agent/prompts.py](./agent/prompts.py)：提示词模板
- [examples/litellm/agent/config.py](./agent/config.py)：环境变量读取与默认模型名配置
- [examples/litellm/run_agent.py](./run_agent.py)：测试入口，遍历多个模型依次执行查询

## 关键代码解释

这一节用于快速定位"LiteLLM 模型创建、工具调用、多模型轮询"三条核心链路。

### 1) LiteLLMModel 创建与 Agent 组装（`agent/agent.py`）

- 通过 `get_model_config()` 读取环境变量中的 `api_key`、`base_url`
- 使用 `LiteLLMModel(model_name=..., api_key=..., api_base=...)` 创建模型实例
- 使用 `LlmAgent` 组装天气助手，挂载 `FunctionTool(get_weather_report)`
- 支持通过 `create_agent(model_name)` 动态指定模型名，便于多模型轮询

### 2) 提示词与会话变量（`agent/prompts.py` + `run_agent.py`）

- 提示词中使用 `{user_city}` 占位符，用户未指定城市时默认使用会话状态中的城市
- 在 `run_agent.py` 中创建会话时写入 state（如 `user_name`、`user_city`）
- 运行时由框架完成状态到提示词变量的映射与渲染

### 3) 多模型轮询与流式事件处理（`run_agent.py`）

- 在 `MODELS_TO_RUN` 列表中配置多个 `provider/model` 字符串
- 循环遍历列表，每次调用 `create_agent(model_name)` 创建新的 Agent 实例
- 使用 `runner.run_async(...)` 消费事件流
- `event.partial=True` 时打印文本分片
- 完整事件中区分并打印：
  - `function_call`（工具调用）
  - `function_response`（工具返回）

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

在 [examples/litellm/.env](./.env) 中配置（或通过 `export`）：

| 变量 | 必填 | 说明 |
|------|------|------|
| `TRPC_AGENT_API_KEY` | 是 | API Key |
| `TRPC_AGENT_BASE_URL` | 否 | 自建/代理 base URL，不填则使用 LiteLLM 默认路由 |
| `TRPC_AGENT_MODEL_NAME` | 否 | 默认模型名（`config.py` 中硬编码为 `openai/glm-4.7`，可通过环境变量覆盖） |

### 运行命令

```bash
cd examples/litellm
python3 run_agent.py
```

## 运行结果（实测）

```text
============================================================
Model: openai/gpt-5.1
============================================================
User: What will the weather be like in Shanghai for the next three days?
Assistant:
🔧 [Invoke Tool:: get_weather_report({'city': 'Shanghai'})]
📊 [Tool Result: {'temperature': '28°C', 'condition': 'Cloudy', 'humidity': '70%'}]
The current weather in Shanghai is 28°C and cloudy with 70% humidity. ...
----------------------------------------
============================================================
Model: openai/claude-4-5-sonnet-20250929
============================================================
User: What will the weather be like in Shanghai for the next three days?
Assistant:
🔧 [Invoke Tool:: get_weather_report({'city': 'Shanghai'})]
📊 [Tool Result: {'temperature': '28°C', 'condition': 'Cloudy', 'humidity': '70%'}]
Based on the weather data, Shanghai currently has a temperature of 28°C with cloudy skies and 70% humidity. ...
----------------------------------------
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **统一调用正确**：多个 `provider/model` 均能通过 `LiteLLMModel` 统一调用，无需切换模型类
- **工具路由正确**：天气问题正确调用 `get_weather_report`，参数 `city='Shanghai'` 符合用户意图
- **工具结果被正确消费**：回复内容与工具返回数据一致，并能组织为可读答案
- **多模型对比有效**：不同模型的工具调用行为一致，回复风格各异，便于横向比较

说明：该示例每个模型使用独立的 `session_id`，主要验证的是 LiteLLM 统一调用与工具调用的正确性，不强调跨轮记忆一致性。

## 适用场景建议

- 快速验证 LiteLLM 统一模型后端 + Tool Calling 主链路：适合使用本示例
- 需要横向对比多个 LLM 提供商/模型的工具调用能力与回复质量：适合使用本示例
- 需要测试单模型深度能力（多轮对话 + 会话记忆）：建议使用 `examples/llmagent`
