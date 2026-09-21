# CodeAct Tools 示例

本示例演示模型如何生成 Python 控制代码，通过 Agent 已配置的工具获取数据，
并在同一次 invocation 中持续处理工具返回的大对象。NOOA 将这种能力称为
CodeAct Tools。

用户只需要描述“使用 `load_numbers` 工具”，不需要了解
`await self.load_numbers(...)` 等内部调用协议。CodeAct 系统指令负责指导模型
生成正确的工具调用代码。

## 关键特性

- 通过生成的 Python 代码组合现有 Agent 工具
- 同一次 invocation 中跨模型轮次保留 Python 变量
- 使用 ObjectRef 将大对象保留在本地 ObjectStore
- 使用 `doc(self)` 和 `doc(value)` 渐进发现工具与对象能力
- 使用 `return_result(...)` 返回结构化结果
- 使用 Pydantic Schema 校验最终输出
- 与传统 Tool Calling 真实对比请求大小、Token 和耗时

## Agent 层级结构说明

```text
root_agent (LlmAgent + CodeActConfig)
├── tool: load_numbers (FunctionTool)
└── CodeAct Runtime
    ├── self.load_numbers(...)
    ├── persistent Python globals
    ├── ObjectStore / ObjectRef
    └── return_result(...)
```

关键文件：

- [agent/agent.py](./agent/agent.py)：启用 CodeAct 的 Agent
- [agent/prompts.py](./agent/prompts.py)：两轮 CodeAct 测试约束
- [agent/tools.py](./agent/tools.py)：`load_numbers` 工具
- [run_agent.py](./run_agent.py)：持久变量与 ObjectRef 示例
- [compare_tool_calling.py](./compare_tool_calling.py)：真实模型对比程序
- [.env](./.env)：模型配置

## 关键代码解释

Agent 将普通 `FunctionTool` 与 `CodeActConfig` 同时配置：

```python
agent = LlmAgent(
    name="codeact_calculator",
    model=model,
    tools=[FunctionTool(load_numbers)],
    code_act=CodeActConfig(),
    output_schema=CalculationResult,
)
```

模型可以先发现当前可用能力：

```python
print(doc(self))
print(doc(self.load_numbers))
```

框架根据工具名称从当前 Agent 的工具列表中找到真实 `FunctionTool`。生成代码
实际执行时，工具调用形式为：

```python
nums = await self.load_numbers(count=100000)
```

`load_numbers` 返回十万个整数。该列表不会完整反馈给模型，而是保存在当前
Runtime 的 ObjectStore。模型看到的是包含类型、长度和有限预览的 ObjectRef。

第一轮代码将对象保存到全局变量 `nums`：

```python
nums = await self.load_numbers(count=100000)
print(len(nums))
print(doc(nums))
```

第二轮代码复用同一个变量，不重新调用工具：

```python
total = sum(number * number for number in nums if number % 7 == 0)
return_result({
    "expression": "sum(n*n for n in range(100000) if n % 7 == 0)",
    "value": total,
})
```

预期结果为 `47616904735715`。

## 环境要求

- Python 3.10+，推荐 Python 3.12
- 可用的 OpenAI 兼容模型服务
- 足够容纳对比测试请求的模型上下文窗口
- 默认使用 `InProcessCodeActRuntime`

> `InProcessCodeActRuntime` 会在 Agent 宿主进程执行模型生成的 Python，
> 不提供 OS 级安全隔离，仅适用于受控开发和测试环境。

## 构建步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
./build.sh
source .venv/bin/activate
```

## 运行步骤

### 配置环境变量

在 [examples/codeact_with_tools/.env](./.env) 中配置，或通过 `export` 设置：

```bash
TRPC_AGENT_API_KEY=...
TRPC_AGENT_BASE_URL=...
TRPC_AGENT_MODEL_NAME=...
```

### 运行基础示例

```bash
cd examples/codeact_with_tools
python3 run_agent.py
```

### 运行真实模型对比

从仓库根目录执行：

```bash
python3 examples/codeact_with_tools/compare_tool_calling.py
```

## 测试方式

### 1. 工具发现测试

运行基础示例后，第一轮代码执行结果应包含 `load_numbers` 能力或返回对象描述。
如果模型不确定工具名称，可以通过以下代码发现能力：

```python
print(doc(self))
```

预期能力列表中包含：

```json
{
  "name": "load_numbers",
  "description": "Return integers from zero up to, but excluding, count."
}
```

### 2. 持久变量测试

`run_agent.py` 要求模型使用两个 CodeAct Cell：

1. 第一轮调用一次 `load_numbers`，将结果保存为 `nums`；
2. 第二轮复用 `nums` 完成计算；
3. 第二轮不能再次调用工具或重新创建数据。

运行输出应只出现一次 `load_numbers` 调用，最终结果应为：

```json
{
  "expression": "sum(n*n for n in range(100000) if n % 7 == 0)",
  "value": 47616904735715
}
```

如果第二轮提示 `nums` 未定义，说明两轮代码没有运行在同一个 Runtime
invocation 中，不符合本测试要求。

### 3. ObjectRef 大对象测试

第一轮执行：

```python
print(len(nums))
print(doc(nums))
```

预期输出包含：

- `length: 100000`
- 对象类型
- 有限长度的预览
- 不包含十万个整数的完整序列化结果

这说明数据保留在本地 ObjectStore，模型只接收有界观察结果。

### 4. 真实 Tool Calling 对比测试

`compare_tool_calling.py` 使用：

- 同一个真实模型
- 同一个 `load_numbers` 工具
- 同一个查询和输出 Schema
- 传统 Tool Calling 与 CodeAct 两条路径

默认对象规模为四万个整数：

```bash
python3 examples/codeact_with_tools/compare_tool_calling.py
```

程序会输出：

- `LLM calls`：真实模型请求次数
- `Tool calls`：工具实际执行次数
- `2nd request chars`：第二轮完整请求字符数
- `Prompt/Completion`：Provider 返回的 Token 数据
- `Elapsed ms`：包含网络调用的总耗时
- `Status`：结果是否正确

CodeAct 的第二轮请求不应包含完整整数列表，因此 `2nd request chars` 和 Prompt
Token 应显著低于传统 Tool Calling。

### 5. 不同对象规模测试

可以调整数据规模：

```bash
CODEACT_COMPARISON_COUNT=20000 \
python3 examples/codeact_with_tools/compare_tool_calling.py
```

建议依次测试：

- `1000`：小对象基线，差距通常不明显
- `20000`：可观察明显的请求大小差异
- `40000`：默认大对象测试
- 更大值：用于验证传统路径的上下文窗口边界

对象过大时，传统 Tool Calling 可能因完整 FunctionResponse 超过上下文窗口而
失败；CodeAct 仍只反馈 ObjectRef 的有界描述。

### 6. 单元测试

```bash
.venv/bin/pytest -q tests/agents/test_codeact.py
.venv/bin/pytest -q tests/tools/test_code_act_tool.py
```

这些测试覆盖 CodeAct 协议、Runtime 状态、工具代理、结构化结果和
Generation Method。真实模型的大对象 Token 对比必须运行示例脚本，不能由
Mock 模型替代。

## 运行结果分析

基础示例满足以下条件时才算通过：

- `load_numbers` 只执行一次
- 第一轮创建 `nums`
- 第二轮复用同一个 `nums`
- ObjectRef 输出不包含完整大对象
- `return_result` 返回正确结构
- 最终值为 `47616904735715`

对比测试主要验证数据传输方式，而不是证明 CodeAct 在所有任务中都更快：

- 传统 Tool Calling 会把完整工具结果序列化到下一轮模型上下文；
- CodeAct 将大对象留在 Runtime，只返回代码执行观察；
- 对于小对象或已经封装好计算逻辑的高层工具，CodeAct 不一定更快；
- 网络延迟会波动，但大对象是否进入模型上下文是稳定的结构差异。

## 测试环境使用建议

- 使用无敏感数据和无副作用的测试工具
- 为 Agent 设置合理的模型调用、工具调用和迭代上限
- 从小对象开始，逐步增加 `CODEACT_COMPARISON_COUNT`
- 同时记录请求字符数和 Provider Token，避免只比较墙钟时间
- 检查模型是否重复调用工具、重新创建变量或伪造执行结果
- 每个并发任务使用独立 invocation，不要依赖跨 invocation 的 Python 变量
- 测试失败后检查 executable code 和 code execution result，而不只看最终回答

## 生产环境使用建议

- 不要在多租户服务中直接执行不可信模型代码
- 使用容器、VM 或其他 OS 级隔离，并限制 CPU、内存、执行时间和进程数
- 限制文件系统、网络出口、环境变量和凭证访问
- 只暴露最小必要工具，避免数据库连接、存储管理器或高权限客户端直接可见
- 工具名称必须唯一，并为参数和返回值提供明确类型与 docstring
- 对有副作用的工具增加权限校验、幂等控制、审计和人工确认
- 当前进程内 Runtime 状态不能跨进程或跨 Worker 迁移
- 同一次 invocation 必须固定在同一个 Worker
- ObjectRef 是进程内引用，不能作为分布式消息直接传输
- Skill 或用户提供的代码应继续通过 Container/Cube CodeExecutor 执行，不要与
  CodeAct 控制代码混用
- 生产使用前必须替换或扩展为具备 OS 级隔离的 CodeAct Runtime

## 适用场景建议

- 工具返回大量数据，但只需要在本地完成过滤、聚合或验证
- 任务需要多步 Python 控制流和跨轮变量状态
- 希望减少大对象在模型上下文中的重复 JSON 往返
- 需要组合多个窄权限工具完成复杂计算
- 不适合简单问答、小对象调用或高风险副作用操作
