# LangGraphAgent Steering 示例

本示例演示 LangGraphAgent 如何通过业务自定义节点消费 steering：工具回边上的 `steering_node` 和图收尾的 `tail_steering` 都会调用 `ctx.consume_steering()`，把追加的用户消息写进图状态，模型在同一个 run 内响应。

## 图结构

```text
START
  │
  ▼
chatbot ──有 tool_call──► tools ──► steering ──► chatbot
  │
  └──无 tool_call──► tail_steering
                       │
          ┌────────────┴────────────┐
          ▼                         ▼
     有 steering               无 steering
          │                         │
          ▼                         ▼
      chatbot                     END
   （再调一次 LLM）
```

- `chatbot`：调用 LLM，router 根据有无 tool call 分流
- `tools`：执行 `get_weather_report`
- `steering`：工具回边上消费 steering，把消息作为 `HumanMessage` 追加到 `state["messages"]`
- `tail_steering`：图结束前再消费一次。有内容就追加消息并回到 `chatbot` 再调一次 LLM，没有才 END
- `consume_steering()` 是 drain，取走即清空；回到 `chatbot` 后不会重复消费同一条消息

关键文件：

- [agent/agent.py](./agent/agent.py)：构建 `StateGraph`，定义 `steering_node` / `tail_steering` 和分流 router，组装 `LangGraphAgent`
- [agent/tools.py](./agent/tools.py)：`get_weather_report` 工具，模拟 1 秒耗时
- [run_agent.py](./run_agent.py)：发送 steering 并校验 Beijing → Shanghai 的工具调用顺序

## 运行方法

1. 在仓库根目录安装依赖：

```bash
pip3 install -e ".[graph]"
```

2. 在 `.env` 中配置（或通过 `export` 设置）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

3. 运行示例：

```bash
cd examples/langgraph_agent_with_steering
python3 run_agent.py
```

## 预期行为

用户先问 Beijing 天气；模型发出第一个工具调用后，示例通过另一个 Runner 发送 steering：

> Sorry, I said it wrong. Please check Shanghai's weather instead.

Beijing 工具执行完后，`steering_node` 消费这条消息，下一次 `chatbot` 调用 Shanghai 的天气工具。

示例输出类似：

```text
user: What's the current weather in Beijing?
assistant:
[0.61s] [function call] get_weather_report({'city': 'Beijing'})
[0.61s] [steering] status=Status(ok=True, error_msg='') text="Sorry, I said it wrong. Please check Shanghai's weather instead."
[Tool executing: fetching weather for Beijing...]
[Tool completed: got result for Beijing]
[1.61s] [function response] get_weather_report: {'city': 'Beijing', ...}
[2.20s] [function call] get_weather_report({'city': 'Shanghai'})
[Tool executing: fetching weather for Shanghai...]
[Tool completed: got result for Shanghai]
[3.20s] [function response] get_weather_report: {'city': 'Shanghai', ...}
No problem! The current weather in Shanghai is **20°C, Sunny, with 80% humidity**.
function call cities: ['Beijing', 'Shanghai']
final answer: No problem! The current weather in Shanghai is **20°C, Sunny, with 80% humidity**.
steering verified
```

如果 steering 没有被接受，或者工具调用顺序不是 `['Beijing', 'Shanghai']`，脚本会以非零状态退出。
