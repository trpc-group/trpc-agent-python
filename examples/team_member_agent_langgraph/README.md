# TeamAgent 成员为 LangGraph 示例

本示例演示 `TeamAgent` 的某一成员为 `LangGraphAgent`：Leader 将计算类任务委派给基于 LangGraph 状态图的工具调用回路，完成乘除等运算。

## 关键特性

- `LangGraphAgent`（`calculator_expert`）内建 `StateGraph` + `ToolNode` + `tools_condition`
- Leader 仍为常规 `TeamAgent`，通过 `delegate_to_member` 驱动子图
- 使用 `langgraph_llm_node` 包装 LLM 节点以兼容 SDK

## Agent 层级结构说明

- 根节点：`TeamAgent`（`math_assistant_team`）
  - 成员：`calculator_expert`（`LangGraphAgent`）

## 关键代码解释

- `agent/agent.py`：`build_calculator_graph()` 编译图并传入 `LangGraphAgent`
- `agent/tools.py`：`calculate` 供 LangGraph 工具节点调用
- `run_agent.py`：两轮算术问句，展示委派与 `calculate` 工具响应

## 环境要求

- Python3.10+，推荐 Python3.12

## 构建步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
./build.sh "[graph, deepseek-langchain]"
source .venv/bin/activate
```

## 运行步骤

### 配置环境变量

在 [examples/team_member_agent_langgraph/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/team_member_agent_langgraph
python3 run_agent.py
```

## 运行结果（实测）

```txt
[START] team_member_agent_langgraph
...
[math_assistant_team] Tool: delegate_to_member, Args: {'member_name': 'calculator_expert', 'task': 'Calculate 15 multiplied by 23.'}
...
[calculator_expert] Tool: calculate, Args: {'operation': 'multiply', 'a': 15, 'b': 23}
...
15 multiplied by 23 is 345.
...
```

## 结果分析（是否符合要求）

符合本示例测试要求：两轮计算均经 `delegate_to_member` 进入 LangGraph 成员并返回正确数值，说明 Team 与 LangGraph 成员集成正常。

## 适用场景建议

- 已有 LangGraph 工作流希望作为团队中的「专家角色」复用时，封装为 `LangGraphAgent` 成员
- 适合计算、审批子图、固定 DAG 类任务，与 Leader 的 LLM 编排互补
