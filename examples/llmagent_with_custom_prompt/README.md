# LLM Agent 自定义提示词注入示例

本示例演示如何通过 `add_name_to_instruction` 与 `default_transfer_message` 两个参数，控制框架对 system instruction 的自动注入行为，并验证多 Agent 路由效果是否稳定。

## 关键特性

- **可控的名称注入**：通过 `add_name_to_instruction` 决定是否自动加上 Agent 名称前缀
- **可控的转发指令注入**：通过 `default_transfer_message` 使用默认、关闭或自定义转发提示
- **多场景对比验证**：同一组用户请求在 3 种配置下运行并对比输出
- **路由行为可观测**：打印实际发送给 LLM 的 system instruction，直观看到注入差异
- **业务结果一致性**：不同注入策略下仍能正确路由到天气与翻译子 Agent

## Agent 层级结构说明

本例是“协调 Agent + 两个子 Agent”的多 Agent 路由示例：

```text
Coordinator (LlmAgent)
├── WeatherAssistant (LlmAgent + get_weather_report)
└── TranslationAssistant (LlmAgent + translate_text)
```

关键文件：

- [examples/llmagent_with_custom_prompt/agent/agent.py](./agent/agent.py)：Agent 构建与注入参数配置
- [examples/llmagent_with_custom_prompt/agent/prompts.py](./agent/prompts.py)：基础提示词与自定义转发模板
- [examples/llmagent_with_custom_prompt/agent/tools.py](./agent/tools.py)：天气和翻译工具
- [examples/llmagent_with_custom_prompt/agent/config.py](./agent/config.py)：环境变量读取
- [examples/llmagent_with_custom_prompt/run_agent.py](./run_agent.py)：三种场景对比测试入口

## 关键代码解释

这一节用于快速定位“提示词注入行为差异”如何产生。

### 1) 注入参数入口（`agent/agent.py`）

- `add_name_to_instruction=True`：自动注入 `You are an agent who's name is [xxx].`
- `add_name_to_instruction=False`：不注入名称前缀
- `default_transfer_message=None`：使用框架默认转发提示
- `default_transfer_message=<custom>`：用自定义转发提示替换默认内容

### 2) 观测点设计（`before_model_callback`）

- 在 `_print_system_instruction` 中打印最终发送给 LLM 的 instruction
- 可直接验证“框架自动注入”和“自定义注入”是否生效

### 3) 三种测试场景（`run_agent.py`）

- **Scenario 1**：默认注入（名称 + 默认转发）
- **Scenario 2**：关闭名称注入（仅保留默认转发）
- **Scenario 3**：名称注入 + 自定义转发提示

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

在 [examples/llmagent_with_custom_prompt/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/llmagent_with_custom_prompt
python3 run_agent.py
```

## 运行结果（实测）

```text
Scenario 1: Default
- Coordinator instruction 含自动名称注入：
  "You are an agent who's name is [Coordinator]."
- 自动注入默认 transfer 指令（列出可转发子 Agent）
- 请求 1 路由到 WeatherAssistant，调用 get_weather_report 成功
- 请求 2 路由到 TranslationAssistant，调用 translate_text 成功

Scenario 2: add_name_to_instruction=False
- Coordinator/子 Agent instruction 不再包含自动名称前缀
- 默认 transfer 指令仍保留
- 天气与翻译两个请求仍正确路由并返回正确结果

Scenario 3: Custom default_transfer_message
- 名称注入保留（add_name_to_instruction=True）
- 默认 transfer 指令被自定义提示替换
- 路由与工具调用行为保持正确
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **注入控制有效**：Scenario 1/2 在名称注入上有明显差异，符合参数设计
- **自定义转发生效**：Scenario 3 的 Coordinator instruction 出现自定义 transfer 文案
- **业务行为稳定**：三种配置下都能正确路由到天气与翻译子 Agent
- **工具调用正确**：`get_weather_report` 与 `translate_text` 的调用和结果均符合用户请求

## 适用场景建议

- 需要精确控制 system prompt 组成：适合使用本示例
- 需要自定义多 Agent 路由提示模板：适合使用本示例
- 只验证单 Agent 基础工具调用：建议使用 `examples/llmagent`
