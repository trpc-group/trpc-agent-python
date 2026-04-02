# 工具提示词（Tool Prompt）格式示例

本示例演示使用 XML 风格 `tool_prompt` 时，模型如何输出 `<function_calls>` 并被框架解析为实际工具调用，完成天气查询与预报。

## 关键特性

- 自定义工具调用文本格式（见模型输出中的 `<invoke>` 片段）
- 与标准 `get_weather_report` / `get_weather_forecast` 工具配合
- 多会话轮次覆盖缺省城市澄清、单日与多日预报

## Agent 层级结构说明

```text
root_agent (LlmAgent, tool_prompt=XML style)
└── tools: get_weather_report, get_weather_forecast
```

关键文件：

- [examples/llmagent_with_tool_prompt/agent/agent.py](./agent/agent.py)
- [examples/llmagent_with_tool_prompt/run_agent.py](./run_agent.py)
- [examples/llmagent_with_tool_prompt/.env](./.env)

## 关键代码解释

- Agent 配置中指定工具提示模板，引导模型用标签包裹工具名与参数
- Runner 将解析后的调用映射到已注册工具并回灌结果

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

在 [examples/llmagent_with_tool_prompt/.env](./.env) 中配置（或通过 `export` 设置）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/llmagent_with_tool_prompt
python3 run_agent.py
```

## 运行结果（实测）


```text
📝 User: What's the weather like today?
🤖 Assistant: ... which city ...
📝 User: What's the current weather in Guangzhou?
🤖 Assistant: <function_calls>...<tool_name>get_weather_report</tool_name>...
🔧 [Invoke Tool:: get_weather_report({'city': 'Guangzhou'})]
📊 [Tool Result: {'temperature': '32°C', 'condition': 'Thunderstorm', ...}]
...
[END] llmagent_with_tool_prompt (exit_code=0)
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- 首轮澄清城市；后续轮次出现 XML 风格工具块并成功执行工具
- `exit_code=0`，`error.txt` 为空

## 适用场景建议

- 需与仅支持特定工具语法的模型或遗留提示词对齐的集成
- 对比 JSON tool calls 与 XML 风格可解析性的评测
