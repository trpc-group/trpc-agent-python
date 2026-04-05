# TeamAgent 人机协同（HITL）示例

本示例演示 `TeamAgent` 在 Leader 侧挂载 `LongRunningFunctionTool(request_approval)`：当用户意图涉及「发布」等敏感动作时触发审批挂起，模拟人工通过后再继续执行。

## 关键特性

- `assistant` 成员负责 `search_info` 等信息类工具调用
- Leader 使用 `LongRunningFunctionTool` 包装审批函数，运行时可进入 pending 状态
- `run_agent.py` 模拟人工写入 `approved` 结果后恢复会话

## Agent 层级结构说明

- 根节点：`TeamAgent`（`hitl_team`）
  - 成员：`assistant`（`LlmAgent`）

## 关键代码解释

- `agent/agent.py`：`approval_tool = LongRunningFunctionTool(request_approval)` 放入 Team 的 `tools`
- `agent/tools.py`：`request_approval`、`search_info` 的实现与 HITL 演示配合
- `run_agent.py`：捕获 pending、打印 `HITL TRIGGERED!` 后注入批准结果并继续 `run_async`

## 环境与运行

- Python 3.10+；仓库根目录 `pip install -e .`
- 配置 `TRPC_AGENT_API_KEY`、`TRPC_AGENT_BASE_URL`、`TRPC_AGENT_MODEL_NAME`

```bash
cd examples/team_human_in_the_loop
python3 run_agent.py
```

## 运行结果（实测）

```txt
[START] team_human_in_the_loop
...
[hitl_team] Tool: request_approval
...
HITL TRIGGERED!
Function: request_approval
...
Waiting for human intervention...
Human provides approval: {'status': 'approved', ...
Resuming team execution...
...
HITL Flow Completed!
[END] team_human_in_the_loop (exit_code=0)
```

## 结果分析（是否符合要求）

符合本示例测试要求：`exit_code=0`；审批从 pending 到 approved、恢复执行与最终「发布」说明完整，与 HITL 设计一致。

## 适用场景建议

- 发布、扣款、对外发送等需人工确认的流程，可用 `LongRunningFunctionTool` 与外部审批系统对接
- 可与真实队列/工单系统集成，替换示例中的模拟批准
