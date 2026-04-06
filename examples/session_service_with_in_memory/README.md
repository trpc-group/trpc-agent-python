# 内存型 Session Service 示例

本示例演示 `InMemorySessionService` 在多轮对话中的会话内记忆，以及多次运行（First/Second/Third run）下与清理任务相关的日志表现。

## 关键特性

- 同一会话内可记住用户姓名、颜色偏好等（依模型输出）
- 跨“运行段”行为在 `out.txt` 中分段展示
- 日志可能出现 `Cleanup completed`、`Cleanup task started` 等 INFO

## Agent 层级结构说明

```text
root_agent (LlmAgent)
└── tools: get_weather_report 等（见 agent 定义）
```

关键文件：

- [examples/session_service_with_in_memory/agent/agent.py](./agent/agent.py)
- [examples/session_service_with_in_memory/run_agent.py](./run_agent.py)
- [examples/session_service_with_in_memory/.env](./.env)

## 关键代码解释

- 每段 run 使用脚本定义的会话策略，观察会话服务生命周期
- 与 Memory Service 示例不同：此处强调 session 存储而非独立 memory 工具检索

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

在 [examples/session_service_with_in_memory/.env](./.env) 中配置（或通过 `export` 设置）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/session_service_with_in_memory
python3 run_agent.py
```

## 运行结果（实测）


```text
First run
🤖 Assistant: No, I don't have the ability to remember ... between conversations...
🔧 [Invoke Tool: get_weather_report({'city': 'Paris'})]
📊 [Tool Result: {'status': 'success', 'report': 'The weather in Paris is sunny...'}]
...
Second run
🤖 Assistant: Yes, Alice! I remember your name from earlier in this conversation...
...
[2026-04-01 ...] Cleanup completed: deleted 3 items ...
[END] session_service_with_in_memory (exit_code=0)
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- 多段运行与会话内/跨段语义在日志中可区分；正常以 `exit_code=0` 结束；`error.txt` 为空

## 适用场景建议

- 本地开发、单测、无需外部存储的快速会话原型
- 观察 session 清理周期与并发下资源回收的调试
