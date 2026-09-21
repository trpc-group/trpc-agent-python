# CodeAct Generation Method 示例

本示例演示如何使用 `CodeActTool` 实现函数体为 `...` 的函数。模型根据函数
签名、类型标注和 docstring 生成 Python 实现，框架执行代码并校验返回类型。
这类函数在 NOOA 中称为 Generation Method（生成方法）。

示例先使用 `prefer_approved` 生成并审批候选，再重新创建
`approved_only` Agent，共享同一个文件仓库并执行审批代码。

## 关键特性

- 使用函数签名和 docstring 定义生成契约
- 使用 Pydantic 模型校验输入和输出
- 保存模型生成的代码候选
- 人工审批候选版本并支持回滚
- 对比 `prefer_approved` 与 `approved_only` 的执行行为
- 函数契约或可用工具变化后自动拒绝不兼容版本
- 支持 InMemory、File、Redis 和 SQL 实现仓库

## Agent 层级结构说明

```text
root_agent (LlmAgent)
└── tools
    └── analyze_order_operations (CodeActTool)
        ├── no approved version: nested CodeAct Agent generates code
        └── approved version: CodeAct Runtime executes stored code directly
```

关键文件：

- [agent/agent.py](./agent/agent.py)：订单模型、Generation Method 和
  `CodeActTool` 配置
- [agent/config.py](./agent/config.py)：模型配置
- [run_agent.py](./run_agent.py)：生成、审批和复用流程
- [.env.example](./.env.example)：环境变量模板

## 关键代码解释

Generation Method 只有函数声明，没有确定性函数体：

```python
def analyze_order_operations(
    orders: list[OrderRecord],
    overdue_days: int = 3,
    high_value_threshold: float = 1500.0,
) -> OrderOperationsReport:
    """Analyze order operations using deterministic business rules."""
    ...
```

示例将它包装为 `CodeActTool`：

```python
CodeActTool(
    analyze_order_operations,
    implementation_store=implementation_store,
    implementation_policy=implementation_policy,
)
```

第一阶段使用 `prefer_approved`：

1. 根据函数签名验证参数；
2. 查询与当前函数契约和可用工具兼容的审批版本；
3. 因测试仓库为空，启动嵌套 CodeAct Agent 生成实现；
4. 执行代码并按照 `OrderOperationsReport` 校验结果；
5. 保存、打印并审批候选版本。

第二阶段关闭第一阶段 Runner，重新创建 `approved_only` Agent：

1. 从同一个 File Store 读取审批版本；
2. 不创建 Nested Agent，直接将审批代码交给 CodeAct Runtime；
3. 使用新的 `high_value_threshold` 参数执行代码；
4. 按照相同输出 Schema 校验 `return_result`；
5. 检查候选版本集合没有变化；

`run_agent.py` 每次启动都会清理示例目录中的 `.codeact` 测试仓库，确保第一
阶段一定从无审批版本开始。示例会自动调用 `approve()`，仅用于展示策略切换；
生产环境不能照搬自动审批逻辑。

## 环境要求

- Python 3.10+，推荐 Python 3.12
- 可用的 OpenAI 兼容模型服务
- 默认使用 `InProcessCodeActRuntime`

> `InProcessCodeActRuntime` 会在 Agent 宿主进程执行模型生成的 Python，
> 不提供安全隔离，仅适用于受控开发和测试环境。

## 构建步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
./build.sh
source .venv/bin/activate
```

## 运行步骤

### 配置环境变量

```bash
cp examples/codeact_with_generation_method/.env.example \
   examples/codeact_with_generation_method/.env
```

在 `.env` 中填写：

```bash
TRPC_AGENT_API_KEY=...
TRPC_AGENT_BASE_URL=...
TRPC_AGENT_MODEL_NAME=...
```

### 运行命令

```bash
cd examples/codeact_with_generation_method
python3 run_agent.py
```

## 测试方式

### 1. `prefer_approved` 与 `approved_only` 对比测试

直接运行示例。脚本会创建并清理专用的 `.codeact` File Store，然后依次执行：

1. 使用阈值 `1500` 调用函数；
2. `prefer_approved` 在没有审批版本时生成候选代码；
3. 打印并审批候选；
4. 关闭第一阶段 Agent；
5. 使用相同 Store 创建 `approved_only` Agent；
6. 将阈值改为 `2100` 再次调用；
7. 只执行审批代码，并确认没有产生新候选版本。

预期关键输出：

```text
=== Phase 1: prefer_approved ===
Policy: prefer_approved
Store: FileCodeActImplementationStore
Candidate: <version>
Approved: <same-version>

=== Phase 2: approved_only ===
Policy: approved_only
Store: FileCodeActImplementationStore
approved_only reused: <same-version>
executed directly by the CodeAct runtime
no nested Agent or generation model was called
```

第二阶段仍会调用父 Agent 模型生成 Tool Call 和最终回答，但
`CodeActTool` 内部不会再次调用模型生成函数实现。

### 2. 纯动态生成测试

如果只需要验证模型能否生成正确实现，不需要保存或复用代码，可以使用：

```python
tool = CodeActTool(
    analyze_order_operations,
    model=model,
    implementation_policy="dynamic",
)
```

未显式配置 Store 时，每次调用都会动态生成，且不会保存候选。如果希望保留
动态生成记录用于后续评估，可以显式传入实现仓库；`dynamic` 仍然不会读取
审批版本。

### 3. 审批版本强制执行测试

先在测试环境通过 `prefer_approved` 生成、测试并审批候选，然后使用同一个
持久化仓库创建 `approved_only` Tool：

```python
store = FileCodeActImplementationStore("/tmp/codeact-generation-method-test")

tool = CodeActTool(
    analyze_order_operations,
    implementation_store=store,
    implementation_policy="approved_only",
)
```

存在兼容审批版本时会直接执行；不存在审批版本，或者函数签名、docstring、
输入输出 Schema、可用工具发生变化时，会直接报错，不会调用模型兜底。

### 4. 单元测试

```bash
.venv/bin/pytest -q tests/tools/test_code_act_tool.py
.venv/bin/pytest -q tests/tools/test_code_act_implementation.py
```

前者验证 Generation Method 的生成、审批、复用和默认内存仓库；后者验证
File、Redis 和 SQL 实现仓库的版本生命周期。

## 运行结果分析

示例中的五条订单预期得到：

```json
{
  "status_counts": {
    "paid": 1,
    "shipped": 2,
    "delivered": 1,
    "cancelled": 1
  },
  "active_amount": 3000.0,
  "overdue_order_ids": ["O-1001", "O-1005"],
  "high_value_customer_ids": ["C-A"]
}
```

审批后第二次调用将 `high_value_threshold` 改为 `2100`，预期
`high_value_customer_ids` 变为 `[]`。这说明复用的是函数实现代码，而不是
第一次调用的计算结果。

需要注意，第二次调用仍会使用父 Agent 模型生成 Tool Call 和最终自然语言
回答；省略的是 `CodeActTool` 内部用于生成函数实现的嵌套模型调用。

## 测试环境使用建议

- 优先使用 `dynamic` 或 `prefer_approved`
- 使用 InMemory Store 快速验证单进程流程
- 使用独立 File、Redis 或 SQL 命名空间，避免污染生产审批记录
- 保存每个候选版本的测试结果，再决定是否审批
- 对边界输入、异常输入和外部工具失败进行回归测试
- 函数签名、docstring 或能力列表变化后重新生成并审批
- 示例的自动审批只用于演示，不应作为测试通过的判定条件

## 生产环境使用建议

- 使用 `approved_only`，禁止在请求链路中临时生成生产代码
- 使用 Redis 或 SQL 作为多进程、多 Worker 的共享实现仓库
- File Store 仅用于确认所有请求固定落在同一主机、同一文件系统的场景
- 将候选生成、测试、审批和生产执行拆分为独立阶段
- 审批接口必须接入身份认证、权限控制、审计记录和版本回滚
- 审批前执行业务测试、安全审查和资源消耗检查
- 使用明确的审批人身份，不要把 `approved_by` 当作身份认证机制
- 固定生产依赖和可用工具，避免 `capability_hash` 意外变化
- 不要在多租户生产服务中直接使用默认进程内运行时执行不可信模型代码
- 使用容器、VM 或其他 OS 级隔离，并限制文件系统、网络、凭证和执行资源

## 适用场景建议

- 业务规则复杂但可通过输入输出契约明确描述
- 希望积累和迭代模型生成的稳定实现
- 希望审批后减少嵌套模型调用和结果波动
- 需要对生成代码进行版本管理、回滚和跨 Worker 复用
