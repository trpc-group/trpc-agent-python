# 子 Agent 路由示例

本示例演示协调型 Agent 根据用户问题 `transfer_to_agent` 到技术支持或销售顾问，并由子 Agent 调用领域工具完成回答。

## 关键特性

- 场景一：设备故障类问题 → `technical_support` + `check_system_status`
- 场景二：产品咨询 → `sales_consultant` + `get_product_info`
- 日志打印 consult id 生成与转交结果

## Agent 层级结构说明

```text
customer_service_coordinator
├── technical_support (LlmAgent + check_system_status)
└── sales_consultant (LlmAgent + get_product_info)
```

关键文件：

- [examples/multi_agent_subagent/agent/agent.py](./agent/agent.py)
- [examples/multi_agent_subagent/run_agent.py](./run_agent.py)
- [examples/multi_agent_subagent/.env](./.env)

## 关键代码解释

- 协调者先 `generate_consult_id`，再 `transfer_to_agent`
- 子 Agent 独立工具集，体现多角色客服分流

## 环境与运行

### 环境要求

- Python 3.12

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 环境变量要求

在 [examples/multi_agent_subagent/.env](./.env) 中配置（或通过 `export` 设置）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/multi_agent_subagent
python3 run_agent.py
```

## 运行结果（实测）


```text
Scenario 1: Technical Support
🔧 [customer_service_coordinator] Invoke Tool: transfer_to_agent ... 'technical_support'
🔧 [technical_support] Invoke Tool: check_system_status ... 'speaker'
🔧 [technical_support] Tool Result: {'result': 'System diagnostic for speaker: Status OK...'}
...
Scenario 2: Sales Inquiry
🔧 [customer_service_coordinator] Invoke Tool: transfer_to_agent ... 'sales_consultant'
🔧 [sales_consultant] Invoke Tool: get_product_info ... 'security'
[END] multi_agent_subagent (exit_code=0)
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- 两场景均完成转交、工具调用与回复；`exit_code=0`，`error.txt` 为空

## 适用场景建议

- 企业前台统一入口 + 后端专业座席的多 Agent 设计参考
- 教学演示 transfer 工具与多工具集隔离
