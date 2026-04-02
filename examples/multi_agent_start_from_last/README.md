# start_from_last_agent 随访示例

本示例演示多 Agent 协作中开启 `start_from_last_agent=True` 时，用户后续提问保持在最近活跃的子 Agent（如销售顾问），而不是回到协调者重新路由。

## 关键特性

- 首轮由 coordinator `transfer_to_agent` 到 `sales_consultant`
- 第二、三轮无再次显式 transfer 日志，仍由 `sales_consultant` 调用 `get_product_info` 应答
- 展示会话 ID 与多轮用户输入

## Agent 层级结构说明

```text
coordinator（协调 Agent）
├── sales_consultant（子 Agent，工具 get_product_info）
└── ...（其他子 Agent，按 agent 定义）
```

关键文件：

- [examples/multi_agent_start_from_last/agent/agent.py](./agent/agent.py)
- [examples/multi_agent_start_from_last/run_agent.py](./run_agent.py)
- [examples/multi_agent_start_from_last/.env](./.env)

## 关键代码解释

- Runner/Team 配置 `start_from_last_agent=True`
- 用户 Turn 2/3 的意图延续由上次活跃子 Agent 直接处理

## 环境与运行

### 环境要求

- Python 3.10+（推荐 3.12）

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 环境变量要求

在 [examples/multi_agent_start_from_last/.env](./.env) 中配置（或通过 `export` 设置）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/multi_agent_start_from_last
python3 run_agent.py
```

## 运行结果（实测）


```text
Multi-Agent Demo: start_from_last_agent=True
[Turn 1] User: I'm interested in your smart speakers...
[coordinator] Tool: transfer_to_agent({'agent_name': 'sales_consultant'})
[sales_consultant] Tool: get_product_info({'product_type': 'speakers'})
...
[Turn 2] User: What about the display products?
[sales_consultant] Tool: get_product_info({'product_type': 'displays'})
...
Demo completed!
[END] multi_agent_start_from_last (exit_code=0)
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- Turn 2/3 仍由 `sales_consultant` 处理，体现“留在上一子 Agent”；`exit_code=0`，`error.txt` 为空

## 适用场景建议

- 客服场景中用户连续追问同一业务线，减少重复路由延迟
- 与显式每次从 coordinator 开始的行为做 A/B 对比
