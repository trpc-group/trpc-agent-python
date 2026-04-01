# LlmAgent使用模型工厂的示例代码

## 环境要求
Python版本: 3.10+（强烈建议使用3.12）

## 在trpc-agent-python框架代码下如何运行此代码示例

1. 下载trpc-agent-python代码并安装

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent
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
cd examples/llmagent_with_model_factory/
python3 run_agent.py
```

## 模型创建回调

模型创建回调是一个异步函数，可以在每次运行时动态创建模型。通过 `RunConfig.custom_data` 传递的数据会传给此函数。

```python
async def create_model(custom_data: dict) -> LLMModel:
    # custom_data 来自 RunConfig.custom_data
    print(f"Received custom_data: {custom_data}")
    return OpenAIModel(model_name="gpt-4", ...)

# 创建 agent 时传入工厂函数
agent = LlmAgent(model=create_model, ...)

# 创建 runner
runner = Runner(app_name="app", agent=agent, session_service=session_service)

# 通过 run_async 的 run_config 参数传递 custom_data
run_config = RunConfig(custom_data={"user_tier": "premium"})
async for event in runner.run_async(..., run_config=run_config):
    # 处理事件...
```

预期输出如下，将会打印出custom_data：

```text
📝 User: What's the weather like in Beijing today?
🤖 Assistant: 📦 Model creation function received custom_data: {'user_tier': 'premium'}

🔧 [Tool: get_weather_report({'city': 'Beijing'})]
📊 [Result: {'temperature': '25°C', 'condition': 'Sunny', 'humidity': '60%'}]
The weather in Beijing today is sunny with a temperature of 25°C. The humidity is at 60%. It's a pleasant day!
```
