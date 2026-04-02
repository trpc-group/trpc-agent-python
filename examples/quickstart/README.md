# Quickstart 最小示例

本示例演示最简 `LlmAgent`：无城市时模型先澄清，指定城市后调用 `get_weather_report` 返回结构化天气结果。

## 关键特性

- 两轮对话：泛问 → 具体城市
- `InMemorySessionService` + `Runner` 标准用法
- 工具调用与 `Tool Result` 打印清晰

## Agent 层级结构说明

```text
root_agent (LlmAgent)
└── tools: get_weather_report
```

关键文件：

- [examples/quickstart/agent/agent.py](./agent/agent.py)
- [examples/quickstart/run_agent.py](./run_agent.py)
- [examples/quickstart/.env](./.env)

## 关键代码解释

- 每轮使用新 `session_id` 或按脚本逻辑创建会话
- 展示环境变量加载后与云端模型的一次完整 tool loop

## 环境与运行

### 环境要求

- Python 3.10+（推荐 3.12）

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 环境变量要求

在 [examples/quickstart/.env](./.env) 中配置（或通过 `export` 设置）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/quickstart
python3 run_agent.py
```

## 运行结果（实测）


```text
📝 User: What's the weather like today?
🤖 Assistant: Could you please specify the city...
📝 User: What's the current weather in Beijing?
🔧 [Invoke Tool: get_weather_report({'city': 'Beijing'})]
📊 [Tool Result: {'temperature': '25°C', 'condition': 'Sunny', 'humidity': '60%'}]
The current weather in Beijing is sunny ...
[END] quickstart (exit_code=0)
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- 澄清与工具调用路径均出现；`exit_code=0`，`error.txt` 为空

## 适用场景建议

- 新用户验证环境与 API Key 的首选入口
- 二次开发时对照的最小可运行骨架
