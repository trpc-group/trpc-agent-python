# SQL 后端 Session Service 示例

本示例演示基于 SQL 的 `SessionService`：多轮对话持久化，多次运行下仍可从存储恢复会话视图；日志含清理任务删除计数。

## 关键特性

- First/Second/Third run 分段，对比会话内连续追问与重新开始的差异
- 工具调用如 `get_weather_report` 与 session 状态交织出现
- `Cleanup completed: deleted N items` 表明后台维护执行

## Agent 层级结构说明

```text
root_agent (LlmAgent)
└── tools: get_weather_report 等（见 agent 定义）
```

关键文件：

- [examples/session_service_with_sql/agent/agent.py](./agent/agent.py)
- [examples/session_service_with_sql/run_agent.py](./run_agent.py)
- [examples/session_service_with_sql/.env](./.env)

## 关键代码解释

- 初始化 SQL Session 后端并注入 `Runner`
- 与内存版脚本结构对称，便于对比持久化语义

## 环境与运行

### 环境要求

- Python 3.12
- 按 `.env` 提供可用的 SQL 配置

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 环境变量要求

在 [examples/session_service_with_sql/.env](./.env) 中配置模型与数据库相关变量（以该文件为准）。

### 运行命令

```bash
cd examples/session_service_with_sql
python3 run_agent.py
```

## 运行结果（实测）


```text
First run
🤖 Assistant: No, I don't have the ability to remember ... between conversations...
🔧 [Invoke Tool: get_weather_report({'city': 'Paris'})]
...
Second run
🤖 Assistant: Yes, Alice! I remember your name is Alice, and your favorite color is blue...
...
[2026-04-01 ...] Cleanup completed: deleted 3 items ...
Third run
🤖 Assistant: No, I don't have the ability to remember ... between conversations...
[END] session_service_with_sql (exit_code=0)
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- 第二轮体现跨轮持久记忆，第三轮回到无跨会话记忆说明；`exit_code=0`，`error.txt` 为空

## 适用场景建议

- 需要服务重启后保留用户会话的生产路径验证
- 与 Redis 等其它 Session 后端做行为对比测试
