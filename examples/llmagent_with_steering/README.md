# LlmAgent Steering 示例

本示例演示 LlmAgent 的 steering（插话）能力：在 Agent 执行过程中追加一条用户消息，
Agent 在下一次 LLM 请求前消费它，并在同一个 run 内响应该消息。

## 功能说明

示例中的 weather agent 有一个耗时 1 秒的天气工具。用户第一句话要求查 Beijing 的天气；
工具调用返回后，示例通过 `send_steering()` 追加一条纠正：

> Sorry, I said it wrong. Please check Shanghai's weather instead.

模型在下一轮 LLM 请求前看到这条消息，并在同一个 run 内改为调用 Shanghai 的天气工具。

- `run_agent.py`：单进程。Beijing 工具响应到达后，另一个 Runner 发送 steering 消息。
- `run_agent_with_multi_worker.py`：用 `fork` 启动两个 worker。worker A 跑 Agent 并
  报告 Beijing 工具调用完成；worker B 在共享 entry 里写入 steering 消息，验证跨 worker
  的插话。worker A 会等 worker B 写完再继续消费事件流，避免下一轮 LLM 请求抢在
  steering 落共享内存之前发出。

## 环境要求

Python 版本：3.10+（强烈建议使用 3.12）

## 运行方法

1. 在 trpc-agent-python 仓库根目录安装：`pip3 install -e .`（参考 `examples/llmagent_with_cancel/README.md`）。

2. 在 `.env` 中设置环境变量（也可以通过 export 设置）：

   - `TRPC_AGENT_API_KEY=your-api-key`
   - `TRPC_AGENT_BASE_URL=your-base-url`
   - `TRPC_AGENT_MODEL_NAME=your-model-name`

3. 运行示例：

```bash
cd examples/llmagent_with_steering/
python3 run_agent.py
python3 run_agent_with_multi_worker.py
```

## 预期行为

单进程：

```text
user: What's the current weather in Beijing?
assistant:
[1.16s] [function call] get_weather_report({'city': 'Beijing'})
[Tool executing: fetching weather for Beijing...]
[2.17s] [function response] get_weather_report: {'city': 'Beijing', ...}
[2.17s] [steering] sent after first tool call: status=SteeringStatus.ACCEPTED text='Sorry, I said it wrong. Please check Shanghai's weather instead.'
[2.95s] [function call] get_weather_report({'city': 'Shanghai'})
function call cities: ['Beijing', 'Shanghai']
steering verified
```

多 worker：

```text
[master pid=...] forked workers: [...]
[worker A pid=... 0.01s] start run: session=...
[worker A pid=... 0.96s] function call: get_weather_report({'city': 'Beijing'})
[worker A pid=... 1.97s] function response: get_weather_report: {'city': 'Beijing', ...}
[worker B pid=... 1.97s] send steering: session=...
[worker B pid=... 1.98s] steering status: SteeringStatus.ACCEPTED
[worker A pid=... 2.64s] function call: get_weather_report({'city': 'Shanghai'})
[master pid=...] multi-worker steering verified
```

如果 steering 没有被接受，或者 Beijing 工具调用之后没有出现 Shanghai 的工具调用，脚本会以
非零状态退出。

## 观察日志

`run_agent.py` 在结束时会把 session 里的事件按顺序打印出来。steering 事件本身是
`visible=False` 的 user 事件，不会出现在事件流里，但会持久化到 session，模型在后续
LLM 请求中能看到它。可以看到它的文本带有 `[USER STEERING — sent while the task was
running]` 标记。
