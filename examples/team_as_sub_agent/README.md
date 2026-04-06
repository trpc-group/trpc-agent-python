# TeamAgent 作为子智能体示例

本示例演示根节点为 `LlmAgent`（coordinator），其 `sub_agents` 包含一个 `TeamAgent`（finance_team）与并列的 `report_agent`，并结合 `transfer_to_agent` 在父子、子智能体之间切换。

## 关键特性

- `finance_team`、`report_agent` 作为 coordinator 的子智能体
- `analyst` 关闭 `disallow_transfer_to_parent` / `disallow_transfer_to_peers`，可转回 coordinator 或侧挂 agent
- `share_member_interactions=True` 便于团队内上下文共享

## Agent 层级结构说明

- 根节点：`LlmAgent`（`coordinator`）
  - 子智能体：`TeamAgent`（`finance_team`）
    - 成员：`analyst`（`LlmAgent`）
  - 子智能体：`report_agent`（`LlmAgent`）

## 关键代码解释

- `agent/agent.py`：`coordinator` 的 `sub_agents=[finance_team, report_agent]`；`finance_team` 内仅挂载 `analyst`（与代码一致）
- `agent/tools.py`：`analyze_financial_data`、`generate_report` 等模拟工具
- `run_agent.py`：打印架构说明并执行转账与报告相关场景

## 环境与运行

- Python 3.12；仓库根目录 `pip install -e .`
- 配置 `TRPC_AGENT_API_KEY`、`TRPC_AGENT_BASE_URL`、`TRPC_AGENT_MODEL_NAME`

```bash
cd examples/team_as_sub_agent
python3 run_agent.py
```

## 运行结果（实测）

```txt
[START] team_as_sub_agent
...
[coordinator] Tool: transfer_to_agent
  Args: {'agent_name': 'finance_team'}
...
[finance_team] Tool: delegate_to_member
  Args: {'member_name': 'analyst', ...
...
[finance_team] Tool: transfer_to_agent
  Args: {'agent_name': 'coordinator'}
...
Demo completed!
[END] team_as_sub_agent (exit_code=0)
```

## 结果分析（是否符合要求）

符合本示例测试要求：`exit_code=0`；日志中出现 coordinator ↔ finance_team 的 transfer、成员分析与 report 流程，与子 Agent + 转账机制说明一致。

## 适用场景建议

- 需要在统一入口下组合「小团队」与「独立专家」路由时使用 `sub_agents` + `transfer_to_agent`
- 适合作为多业务线调度、升级到人/到报告节点的参考实现
