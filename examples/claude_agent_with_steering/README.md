# ClaudeAgent Steering 示例

本示例演示 ClaudeAgent 在工具执行期间接收 steering：用户先问 Beijing 天气，模型发起天气工具调用后，示例通过另一个 Runner 发送一条修正消息；当前工具结束后，Claude 在同一个 turn 内读取这条消息，改为查询 Shanghai。

## 执行流程

```text
user: Beijing 天气
        │
        ▼
ClaudeAgent ──tool_use──► get_weather_report("Beijing")
        │                              │
        │                              │ 工具执行中
        │                              │
        │◄──── send_steering ──────────┤
        │      "改成 Shanghai"          │
        │                              │
        │◄──── tool_result ────────────┘
        │
        ▼
Claude 读取 steering ──► get_weather_report("Shanghai")
        │
        ▼
最终回答 Shanghai 天气
```

关键文件：

- [agent/agent.py](./agent/agent.py)：创建 `ClaudeAgent`，`enable_session` 默认 `false`
  （由 Claude 管理对话历史）；设置 `CLAUDE_ENABLE_SESSION=true` 可改由 tRPC Session 管理
- [agent/tools.py](./agent/tools.py)：`get_weather_report` 工具，模拟 2 秒耗时
- [run_agent.py](./run_agent.py)：发送 steering，并校验 Beijing → Shanghai 的工具调用顺序

## 运行方法

1. 在仓库根目录安装依赖：

```bash
pip3 install -e ".[agent-claude]"
```

2. 在 `.env` 中配置（或通过 `export` 设置）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

可选：

- `CLAUDE_ENABLE_SESSION`：默认 `false`；设为 `true` 时由 tRPC Session 管理历史

3. 运行示例：

```bash
cd examples/claude_agent_with_steering
python3 run_agent.py
```

用 tRPC Session 管理历史：

```bash
CLAUDE_ENABLE_SESSION=true python3 run_agent.py
```

## 预期行为

模型发出第一个天气工具调用后，示例立即发送 steering：

> Sorry, I said it wrong. Please check Shanghai's weather instead.

Beijing 工具执行结束后，Claude 读取 steering 并调用 Shanghai 工具，最终回答 Shanghai 的天气。

输出类似：

```text
enable_session=False
user: What's the current weather in Beijing? Use the weather tool and answer with the result.
assistant:
[1.52s] [function call] mcp__claude_weather_agent_with_steering_tools__get_weather_report({'city': 'Beijing'})
[1.52s] [steering] status=Status(ok=True, error_msg='') text="Sorry, I said it wrong. Please check Shanghai's weather instead. ..."
[Tool executing: fetching weather for Beijing...]
[Tool completed: got result for Beijing]

[3.53s] [function response] mcp__claude_weather_agent_with_steering_tools__get_weather_report: {'result': "{'city': 'Beijing', ...}"}
[4.11s] [function call] mcp__claude_weather_agent_with_steering_tools__get_weather_report({'city': 'Shanghai'})
[Tool executing: fetching weather for Shanghai...]
[Tool completed: got result for Shanghai]

[6.12s] [function response] mcp__claude_weather_agent_with_steering_tools__get_weather_report: {'result': "{'city': 'Shanghai', ...}"}
Shanghai 当前的天气情况：...
function call cities: ['Beijing', 'Shanghai']
steering verified
```

`CLAUDE_ENABLE_SESSION=true` 时工具调用顺序和最终回答一致，区别只是对话历史由 tRPC Session 管理。

## 验证结果

`CLAUDE_ENABLE_SESSION=false` 和 `CLAUDE_ENABLE_SESSION=true` 各跑了一遍，两个模式都输出：

```text
function call cities: ['Beijing', 'Shanghai']
steering verified
```
