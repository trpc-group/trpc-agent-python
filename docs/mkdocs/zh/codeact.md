# CodeAct

CodeAct 是 `LlmAgent` 的可选执行模式。启用后，模型通过生成 Python 代码完成
工具编排、数据处理和结果提交，而不是只依赖传统 Function Calling。

tRPC-Agent-Python 的 CodeAct 设计参考了
[NVIDIA NeMo Labs OO Agents（NOOA）](https://github.com/NVIDIA-NeMo/labs-OO-Agents)，
并结合现有 Agent、Tool、Runner 和 Event 体系实现。

## 概述

CodeAct 提供两类能力：

### CodeAct Tools

模型在持久 Python 环境中通过 `self.<tool>(...)` 调用 Agent 已配置的工具：

```python
numbers = await self.load_numbers(count=100000)
total = sum(number * number for number in numbers if number % 7 == 0)
return_result({"value": total})
```

工具返回的大对象可以保留在本地 ObjectStore，后续代码继续使用同一个变量，
不需要把完整对象反复序列化到模型上下文。

完整运行、测试和结果分析请参考
[CodeAct Tools 示例 README](../../../examples/codeact_with_tools/README.md)。

### Generation Method

`CodeActTool` 可以为函数体只有 `...` 的函数动态生成实现。函数名、参数、
类型标注和 docstring 共同定义实现契约：

```python
def analyze_order_operations(
    orders: list[OrderRecord],
    overdue_days: int = 3,
) -> OrderOperationsReport:
    """Analyze order statuses, amounts and overdue orders."""
    ...
```

生成成功的代码可以保存为候选版本，经过人工审批后直接复用，减少后续模型调用。

完整的生成、审批、复用和回滚流程请参考
[Generation Method 示例 README](../../../examples/codeact_with_generation_method/README.md)。

## 功能特性

### 使用 Python 编排工具

模型可以使用循环、分支、推导式、聚合、异常处理和临时变量组合多个工具，
不需要为每个中间步骤单独设计工具。

CodeAct 不会替换现有工具体系。`FunctionTool`、`BaseToolSet`、参数转换、
`InvocationContext`、filters 以及 before/after callbacks 仍按原有流程工作，
只是模型改为通过 Python 中的 `self` 代理调用它们。

### 持久 Python 变量

同一次 invocation 中，每轮生成的代码共享一组 Python 全局变量。第一轮加载的
数据可以在后续轮次继续使用，无需再次调用工具。

该状态只在当前 invocation 内有效。invocation 结束后，Runtime 会清理变量和
ObjectStore。

### 大对象按引用保留

工具返回值经过 `CodeActObjectStore.wrap(...)` 处理：

- 小型、可 JSON 序列化的值直接返回；
- 大型列表、字典和复杂对象保存在 ObjectStore；
- Python 环境获取指向真实对象的 `CodeActObjectRef`；
- 模型只看到类型、长度和有限预览。

`ObjectRef` 支持索引、切片、迭代、`len()` 和公开方法调用，因此生成代码仍可
直接处理真实对象。

ObjectRef 的作用是避免框架自动传输完整对象，并不是强制防泄露机制。如果代码
主动执行 `print(list(value))`，完整内容仍会进入模型上下文。

### 能力按需发现

模型可以使用 `doc()` 查看当前可用工具和对象信息：

```python
print(doc(self))
print(doc(self.load_numbers))
print(doc(numbers))
```

这避免了把所有工具的完整 Schema 和大型对象内容长期放在模型请求中。

### 结构化结果校验

模型使用 `return_result(value)` 提交最终结果。配置 `output_schema` 后，
框架会使用 Pydantic 校验返回值：

```python
class CalculationResult(BaseModel):
    expression: str
    value: int
```

校验失败时，框架生成 `codeact.validation_error` 事件，并允许模型在新的代码
单元中修正。校验成功后生成 `object="codeact.result"` 的最终 Event，不再额外
调用模型生成总结文本。

### 单代码单元执行

每次模型响应只执行第一个 fenced `python` 代码块。代码块后的文本或其他代码块
不会被推测性执行，模型必须等待真实执行结果后再决定下一步。

`return_result(...)` 会立即结束当前代码单元，防止提交最终结果后继续执行有
副作用的代码。

### 生成未完成函数

`CodeActTool` 只接受函数体由可选 docstring 和 `...` 组成的函数。调用时会：

1. 根据函数签名验证输入；
2. 创建嵌套 CodeAct invocation；
3. 通过 `self.get_function_arguments()` 提供本次调用参数；
4. 允许生成代码调用显式配置或继承的工具；
5. 根据函数返回类型校验结果；
6. 将结果作为普通 Tool Response 返回父 Agent。

该能力不会修改源文件中的函数体。普通 `FunctionTool` 仍执行开发者编写的确定性
代码，只有显式使用 `CodeActTool` 的函数才会由模型实现。

### 固化与复用生成代码

`CodeActTool` 可以保存通过输入和输出校验的生成代码。候选版本经过审批后，
后续调用直接由 CodeAct Runtime 执行，无需再次创建嵌套 Agent 或请求模型。

框架会根据函数模块、名称、签名、docstring、输入/输出 Schema 计算契约哈希，
并根据可调用工具计算能力哈希。契约或工具能力发生变化后，旧实现不会自动复用。

### CodeAct Runtime 与 CodeExecutor 解耦

CodeAct Runtime 用于执行模型生成的控制代码；CodeExecutor 用于普通代码块、
Skill 脚本或用户指定代码。两者可以同时配置，但职责不同：

```text
模型控制代码 → CodeAct Runtime
Skill / 用户代码 → Local、Container 或 Cube CodeExecutor
```

启用 CodeAct 后，模型响应中的 Python 代码只交给 `code_act.runtime`，
`code_executor` 不会重复执行同一个代码块。业务工具仍可通过明确入口调用
CodeExecutor，在容器或远端沙箱中执行用户代码。

## 工作流程

### CodeAct Tools 执行流程

```text
用户请求
→ LlmAgent 注入 CodeAct 执行协议
→ 模型生成一个 Python Cell
→ CodeActResponseProcessor 提取代码
→ CodeAct Runtime 执行代码
→ self.<tool>(...) 调用现有工具
→ 大对象保留为 ObjectRef
→ 执行观察返回模型
→ 后续 Cell 复用已有变量
→ return_result(...) 提交并校验结果
```

### Generation Method 执行流程

```text
父 Agent 调用 CodeActTool
→ 校验函数参数
→ 查询兼容的审批实现
├─ 找到：Runtime 直接执行审批代码
└─ 未找到：嵌套 CodeAct Agent 生成并执行代码
→ 校验函数返回类型
→ 保存成功候选（配置 Store 时）
→ 返回父 Agent
```

## 架构说明

### 请求处理

配置 `LlmAgent.code_act` 后，框架向模型注入 CodeAct 协议，包括：

- 每次响应只生成一个 Python 代码块；
- 使用 `await self.<tool>(...)` 调用工具；
- 使用 `doc()` 查看工具和对象；
- 使用 `print(...)` 提交中间观察；
- 使用 `return_result(...)` 提交最终结果；
- 在 `max_iterations` 范围内完成任务。

在支持工具的 Runtime 中，Agent 工具不会再作为普通 provider Function
Calling Schema 发送，模型通过 `self` 代理访问工具。

### 响应处理

`CodeActResponseProcessor` 提取首个 Python 代码块，注入
`return_result(...)` 协议并交给 Runtime。Runtime 返回 stdout、stderr 和
执行状态后，Processor 创建代码执行 Event。

未调用 `return_result(...)` 时，观察结果进入历史并触发下一次模型调用；
提交有效结果后结束当前 Agent 循环。

### 持久运行时

默认 `InProcessCodeActRuntime` 按 invocation 保存：

```python
class _ExecutionState:
    globals: dict[str, Any]
    store: CodeActObjectStore
    agent_proxy: CodeActAgentProxy
```

- `globals`：保存跨代码单元变量；
- `store`：保存大型或复杂 Python 对象；
- `agent_proxy`：将 Agent 工具暴露为 `self.<tool>(...)`。

代码使用 `ast.PyCF_ALLOW_TOP_LEVEL_AWAIT` 编译，因此可以直接在顶层调用异步
工具，不需要创建事件循环。

### 工具代理

`CodeActAgentProxy` 解析当前 Agent 的 `BaseTool` 和 `BaseToolSet`，并按名称
建立映射：

```text
self.load_numbers(count=100000)
→ CodeActAgentProxy
→ BaseTool.run_async(...)
→ FunctionTool
→ load_numbers(...)
```

工具代理保留以下原有能力：

- 必填参数与类型检查；
- `tool_context` 自动注入；
- 同步和异步函数兼容；
- filters 与 callbacks；
- invocation 工具调用次数限制。

当前代理要求使用关键字参数，并通过 `await` 调用：

```python
numbers = await self.load_numbers(count=100000)
```

位置参数和进度流式工具暂不支持。

## 基本用法

### 启用 CodeAct Tools

```python
from pydantic import BaseModel

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.codeact import CodeActConfig
from trpc_agent_sdk.tools import FunctionTool


class CalculationResult(BaseModel):
    expression: str
    value: int


def load_numbers(count: int) -> list[int]:
    """Return integers from zero up to, but excluding, count."""
    return list(range(count))


agent = LlmAgent(
    name="codeact_calculator",
    model=model,
    instruction="Use load_numbers once and calculate the requested result.",
    tools=[FunctionTool(load_numbers)],
    code_act=CodeActConfig(),
    output_schema=CalculationResult,
)
```

用户指令只需要说明使用哪个工具，不需要要求用户或业务提示词写出
`await self.load_numbers(...)`。`CodeActConfig` 生成的系统协议会指导模型把
工具名转换为正确的 Python 调用。

运行方式、完整代码、预期结果和真实 Tool Calling 对比请参考
[CodeAct Tools 示例 README](../../../examples/codeact_with_tools/README.md)。

### 启用 Generation Method

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.tools import CodeActTool


def analyze_order_operations(
    orders: list[OrderRecord],
    overdue_days: int = 3,
) -> OrderOperationsReport:
    """Analyze order statuses, amounts and overdue orders."""
    ...


agent = LlmAgent(
    name="order_agent",
    model=model,
    tools=[CodeActTool(analyze_order_operations)],
)
```

`CodeActTool` 会根据函数契约生成实现，并按照 `OrderOperationsReport` 校验
返回结果。完整业务输入、生成代码和执行结果请参考
[Generation Method 示例 README](../../../examples/codeact_with_generation_method/README.md)。

## 配置说明

### CodeActConfig

```python
from trpc_agent_sdk.codeact import CodeActConfig
from trpc_agent_sdk.codeact import InProcessCodeActRuntime

config = CodeActConfig(
    runtime=InProcessCodeActRuntime(),
    max_iterations=10,
    require_result=True,
)
```

- `runtime`：执行模型代码的 Runtime，默认是 `InProcessCodeActRuntime`；
- `max_iterations`：一次 invocation 允许的最大模型/代码循环次数，默认 `10`；
- `require_result`：是否要求模型必须通过 `return_result(...)` 结束；
- `text_only_retry_message`：模型只返回文本时使用的重试提示。

### CodeActTool

```python
tool = CodeActTool(
    analyze_order_operations,
    model=model,
    tools=capability_tools,
    code_act=CodeActConfig(),
    filters=filters,
    implementation_store=store,
    implementation_policy="prefer_approved",
)
```

- `model`：生成函数实现的模型；未配置时使用调用上下文中的模型；
- `tools`：生成代码可调用的额外工具；
- `code_act`：嵌套调用使用的 CodeAct 配置；
- `filters`：应用于 Tool 的过滤器；
- `implementation_store`：候选和审批实现仓库；
- `implementation_policy`：代码生成与审批版本的选择策略。

## 实现固化

### 策略

`implementation_policy` 支持：

- `dynamic`：每次都由模型生成；配置 Store 时保存成功候选；
- `prefer_approved`：优先执行兼容的审批版本，没有时动态生成；
- `approved_only`：只执行兼容的审批版本，没有时直接报错。

非 `dynamic` 策略没有显式配置 Store 时，默认使用
`InMemoryCodeActImplementationStore`。该默认值不会跨进程或重启保留数据，
生产环境应配置持久化 Store。

### 审批与回滚

```python
candidates = await tool.list_implementations()
candidate = candidates[-1]

await tool.approve(candidate.version, approved_by="reviewer")
```

重新审批历史版本即可回滚：

```python
await tool.approve(previous_version, approved_by="rollback")
```

审批只表示选择要复用的代码版本。业务正确性、安全性、权限和性能测试仍应由
应用在审批前完成。

### 存储后端

框架提供以下实现：

- `InMemoryCodeActImplementationStore`：单进程测试；
- `FileCodeActImplementationStore`：单机文件持久化；
- `RedisCodeActImplementationStore`：多 Worker 共享；
- `SqlCodeActImplementationStore`：关系数据库持久化。

```python
from trpc_agent_sdk.codeact import FileCodeActImplementationStore
from trpc_agent_sdk.codeact import RedisCodeActImplementationStore
from trpc_agent_sdk.codeact import SqlCodeActImplementationStore

file_store = FileCodeActImplementationStore(".codeact")
redis_store = RedisCodeActImplementationStore(
    redis_url="redis://localhost:6379/0",
)
sql_store = SqlCodeActImplementationStore(
    db_url="sqlite:///codeact.db",
)
```

这些后端复用 `trpc_agent_sdk.storage`，也可以通过 `storage=` 注入已有
`FileStorage`、`RedisStorage` 或 `SqlStorage`。应用停止时应调用
`await store.close()`；注入外部 Storage 时，其生命周期仍由调用方管理。

完整的策略切换、自动审批演示和生产使用建议请参考
[Generation Method 示例 README](../../../examples/codeact_with_generation_method/README.md)。

## 示例

### CodeAct Tools

[examples/codeact_with_tools/README.md](../../../examples/codeact_with_tools/README.md)
包含：

- `self.<tool>(...)` 工具调用；
- 两个 CodeAct Cell 之间复用变量；
- ObjectRef 大对象处理；
- 结构化结果返回；
- CodeAct 与真实 Tool Calling 的请求大小、Token 和耗时对比；
- 不同对象规模的测试方式；
- 测试与生产环境注意事项。

### Generation Method

[examples/codeact_with_generation_method/README.md](../../../examples/codeact_with_generation_method/README.md)
包含：

- 使用 `CodeActTool` 实现 `...` 函数；
- 查看模型生成的实际代码；
- `prefer_approved` 生成并审批候选；
- `approved_only` 不调用模型、直接执行审批代码；
- File、Redis、SQL Store 的使用场景；
- 候选测试、审批和回滚建议。

具体运行命令、测试输入、输出对照和验收条件以对应示例 README 为准，本文不再
重复维护示例执行结果。

## 与传统 Tool Calling 的差异

传统 Tool Calling：

```text
工具 Schema 发送给模型
→ 模型返回 FunctionCall
→ 框架执行工具
→ 完整结果序列化为 FunctionResponse
→ 下一轮模型再次接收结果
```

CodeAct Tools：

```text
模型生成 Python
→ self.<tool>(...) 调用工具
→ 结果保留在 Python Runtime
→ 本地完成循环、过滤和聚合
→ 只提交必要观察和最终结果
```

CodeAct 的主要收益不是改变工具调用语法，而是：

- 使用 Python 表达复杂控制流；
- 避免大型中间结果反复 JSON 序列化；
- 减少大对象进入模型上下文产生的 Token；
- 在同一次 invocation 中持续处理真实 Python 对象。

对于简单参数和小型返回值，传统 Tool Calling 更直观，也更容易逐次审批和审计。

## 适用场景

CodeAct 适合：

- 大型列表、表格或复杂对象的过滤和聚合；
- 需要循环、分支和多步计算的任务；
- 多个工具之间存在中间数据依赖；
- 工具返回值很大，但最终结果较小；
- 使用函数契约生成可校验的实现；
- 对生成实现进行候选管理、审批和复用。

传统 Tool Calling 更适合：

- 单次、参数简单的 API 调用；
- 每次调用都需要人工确认或独立审计；
- 支付、删除等高权限副作用操作；
- 不需要 Python 控制流的小型任务。

## 限制

### 进程与 Worker

`InProcessCodeActRuntime` 的 `globals` 和 ObjectStore 位于当前 Worker 内存。
同一次 invocation 的后续模型调用必须继续在同一个 Worker 执行。

以下情况会丢失状态：

- 请求迁移到其他 Worker；
- Worker 重启；
- 进程崩溃或故障转移；
- invocation 已完成并释放 Runtime。

Session Event 只能保存代码和执行结果，不能恢复任意 Python 活对象。
Redis/SQL Implementation Store 只负责保存 Generation Method 的代码版本，
不会将当前 invocation 的变量或 ObjectRef 变成分布式状态。

如需跨 Worker 执行，需要实现外部状态化的 `BaseCodeActRuntime`，并提供远程
对象句柄、生命周期管理和并发控制。

### 当前 Runtime 能力

当前内置实现只有 `InProcessCodeActRuntime`，尚未提供 Container 或远端沙箱
CodeAct Runtime。Container/Cube CodeExecutor 不能直接替代它，因为普通
CodeExecutor 不具备 `self` 工具代理、持久 Python 活对象和 ObjectRef 协议。

### 工具调用限制

- 通过 `self` 调用工具时必须使用关键字参数；
- 异步能力必须使用 `await`；
- 进度流式 Tool 暂不通过 CodeAct 代理执行；
- ObjectRef 不能跨进程直接传输；
- 生成代码仍可能主动打印大型对象。

## 安全建议

`InProcessCodeActRuntime` 在 Agent 宿主进程执行模型生成的 Python，拥有与
宿主相同的文件、网络、环境变量和进程权限。它不是安全沙箱，只适用于开发、
测试和受信任输入。

生产环境使用 CodeAct 前，应至少做到：

- 使用容器、VM 或远端沙箱提供 OS 级隔离；
- 限制 CPU、内存、执行时间、文件系统和进程数；
- 限制网络出口、环境变量和凭证访问；
- 只向 `self` 暴露最小必要权限的工具；
- 为写操作增加鉴权、幂等、审计和人工确认；
- 对 Generation Method 候选执行测试和安全审查后再审批；
- 为 invocation 提供 Worker 粘滞或分布式 Runtime；
- 将 Skill 和用户代码交给 Container/Cube CodeExecutor，不与 CodeAct
  控制代码混用。

在具备隔离 Runtime 前，不应在多租户生产服务中直接执行不可信模型生成代码。
