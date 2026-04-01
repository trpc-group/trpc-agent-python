# LLM Agent 禁用框架自动注入提示词示例

本示例演示如何使用 `add_name_to_instruction` 和 `default_transfer_message` 参数控制框架对提示词的自动注入行为。

## 关键特性

- **`add_name_to_instruction`**：控制框架是否在 instruction 中自动注入 Agent 名称（`"You are an agent who's name is [name]."`）
- **`default_transfer_message`**：控制框架在配置 `sub_agents` 时是否自动注入子 Agent 相关的转发指令
- **多场景对比**：本示例同时运行三种配置，直观展示不同设置对 Agent 行为的影响

## Agent 层级结构

```
Coordinator - 客户服务协调员，路由请求到子 Agent
├── WeatherAssistant - 天气查询助手
└── TranslationAssistant - 翻译助手
```

## 三种测试场景

| 场景 | add_name_to_instruction | default_transfer_message | 说明 |
|------|------------------------|-------------------------|------|
| 1 | True (默认) | None (默认) | 框架完全自动注入 |
| 2 | False | None (默认) | 禁用名称注入，保留转发指令注入 |
| 3 | True (默认) | 自定义字符串 | 保留名称注入，使用自定义转发指令 |

## 环境要求

Python版本: 3.10+（强烈建议使用3.12）

## 在trpc-agent-python框架代码下如何运行此代码示例

1. 下载trpc-agent-python代码并安装

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

2. 运行此代码示例

在 `.env` 文件中设置使用 LLM 相关的变量（也可以通过export设置）:
- TRPC_AGENT_API_KEY
- TRPC_AGENT_BASE_URL
- TRPC_AGENT_MODEL_NAME

然后运行下面的命令：

```bash
cd examples/llmagent_with_custom_prompt/
python3 run_agent.py
```

## 关键代码解释

### 1. 创建带自定义提示词配置的 Agent（agent/agent.py）

```python
from trpc_agent_sdk.agents import LlmAgent

coordinator = LlmAgent(
    name="Coordinator",
    model=model,
    instruction=COORDINATOR_INSTRUCTION,
    sub_agents=[weather_assistant, translation_assistant],
    # 禁用自动注入 Agent 名称到 instruction 中
    add_name_to_instruction=False,
    # 使用自定义转发指令替代框架默认注入
    default_transfer_message=CUSTOM_TRANSFER_MESSAGE,
)
```

### 2. 参数配置说明

#### add_name_to_instruction

| 值 | 行为 |
|----|------|
| True (默认) | 框架自动在 instruction 前注入 `"You are an agent who's name is [name]."` |
| False | 不注入，instruction 原样传递给 LLM |

#### default_transfer_message

| 值 | 行为 |
|----|------|
| None (默认) | 框架自动注入子 Agent 的转发指令 |
| 空字符串 `""` | 禁用框架自动注入，不添加任何转发指令 |
| 自定义字符串 | 使用自定义内容替代框架默认注入的转发指令 |

## 适用场景

| 场景 | 推荐配置 |
|------|---------|
| 需要完全控制提示词 | `add_name_to_instruction=False` + `default_transfer_message=""` |
| 自定义转发逻辑 | `default_transfer_message="自定义指令"` |
| 使用框架默认行为 | 不设置这两个参数（使用默认值） |
| 多语言提示词 | `add_name_to_instruction=False`，手动在 instruction 中设置名称 |
