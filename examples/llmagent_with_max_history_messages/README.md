# LLM Agent 历史消息控制示例

本示例演示如何使用 `max_history_messages` 参数控制 Agent 在多轮对话中可见的历史消息数量。

## 关键特性

- **历史消息数量限制**：通过 `max_history_messages` 参数控制 Agent 每次请求时携带的历史消息条数
- **Token 用量优化**：在长对话场景下，限制历史消息可以有效降低 Token 消耗，减少推理成本
- **灵活配置**：`max_history_messages=0` 表示不限制（默认行为），设为正整数 N 则仅保留最近 N 条消息
- **多场景对比**：本示例同时运行无限制和有限制两种模式，直观展示历史控制的效果差异

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
cd examples/llmagent_with_max_history_messages/
python3 run_agent.py
```

## 关键代码解释

### 1. 创建带历史限制的 Agent（agent/agent.py）

```python
agent = LlmAgent(
    name="assistant",
    model=_create_model(),
    instruction=INSTRUCTION,
    max_history_messages=max_history_messages,  # 核心参数
)
```

`max_history_messages` 是 `LlmAgent` 的构造参数，用于设置每次 LLM 调用时携带的最大历史消息条数：
- 设为 `0`（默认）：不限制，Agent 可以看到当前 session 中的所有历史消息
- 设为正整数 `N`：仅保留最近的 N 条消息（包括 user 和 assistant 消息）

### 2. 多场景对比运行（run_agent.py）

```python
test_scenarios = [
    {"title": "No History Limit", "max_history_messages": 0},    # 无限制
    {"title": "Limited History",  "max_history_messages": 2},    # 仅保留最近 2 条
]
```

示例通过同一组对话序列分别在两种配置下运行，最后一轮提问 "What do you know about me?" 来检验 Agent 对历史信息的记忆程度：
- **无限制模式**：Agent 能回忆出所有之前提到的信息（姓名、职业、爱好）
- **限制为 2 条**：Agent 仅能感知最近 2 条消息中的内容，早期信息会丢失

## 适用场景

| 场景 | 推荐配置 |
|------|---------|
| 短对话（< 10 轮） | `max_history_messages=0`，保留全部上下文 |
| 长对话（数十轮以上） | 设为合理值（如 10~20），控制 Token 开销 |
| 无状态问答 | 设为 `2`，仅保留上一轮交互 |
| 需要完整上下文的任务 | `max_history_messages=0`，确保不丢失关键信息 |
