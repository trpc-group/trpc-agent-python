# DynamicAgentTool 使用示例

演示 `DynamicAgentTool` 的用法——在调用时动态定义子 Agent 角色，无需预注册专家类型。

## 两种模式

- **minimal**：父 Agent 与子 Agent 共享工具面，LLM 通过 `tools` 参数按需缩窄
- **bounded**：工具全部封装在 `DynamicAgentTool` 内部，父 Agent 无法直接调用

无论哪种模式，子 Agent 的工具面始终在代码定义的能力边界内，LLM 只能缩小、不可越界。

## 运行

```bash
# minimal（默认）—— 父 Agent 与子 Agent 共享工具
python run_agent.py

# bounded —— 工具封装在 dynamic_agent 内部
python run_agent.py --mode bounded
```

## 结构

```
minimal:
orchestrator (LlmAgent)
├── tools: calculator, current_time, word_count
└── tools: DynamicAgentTool
    └── 子 Agent 继承父 Agent 的全部工具

bounded:
orchestrator (LlmAgent)
└── tools: DynamicAgentTool(tools=[calculator, current_time, word_count])
    └── 子 Agent 拥有固定工具集，父 Agent 不可直接使用
```

## 关键代码

```python
from trpc_agent_sdk.tools import DynamicAgentTool

workspace_tools = [calculator, current_time, word_count]

# minimal —— 子 Agent 继承父 Agent 工具面
DynamicAgentTool(skip_summarization=True)

# bounded —— 工具限定在 capability surface 内
DynamicAgentTool(
    tools=tuple(workspace_tools),
    skip_summarization=True,
)
```

## `dynamic_agent` 调用参数

- `prompt`（必填）— 子 Agent 的完整任务描述
- `instruction`（可选）— 本次调用的角色 / 系统提示
- `tools`（可选）— 授予的精确工具名列表，省略则允许全部

## 适用场景

- 工具池固定，但每次任务需要不同的子集
- 需要 LLM 现场决定子 Agent 角色和工具组合
- 不想为每种组合预注册专家 Agent
