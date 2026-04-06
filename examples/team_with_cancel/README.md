# TeamAgent 取消示例

本示例演示在 `TeamAgent` 运行过程中调用取消 API：覆盖 Leader 流式规划阶段、成员工具执行阶段等检查点，并验证取消后会话仍可基于记忆继续对话。

## 关键特性

- `Runner` 与 SDK cancel 模块配合，在收到若干事件后主动 `cancel`
- `content_team_with_cancel` 启用 `add_history_to_leader=True`、`num_history_runs=3` 保留上下文
- 工具侧通过延迟模拟长操作，便于命中取消窗口

## Agent 层级结构说明

- 根节点：`TeamAgent`（`content_team_with_cancel`）
  - 成员：`researcher`（`LlmAgent`）、`writer`（`LlmAgent`）

## 关键代码解释

- `agent/agent.py`：与基础内容团队类似，但团队名与历史选项针对 cancel 演示调整
- `run_agent.py`：多场景顺序执行——先触发取消再追问「what happened?」等
- `agent/tools.py`：`search_web`、`check_grammar` 等（含延迟）

## 环境与运行

- Python 3.12；仓库根目录 `pip install -e .`
- 配置 `TRPC_AGENT_API_KEY`、`TRPC_AGENT_BASE_URL`、`TRPC_AGENT_MODEL_NAME`

```bash
cd examples/team_with_cancel
python3 run_agent.py
```

## 运行结果（实测）

```txt
[START] team_with_cancel
...
⏸️  Requesting cancellation after 10 events...
Run marked for cancellation ...
TeamAgent 'content_team_with_cancel' cancelled during leader planning
...
❌ Team execution was cancelled: Run for session ... was cancelled
...
📝 User Query 2: what happened?
...
It seems the previous task ... was interrupted by a cancellation.
...
[END] team_with_cancel (exit_code=0)
```

## 结果分析（是否符合要求）

符合本示例测试要求：`exit_code=0`；日志明确记录取消发生阶段与后续追问的上下文恢复，说明取消与记忆行为符合演示预期。

## 适用场景建议

- 长时团队任务需要用户中止、超时或上游信号中断时，接入 cancel 与会话历史策略
- 可根据业务选择保留部分 partial 结果供下一轮继续
