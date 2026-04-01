# LiteLLM 示例

用 LiteLLM 作为模型后端，任意 provider/model 统一一套调用方式。

## 用法

### 用法一：显式创建模型并接入 Agent

```python
from trpc_agent_sdk.models import LiteLLMModel
from trpc_agent_sdk.agents.llm_agent import LlmAgent

model = LiteLLMModel(
    model_name="openai/gpt-4o",   # 必填：provider/model
    api_key="sk-xxx",             # 必填（或设环境变量 OPENAI_API_KEY）
    base_url="https://api.openai.com/v1",  # 可选，自建/代理时必填
)
agent = LlmAgent(name="my_agent", model=model, instruction="...")
```

### 用法二：传模型名，由 Registry 匹配到 LiteLLMModel

不显式 `LiteLLMModel(...)`，只传 **provider/model 字符串**。框架会根据 `ModelRegistry` 的 `supported_models` 正则（如 `openai/.*`、`anthropic/.*`）匹配到 `LiteLLMModel` 并自动创建实例。此时 API Key、base_url 等依赖环境变量（如 `OPENAI_API_KEY`、`OPENAI_API_BASE`），因为构造时只传入模型名。

```python
from trpc_agent_sdk.agents.llm_agent import LlmAgent

# 传入模型名即可，内部会匹配到 LiteLLMModel 并 create_model("openai/gpt-4o")
agent = LlmAgent(name="my_agent", model="openai/gpt-4o", instruction="...")
```

`model_name` 必须为 **provider/model**，例如：`openai/gpt-4o`、`anthropic/claude-3-5-sonnet`、`gemini/gemini-1.5-pro`。其余 kwargs（如 `temperature`、`timeout`）会透传 LiteLLM，见 [LiteLLM 文档](https://docs.litellm.ai/docs/completion)。

### 2. 运行本示例

在 `examples/litellm/` 下创建 `.env`（或在当前 shell 设置环境变量）：

```bash
# TRPC_AGENT_API_KEY、TRPC_AGENT_MODEL_NAME（provider/model）必填；TRPC_AGENT_BASE_URL 可选
TRPC_AGENT_API_KEY=sk-xxx
TRPC_AGENT_MODEL_NAME=openai/gpt-4o
# TRPC_AGENT_BASE_URL=https://api.openai.com/v1
```

然后执行：

```bash
cd examples/litellm
python run_agent.py
```

| 变量 | 必填 | 说明 |
|------|------|------|
| `TRPC_AGENT_API_KEY` | 是 | API Key |
| `TRPC_AGENT_MODEL_NAME` | 是 | provider/model，如 `openai/gpt-4o` |
| `TRPC_AGENT_BASE_URL` | 否 | 自建/代理 base URL |

### 3. 示例结构

- **创建模型**：`agent/config.py` 读环境变量，`agent/agent.py` 里 `LiteLLMModel(...)` + `LlmAgent(..., model=...)`
- **跑对话**：`run_agent.py` 里 `Runner` + `runner.run_async(...)` 流式收回复
