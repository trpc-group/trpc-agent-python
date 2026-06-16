# LLM Agent 模型重试示例

本示例演示如何在模型构造时传入 `ModelRetryConfig`，让业务代码不用自己实现重试逻辑；当 LLM 请求遇到限流、超时、网络波动等瞬时错误时，SDK 会自动重试。

## 关键特性

- **按需开启重试**：只有显式传入 `ModelRetryConfig` 的模型才会启用 SDK 托管重试。
- **一套配置，多种模型可用**：同一个 `ModelRetryConfig` 可以传给 OpenAI、Anthropic、LiteLLM 等模型实现，统一控制重试次数和指数退避。
- **Provider 自己判断是否重试**：OpenAI、Anthropic、LiteLLM 会按各自 SDK 的错误语义、响应 header 和 HTTP status 判断是否重试。
- **避免重复输出内容**：流式输出已经产生内容后如果再失败，错误会直接透出，不会重试并重复输出内容。
- **优先使用服务端等待时间**：默认会读取 `Retry-After` / `retry-after-ms`，有服务端等待时间时优先使用。

## Agent 层级结构说明

本例是单 Agent 示例，重试配置绑定在模型上：

```text
weather_retry_agent (LlmAgent)
├── model: OpenAIModel(..., model_retry_config=ModelRetryConfig(...))
├── tool: get_weather_report(city)
└── runner: 无自定义重试逻辑
```

关键文件：

- [examples/llmagent_with_model_retry/agent/agent.py](./agent/agent.py)：创建模型并注入 `ModelRetryConfig`
- [examples/llmagent_with_model_retry/agent/config.py](./agent/config.py)：读取模型连接与重试环境变量
- [examples/llmagent_with_model_retry/agent/tools.py](./agent/tools.py)：天气工具实现
- [examples/llmagent_with_model_retry/agent/prompts.py](./agent/prompts.py)：提示词
- [examples/llmagent_with_model_retry/run_agent.py](./run_agent.py)：运行入口，展示业务层无需手写重试

## 关键代码解释

### 1) 创建重试配置

`agent/config.py` 从环境变量构造：

```python
from trpc_agent_sdk.configs import ExponentialBackoffConfig
from trpc_agent_sdk.configs import ModelRetryConfig

ModelRetryConfig(
    num_retries=2,  # 最多重试 2 次（不包含首次请求）
    backoff=ExponentialBackoffConfig(
        initial_backoff=1.0,  # 首次重试前等待 1 秒
        max_backoff=8.0,  # 单次重试等待时间最多 8 秒
        multiplier=2.0,  # 每次失败后等待时间按 2 倍递增
        jitter=True,  # 加入随机抖动，避免并发重试同时打到服务
    ),
)
```

### 2) 注入模型

`agent/agent.py` 将配置传给模型构造器：

```python
OpenAIModel(
    model_name=model_name,
    api_key=api_key,
    base_url=base_url,
    model_retry_config=retry_config,  # 将重试配置注入模型，调用失败时按配置自动重试
)
```

### 3) 配置字段说明

- `num_retries`：初始请求失败后的额外重试次数，不包含第一次请求。
- `initial_backoff`：第一次重试前的基础等待时间，单位秒。
- `max_backoff`：单次等待时间上限，单位秒。
- `multiplier`：指数退避倍数。
- `jitter`：是否启用 full jitter，避免并发请求同时重试。

SDK 会优先尊重 provider 返回的 `Retry-After` / `retry-after-ms` 服务端建议等待时间。

### 4) Runner 不需要重试逻辑

`run_agent.py` 仍然只调用：

```python
async for event in runner.run_async(...):
    ...
```

如果模型调用在产出内容前遇到可重试错误，SDK 会按 `ModelRetryConfig` 自动重试。

## 会重试和不会重试的场景

不同 provider 会按各自 SDK 的错误语义判断异常类型；对于带响应信息的错误，通用优先级如下：

### 会重试

- 响应 header `x-should-retry: true`。
- HTTP status 为 `408` / `409` / `429` / `>=500`。
- OpenAI / Anthropic 相关的超时、连接类瞬时错误。

### 不会重试

- 响应 header `x-should-retry: false`。
- HTTP status 为其他 `4xx` 错误，例如 `400` / `401` / `403` / `404`。
- 重试次数已耗尽。
- 流式输出已经产生内容后才发生的错误。

## 环境与运行

### 环境要求

- Python 3.10+

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 环境变量要求

在 [examples/llmagent_with_model_retry/.env](./.env) 中配置（或通过 `export` 设置）：

```bash
# 必填：模型连接配置
TRPC_AGENT_API_KEY=<your-api-key>
TRPC_AGENT_BASE_URL=<openai-compatible-base-url>
TRPC_AGENT_MODEL_NAME=<model-name>

# 可选：模型重试配置；不设置时使用示例默认值
TRPC_AGENT_MODEL_RETRY_NUM_RETRIES=2
TRPC_AGENT_MODEL_RETRY_INITIAL_BACKOFF=1.0
TRPC_AGENT_MODEL_RETRY_MAX_BACKOFF=8.0
TRPC_AGENT_MODEL_RETRY_BACKOFF_MULTIPLIER=2.0
TRPC_AGENT_MODEL_RETRY_JITTER=true
```

### 运行命令

```bash
cd examples/llmagent_with_model_retry
python3 run_agent.py
```

## 运行结果示例

```text
Model retry enabled: {'num_retries': 2, 'backoff': {'initial_backoff': 1.0, 'max_backoff': 8.0, 'multiplier': 2.0, 'jitter': True}}
Session ID: fdb9e370...
User: What's the current weather in Beijing?
Assistant:
Invoke Tool: get_weather_report({'city': 'Beijing'})
Tool Result: {'temperature': '25°C', 'condition': 'Sunny', 'humidity': '60%'}

# 当模型服务返回可重试错误时，SDK 会在模型层自动重试：
[WARNING] Model call failed (exception=RateLimitError); retrying in 0.46s (attempt 1/2).
```
