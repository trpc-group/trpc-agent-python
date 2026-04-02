# TeamAgent 并行委派示例

本示例演示 `TeamAgent` 设置 `parallel_execution=True` 时，Leader 在同一轮内向多名成员下发 `delegate_to_member`，底层以并发方式执行并再综合结果。

## 关键特性

- `parallel_execution=True`：多成员任务 `asyncio.gather` 式并发（日志中带时间戳可观察重叠）
- 三名分析师：`market_analyst`、`competitor_analyst`、`risk_analyst`
- `share_member_interactions=True` 与 `get_current_date` 辅助 Leader 规划

## Agent 层级结构说明

- 根节点：`TeamAgent`（`analysis_team`）
  - 成员：`market_analyst`、`competitor_analyst`、`risk_analyst`（均为 `LlmAgent`）

## 关键代码解释

- `agent/agent.py`：`TeamAgent(..., parallel_execution=True, ...)`
- `run_agent.py`：单用户长查询，打印带 `[秒]` 前缀的时间线
- `agent/tools.py`：`analyze_market_trends`、`analyze_competitor`、`analyze_risks`

## 环境与运行

- Python 3.10+；仓库根目录 `pip install -e .`
- 配置 `TRPC_AGENT_API_KEY`、`TRPC_AGENT_BASE_URL`、`TRPC_AGENT_MODEL_NAME`

```bash
cd examples/team_parallel_execution
python3 run_agent.py
```

## 运行结果（实测）


```
[START] team_parallel_execution
...
[3.97s] [analysis_team] Tool: delegate_to_member
...
[3.97s] [analysis_team] Tool: delegate_to_member
...
Demo completed in 42.60 seconds!
Note: With parallel_execution=True, the three analyst delegations
execute concurrently.
[END] team_parallel_execution (exit_code=0)
```

## 结果分析（是否符合要求）

符合本示例测试要求：`exit_code=0`；同一时间戳上多条委派与文末耗时说明与并行模式相符，最终合成报告覆盖市场、竞品与监管风险。

## 适用场景建议

- 多源独立分析（可并行）且最后需 Leader 汇总时使用 `parallel_execution=True`
- 若成员之间有严格顺序依赖，应关闭并行或拆成多轮委派
