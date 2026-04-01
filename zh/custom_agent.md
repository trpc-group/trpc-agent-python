# 自定义 Agent

当框架提供预设多Agent模式（Chain/Parallel/Cycle）及其组合使用，无法满足你的需求时，可以使用通过直接继承`BaseAgent`并实现自定义控制流来定义**任意多Agent编排逻辑**。

当然，Custom Agent 也适用于框架预设的多 Agent 模式（Chain / Parallel / Cycle）及其组合无法满足的**复杂编排场景**：条件路由、动态 Agent 选择、复杂状态管理等都能在一个 `_run_async_impl` 里自由实现。


## 适用场景
Custom Agents适用于框架提供的Multi Agents无法满足的复杂场景，如下示例：

- **条件逻辑**：根据运行时条件或前一步的结果执行不同的子Agent或采取不同路径，比如，智能诊断系统先让TriageAgent分析症状，如果发现是"发热+咳嗽"则调用RespiratoryAgent，如果是"胸痛+气短"则调用CardiacAgent，不同路径的Agent会使用完全不同的问诊策略
- **复杂状态管理**：在整个工作流中实现超越简单顺序传递的复杂状态维护和更新逻辑，比如，多轮辩论系统中ArgumentAgent提出观点后，CriticAgent反驳并更新`argument_strength`状态，DefenderAgent根据强度值决定是否需要ReinforcementAgent加强论证，还是直接进入ConclusionAgent
- **外部系统集成**：在编排流程控制中直接集成对外部API、数据库或自定义库的调用，比如，智能新闻写作系统让DataCollectorAgent调用新闻API收集素材，根据API返回状态决定是否让FactCheckerAgent验证信息，最后让WriterAgent基于验证结果选择不同的写作风格
- **动态Agent选择**：基于对情况或输入的动态评估来选择下一步运行哪个子Agent，比如，代码审查系统让ComplexityAnalyzerAgent分析代码复杂度，如果complexity_score > 8则调用SeniorReviewerAgent和SecurityAuditorAgent，如果< 3则只调用BasicReviewerAgent
- **业务定制工作流**：实现不符合标准顺序、并行或循环结构的编排逻辑，比如，AI游戏策略系统在StrategyAgent、TacticsAgent、ExecutionAgent之间形成三角循环，每个Agent都可能因为战局变化将控制权转给其他两个Agent中的任意一个

## 实现要点

继承 `BaseAgent` 并实现 `_run_async_impl` 方法即可：

```python
from trpc_agent.agents import BaseAgent
from trpc_agent.context import InvocationContext
from trpc_agent.events import Event
from typing import AsyncGenerator

class MyCustomAgent(BaseAgent):
    """自定义 Agent 示例"""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        # 在这里实现你的自定义逻辑
        ...
```
## 核心方法详解

`_run_async_impl`方法是Custom Agent的核心，你需要在其中实现：

1. **运行sub_agent**：使用`sub_agent.run_async(ctx)`执行子Agent并传递事件
2. **管理状态**：通过`ctx.session.state`读写状态字典在Agent调用间传递数据
3. **实现控制流**：使用Python标准构造（`if`/`elif`/`else`、`for`/`while`循环、`try`/`except`）创建复杂的条件或迭代工作流


## 实现自定义逻辑
### 核心方法

| 方法 | 签名 | 说明 |
|------|------|------|
| `_run_async_impl` | `(ctx: InvocationContext) -> AsyncGenerator[Event, None]` | **必须实现**。核心业务逻辑，通过 `yield` 产出事件流 |
| `run_async` | `(parent_context: InvocationContext) -> AsyncGenerator[Event, None]` | 继承自 `BaseAgent` 的公共入口，自动管理回调和上下文，内部调用 `_run_async_impl` |

### 核心模式

- 通过 `ctx.session.state` 读写状态，在 Agent 调用间传递数据
- 使用 `sub_agent.run_async(ctx)` 运行子 Agent，用 `async for event in ...` 逐条 yield 事件
- 使用 `create_text_event(ctx, text)` 创建自定义文本事件
- 通过 `ctx.actions.escalate` 或 `ctx.session.state` 判断是否提前终止

### 运行子 Agent

在 `_run_async_impl` 中按需选择运行子 Agent 的时机。子 Agent 可以是 `LlmAgent`、`ChainAgent`、`ParallelAgent`，甚至是另一个自定义 Agent。

```python
async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
    async for event in self.some_sub_agent.run_async(ctx):
        yield event
```

### 状态管理与条件控制流

推荐通过框架 State 管理运行状态，让 Agent 灵活参与条件控制流程。

```python
async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
    # 读取前一个 Agent 写入的数据（字段由 Agent 的 output_key 设置）
    previous_result = ctx.session.state.get("some_key")

    if previous_result == "value_a":
        async for event in self.agent_a.run_async(ctx):
            yield event
    else:
        async for event in self.agent_b.run_async(ctx):
            yield event
```

### 终止 Agent 执行

在某些场景下（如 Cycle 循环），需要提前终止 Agent 应用执行，可以使用`ctx.actions.escalate`等方式来提前终止。

```python
async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
    async for event in self.some_sub_agent.run_async(ctx):
        if ctx.actions.escalate:
            return
        if ctx.session.state.get("process_complete"):
            return
        yield event
```

### 控制 Event 可见性

在某些场景下，Agent执行过程中产生了一些信息，但不希望这些信息对外部可见（比如关键思考过程）。可以通过设置事件的`visible`字段来控制事件是否在Runner中被返回。

框架也提供了`create_text_event`工具函数来方便创建文本Event：

```python
from trpc_agent.events import create_text_event

async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
    async for event in self.analyzer.run_async(ctx):
        if should_hide(event):
            event.visible = False
        yield event

    # 创建一个不可见的内部日志事件
    yield create_text_event(
        ctx=ctx,
        text="内部处理：正在分析文档类型...",
        visible=False,
    )
```

## 示例：智能文档处理Agent

以下是一个实际的Custom Agent示例，展示根据文档类型动态选择处理流程：

```python
from trpc_agent.agents import BaseAgent, LlmAgent, ChainAgent
from trpc_agent.context import InvocationContext
from trpc_agent.events import Event
from pydantic import ConfigDict
from typing import AsyncGenerator

class SmartDocumentProcessor(BaseAgent):
    """智能文档处理Agent
    
    根据文档类型和内容复杂度动态选择处理策略：
    - 简单文档：直接处理
    - 复杂文档：分析→处理→验证
    - 技术文档：特殊处理流程
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
    )

    def __init__(self, **kwargs):
        # 定义各种处理Agent
        self.document_analyzer = LlmAgent(
            name="document_analyzer",
            model="deepseek-chat", 
            instruction="分析文档类型和复杂度，输出：simple/complex/technical",
            output_key="doc_type"
        )
        
        self.simple_processor = LlmAgent(
            name="simple_processor",
            model="deepseek-chat",
            instruction="处理简单文档：{user_input}",
            output_key="processed_content"
        )
        
        # 复杂文档处理链：分析→处理
        complex_analyzer = LlmAgent(
            name="complex_analyzer", 
            model="deepseek-chat",
            instruction="深度分析复杂文档结构和要点：{user_input}",
            output_key="complex_analysis"
        )
        
        complex_processor = LlmAgent(
            name="complex_processor",
            model="deepseek-chat", 
            instruction="基于分析处理复杂文档：{complex_analysis}",
            output_key="processed_content"
        )
        
        # 使用ChainAgent封装复杂文档处理流程
        self.complex_processor_chain = ChainAgent(
            name="complex_processor_chain",
            description="Complex document processing: analyze → process", 
            sub_agents=[complex_analyzer, complex_processor]
        )
        
        self.technical_processor = LlmAgent(
            name="technical_processor",
            model="deepseek-chat",
            instruction="使用技术文档专用流程处理：{user_input}",
            output_key="processed_content"
        )
        
        self.quality_validator = LlmAgent(
            name="quality_validator",
            model="deepseek-chat",
            instruction="验证处理质量：{processed_content}，如有问题输出建议",
            output_key="quality_feedback"
        )
        
        # 将所有Agent添加到sub_agents
        sub_agents = [
            self.document_analyzer,
            self.simple_processor,
            self.complex_processor_chain,  # 使用ChainAgent封装的复杂文档处理流程
            self.technical_processor,
            self.quality_validator
        ]
        
        super().__init__(sub_agents=sub_agents, **kwargs)
    
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """实现智能文档处理的自定义逻辑"""
        
        # 第一步：分析文档类型
        async for event in self.document_analyzer.run_async(ctx):
            yield event
        
        doc_type = ctx.session.state.get("doc_type", "simple")
        
        # 第二步：根据文档类型选择处理策略
        if doc_type == "simple":
            # 简单文档直接处理
            async for event in self.simple_processor.run_async(ctx):
                yield event
                
        elif doc_type == "complex":
            # 复杂文档：使用ChainAgent执行 分析→处理 流程
            async for event in self.complex_processor_chain.run_async(ctx):
                yield event
                
            # 复杂文档需要质量验证
            async for event in self.quality_validator.run_async(ctx):
                yield event
                
        elif doc_type == "technical":
            # 技术文档使用专门流程
            async for event in self.technical_processor.run_async(ctx):
                yield event
                
            # 技术文档也需要验证
            async for event in self.quality_validator.run_async(ctx):
                yield event
        
        # 可以在这里添加更多条件逻辑，比如：
        # - 检查处理结果质量决定是否重新处理
        # - 根据用户权限决定是否执行额外步骤
        # - 基于外部系统状态调整处理流程
```

## 完整示例

完整的Custom Agent示例见：[examples/llmagent_with_custom_agent/run_agent.py](../../examples/llmagent_with_custom_agent/run_agent.py)


## 扩展建议

| 方向 | 做法 |
|------|------|
| **引入工具** | 子 Agent 配置 `tools` 参数，如 `FunctionTool` 串接数据库 / HTTP / 内部服务 |
| **增加校验** | 在分支前做参数校验、风控、开关控制 |
| **渐进演进** | 当 if-else 过多或需要协作时，平滑切换到 `ChainAgent` / `ParallelAgent` 或 `Graph` |
