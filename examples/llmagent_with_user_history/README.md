# LLM Agent 使用用户历史记录示例

本示例演示如何使用 `HistoryRecord` 将用户的历史对话记录注入到 Agent 的上下文中，使 Agent 能够基于历史会话信息回答问题。

## 关键特性

- **用户历史记录注入**：通过 `HistoryRecord` 构造用户的历史对话记录，并将其作为上下文传递给 Agent
- **历史优先查询**：Agent 优先从历史会话中查找答案，减少不必要的工具调用
- **会话持久化**：通过 `RunConfig(save_history_enabled=True)` 开启会话历史保存

## 环境要求

Python版本: 3.10+（强烈建议使用3.12）

## 在trpc-agent-python框架代码下如何运行此代码示例

1. 下载trpc-agent-python代码并安装

```bash
git clone https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent
cd trpc-agent
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
cd examples/llmagent_with_user_history/
python3 run_agent.py
```

## 关键代码解释

### 1. 构造用户历史记录（agent/tools.py）

```python
from trpc_agent_sdk.sessions import HistoryRecord

def make_user_history_record() -> HistoryRecord:
    """构造用户历史记录，模拟用户之前的对话历史"""
    record: dict[str, str] = {
        "What's your name?": "My name is Alice",
        "what is the weather like in paris?": "The weather in Paris is sunny...",
        "Do you remember my name?": "It seems I don't have your name stored...",
    }

    history_record = HistoryRecord()
    for query, answer in record.items():
        history_record.add_record(query, answer)
    return history_record
```

`HistoryRecord` 用于构建用户的历史对话记录。通过 `add_record(query, answer)` 方法逐条添加历史问答对，然后在运行时通过 `build_content(query)` 将历史记录构建为可注入的内容。

### 2. 创建 Agent（agent/agent.py）

```python
from trpc_agent_sdk.agents import LlmAgent

agent = LlmAgent(
    name="assistant_agent",
    description="普通的问答助手",
    model=_create_model(),
    instruction=INSTRUCTION,
)
```

Agent 的 instruction 中指示其优先从历史会话中查找答案，减少大模型的工具调用。

### 3. 运行 Agent 并注入历史记录（run_agent.py）

```python
from trpc_agent_sdk.configs import RunConfig

history_record = make_user_history_record()
history_content = history_record.build_content(query)
user_content = Content(parts=[Part.from_text(text=query)])

run_config = RunConfig(save_history_enabled=True)
async for event in runner.run_async(
    user_id=user_id,
    session_id=session_id,
    new_message=[history_content, user_content],
    run_config=run_config,
):
    ...
```

核心步骤：
1. 调用 `make_user_history_record()` 获取历史记录对象
2. 通过 `history_record.build_content(query)` 将历史记录构建为上下文内容
3. 将 `history_content` 和 `user_content` 一起作为 `new_message` 列表传入 `runner.run_async`
4. 设置 `RunConfig(save_history_enabled=True)` 开启会话历史保存

## 适用场景

| 场景 | 说明 |
|------|------|
| 用户画像增强 | 将用户的历史偏好、行为记录注入 Agent 上下文 |
| 跨会话记忆 | 在新会话中引入之前会话的关键信息 |
| 知识库问答 | 将用户相关的知识库内容作为历史记录注入 |
| 个性化服务 | 基于用户历史交互提供定制化回答 |
