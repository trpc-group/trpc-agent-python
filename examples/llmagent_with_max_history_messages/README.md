# LLM Agent 历史消息上限示例

本示例演示如何通过 `max_history_messages` 控制 Agent 可见的会话历史长度，并验证在“无限制”与“限制最近 N 条”两种模式下，回答内容的记忆差异。

## 关键特性

- **历史窗口控制**：通过 `max_history_messages` 精确控制每轮推理可见的历史条数
- **成本优化能力**：限制历史窗口可降低长对话 token 消耗
- **行为可验证**：使用同一组 4 轮问答，在不同配置下对比最终“你知道我什么”回答
- **配置简单**：`0` 表示无限制；正整数 `N` 表示仅保留最近 `N` 条历史
- **兼顾质量与成本**：便于在上下文完整性和推理成本间做取舍

## Agent 层级结构说明

本例是单 Agent 示例，不涉及多 Agent 分层路由：

```text
assistant (LlmAgent)
├── model: OpenAIModel
├── instruction: context-aware prompt
└── max_history_messages: 0 or 2 (scenario configurable)
```

关键文件：

- [examples/llmagent_with_max_history_messages/agent/agent.py](./agent/agent.py)：Agent 创建与 `max_history_messages` 配置
- [examples/llmagent_with_max_history_messages/agent/prompts.py](./agent/prompts.py)：强调“可见上下文”行为的提示词
- [examples/llmagent_with_max_history_messages/agent/config.py](./agent/config.py)：模型环境变量读取
- [examples/llmagent_with_max_history_messages/run_agent.py](./run_agent.py)：两种场景对比运行

## 关键代码解释

这一节用于快速定位“历史窗口参数如何影响最终回答”。

### 1) 参数入口（`agent/agent.py`）

- 通过 `create_agent(max_history_messages=...)` 创建 Agent
- `max_history_messages=0`：不限制历史（默认）
- `max_history_messages=2`：仅保留最近 2 条历史消息

### 2) 对比实验设计（`run_agent.py`）

- 场景 1：`max_history_messages=0`
- 场景 2：`max_history_messages=2`
- 两个场景都使用同一组四轮输入：
  1. 姓名
  2. 职业
  3. 爱好
  4. 询问“你知道我什么”

### 3) 观测指标

- 重点观察第 4 轮是否还能提到第 1 轮信息（姓名）
- 用于验证历史裁剪是否按预期生效

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

在 [examples/llmagent_with_max_history_messages/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/llmagent_with_max_history_messages
python3 run_agent.py
```

## 运行结果（实测）

```text
Scenario 1: No History Limit (max_history_messages=0)
- Turn 4 回答中包含：
  1) Name: Alice
  2) Profession: Software engineer
  3) Hobby: Playing piano

Scenario 2: Limited History (max_history_messages=2)
- Turn 4 回答中仅包含：
  1) Profession: Software engineer
  2) Hobby: Playing piano
- 未再提及 Name: Alice（早期信息被历史窗口裁剪）
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **无限制场景正确**：Agent 保留完整历史，能在第 4 轮回忆姓名、职业、爱好
- **限制场景正确**：Agent 只基于最近历史回答，早期姓名信息被裁剪
- **对比效果明确**：同一输入在两种配置下产生可解释、可复现的记忆差异
- **参数语义验证通过**：`max_history_messages` 的行为与设计一致

## 适用场景建议

- 需要完整多轮记忆：`max_history_messages=0`
- 需要控制 token 成本：设置为 10~20 等合理窗口
- 需要强调最近上下文：设置为较小值（如 2~5）
