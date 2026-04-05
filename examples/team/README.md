# TeamAgent 内容团队（Coordinate）示例

本示例演示 `TeamAgent` 在 Coordinate 模式下由 Leader 依次委派研究员与写手，在同一会话中完成多轮「撰稿 + 补充」类任务。

## 关键特性

- `share_member_interactions=True`，Leader 可综合成员工具调用与回复
- Leader 持有 `get_current_date`；成员分别持有 `search_web`、`check_grammar`
- 两轮用户消息共享同一 `session_id`，展示多轮对话

## Agent 层级结构说明

- 根节点：`TeamAgent`（`content_team`）
  - 成员：`researcher`（`LlmAgent`）、`writer`（`LlmAgent`）

## 关键代码解释

- `agent/agent.py`：构造 `researcher`、`writer` 与 `TeamAgent`，传入 `LEADER_INSTRUCTION` 等
- `run_agent.py`：创建单次会话，循环发送两条 `demo_queries` 并打印事件
- `agent/tools.py`：模拟 `search_web`、`check_grammar`、`get_current_date`

## 环境与运行

- Python 3.10+；仓库根目录 `pip install -e .`
- 配置 `TRPC_AGENT_API_KEY`、`TRPC_AGENT_BASE_URL`、`TRPC_AGENT_MODEL_NAME`

```bash
cd examples/team
python3 run_agent.py
```

## 运行结果（实测）

```txt
[START] team
Content Team Demo - Coordinate Mode
...
[content_team] Tool: delegate_to_member, Args: {'member_name': 'researcher', ...
[researcher] Tool: search_web, Args: {'query': 'renewable energy trends and statistics 2026'}
...
[content_team] Tool: delegate_to_member, Args: {'member_name': 'writer', ...
...
Demo completed!
[END] team (exit_code=0)
```

## 结果分析（是否符合要求）

符合本示例测试要求：`exit_code=0`；两轮对话均出现 Leader 委派、成员工具调用与最终成文输出，流程与 Coordinate 模式设计一致。

## 适用场景建议

- 需要「检索 / 草稿 / 审核」等多角色流水线且由单一团队入口编排时使用 `TeamAgent`
- 可在此结构上替换工具实现或增加成员
