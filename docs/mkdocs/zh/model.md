# tRPC-Agent 模型调用

## 概述

tRPC-Agent 提供多种模型接入方式，目前支持 OpenAI 协议（OpenAIModel）、Anthropic 协议（AnthropicModel）、以及通过 LiteLLM 统一多厂商的 LiteLLMModel。若有其他模型接入需求，欢迎联系我们。

tRPC-Agent 内的模型具有以下核心特性：

- **多协议支持**：提供 OpenAIModel、AnthropicModel、LiteLLMModel 等，兼容公司内外多数 OpenAI-like 及 Anthropic 接口
- **流式响应支持**：支持流式输出，实现实时交互体验
- **多模态能力**：支持文本、图像等多模态内容处理（如 hunyuan 多模态模型）
- **Prompt Cache 支持**：支持跨 OpenAI、Anthropic 与 LiteLLM 路由的统一 prompt cache 配置，降低长提示词和多轮会话的重复输入成本
- **模型重试支持**：支持在模型层配置重试，SDK 将在限流等异常发生时自动重试，并按指数退避策略进行退避
- **可扩展配置**：支持 GenerateContentConfig、HttpOptions、client_args 等自定义配置项，满足不同场景需求

## 快速上手

在 tRPC-Agent 中，Model 作为 LlmAgent 的「大脑」通过 `model` 参数传入。创建好模型实例（如 `OpenAIModel`、`AnthropicModel`、`LiteLLMModel`）后，将其传给 `LlmAgent` 即可在对话与工具调用中使用该模型。

### 环境变量配置

```bash
# 模型供应商的 API 密钥
export TRPC_AGENT_API_KEY="your-api-key"
# 模型供应商的 API 基础地址（如自定义代理或私有化部署地址）
export TRPC_AGENT_BASE_URL="your-base-url"
# 模型名称，例如 deepseek-chat
export TRPC_AGENT_MODEL_NAME="your-model-name"
```

### 创建模型与 Agent

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

from .prompts import INSTRUCTION
from .tools import get_weather_report
from .config import get_model_config


def _create_model() -> LLMModel:
    """ 创建模型实例 """
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent() -> LlmAgent:
    """ 创建 LlmAgent """
    agent = LlmAgent(
        name="assistant",  # agent 名称
        description="A helpful assistant for conversation",
        model=_create_model(),  # 把上面初始化好的模型传入
        instruction=INSTRUCTION, # 系统指令，用于约束模型的角色和行为
        tools=[FunctionTool(get_weather_report)],  # 传入工具
    )
    return agent


root_agent = create_agent()
```

更加完整示例见代码仓库[快速开始](../../../examples/quickstart/run_agent.py)。


下面按照协议种类介绍使用方式：

## OpenAIModel

目前大部分的LLM基本都提供了OpenAI协议的接入方式，采用`OpenAIModel`类构建模型实例:

- 可从各种模型提供商获取模型名称、API 密钥和基础 URL，分别对应类构造参数中的 `model_name`、`api_key`、`base_url`
- 涉及模型特有参数时，可通过 `GenerateContentConfig` 进行配置

以下列举一些模型提供商的base_url和模型名称：

**1. OpenAI 官方**

- 基础 URL：`https://api.openai.com/v1`
- 模型名称：`gpt-4o`、`gpt-4o-mini` 等

**2. DeepSeek**

- 基础 URL：如 `https://api.deepseek.com/v1`
- 模型名称：`deepseek-chat`、`deepseek-reasoner`等

**3. 腾讯混元**

- 基础 URL：`https://api.hunyuan.cloud.tencent.com/v1`
- 模型名称：`hunyuan-t1-latest`、`hunyuan-t1-vision-20250619` 等

**4. 其他提供商**

- **Qwen**：基础 URL 如 `https://dashscope.aliyuncs.com/compatible-mode/v1`，模型名称：各种 Qwen 模型

### 配置方式

#### 环境变量方式

```bash
# 模型供应商的 API 密钥
export TRPC_AGENT_API_KEY="your-api-key"
# 模型供应商的 API 基础地址（如自定义代理或私有化部署地址）
export TRPC_AGENT_BASE_URL="your-base-url"
# 模型名称，例如 deepseek-chat、gpt-4o
export TRPC_AGENT_MODEL_NAME="your-model-name"
```

#### 代码方式

```python
from trpc_agent_sdk.models import OpenAIModel

model = OpenAIModel(
    model_name="deepseek-chat",
    api_key="your-api-key",
    base_url="https://api.deepseek.com/v1",
)
```

### 各个平台模型服务的对接方式：

#### hunyuan模型调用方式

```python
from trpc_agent_sdk.models import OpenAIModel

LlmAgent(
    ...,
    model=OpenAIModel(
        model_name="hunyuan-t1-latest",
        api_key="your-api-key", # 替换为实际的key
        base_url="https://api.hunyuan.cloud.tencent.com/v1",
    )
)
```

#### hunyuan 多模态模型调用方式

对于图像模态，请正确填写 `mime_type`（如 `image/png`、`image/jpeg`）。框架会自动把传入的图片原始 bytes 编码为 base64，并拼接成 `data:{mime_type};base64,{base64_data}` 这种 `image_uri` 格式，因此你只需要传入原始图片内容，不需要手动做 base64 编码或自己拼接 `image_uri`。

```python
LlmAgent(
    ...,
    model=OpenAIModel(
        model_name="hunyuan-t1-vision-20250619",
        api_key="your-api-key", # 替换为实际的key
        base_url="https://api.hunyuan.cloud.tencent.com/v1",
        # 增加头部
        client_args={ "default_headers": {"Accept": "*/*", "Content-Type": "application/json"}},
    ),
)

query_text = "请你介绍一下这张图片"

image_path = "your-image-path"
with open(image_path, "rb") as f:
            image_data = f.read()

user_content = Content(
    parts=[
         Part.from_text(text=query_text),
         Part.from_bytes(data=image_data, mime_type="image/png")
    ]
)

runner.run_async(xxx, new_message=user_content)
```

## AnthropicModel

AnthropicModel 用于对接 Claude 等 Anthropic 兼容平台。若需直接使用 Claude 等外部模型服务，可通过 Anthropic 协议接入；框架已支持该协议，采用 `AnthropicModel` 类构建模型实例。

### 配置方式

#### 环境变量方式

```bash
# 模型供应商的 API 密钥
export TRPC_AGENT_API_KEY="your-api-key"
# 模型供应商的 API 基础地址（如自定义代理或私有化部署地址）
export TRPC_AGENT_BASE_URL="your-base-url"
# 模型名称，例如 claude-3-5-sonnet-20241022
export TRPC_AGENT_MODEL_NAME="your-model-name"
```

#### 代码方式

以智谱大模型为例：

```python
from trpc_agent_sdk.models import AnthropicModel

LlmAgent(
    ...,
    model=AnthropicModel(
        model_name=os.environ.get("TRPC_AGENT_MODEL_NAME", "glm-4.6"),
        api_key=os.environ.get("TRPC_AGENT_API_KEY", ""),
        base_url=os.environ.get("TRPC_AGENT_BASE_URL", "https://open.bigmodel.cn/api/anthropic"),
    ),
)
```

## LiteLLMModel
随着多个大模型供应商的出现，一些供应商定义了各自的 API 规范。目前，框架已接入 OpenAI 和 Anthropic 的 API（如上文所述），然而，不同供应商在实例化方式和配置项上存在差异，开发者在切换供应商时往往需要修改大量代码，增加了切换成本。
为了解决这一问题，tRPC-Agent 支持通过 [LiteLLM](https://docs.litellm.ai/) 统一接入多厂商模型，使用 **provider/model** 格式（如 `openai/gpt-4o`、`anthropic/claude-3-5-sonnet`、`gemini/gemini-1.5-pro`），一套调用方式切换不同后端。LiteLLMModel 继承 OpenAIModel，仅覆盖 API 调用路径为 `litellm.acompletion`，从而简化了供应商切换的复杂度。

### 环境变量方式配置

```bash
# 模型供应商的 API 密钥
export TRPC_AGENT_API_KEY="your-api-key"
# 模型供应商的 API 基础地址（如自定义代理或私有化部署地址）
export TRPC_AGENT_BASE_URL="your-base-url"
# 使用 provider/model 格式指定模型，例如 openai/gpt-4o、anthropic/claude-3-5-sonnet
export TRPC_AGENT_MODEL_NAME="your-model-name"
```

### 代码方式显式创建模型

```python
from trpc_agent_sdk.models import LiteLLMModel
from trpc_agent_sdk.agents import LlmAgent

model = LiteLLMModel(
    model_name="openai/gpt-4o",   # 必填：provider/model
    api_key="sk-xxx",             # 必填（或设环境变量 TRPC_AGENT_API_KEY）
    base_url="https://api.openai.com/v1",  # 可选，自建/代理时必填
)
LlmAgent(..., model=model, instruction="...")
```

### 传入模型名由 Registry 匹配

不显式实例化 `LiteLLMModel`，只传模型名字符串；框架根据 `ModelRegistry` 的 `supported_models` 正则匹配到 LiteLLMModel 并创建实例。此时 API Key、base_url 等依赖环境变量（如 `OPENAI_API_KEY`、`OPENAI_API_BASE`）。

```python
from trpc_agent_sdk.agents import LlmAgent

LlmAgent(..., model="openai/gpt-4o", instruction="...")
```

### 多厂商示例

| 厂商 | model_name 示例 | 环境变量（可选） |
|------|------------------|------------------|
| OpenAI | `openai/gpt-4o` | `OPENAI_API_KEY` |
| Anthropic | `anthropic/claude-3-5-sonnet` | `ANTHROPIC_API_KEY` |
| Google Gemini | `gemini/gemini-1.5-pro` | `GEMINI_API_KEY` |
| 自建/代理 | `openai/gpt-4o` | 需设 `base_url` |

更多用法与运行示例见 [examples/litellm](../../../examples/litellm/README.md)。


## 核心设计
### LLMModel 类

所有具体模型实现（如 `OpenAIModel`、`AnthropicModel`、`LiteLLMModel`）都继承`LLMModel`。

```python
class LLMModel(FilterRunner):
    """所有模型实现的抽象基类。"""

    def __init__(self, model_name: str, filters_name: Optional[list[str]] = None, **kwargs):
        # 从可选参数中取出模型过滤器列表
        filters: list = kwargs.get("filters", [])
        # 初始化父类 FilterRunner，并把过滤器挂到当前模型上
        super().__init__(filters_name=filters_name, filters=filters)
        # 保存模型名，例如 deepseek-chat、gpt-4o 等
        self._model_name = model_name
        # 保存其余初始化参数，供具体模型实现按需读取
        self.config = kwargs
        # 标记当前 FilterRunner 的类型为模型
        self._type = FilterType.MODEL
        # 从参数中读取 API Key
        self._api_key: str = kwargs.get(const.API_KEY, "")
        # 从参数中读取模型服务的基础 URL
        self._base_url: str = kwargs.get(const.BASE_URL, "")
```

### Request 结构

请求结构由 `RequestABC` 和其子类 `LlmRequest` 组成。基础字段定义在 `RequestABC` 中，和模型调用直接相关的内容包括模型名、消息内容、生成配置以及工具字典。

```python
class RequestABC(BaseModel):
    model: Optional[str] = None
    """模型名称。"""

    contents: list[Content] = Field(default_factory=list)
    """发送给模型的消息内容。"""

    config: Optional[GenerateContentConfig] = None
    """生成请求的附加配置。"""

    live_connect_config: LiveConnectConfig = LiveConnectConfig()
    """实时连接场景下的附加配置。

    不应在 generate_content_config 中设置 tools。
    """

    tools_dict: dict[str, Any] = Field(default_factory=dict, exclude=True)
    """工具实例字典。"""
```

在此基础上，`LlmRequest` 增加了流式工具调用相关字段，并给出了几个辅助方法实现：

```python
class LlmRequest(RequestABC):
    streaming_tool_names: Optional[Set[str]] = None
    """需要接收流式参数的工具名集合。"""

    def append_instructions(self, instructions: list[str]) -> None:
        """向请求中追加系统指令。"""
        ...

    def append_tools(self, tools: list[Any]) -> None:
        """向请求中追加可供模型调用的工具。"""
        ...

    def set_output_schema(self, base_model: type[BaseModel]) -> None:
        """设置结构化输出对应的 Pydantic Schema。"""
        ...
```

### Response 结构

响应结构由 `ResponseABC` 定义通用字段，`LlmResponse` 负责把底层 `GenerateContentResponse` 转成框架统一响应。

```python
class ResponseABC(BaseModel):
    content: Optional[Content] = None
    """响应内容。"""

    grounding_metadata: Optional[GroundingMetadata] = None
    """响应中的 grounding 元数据。"""

    partial: Optional[bool] = None
    """表示当前文本内容是否为未完成流中的一个片段。"""

    turn_complete: Optional[bool] = None
    """表示当前模型响应是否已经结束。"""

    error_code: Optional[str] = None
    """错误码，不同模型的取值可能不同。"""

    error_message: Optional[str] = None
    """错误信息。"""

    interrupted: Optional[bool] = None
    """表示模型生成过程是否被中断。"""

    custom_metadata: Optional[dict[str, Any]] = None
    """LlmResponse 上附带的自定义元数据。"""

    usage_metadata: Optional[GenerateContentResponseUsageMetadata] = None
    """LlmResponse 的用量统计信息。"""

    response_id: Optional[str] = None
    """模型 API 返回的响应 ID。"""
```

## 高级特性
### 流式输出

Runner 默认以流式方式返回事件，`event.partial` 为 True 时表示 LLM 流式返回的 Token。可通过 `run_config=RunConfig(streaming=False)` 关闭流式。

```python
from trpc_agent_sdk.configs import RunConfig

async for event in runner.run_async(
    user_id=user_id,
    session_id=session_id,
    new_message=user_content,
    run_config=RunConfig(streaming=False), # 关闭流式
):
    ...
```

### 高级参数配置

通过 `GenerateContentConfig` 调整 LLM 的生成行为，如 temperature、top_p、max_output_tokens 等：

```python
from trpc_agent_sdk.types import GenerateContentConfig

LlmAgent(
    name="weather_agent",
    model=OpenAIModel(...),
    instruction="...",
    tools=[weather_tool],
    generate_content_config=GenerateContentConfig(
        temperature=0.1,
        top_p=0.95,
        max_output_tokens=1000,
    ),
)
```

### Prompt Cache

Prompt Cache 适用于系统提示词较长、工具定义较多或多轮会话前缀高度稳定的场景。很多 provider（包括 `openai/sglang` 这类 OpenAI 兼容推理服务）本身已经支持服务端自动前缀缓存。`tRPC-Agent` 并不替代 provider 的缓存实现，而是提供统一的缓存管理提示与缓存观测能力。

`tRPC-Agent` 通过 `PromptCacheConfig` 暴露这些能力，目前可用于 `OpenAIModel`、`AnthropicModel` 以及带 provider 前缀的 `LiteLLMModel`。不同供应商对缓存控制和统计字段的支持不完全相同，SDK 会尽量将管理选项和缓存用量指标映射到对应协议：

| Provider | SDK 能力 | 典型统计字段 |
|----------|----------|--------------|
| Anthropic | 根据 `breakpoints` 管理显式 `cache_control` 断点 | `cache_read_input_tokens`、`cache_creation_input_tokens` |
| OpenAI / OpenAI 兼容端点 | 在支持时传递 `prompt_cache_key` / `prompt_cache_retention` 等缓存提示；缓存创建和命中仍由 provider 侧自动前缀缓存负责 | 通常只有 `cache_read_input_tokens` |
| LiteLLM | 根据 `provider/model` 前缀选择 Anthropic 风格或 OpenAI 风格的缓存管理路径 | 取决于最终路由的 provider |

#### 模型级配置

模型级配置会作为该模型实例默认的 prompt cache 管理与观测配置，适合在所有请求中复用同一套缓存提示：

```python
from trpc_agent_sdk.configs import PromptCacheConfig
from trpc_agent_sdk.models import OpenAIModel

model = OpenAIModel(
    model_name="gpt-4o",
    api_key="your-api-key",
    prompt_cache_config=PromptCacheConfig(
        enabled=True,
        ttl="24h",
        prompt_cache_key="weather-concierge-v1",
    ),
)
```

#### 单次运行覆盖

也可以通过 `RunConfig.prompt_cache` 对单次 `runner.run_async()` 覆盖 prompt cache 配置。单次运行配置会按字段覆盖模型级配置，适合按用户、租户或业务场景设置不同的缓存提示：

```python
from trpc_agent_sdk.configs import PromptCacheConfig
from trpc_agent_sdk.configs import RunConfig

async for event in runner.run_async(
    user_id=user_id,
    session_id=session_id,
    new_message=user_content,
    run_config=RunConfig(
        prompt_cache=PromptCacheConfig(
            enabled=True,
            prompt_cache_key="weather-concierge-user-42",
        ),
    ),
):
    ...
```

#### Anthropic 断点配置

Anthropic 风格的缓存需要选择断点位置。`breakpoints` 支持以下值：

- `"system"`：缓存系统提示词，适合长 instruction 场景
- `"tools"`：缓存最后一个工具定义，适合工具较多或工具 schema 较大的场景
- `"messages"`：缓存最近一条 assistant 消息，适合多轮会话中不断增长的稳定历史前缀

```python
from trpc_agent_sdk.configs import PromptCacheConfig
from trpc_agent_sdk.models import AnthropicModel

model = AnthropicModel(
    model_name="claude-3-5-sonnet-20241022",
    api_key="your-api-key",
    prompt_cache_config=PromptCacheConfig(
        enabled=True,
        ttl="1h",
        breakpoints=["tools", "system", "messages"],
    ),
)
```

建议从 `["tools", "system"]` 开始；当长多轮会话需要缓存不断增长的历史前缀时，再加入 `"messages"`。部分 Anthropic 代理或 Bedrock 路由对最小缓存块大小有要求，如果提示词过短，可能不会产生缓存写入。

#### LiteLLM 路由

使用 `LiteLLMModel` 时，模型名需要带 `provider/model` 前缀。SDK 会根据 provider 前缀选择对应的缓存管理映射，例如：

```python
from trpc_agent_sdk.configs import PromptCacheConfig
from trpc_agent_sdk.models import LiteLLMModel

model = LiteLLMModel(
    model_name="openai/gpt-4o",
    api_key="your-api-key",
    prompt_cache_config=PromptCacheConfig(
        enabled=True,
        prompt_cache_key="shared-prefix-v1",
    ),
)
```

如果模型名缺少 provider 前缀，SDK 无法判断应使用哪类缓存管理协议，因此 SDK 管理的缓存提示可能不会生效。

#### 读取缓存统计

模型响应的 `usage_metadata` 中会尽量归一化缓存统计字段：

```python
async for event in runner.run_async(...):
    usage = getattr(event, "usage_metadata", None)
    if usage:
        print(usage.cache_read_input_tokens)      # 从缓存读取的输入 token 数
        print(usage.cache_creation_input_tokens)  # 写入缓存的输入 token 数，通常仅 Anthropic 上报
        print(usage.prompt_token_count)           # 总输入 token 数
```

不同模型服务上报的字段并不完全一致。OpenAI 兼容端点通常只上报缓存读取，不上报缓存写入；负载均衡代理场景下，不同后端实例的 KV 缓存可能尚未全部预热，因此命中率可能在前几次运行中波动。

完整可运行示例见 [examples/llmagent_with_prompt_cache](../../../examples/llmagent_with_prompt_cache/README.md)。

### 模型重试

模型重试适用于 LLM 请求遇到限流、超时、网络抖动、临时服务不可用等瞬时错误的场景。通过在模型构造时传入 `ModelRetryConfig`，可以让 SDK 在模型层统一处理重试，业务代码和 Runner 调用无需自己实现重试循环。

目前 `OpenAIModel`、`AnthropicModel` 和 `LiteLLMModel` 均支持模型重试。该能力默认关闭，只有显式传入 `model_retry_config` 时才会启用。

#### 基本用法

```python
from trpc_agent_sdk.configs import ExponentialBackoffConfig
from trpc_agent_sdk.configs import ModelRetryConfig
from trpc_agent_sdk.models import OpenAIModel

model = OpenAIModel(
    model_name="deepseek-chat",
    api_key="your-api-key",
    base_url="https://api.deepseek.com/v1",
    model_retry_config=ModelRetryConfig(
        num_retries=2,  # 初始请求失败后的额外重试次数，不包含第一次请求
        backoff=ExponentialBackoffConfig(
            initial_backoff=1.0,  # 第一次重试前的基础等待时间，单位秒
            max_backoff=8.0,  # 单次重试等待时间上限，单位秒
            multiplier=2.0,  # 指数退避倍数
            jitter=True,  # 是否启用 full jitter，避免并发请求同时重试
        ),
    ),
)
```

#### 配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `num_retries` | `2` | 初始请求失败后的额外重试次数，不包含第一次请求；设置为 `0` 表示不额外重试 |
| `backoff.initial_backoff` | `1.0` | 第一次重试前的基础等待时间，单位秒 |
| `backoff.max_backoff` | `10.0` | 单次重试等待时间上限，单位秒 |
| `backoff.multiplier` | `2.0` | 指数退避倍数，例如第 1、2、3 次重试前分别按 `initial_backoff * multiplier^attempt` 计算 |
| `backoff.jitter` | `True` | 是否启用 full jitter；开启后会在 `0` 到当前退避时间之间随机等待，避免并发请求同时重试 |

如果 provider 返回 `Retry-After` 或 `retry-after-ms`，且等待时间在合理范围内，SDK 会优先使用服务端建议的等待时间；否则使用指数退避配置计算等待时间。

#### 重试判定

SDK 会结合响应 header、HTTP status 和 provider SDK 的异常语义判断是否重试。通用优先级如下：

- `x-should-retry: true` 会强制视为可重试，`x-should-retry: false` 会强制视为不可重试。
- HTTP status `408`、`409`、`429` 和 `>=500` 通常视为可重试。
- 其他 `4xx` 错误（如 `400`、`401`、`403`、`404`）通常不会重试。
- OpenAI / Anthropic 的超时、连接类异常通常视为可重试。
- `LiteLLMModel` 优先使用 LiteLLM 归一化异常上的 header 和 status 信息做判断。

#### 流式输出注意事项

为了避免重复输出内容，模型重试只会发生在本次模型调用尚未产出用户可见内容（文本或工具调用）之前。如果流式响应已经产出部分内容后又发生异常，SDK 会直接返回最终错误响应，而不会重放整个请求。

完整可运行示例见 [examples/llmagent_with_model_retry](../../../examples/llmagent_with_model_retry/README.md)。

### 自定义 HTTP Header

通过 `client_args` 的 `default_headers` 或 `GenerateContentConfig` 的 `HttpOptions` 传递额外头部，适用于网关、专有平台或代理环境。例如：

```python
OpenAIModel(
    model_name="deepseek-chat",
    base_url="...",
    api_key="...",
    client_args={"default_headers": {"X-Custom-Header": "custom-value", "X-Request-ID": "req-123"}},
)
```
