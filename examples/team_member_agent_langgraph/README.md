# TeamAgent 使用 LangGraphAgent 成员示例

本示例演示如何将 LangGraphAgent 作为 TeamAgent 的成员使用。团队领导将任务委派给由 LangGraph 驱动的成员代理。

## 功能说明

TeamAgent 支持与 LangGraph 框架集成：
- **Leader（领导）**: 使用 LlmAgent 协调任务
- **LangGraph Member（LangGraph成员）**: 使用 LangGraphAgent 执行计算任务

本示例展示了 LangGraphAgent 如何支持 override_messages 以实现 TeamAgent 成员控制。

## 环境要求

Python版本: 3.10+（强烈建议使用3.12）

## 运行方法

1. 下载并安装 trpc-agent-python

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

2. 在 `.env` 文件中设置环境变量（也可以通过export设置）:
   - TRPC_AGENT_API_KEY
   - TRPC_AGENT_BASE_URL
   - TRPC_AGENT_MODEL_NAME

3. 运行示例:

```bash
cd examples/team_member_agent_langgraph/
python3 run_agent.py
```

## 预期行为

本示例在同一个会话中发送2条消息：

1. "Calculate 15 * 23 for me" → Leader 委派给 calculator_expert (LangGraphAgent)
2. "What's 100 divided by 4?" → Leader 继续委派给 calculator_expert 计算

输出如下所示：

```
TeamAgent with LangGraphAgent Member Example
Demonstrates: Leader -> LangGraph Member (calculator_expert)

============================================================
TeamAgent with LangGraphAgent Member Demo
============================================================

[Turn 1] User: Calculate 15 * 23 for me
----------------------------------------

[math_assistant_team] Tool: call_member, Args: {'member_name': 'calculator_expert', ...}

[calculator_expert] Tool: calculate, Args: {'operation': 'multiply', 'a': 15, 'b': 23}

[calculator_expert] Tool Response: Result: 15 multiply 23 = 345

[calculator_expert] The result of 15 multiplied by 23 is 345.

[math_assistant_team] The calculation result is 345.

[Turn 2] User: What's 100 divided by 4?
----------------------------------------
...

============================================================
Demo completed!
```

## 技术说明

- 使用 `@langgraph_tool_node` 装饰器包装工具函数
- 使用 `@langgraph_llm_node` 装饰器包装 LLM 节点
- LangGraph 图使用 `StateGraph` 构建
- 工具条件路由使用 `tools_condition`
