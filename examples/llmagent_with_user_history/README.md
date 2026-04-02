# 用户历史注入（HistoryRecord）示例

本示例演示通过 `HistoryRecord` 向单次请求注入与当前问题相关的历史问答，使模型在不依赖会话内记忆字段时仍能“引用”过往内容；同时展示与真实会话记忆不一致时的行为。

## 关键特性

- 多轮共享同一 `session_id`，但历史主要来自 `make_user_history_record().build_content(query)`
- 第二轮问题依赖注入历史中的巴黎天气描述
- 第三轮追问姓名时，若未在会话消息中显式出现，模型可能回答“未告知”

## Agent 层级结构说明

```text
root_agent (LlmAgent)
└── tools: 无（或按 agent 定义）；上下文增强来自 HistoryRecord
```

关键文件：

- [examples/llmagent_with_user_history/agent/tools.py](./agent/tools.py)（`make_user_history_record`）
- [examples/llmagent_with_user_history/run_agent.py](./run_agent.py)
- [examples/llmagent_with_user_history/.env](./.env)

## 关键代码解释

- 每轮将 `history_content` 与用户当前 `query` 一并作为输入交给 `Runner`
- 用于验证“外部检索到的用户历史”与“当前 session 消息列表”的区分

## 环境与运行

### 环境要求

- Python 3.10+（推荐 3.12）

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 环境变量要求

在 [examples/llmagent_with_user_history/.env](./.env) 中配置（或通过 `export` 设置）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/llmagent_with_user_history
python3 run_agent.py
```

## 运行结果（实测）


```text
📝 User: What's your name?
🤖 Assistant: My name is Alice! ...
📝 User: what is the weather like in paris?
🤖 Assistant: From our previous conversation, I mentioned that the weather in Paris is sunny ...
📝 User: Do you remember my name?
🤖 Assistant: I don't believe you've shared your name with me yet! ...
[END] llmagent_with_user_history (exit_code=0)
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- 注入历史支撑了第二轮“巴黎天气”的续写式回答；第三轮体现会话消息与注入块差异
- `exit_code=0`，`error.txt` 为空

## 适用场景建议

- RAG/CRM 拉取用户历史摘要并拼入当轮提示的产品形态
- 调试“模型认为用户说过什么”与真实 session 事件列表不一致的问题
