# ClaudeAgent 基础示例

本示例演示如何使用 ClaudeAgent 创建一个简单的天气查询助手。

## 功能说明

本示例展示了 ClaudeAgent 的基本使用方法:
- **创建 ClaudeAgent**: 配置模型、指令和工具
- **使用 FunctionTool**: 将 Python 函数注册为工具
- **运行 Agent**: 使用 Runner 处理用户请求并获取响应

## 环境要求

Python版本: 3.10+(强烈建议使用3.12)

## 运行方法

1. 下载并安装 trpc-agent-python

```bash
git clone https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent
cd trpc-agent
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .[agent-claude]
```

2. 在 `.env` 文件中设置环境变量(也可以通过export设置):
   - TRPC_AGENT_API_KEY
   - TRPC_AGENT_BASE_URL
   - TRPC_AGENT_MODEL_NAME

3. 运行示例:

```bash
cd examples/claude_agent/
python3 run_agent.py
```

## 预期行为

输出如下所示:

```
================================================================================
ClaudeAgent Basic Demo - Weather Query
================================================================================

[2026-01-12 11:13:20][INFO][trpc_agent][trpc_agent_ecosystem/agents/claude/_setup.py:209][2947095] Proxy server proxy process started (PID: 2948120)
[2026-01-12 11:13:20][INFO][trpc_agent][trpc_agent_ecosystem/agents/claude/_setup.py:226][2947095] Proxy server is ready at http://0.0.0.0:8082
🆔 Session ID: 9b405d41...
📝 User: What is the weather in Beijing?

🤖 Agent: 
🔧 [Tool Call: mcp__claude_weather_agent_tools__get_weather({"city": "Beijing"})]
📊 [Tool Result: {"result": "{'city': 'Beijing', 'temperature': '25C', 'condition': 'Sunny', 'humidity': '60%'}"}]
The current weather in Beijing is sunny with a temperature of 25°C and humidity at 60%.

================================================================================
Demo completed!
================================================================================
[2026-01-12 11:13:27][INFO][trpc_agent][trpc_agent_ecosystem/agents/claude/_setup.py:262][2947095] Terminating proxy process (PID: 2948120)...
[2026-01-12 11:13:27][INFO][trpc_agent][trpc_agent_ecosystem/agents/claude/_setup.py:274][2947095] Subprocess terminated successfully.
🧹 Claude environment cleaned up
```
