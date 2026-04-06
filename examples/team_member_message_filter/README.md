# 成员消息过滤示例

本示例演示 `TeamAgent` 的 `member_message_filter`：对 `analyst` 指定自定义异步过滤器（基于 `keep_last_member_message` 并附加日志），控制 Leader 看到的成员消息聚合形态。

## 关键特性

- `create_team_with_custom_filter()` 使用 `message_filter={"analyst": custom_keep_message}`
- `custom_keep_message` 内部调用 `keep_last_member_message` 再打印调试块
- 分析师多步调用 `fetch_sales_data`、`calculate_statistics` 等，适合观察过滤效果

## Agent 层级结构说明

- 根节点：`TeamAgent`（`analysis_team`）
  - 成员：`analyst`（`LlmAgent`）

## 关键代码解释

- `agent/agent.py`：`create_team(..., member_message_filter=...)` 与 `root_agent = create_team_with_custom_filter()`
- `run_agent.py`：简化打印 tool call/response 与流式文本
- `agent/tools.py`：销售拉取与统计、趋势分析等模拟工具

## 环境与运行

- Python 3.12；仓库根目录 `pip install -e .`
- 配置 `TRPC_AGENT_API_KEY`、`TRPC_AGENT_BASE_URL`、`TRPC_AGENT_MODEL_NAME`

```bash
cd examples/team_member_message_filter
python3 run_agent.py
```

## 运行结果（实测）

```txt
[START] team_member_message_filter
...
[analysis_team] Tool call: delegate_to_member
...
[analyst] Tool call: fetch_sales_data
...
[analyst] Tool call: calculate_statistics
...
### Summary of Analysis
This year's sales performance shows strong growth across all regions...
...
[END] team_member_message_filter (exit_code=0)
```

## 结果分析（是否符合要求）

符合本示例测试要求：`exit_code=0`；多步工具调用后仍能产出区域销售总结，自定义过滤逻辑在运行中可被触发（控制台附加日志），与 `member_message_filter` 演示目标一致。

## 适用场景建议

- 成员中间步骤冗长、Leader 只需末条结论时，使用 `keep_last_member_message` 或按成员名字典配置过滤器
- 可在过滤器中接入日志、裁剪或结构化提取再喂给 Leader
