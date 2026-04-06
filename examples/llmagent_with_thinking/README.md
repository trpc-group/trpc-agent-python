# LlmAgent 思考能力与工具调用示例

本示例演示在启用思考相关能力时，`LlmAgent` 结合天气工具完成多城市查询与多日预报。

## 关键特性

- 多轮独立会话（每轮新 `session_id`）演示不同问法
- 工具：`get_weather_report`、`get_weather_forecast`
- 输出包含工具调用、工具结果与自然语言回答

## Agent 层级结构说明

```text
root_agent (LlmAgent, thinking enabled)
└── tools: get_weather_report, get_weather_forecast
```

关键文件：

- [examples/llmagent_with_thinking/agent/agent.py](./agent/agent.py)
- [examples/llmagent_with_thinking/agent/tools.py](./agent/tools.py)
- [examples/llmagent_with_thinking/run_agent.py](./run_agent.py)
- [examples/llmagent_with_thinking/.env](./.env)

## 关键代码解释

- `Runner` + `InMemorySessionService` 按预设问题列表循环调用 `run_async`
- Agent 配置中打开思考能力，模型在工具前后组织回复

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

在 [examples/llmagent_with_thinking/.env](./.env) 中配置（或通过 `export` 设置）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/llmagent_with_thinking
python3 run_agent.py
```

## 运行结果（实测）


```text
📝 User: What's the weather like today?
🔧 [Invoke Tool:: get_weather_report({'city': 'Beijing'})]
📊 [Tool Result: {'temperature': '25°C', 'condition': 'Sunny', 'humidity': '60%'}]
...
📝 User: What will the weather be like in Shanghai for the next three days?
🔧 [Invoke Tool:: get_weather_forecast({'city': 'Shanghai', 'days': 3})]
...
[END] llmagent_with_thinking (exit_code=0)
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- 三轮问答均完成工具调用并得到结构化结果与总结回复
- 进程以 `exit_code=0` 结束，`error.txt` 为空

## 适用场景建议

- 需要对比“开思考”与默认推理路径对工具选择影响的实验
- 天气类多工具编排的教学与最小复现
