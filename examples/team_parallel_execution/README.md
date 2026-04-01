# TeamAgent 并行执行示例

本示例演示 TeamAgent 的 `parallel_execution=True` 功能，该功能支持多个成员委派的并发执行。

## 功能说明

当 TeamAgent 设置 `parallel_execution=True` 时：
- 单次领导者回合中的多个委派信号将并发执行
- 使用 `asyncio.gather` 实现并行执行
- 显著减少委派多个成员时的总执行时间

### 顺序执行 vs 并行执行

```
顺序执行 (parallel_execution=False):
  Leader -> analyst1 (1s) -> analyst2 (1s) -> analyst3 (1s)
  总时间: 3 秒

并行执行 (parallel_execution=True):
  Leader -> [analyst1 | analyst2 | analyst3] (同时运行)
  总时间: ~1 秒 (取决于最长的单个执行时间)
```

## 团队结构

- **analysis_team** (设置 `parallel_execution=True` 的 TeamAgent)
  - **market_analyst**: 使用 `analyze_market_trends` 工具分析市场趋势
  - **competitor_analyst**: 使用 `analyze_competitor` 工具分析竞争对手
  - **risk_analyst**: 使用 `analyze_risks` 工具评估风险

## 环境要求

Python 版本: 3.10+（强烈建议使用 3.12）

## 运行方法

1. 下载并安装 trpc-agent-python

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

2. 在 `.env` 文件中设置环境变量（也可以通过 export 设置）:
   - TRPC_AGENT_API_KEY
   - TRPC_AGENT_BASE_URL
   - TRPC_AGENT_MODEL_NAME

3. 运行示例:

```bash
cd examples/team_parallel_execution/
python3 run_agent.py
```

## 预期行为

本示例发送一个查询，触发领导者同时委派给所有三个分析师：

```
用户: Please provide a comprehensive analysis of the technology sector, 
      including Google as a key competitor, and assess regulatory risks.

预期流程:
1. Leader 调用 delegate_to_member 委派给 market_analyst
2. Leader 调用 delegate_to_member 委派给 competitor_analyst  
3. Leader 调用 delegate_to_member 委派给 risk_analyst
4. 三个分析师并行执行
5. Leader 综合所有结果
```

## 示例输出

```
Parallel Execution Team Example
Demonstrates parallel_execution=True: Leader -> [analyst1 | analyst2 | analyst3]

======================================================================
Parallel Execution Team Demo
======================================================================

Session ID: abc12345...

This demo shows how TeamAgent executes multiple delegations in PARALLEL.
The leader will delegate to 3 analysts simultaneously, and they will
execute concurrently using asyncio.gather.

----------------------------------------------------------------------

User: Please provide a comprehensive analysis of the technology sector...
--------------------------------------------------

[0.50s] [analysis_team] Tool: delegate_to_member
         Args: {'member_name': 'market_analyst', 'task': '...'}

[0.51s] [analysis_team] Tool: delegate_to_member
         Args: {'member_name': 'competitor_analyst', 'task': '...'}

[0.52s] [analysis_team] Tool: delegate_to_member
         Args: {'member_name': 'risk_analyst', 'task': '...'}

[1.20s] [market_analyst] Tool: analyze_market_trends
[1.21s] [competitor_analyst] Tool: analyze_competitor
[1.22s] [risk_analyst] Tool: analyze_risks

... (由于并行执行，三个分析师几乎同时完成)

[2.50s] [analysis_team] Based on the comprehensive analysis from all three...

======================================================================
Demo completed in 2.50 seconds!
======================================================================

Note: With parallel_execution=True, the three analyst delegations
execute concurrently. If this were sequential, total time would be
significantly longer (sum of all analyst execution times).
```

## 关键代码

关键设置在 `agent/agent.py` 中：

```python
analysis_team = TeamAgent(
    name="analysis_team",
    model=model,
    members=[market_analyst, competitor_analyst, risk_analyst],
    instruction=LEADER_INSTRUCTION,
    parallel_execution=True,  # 启用并行执行！
    share_member_interactions=True,
)
```

## 实现细节

当 `parallel_execution=True` 且存在多个委派信号时：

1. TeamAgent 从领导者的响应中检测到多个 `DelegationSignal` 对象
2. 不再使用 for 循环顺序执行，而是调用 `_execute_delegations_parallel` 方法
3. `asyncio.gather` 并发运行所有成员执行
4. 收集所有结果并提供给领导者进行综合分析
