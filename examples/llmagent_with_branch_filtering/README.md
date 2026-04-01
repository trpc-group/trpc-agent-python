# LLM Agent 使用Branch过滤历史消息示例

本示例演示如何使用 `message_branch_filter_mode` 参数控制多 Agent 层级结构中各 Agent 对历史消息的可见范围。

## 关键特性

- **分支级别的消息隔离**：通过 `message_branch_filter_mode` 参数控制 Agent 在多 Agent 协作场景中对历史消息的可见性，粒度为每个 Agent 所在的分支
- **三种过滤模式**：
  - `BranchFilterMode.ALL`：Agent 可以看到所有分支的消息，适合需要完整上下文的场景
  - `BranchFilterMode.PREFIX`：Agent 只能看到祖先、自身和后代分支的消息，实现层级隔离
  - `BranchFilterMode.EXACT`：Agent 仅能看到自身分支的消息，实现完全隔离
- **多场景对比**：本示例同时运行三种模式，直观展示分支过滤对 Agent 上下文可见性的影响

## Agent 层级结构

```
CustomerService (EXACT - always) - 主协调器
├── TechnicalSupport (configurable: ALL/PREFIX/EXACT) - 处理技术问题
│   └── DatabaseExpert (same as TechnicalSupport) - 数据库专家
└── BillingSupport (same as TechnicalSupport) - 处理账单查询
```

分支命名：
- CustomerService: `"CustomerService"`
- TechnicalSupport: `"CustomerService.TechnicalSupport"`
- DatabaseExpert: `"CustomerService.TechnicalSupport.DatabaseExpert"`
- BillingSupport: `"CustomerService.BillingSupport"`

## 环境要求

Python版本: 3.10+（强烈建议使用3.12）

## 在trpc-agent-python框架代码下如何运行此代码示例

1. 下载trpc-agent-python代码并安装

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

2. 运行此代码示例

在 `.env` 文件中设置使用 LLM 相关的变量（也可以通过export设置）:
- TRPC_AGENT_API_KEY
- TRPC_AGENT_BASE_URL
- TRPC_AGENT_MODEL_NAME

然后运行下面的命令：

```bash
cd examples/llmagent_with_branch_filtering/
python3 run_agent.py
```

## 关键代码解释

### 1. 创建带分支过滤的 Agent 层级（agent/agent.py）

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.agents import BranchFilterMode

database_expert = LlmAgent(
    name="DatabaseExpert",
    model=model,
    instruction=DATABASE_EXPERT_INSTRUCTION,
    tools=[FunctionTool(diagnose_database_issue)],
    # 核心参数：控制该 Agent 能看到哪些分支的历史消息
    # 每个 Agent 在层级中有唯一的分支路径（如 "CustomerService.TechnicalSupport.DatabaseExpert"），
    # message_branch_filter_mode 决定了在组装 LLM prompt 时，按分支路径过滤历史消息的策略
    message_branch_filter_mode=filter_mode,
)

technical_support = LlmAgent(
    name="TechnicalSupport",
    model=model,
    instruction=TECHNICAL_SUPPORT_INSTRUCTION,
    tools=[FunctionTool(check_server_status)],
    sub_agents=[database_expert],
    # 同一层级中的多个 sub-agent 可以使用不同的过滤模式，
    # 这里统一使用 filter_mode 以便对比演示
    message_branch_filter_mode=filter_mode,
)

customer_service = LlmAgent(
    name="CustomerService",
    model=model,
    instruction=CUSTOMER_SERVICE_INSTRUCTION,
    sub_agents=[technical_support, billing_support],
    # 根节点使用 EXACT：只看自身分支消息，不受子 Agent 消息干扰，
    # 保证路由决策仅基于用户原始请求
    message_branch_filter_mode=BranchFilterMode.EXACT,
)
```

`message_branch_filter_mode` 是 `LlmAgent` 的构造参数，用于设定 Agent 在多 Agent 层级中如何过滤不同分支的消息。

**工作原理**：在多 Agent 协作中，每个 Agent 拥有唯一的分支路径（由 Agent 名称按层级用 `.` 拼接而成）。当 Agent 需要调用 LLM 时，框架会根据 `message_branch_filter_mode` 决定将 Session 中哪些分支的历史消息注入到 LLM 的 prompt 中。

**三种模式详解**：

- `BranchFilterMode.ALL`：不做任何过滤，Agent 可以看到 Session 中所有分支的消息。适合需要全局上下文的协作场景，但可能引入无关信息
- `BranchFilterMode.PREFIX`：Agent 只能看到分支路径与自身有前缀关系的消息。例如 `DatabaseExpert`（路径 `CustomerService.TechnicalSupport.DatabaseExpert`）可以看到 `CustomerService`（祖先）、`TechnicalSupport`（父级）和自身的消息，但看不到 `BillingSupport`（兄弟分支）。适合层级工作流，既保留上下文传递，又实现部门间隔离
- `BranchFilterMode.EXACT`：Agent 仅能看到自身分支产生的消息，与其他所有分支完全隔离。适合无状态操作或需要最大隐私保护的场景

### 2. 多场景对比运行（run_agent.py）

```python
test_scenarios = [
    {"title": "BranchFilterMode.ALL",    "filter_mode": BranchFilterMode.ALL},
    {"title": "BranchFilterMode.PREFIX", "filter_mode": BranchFilterMode.PREFIX},
    {"title": "BranchFilterMode.EXACT",  "filter_mode": BranchFilterMode.EXACT},
]
```

示例通过同一组客户支持对话在三种模式下运行：
- **ALL 模式**：BillingSupport 可以看到 TechnicalSupport 和 DatabaseExpert 讨论的技术问题
- **PREFIX 模式**：BillingSupport 只能看到 CustomerService（父节点）和自身的消息，无法看到 TechnicalSupport 分支
- **EXACT 模式**：每个 Agent 只能看到自身分支的消息，完全隔离

### 3. 核心区别：分支路径匹配

> 缩写说明：CS = CustomerService，TS = TechnicalSupport，DB = DatabaseExpert，BS = BillingSupport

| Agent | 分支路径 | ALL 可见 | PREFIX 可见 | EXACT 可见 |
|-------|---------|---------|------------|-----------|
| CustomerService (CS) | `CustomerService` | 全部 | 全部 | 仅 CS |
| TechnicalSupport (TS) | `CustomerService.TechnicalSupport` | 全部 | CS + TS + DB（自身及上下级） | 仅 TS |
| DatabaseExpert (DB) | `CustomerService.TechnicalSupport.DatabaseExpert` | 全部 | CS + TS + DB（自身及上级链路） | 仅 DB |
| BillingSupport (BS) | `CustomerService.BillingSupport` | 全部 | CS + BS（自身及上级，看不到 TS/DB 兄弟分支） | 仅 BS |

## 适用场景

| 场景 | 推荐模式 |
|------|---------|
| 需要完整上下文的协作场景 | `BranchFilterMode.ALL`，所有 Agent 共享全部对话 |
| 部门层级隔离 | `BranchFilterMode.PREFIX`，子 Agent 继承父 Agent 上下文 |
| 最大隐私保护 | `BranchFilterMode.EXACT`，每个 Agent 完全独立 |
| 平行独立任务 | `BranchFilterMode.EXACT`，避免无关上下文干扰 |
| 层级工作流（上下级协作） | `BranchFilterMode.PREFIX`，保留层级关系的上下文 |
