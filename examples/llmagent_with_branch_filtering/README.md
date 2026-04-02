# LLM Agent 分支历史过滤示例

本示例演示如何基于 `LlmAgent` 在多 Agent 协作链路中使用 `message_branch_filter_mode`，并验证 `ALL / PREFIX / EXACT` 三种模式下的历史消息可见性差异。

## 关键特性

- **分支级消息过滤**：按 Agent 所在分支路径过滤历史消息，控制可见范围
- **三种过滤模式对比**：同一组请求分别在 `ALL`、`PREFIX`、`EXACT` 模式下运行
- **多 Agent 协作链路**：覆盖客服路由、技术诊断、数据库专家、账单查询四类角色
- **可观测的行为差异**：通过工具调用参数与最终回复内容验证上下文是否被隔离
- **统一测试输入**：三种模式使用同一组请求，便于横向比较

## Agent 层级结构说明

本例是多 Agent 层级协作示例：

```text
CustomerService (EXACT - always)
├── TechnicalSupport (ALL / PREFIX / EXACT)
│   └── DatabaseExpert (ALL / PREFIX / EXACT)
└── BillingSupport (ALL / PREFIX / EXACT)
```

分支路径：

- `CustomerService`
- `CustomerService.TechnicalSupport`
- `CustomerService.TechnicalSupport.DatabaseExpert`
- `CustomerService.BillingSupport`

关键文件：

- [examples/llmagent_with_branch_filtering/agent/agent.py](./agent/agent.py)：构建多 Agent 层级与过滤模式
- [examples/llmagent_with_branch_filtering/agent/tools.py](./agent/tools.py)：技术检查、数据库诊断、账单查询工具
- [examples/llmagent_with_branch_filtering/agent/prompts.py](./agent/prompts.py)：各角色提示词
- [examples/llmagent_with_branch_filtering/agent/config.py](./agent/config.py)：模型环境变量读取
- [examples/llmagent_with_branch_filtering/run_agent.py](./run_agent.py)：三种模式对比测试入口


## 关键代码解释

这一节用于快速定位“过滤逻辑到底在哪实现、怎么生效”。

### 1) Agent 层级与过滤参数（`agent/agent.py`）

- `CustomerService` 固定使用 `BranchFilterMode.EXACT`
- `TechnicalSupport`、`DatabaseExpert`、`BillingSupport` 使用统一的 `filter_mode`（由场景传入）
- 关键参数是 `message_branch_filter_mode`，它决定该 Agent 组装 prompt 时可见哪些分支历史

### 2) 场景驱动测试（`run_agent.py`）

- 通过 `test_scenarios` 依次运行 `ALL`、`PREFIX`、`EXACT`
- 三个场景使用同一组 customer requests，保证横向对比公平
- 每个场景复用同一个 `session_id` 进行多轮对话，用于观察历史消息累积后的可见性差异

### 3) 分支可见性规则（核心）

- `ALL`：可见全部分支消息
- `PREFIX`：可见祖先 + 自身 + 后代，兄弟分支不可见
- `EXACT`：仅自身分支可见

| Agent | 分支路径 | ALL 可见 | PREFIX 可见 | EXACT 可见 |
|-------|---------|---------|------------|-----------|
| CustomerService | `CustomerService` | 全部 | 全部 | 仅自身 |
| TechnicalSupport | `CustomerService.TechnicalSupport` | 全部 | CS + TS + DB | 仅自身 |
| DatabaseExpert | `CustomerService.TechnicalSupport.DatabaseExpert` | 全部 | CS + TS + DB | 仅自身 |
| BillingSupport | `CustomerService.BillingSupport` | 全部 | CS + BS | 仅自身 |

## 环境与运行

### 环境要求

- Python 3.10+（强烈建议 3.12）

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 环境变量要求

在 [examples/llmagent_with_branch_filtering/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/llmagent_with_branch_filtering
python3 run_agent.py
```

## 运行结果（实测）

以下是实测输出要点：

```text
Scenario 1: BranchFilterMode.ALL
- BillingSupport 在账单回复中引用了前两轮技术上下文（服务状态、数据库慢查询建议）

Scenario 2: BranchFilterMode.PREFIX
- BillingSupport 回复中明确表示“看不到此前技术问题”
- 体现兄弟分支隔离（Billing 分支看不到 TechnicalSupport/DatabaseExpert 分支）

Scenario 3: BranchFilterMode.EXACT
- DatabaseExpert 工具调用参数退化为更泛化的 symptom: 'general performance issues'
- 诊断结果变为“信息不足，需要更多细节”
- BillingSupport 同样表示看不到前序技术上下文
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **`ALL` 符合预期**：跨分支上下文可见，账单分支可引用技术分支历史
- **`PREFIX` 符合预期**：仅祖先/自身/后代可见，兄弟分支隔离
- **`EXACT` 符合预期**：仅自身分支可见，隔离最强，跨分支上下文不可用
- **对比有效**：三种模式在同一输入下表现出清晰且稳定的隔离梯度

## 适用场景建议

- 需要全局协作上下文：`BranchFilterMode.ALL`
- 需要部门层级协作且避免兄弟干扰：`BranchFilterMode.PREFIX`
- 需要最大隔离和隐私：`BranchFilterMode.EXACT`
