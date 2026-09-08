# LLM Agent 模型额外字段上报示例

本示例演示如何通过 `OpenAIModel.response_metadata_extractor`，从 OpenAI
兼容服务的响应中提取厂商扩展字段，并通过 `LlmResponse.custom_metadata`
传递给业务代码和 tracing 链路。

## 关键特性

- **显式白名单提取**：业务通过回调决定允许上报哪些厂商字段，避免透传完整原始响应。
- **兼容流式与非流式响应**：回调接收响应的完整 `model_dump()` 字典；流式模式只读取第一个事件。
- **稳定的元数据命名空间**：结果保存在
  `custom_metadata["provider_response_metadata"]` 中。
- **失败不影响模型调用**：回调返回 `None`、非字典、不可 JSON 序列化数据或抛出异常时，SDK 会忽略该元数据。
- **自动进入 tracing**：提取结果会作为 provider response metadata 上报到模型调用 span。

## Agent 层级结构说明

本例是单 Agent 示例，额外字段提取器绑定在模型上：

```text
weather_agent (LlmAgent)
├── model: OpenAIModel(..., response_metadata_extractor=_extract_some_field)
├── tool: get_weather_report(city)
└── runner: 从 Event.custom_metadata 读取并打印厂商元数据
```

关键文件：

- [agent/agent.py](./agent/agent.py)：定义白名单提取器并注入 `OpenAIModel`
- [agent/config.py](./agent/config.py)：读取模型连接环境变量
- [agent/tools.py](./agent/tools.py)：天气工具实现
- [agent/prompts.py](./agent/prompts.py)：Agent 提示词
- [run_agent.py](./run_agent.py)：运行入口，读取并打印 provider metadata

## 关键代码解释

这里的 `some_marker` `some_field` 表示厂商返回的额外字段，具体视模型本身返回的真实数据为例，这里只是一个描述

### 1) 定义厂商字段提取器

```python
def _extract_some_field(
    response_data: dict[str, Any],
) -> dict[str, Any] | None:
    some_marker = response_data.get("some_marker")
    if not isinstance(some_marker, dict):
        return None
    some_field = some_marker.get("some_field")
    if not isinstance(some_field, str) or not some_field:
        return None
    return {
        "some_marker": {
            "some_field": some_field,
        },
    }
```

`response_data` 是 OpenAI SDK 响应对象的完整 `model_dump()` 结果。回调只返回
允许进入业务事件和 tracing 的字段，并可在这里统一转换命名格式。

> 流式模式只会使用第一个响应事件执行一次提取，因此兼容服务需要在首个事件中携带厂商字段。

### 2) 将提取器注入模型

`agent/agent.py` 将回调传给 `OpenAIModel`：

```python
OpenAIModel(
    model_name=model_name,
    api_key=api_key,
    base_url=base_url,
    response_metadata_extractor=_extract_some_field,
)
```

### 3) 从事件中读取元数据

```python
from trpc_agent_sdk.models import PROVIDER_RESPONSE_METADATA

provider_metadata = (event.custom_metadata or {}).get(
    PROVIDER_RESPONSE_METADATA
)
```

本例的一次天气问答包含两次模型调用：第一次模型选择天气工具，第二次模型根据
工具结果生成最终回答。因此运行输出中会看到两个不同的 `some_field`。

## 环境要求

- Python3.10+，推荐 Python3.12

## 构建步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
./build.sh
source .venv/bin/activate
```

## 运行步骤

### 配置环境变量

在 [`.env`](./.env) 中配置（或通过 `export` 设置）：

```bash
TRPC_AGENT_API_KEY=<your-api-key>
TRPC_AGENT_BASE_URL=<openai-compatible-base-url>
TRPC_AGENT_MODEL_NAME=<model-name>
```

模型服务需要返回提取器识别的扩展字段。本例期望响应中包含：

```json
{
  "some_marker": {
    "some_field": "<provider-span-id>"
  }
}
```

### 运行命令

```bash
cd examples/llmagent_with_model_extra_fields
python3 run_agent.py
```

## 运行结果示例

```text
Session ID: 926d3fa3...
User: What's the current weather in Beijing?
Assistant: 
Provider metadata:  {"some_marker": {"some_field": "d3f87b419c8e871c"}}

Invoke Tool: get_weather_report({'city': 'Beijing'})
Tool Result: {'temperature': '25°C', 'condition': 'Sunny', 'humidity': '60%'}
Assistant: It's currently **25°C and sunny** in Beijing.
Provider metadata:  {"some_marker": {"some_field": "5ca6786f4a74f5b1"}}
```

`some_field` 由模型服务生成，每次运行都会不同。
