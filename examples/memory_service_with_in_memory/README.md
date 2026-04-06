# 内存型 Memory Service 示例

本示例演示 `InMemoryMemoryService` 与 `load_memory` 工具配合：多轮写入后检索姓名与偏好，并在多次运行（First/Second/Third run）下观察记忆命中与 SDK 清理日志。

## 关键特性

- 记忆读写通过工具暴露给 LlmAgent
- 日志含 `load_memory` 空结果与命中后的 JSON memories
- 可出现 `_in_memory_memory_service` 的过期事件清理 INFO

## Agent 层级结构说明

```text
root_agent (LlmAgent)
└── tools: load_memory, get_weather_report（及记忆相关工具，见 agent 定义）
```

关键文件：

- [examples/memory_service_with_in_memory/agent/agent.py](./agent/agent.py)
- [examples/memory_service_with_in_memory/run_agent.py](./run_agent.py)
- [examples/memory_service_with_in_memory/.env](./.env)

## 关键代码解释

- `Runner` 绑定内存记忆服务，脚本分三段运行模拟进程级多次启动或会话演进
- Agent 通过工具查询记忆并在回答中引用检索结果

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

在 [examples/memory_service_with_in_memory/.env](./.env) 中配置（或通过 `export` 设置）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/memory_service_with_in_memory
python3 run_agent.py
```

## 运行结果（实测）


```text
First run
🔧 [Invoke Tool: load_memory({'query': "user's name"})]
📊 [Tool Result: {'result': '{"memories": []}'}]
...
🔧 [Invoke Tool: load_memory({'query': "user's name"})]
📊 [Tool Result: {'result': '{"memories": [{"content": ...
Yes, your name is Alice! ...
...
[END] memory_service_with_in_memory (exit_code=0)
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- 首轮空记忆、后续命中记忆并与自然语言回答一致；正常结束 `exit_code=0`
- `error.txt` 为空

## 适用场景建议

- 本地开发、单测、演示环境快速验证记忆管线
- 不需要跨进程持久时的最小记忆后端选型
