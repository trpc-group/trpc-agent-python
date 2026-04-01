# TeamAgent 使用 ClaudeAgent 成员示例

本示例演示如何将 ClaudeAgent 作为 TeamAgent 的成员使用。团队领导将任务委派给由 Claude 驱动的成员代理。

## 功能说明

TeamAgent 支持异构成员代理：
- **Leader（领导）**: 使用 LlmAgent 协调任务
- **Claude Member（Claude成员）**: 使用 ClaudeAgent 执行特定任务

本示例展示了 ClaudeAgent 如何支持 override_messages 以实现 TeamAgent 成员控制。

## 环境要求

Python版本: 3.10+（强烈建议使用3.12）

## 运行方法

1. 下载并安装 trpc-agent-python

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .[agent-claude]
```

2. 在 `.env` 文件中设置环境变量（也可以通过export设置）:
   - TRPC_AGENT_API_KEY
   - TRPC_AGENT_BASE_URL
   - TRPC_AGENT_MODEL_NAME

3. 运行示例:

```bash
cd examples/team_member_agent_claude/
python3 run_agent.py
```

## 预期行为

本示例在同一个会话中发送2条消息：

1. "What's the weather in Beijing?" → Leader 委派给 weather_expert (ClaudeAgent)
2. "How about Shanghai?" → Leader 继续委派给 weather_expert 查询

输出如下所示：

```
TeamAgent with ClaudeAgent Member Example
Demonstrates: Leader -> Claude Member (weather_expert)

============================================================
TeamAgent with ClaudeAgent Member Demo
============================================================

[Turn 1] User: What's the weather in Beijing?
----------------------------------------

[assistant_team] Tool: call_member, Args: {'member_name': 'weather_expert', ...}

[weather_expert] Tool: get_weather, Args: {'city': 'beijing'}

[weather_expert] Tool Response: Beijing: Sunny, 25C, humidity 45%

[weather_expert] The weather in Beijing is sunny, 25°C, humidity 45%...

[assistant_team] According to the weather expert, Beijing is sunny today...

[Turn 2] User: How about Shanghai?
----------------------------------------
...

============================================================
Cleaned up Claude environment
```

## 注意事项

- ClaudeAgent 需要调用 `initialize()` 和 `destroy()` 进行初始化和清理
- Claude 环境需要通过 `setup_claude_env()` 和 `destroy_claude_env()` 设置
- 确保在程序结束时正确清理资源
