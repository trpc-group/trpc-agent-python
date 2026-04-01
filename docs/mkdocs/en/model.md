# tRPC-Agent Model

## Overview

tRPC-Agent provides multiple model integration methods, currently supporting the OpenAI protocol (OpenAIModel), the Anthropic protocol (AnthropicModel), and LiteLLMModel for unified multi-vendor access via LiteLLM. If you need to integrate additional models, feel free to contact us.

Models in tRPC-Agent have the following core features:

- **Multi-protocol support**: Provides OpenAIModel, AnthropicModel, LiteLLMModel, etc., compatible with most OpenAI-like and Anthropic interfaces both internally and externally
- **Streaming response support**: Supports streaming output for real-time interactive experiences
- **Multimodal capabilities**: Supports multimodal content processing including text and images (e.g., Hunyuan multimodal models)
- **Extensible configuration**: Supports custom configuration options such as GenerateContentConfig, HttpOptions, and client_args to meet diverse use case requirements

## Quick Start

In tRPC-Agent, a Model serves as the "brain" of an LlmAgent and is passed in via the `model` parameter. After creating a model instance (e.g., `OpenAIModel`, `AnthropicModel`, `LiteLLMModel`), pass it to an `LlmAgent` to use the model in conversations and tool calls.

### Environment Variable Configuration

```bash
# API key for the model provider
export TRPC_AGENT_API_KEY="your-api-key"
# Base URL of the model provider's API (e.g., custom proxy or private deployment address)
export TRPC_AGENT_BASE_URL="your-base-url"
# Model name, e.g., deepseek-chat
export TRPC_AGENT_MODEL_NAME="your-model-name"
```

### Creating Model and Agent

```python
from trpc_agent.agents import LlmAgent
from trpc_agent.models import LLMModel
from trpc_agent.models import OpenAIModel
from trpc_agent.tools import FunctionTool

from .prompts import INSTRUCTION
from .tools import get_weather_report
from .config import get_model_config


def _create_model() -> LLMModel:
    """ Create a model instance """
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent() -> LlmAgent:
    """ Create an LlmAgent """
    agent = LlmAgent(
        name="assistant",  # Agent name
        description="A helpful assistant for conversation",
        model=_create_model(),  # Pass in the initialized model
        instruction=INSTRUCTION, # System instruction to constrain the model's role and behavior
        tools=[FunctionTool(get_weather_report)],  # Pass in tools
    )
    return agent


root_agent = create_agent()
```

For a more complete example, see the repository's [Quick Start](../../../examples/quickstart/run_agent.py).


The following sections describe usage by protocol type:

## OpenAIModel

Most LLMs currently provide access via the OpenAI protocol. Use the `OpenAIModel` class to create a model instance:

- Obtain the model name, API key, and base URL from various model providers, corresponding to the `model_name`, `api_key`, and `base_url` parameters of the class
- Provider-specific fields can be configured via the `GenerateContentConfig` method

Below are the base URLs and model names for some model providers:

**1. OpenAI Official**

- Base URL: `https://api.openai.com/v1`
- Model names: `gpt-4o`, `gpt-4o-mini`, etc.

**2. DeepSeek**

- Base URL: e.g., `https://api.deepseek.com/v1`
- Model names: `deepseek-chat`, `deepseek-reasoner`, etc.

**3. Tencent Hunyuan**

- Base URL: `https://api.hunyuan.cloud.tencent.com/v1`
- Model names: `hunyuan-t1-latest`, `hunyuan-t1-vision-20250619`, etc.

**4. Other Providers**

- **Qwen**: Base URL e.g., `https://dashscope.aliyuncs.com/compatible-mode/v1`, Model names: various Qwen models

### Configuration Methods

#### Via Environment Variables

```bash
# API key for the model provider
export TRPC_AGENT_API_KEY="your-api-key"
# Base URL of the model provider's API (e.g., custom proxy or private deployment address)
export TRPC_AGENT_BASE_URL="your-base-url"
# Model name, e.g., deepseek-chat, gpt-4o
export TRPC_AGENT_MODEL_NAME="your-model-name"
```

#### Via Code

```python
from trpc_agent.models import OpenAIModel

model = OpenAIModel(
    model_name="deepseek-chat",
    api_key="your-api-key",
    base_url="https://api.deepseek.com/v1",
)
```

### Integration with Various Platform Model Services:

#### Hunyuan Model Invocation

```python
from trpc_agent.models import OpenAIModel

LlmAgent(
    ...,
    model=OpenAIModel(
        model_name="hunyuan-t1-latest",
        api_key="your-api-key", # Replace with your actual key
        base_url="https://api.hunyuan.cloud.tencent.com/v1",
    )
)
```

#### Hunyuan Multimodal Model Invocation

For image modality, ensure the `mime_type` is correctly specified (e.g., `image/png`, `image/jpeg`). The framework automatically encodes the raw image bytes to base64 and constructs the `data:{mime_type};base64,{base64_data}` format as the `image_uri`, so you only need to pass in the raw image content without manual base64 encoding or `image_uri` construction.

```python
LlmAgent(
    ...,
    model=OpenAIModel(
        model_name="hunyuan-t1-vision-20250619",
        api_key="your-api-key", # Replace with your actual key
        base_url="https://api.hunyuan.cloud.tencent.com/v1",
        # Add custom headers
        client_args={ "default_headers": {"Accept": "*/*", "Content-Type": "application/json"}},
    ),
)

query_text = "Please describe this image"

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

AnthropicModel is used to integrate with Anthropic-compatible platforms such as Claude. To directly use external model services like Claude, you can connect via the Anthropic protocol, which is already supported by the framework. Use the `AnthropicModel` class to create a model instance.

### Configuration Methods

#### Via Environment Variables

```bash
# API key for the model provider
export TRPC_AGENT_API_KEY="your-api-key"
# Base URL of the model provider's API (e.g., custom proxy or private deployment address)
export TRPC_AGENT_BASE_URL="your-base-url"
# Model name, e.g., claude-3-5-sonnet-20241022
export TRPC_AGENT_MODEL_NAME="your-model-name"
```

#### Via Code

Using Zhipu AI as an example:

```python
from trpc_agent.models import AnthropicModel

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
As multiple LLM providers have emerged, some define their own API specifications. Currently, the framework has integrated OpenAI and Anthropic APIs as described above. However, differences in instantiation methods and configuration options across providers mean that developers often need to modify significant amounts of code when switching providers, increasing the switching cost.
To address this, tRPC-Agent supports unified multi-vendor model access via [LiteLLM](https://docs.litellm.ai/), using the **provider/model** format (e.g., `openai/gpt-4o`, `anthropic/claude-3-5-sonnet`, `gemini/gemini-1.5-pro`) to switch between different backends with a single calling convention. LiteLLMModel extends OpenAIModel, overriding only the API call path to `litellm.acompletion`, thereby simplifying the complexity of provider switching.

### Configuration via Environment Variables

```bash
# API key for the model provider
export TRPC_AGENT_API_KEY="your-api-key"
# Base URL of the model provider's API (e.g., custom proxy or private deployment address)
export TRPC_AGENT_BASE_URL="your-base-url"
# Specify the model in provider/model format, e.g., openai/gpt-4o, anthropic/claude-3-5-sonnet
export TRPC_AGENT_MODEL_NAME="your-model-name"
```

### Explicit Model Creation via Code

```python
from trpc_agent.models import LiteLLMModel
from trpc_agent.agents.llm_agent import LlmAgent

model = LiteLLMModel(
    model_name="openai/gpt-4o",   # Required: provider/model
    api_key="your-api-key",             # Required (or set env var TRPC_AGENT_API_KEY)
    base_url="https://api.openai.com/v1",  # Optional; required for self-hosted/proxy setups
)
LlmAgent(..., model=model, instruction="...")
```

### Model Name Matching via Registry

Instead of explicitly instantiating `LiteLLMModel`, you can simply pass a model name string; the framework uses regex matching against `ModelRegistry`'s `supported_models` to resolve the model name to a LiteLLMModel instance. In this case, the API key, base_url, and other settings rely on environment variables (e.g., `OPENAI_API_KEY`, `OPENAI_API_BASE`).

```python
from trpc_agent.agents.llm_agent import LlmAgent

LlmAgent(..., model="openai/gpt-4o", instruction="...")
```

### Multi-Vendor Examples

| Vendor | model_name Example | Environment Variable (Optional) |
|--------|-------------------|--------------------------------|
| OpenAI | `openai/gpt-4o` | `OPENAI_API_KEY` |
| Anthropic | `anthropic/claude-3-5-sonnet` | `ANTHROPIC_API_KEY` |
| Google Gemini | `gemini/gemini-1.5-pro` | `GEMINI_API_KEY` |
| Self-hosted/Proxy | `openai/gpt-4o` | Requires `base_url` |

For more usage and running examples, see [examples/litellm](../../../examples/litellm/README.md).


## Core Design
### LLMModel Class

All concrete model implementations (e.g., `OpenAIModel`, `AnthropicModel`, `LiteLLMModel`) extend `LLMModel`.

```python
class LLMModel(FilterRunner):
    """Abstract base class for all model implementations."""

    def __init__(self, model_name: str, filters_name: Optional[list[str]] = None, **kwargs):
        # Extract the model filter list from optional arguments
        filters: list = kwargs.get("filters", [])
        # Initialize the parent class FilterRunner and attach filters to the current model
        super().__init__(filters_name=filters_name, filters=filters)
        # Store the model name, e.g., deepseek-chat, gpt-4o, etc.
        self._model_name = model_name
        # Store remaining initialization arguments for concrete implementations to read as needed
        self.config = kxwargs
        # Mark the current FilterRunner type as model
        self._type = FilterType.MODEL
        # Read the API key from arguments
        self._api_key: str = kwargs.get(const.API_KEY, "")
        # Read the model service base URL from arguments
        self._base_url: str = kwargs.get(const.BASE_URL, "")
```

### Request Structure

The request structure consists of `RequestABC` and its subclass `LlmRequest`. The base fields are defined in `RequestABC`, and the fields directly related to model invocation include the model name, message contents, generation configuration, and the tools dictionary.

```python
class RequestABC(BaseModel):
    model: Optional[str] = None
    """Model name."""

    contents: list[Content] = Field(default_factory=list)
    """Message contents sent to the model."""

    config: Optional[GenerateContentConfig] = None
    """Additional configuration for the generation request."""

    live_connect_config: LiveConnectConfig = LiveConnectConfig()
    """Additional configuration for live connection scenarios.

    Tools should not be set in generate_content_config.
    """

    tools_dict: dict[str, Any] = Field(default_factory=dict, exclude=True)
    """Dictionary of tool instances."""
```

Building on this, `LlmRequest` adds fields related to streaming tool calls and provides several helper method implementations:

```python
class LlmRequest(RequestABC):
    streaming_tool_names: Optional[Set[str]] = None
    """Set of tool names that should receive streaming arguments."""

    def append_instructions(self, instructions: list[str]) -> None:
        """Append system instructions to the request."""
        ...

    def append_tools(self, tools: list[Any]) -> None:
        """Append tools available for model invocation to the request."""
        ...

    def set_output_schema(self, base_model: type[BaseModel]) -> None:
        """Set the Pydantic schema for structured output."""
        ...
```

### Response Structure

The response structure defines common fields via `ResponseABC`, while `LlmResponse` is responsible for converting the underlying `GenerateContentResponse` into a unified framework response.

```python
class ResponseABC(BaseModel):
    content: Optional[Content] = None
    """Response content."""

    grounding_metadata: Optional[GroundingMetadata] = None
    """Grounding metadata in the response."""

    partial: Optional[bool] = None
    """Indicates whether the current text content is a fragment within an incomplete stream."""

    turn_complete: Optional[bool] = None
    """Indicates whether the current model response has completed."""

    error_code: Optional[str] = None
    """Error code; values may vary across different models."""

    error_message: Optional[str] = None
    """Error message."""

    interrupted: Optional[bool] = None
    """Indicates whether the model generation process was interrupted."""

    custom_metadata: Optional[dict[str, Any]] = None
    """Custom metadata attached to the LlmResponse."""

    usage_metadata: Optional[GenerateContentResponseUsageMetadata] = None
    """Usage statistics for the LlmResponse."""

    response_id: Optional[str] = None
    """Response ID returned by the model API."""
```

## Advanced Features
### Streaming Output

The Runner returns events in streaming mode by default. When `event.partial` is True, it indicates a token streamed from the LLM. Streaming can be disabled via `run_config=RunConfig(streaming=False)`.

```python
from trpc_agent.configs import RunConfig

async for event in runner.run_async(
    user_id=user_id,
    session_id=session_id,
    new_message=user_content,
    run_config=RunConfig(streaming=False), # Disable streaming
):
    ...
```

### Advanced Parameter Configuration

Use `GenerateContentConfig` to adjust LLM generation behavior, such as temperature, top_p, max_output_tokens, etc.:

```python
from trpc_agent.types import GenerateContentConfig

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


### Custom HTTP Headers

Pass additional headers via `client_args`'s `default_headers` or `GenerateContentConfig`'s `HttpOptions`, suitable for gateways, proprietary platforms, or proxy environments. For example:

```python
OpenAIModel(
    model_name="deepseek-chat",
    base_url="...",
    api_key="...",
    client_args={"default_headers": {"X-Custom-Header": "custom-value", "X-Request-ID": "req-123"}},
)
```
