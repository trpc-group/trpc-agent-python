# 时间线过滤（TimelineFilter）示例

本示例演示同一会话上多次 `run_async` 调用时，`TimelineFilterMode.ALL` 与 `INVOCATION` 对模型可见历史范围的差异。

## 关键特性

- 两组场景：全量历史 vs 单次调用内历史
- 三轮请求串联：先写入偏好与宠物信息，再追问“你知道什么”
- 日志末尾输出模式对比摘要

## Agent 层级结构说明

```text
create_agent(...) (LlmAgent)
└── 无子 Agent；通过 Runner 的 timeline_filter 配置切换模式
```

关键文件：

- [examples/llmagent_with_timeline_filtering/agent/agent.py](./agent/agent.py)
- [examples/llmagent_with_timeline_filtering/run_agent.py](./run_agent.py)
- [examples/llmagent_with_timeline_filtering/.env](./.env)

## 关键代码解释

- `test_scenarios` 中为每种模式构造 `Runner`，共享逻辑的三轮 `demo_queries`
- `INVOCATION` 下第三轮无法看到前两轮在本会话中累积的内容（与 `out.txt` 一致）

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

在 [examples/llmagent_with_timeline_filtering/.env](./.env) 中配置（或通过 `export` 设置）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/llmagent_with_timeline_filtering
python3 run_agent.py
```

## 运行结果（实测）


```text
Scenario 1: TimelineFilterMode.ALL
--- Request 3 ---
🤖 Assistant: ... favorite color is **blue** ... dog named **Max** ...
Scenario 2: TimelineFilterMode.INVOCATION
--- Request 3 ---
🤖 Assistant: I currently don't have any information about your preferences or pets...
Key Takeaways:
- TimelineFilterMode.ALL: Full conversation history
- TimelineFilterMode.INVOCATION: Invocation-scoped history (per runner.run_async() call)
[END] llmagent_with_timeline_filtering (exit_code=0)
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- `ALL` 下第三轮能综合前两轮信息；`INVOCATION` 下第三轮明确表示无此前信息，与模式语义一致
- `exit_code=0`，`error.txt` 为空

## 适用场景建议

- 多轮 HTTP 请求共用会话 ID，但需要隔离单次请求内上下文的 API 设计
- 长会话全量记忆 vs 无状态调用的选型验证
