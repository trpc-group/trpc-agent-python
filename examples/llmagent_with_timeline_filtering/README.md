# LLM Agent 使用Timeline过滤历史消息示例

本示例演示如何使用 `message_timeline_filter_mode` 参数控制 Agent 在同一 Session 内跨 `run_async()` 调用时的历史消息可见范围。

## 关键特性

- **时间线级别的消息隔离**：通过 `message_timeline_filter_mode` 参数控制 Agent 对历史消息的可见性，粒度为每次 `run_async()` 调用（即一次 invocation）
- **两种过滤模式**：
  - `TimelineFilterMode.ALL`：Agent 可以看到当前 Session 中所有历史消息（跨 invocation），适合需要完整上下文的多轮对话
  - `TimelineFilterMode.INVOCATION`：Agent 仅能看到当前 `run_async()` 调用产生的消息，每次调用相互隔离
- **多场景对比**：本示例同时运行两种模式，直观展示时间线过滤对 Agent 记忆能力的影响

## 环境要求

Python版本: 3.10+（强烈建议使用3.12）

## 在trpc-agent-python框架代码下如何运行此代码示例

1. 下载trpc-agent-python代码并安装

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

2. 运行此代码示例

在 `.env` 文件中设置使用 LLM 相关的变量（也可以通过export设置）:
- TRPC_AGENT_API_KEY
- TRPC_AGENT_BASE_URL
- TRPC_AGENT_MODEL_NAME

然后运行下面的命令：

```bash
cd examples/llmagent_with_timeline_filtering/
python3 run_agent.py
```

## 关键代码解释

### 1. 创建带时间线过滤的 Agent（agent/agent.py）

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.agents import TimelineFilterMode

agent = LlmAgent(
    name="timeline_demo",
    model=_create_model(),
    instruction=INSTRUCTION,
    message_timeline_filter_mode=timeline_mode,  # 核心参数：控制跨 run_async() 调用时历史消息的可见范围（ALL=全量可见, INVOCATION=仅当前调用可见）
)
```

`message_timeline_filter_mode` 是 `LlmAgent` 的构造参数，用于设定 Agent 如何过滤跨 invocation 的历史消息：
- `TimelineFilterMode.ALL`（默认）：不做过滤，Agent 可以看到当前 Session 中所有 invocation 产生的消息
- `TimelineFilterMode.INVOCATION`：按 invocation 隔离，Agent 仅能看到当前 `run_async()` 调用中产生的消息

### 2. 多场景对比运行（run_agent.py）

```python
test_scenarios = [
    {"title": "TimelineFilterMode.ALL",        "timeline_mode": TimelineFilterMode.ALL},
    {"title": "TimelineFilterMode.INVOCATION", "timeline_mode": TimelineFilterMode.INVOCATION},
]
```

示例通过同一组对话序列（3 次独立的 `run_async()` 调用）分别在两种模式下运行：
- **ALL 模式**：第 3 次请求 "What do you know about my preferences and pets?" 时，Agent 能完整回忆出前两轮提到的蓝色和宠物名称 Max
- **INVOCATION 模式**：第 3 次请求时，Agent 认为这是"第一次对话"，完全无法感知前两轮的信息

### 3. 核心区别：同一 Session 内多次 run_async()

```python
# 同一个 session_id，多次调用 run_async()
for i, query in enumerate(demo_queries, 1):
    async for event in runner.run_async(
        user_id=user_id,
        session_id=current_session_id,  # 同一 Session
        new_message=user_content,
    ):
        ...
```

关键点在于所有请求共用同一个 `session_id`，消息都持久化在同一个 Session 中。区别仅在于 Agent 在组装 LLM prompt 时是否包含之前 invocation 的消息，这一区别由 `message_timeline_filter_mode` 控制。

## 适用场景

| 场景 | 推荐模式 |
|------|---------|
| 多轮对话、聊天机器人 | `TimelineFilterMode.ALL`，保留完整上下文 |
| 无状态 API 服务 | `TimelineFilterMode.INVOCATION`，每次调用相互隔离 |
| 批量任务处理（复用 Session） | `TimelineFilterMode.INVOCATION`，避免任务间上下文污染 |
| 需要跨请求记忆的助手 | `TimelineFilterMode.ALL`，确保能引用历史信息 |
