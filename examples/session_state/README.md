# Session State 示例

本示例演示会话状态（State）的多种用法：在指令模板中引用状态、在工具中读写偏好、多 Agent 通过 `output_key` 协作写入状态，以及应用/用户/会话三级作用域演示。

## 关键特性

- Example 1：`personalized_assistant` 从状态读取姓名并回答
- Example 2：`update_user_preference` / `get_current_preferences` 更新与列出偏好
- Example 3：多 Agent 协作结果写入 state 并打印摘要
- Example 4：同用户新会话继承用户级状态、新用户隔离会话级状态

## Agent 层级结构说明

```text
（随示例切换）
personalized_assistant (LlmAgent)
preference_agent (LlmAgent + 偏好工具)
requirement_analyzer / solution_planner (LlmAgent，协作写 state)
state_demo_agent (LlmAgent + set_state_at_different_levels)
```

关键文件：

- [examples/session_state/agent/agent.py](./agent/agent.py)
- [examples/session_state/run_agent.py](./run_agent.py)
- [examples/session_state/.env](./.env)

## 关键代码解释

- `run_agent.py` 按块运行四个示例，每块打印用户输入、工具调用与当前各级 state 快照
- 展示 `output_key` 与协作子 Agent 输出合并进 state 的模式

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

在 [examples/session_state/.env](./.env) 中配置（或通过 `export` 设置）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/session_state
python3 run_agent.py
```

## 运行结果（实测）


```text
Example 1: Template reference - using State in Instruction
👤 用户: Can you tell me my name?
🤖 Agent: [personalized_assistant]: ... Your name is Alice ...
Example 2: Modifying State in tools
🔧 [Call tool: update_user_preference]
📊 [Tool result: {'result': 'Preference updated: theme = dark mode'}]
...
Example 4: State scope demonstration
📊 user1 session2 state ... 💬 会话级状态: {}
📊 user2 session3 state ... 👤 用户级状态: {}
[END] session_state (exit_code=0)
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- 四个示例均打印预期阶段标题、工具结果或 state 快照；`exit_code=0`，`error.txt` 为空

## 适用场景建议

- 多轮对话中维护用户偏好、工单字段等结构化上下文
- 多 Agent 流水线中间结果落盘到 state 供后续节点消费
