# SQL 后端 Memory Service 示例

本示例演示基于 SQL 的 Memory Service：与 `load_memory`、天气工具协同，完成多轮对话记忆检索，并可在日志中看到 `Memory cleanup completed` 等后台维护信息。

## 关键特性

- 持久化记忆相对内存后端更适合跨运行复现（依赖 DB 配置）
- 多段 “First run / Second run / Third run” 验证空记忆与命中路径
- 工具结果中为 JSON 字符串形式的 memories 负载

## Agent 层级结构说明

```text
root_agent (LlmAgent)
└── tools: load_memory, get_weather_report 等（见示例 agent）
```

关键文件：

- [examples/memory_service_with_sql/agent/agent.py](./agent/agent.py)
- [examples/memory_service_with_sql/run_agent.py](./run_agent.py)
- [examples/memory_service_with_sql/.env](./.env)

## 关键代码解释

- 初始化 SQL 记忆服务并注入 `Runner`，与内存版示例结构平行
- Agent 通过 `load_memory` 查询后根据返回 JSON 组织回复

## 环境与运行

### 环境要求

- Python 3.10+（推荐 3.12）
- 按 `.env` 配置可用的 SQL 连接（与示例一致）

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 环境变量要求

在 [examples/memory_service_with_sql/.env](./.env) 中配置模型与数据库相关变量（以该文件为准）。

### 运行命令

```bash
cd examples/memory_service_with_sql
python3 run_agent.py
```

## 运行结果（实测）


```text
First run
🔧 [Invoke Tool: load_memory({'query': "user's name"})]
📊 [Tool Result: {'result': '{"memories": []}'}]
...
🔧 [Invoke Tool: get_weather_report({'city': 'Paris'})]
📊 [Tool Result: {'status': 'success', 'report': 'The weather in Paris is sunny...'}]
...
[2026-04-01 ...][INFO]... Memory cleanup completed: deleted ...
[END] memory_service_with_sql (exit_code=0)
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- 记忆查询、天气工具与清理日志均出现，并以 `exit_code=0` 结束
- `error.txt` 为空

## 适用场景建议

- 需要进程重启后仍保留记忆的部署形态验证
- 与运维监控配合观察记忆清理任务是否按预期执行
