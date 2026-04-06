# LLM Agent 模型创建函数示例

本示例演示如何将 `LlmAgent.model` 配置为“模型创建函数”，并在运行时通过 `RunConfig.custom_data` 动态向模型工厂传入上下文参数。

## 关键特性

- **动态模型创建**：`model` 字段可传入异步工厂函数，而非固定模型实例
- **运行时参数注入**：通过 `RunConfig.custom_data` 向模型创建函数传递业务数据
- **可观测创建行为**：示例会打印模型工厂接收到的 `custom_data`
- **保持业务链路不变**：模型动态创建后，工具调用与回答流程与普通 `LlmAgent` 一致
- **便于多租户/分层策略**：可按 `custom_data` 决定模型路由、参数或鉴权策略

## Agent 层级结构说明

本例是单 Agent 示例，不涉及多 Agent 分层路由：

```text
weather_agent (LlmAgent)
├── model: create_model(custom_data)  # async factory
├── tool: get_weather_report(city)
└── run_config.custom_data: {"user_tier": "premium"}
```

关键文件：

- [examples/llmagent_with_model_create_fn/agent/agent.py](./agent/agent.py)：定义 `create_model(custom_data)` 并注入 `LlmAgent`
- [examples/llmagent_with_model_create_fn/agent/tools.py](./agent/tools.py)：天气工具实现
- [examples/llmagent_with_model_create_fn/agent/prompts.py](./agent/prompts.py)：提示词
- [examples/llmagent_with_model_create_fn/agent/config.py](./agent/config.py)：环境变量读取
- [examples/llmagent_with_model_create_fn/run_agent.py](./run_agent.py)：运行入口，传递 `RunConfig.custom_data`

## 关键代码解释

这一节用于快速定位“custom_data 如何进入模型工厂”。

### 1) 模型工厂定义（`agent/agent.py`）

- 定义 `async def create_model(custom_data: dict) -> LLMModel`
- 在函数内读取配置并打印 `custom_data`
- 返回 `OpenAIModel(...)`

### 2) Agent 绑定工厂函数（`agent/agent.py`）

- `LlmAgent(model=create_model, ...)`
- 框架在运行时调用模型工厂，而不是使用静态模型对象

### 3) 运行时传参（`run_agent.py`）

- 构造 `RunConfig(custom_data={"user_tier": "premium"})`
- 通过 `runner.run_async(..., run_config=run_config)` 传入
- 在控制台验证模型工厂收到该参数

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

在 [examples/llmagent_with_model_create_fn/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/llmagent_with_model_create_fn
python3 run_agent.py
```

## 运行结果（实测）

```text
📝 User: What's the weather like in Beijing today?
🤖 Assistant: 📦 Model creation function received custom_data: {'user_tier': 'premium'}

🔧 [Tool: get_weather_report({'city': 'Beijing'})]
📊 [Result: {'temperature': '25°C', 'condition': 'Sunny', 'humidity': '60%'}]
The weather in Beijing today is sunny with a temperature of 25°C. The humidity is at 60%. It's a great day to enjoy the outdoors!
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **custom_data 透传成功**：模型创建函数打印出 `{'user_tier': 'premium'}`
- **模型工厂生效**：说明运行时确实调用了工厂函数而非静态模型
- **业务链路正常**：后续工具调用与天气回答正常完成
- **功能目标达成**：验证了“运行时动态建模 + 业务执行”可同时成立

## 适用场景建议

- 需要按请求上下文动态选择模型或参数：适合使用本示例
- 需要将用户等级、租户信息透传到模型层：适合使用本示例
- 仅验证单 Agent 工具调用主链路：建议使用 `examples/llmagent`
