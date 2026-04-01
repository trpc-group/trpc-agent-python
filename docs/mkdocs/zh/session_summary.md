# Session Summarizer

随着对话轮数增加，Session 中积累的事件会不断增长，导致上下文过长、token 消耗增大。Session Summarizer通过将历史对话智能压缩为摘要，在保留关键上下文的同时有效控制会话体积，是 TRPC Agent 中用于长对话场景下不可或缺的核心组件。

## 概述

Session Summarizer 通过智能分析对话历史，将旧的对话事件总结成简洁的摘要，从而：

- **会话压缩**：将长对话历史压缩为简洁的摘要
- **减少 token 使用**：减少 token 的消耗，节省成本
- **保持重要上下文**：保留关键信息和决策
- **提升性能**：减少处理的事件数量

## 核心组件

### SessionSummarizer 类

主要的总结器类，负责会话压缩的核心逻辑。

### SessionSummary 类

表示会话总结的数据结构，包含总结信息和元数据。

### SummarizerSessionManager 类

会话总结管理器，负责在 SessionService 层面自动触发和管理总结。

---

## 基本用法

### 1. 创建 SessionSummarizer

```python
from trpc_agent_sdk.sessions import SessionSummarizer
from trpc_agent_sdk.models import OpenAIModel

# 创建 LLM 模型
model = OpenAIModel(
    model_name="deepseek-chat",
    api_key="your-api-key",
    base_url="https://api.deepseek.com/v1"
)

# 每 summarizer_count 轮对话后执行总结
# 如果设置的 summarizer_count 为 3，则每 3 轮对话后执行总结
summarizer_count = 3

# 创建总结器
summarizer = SessionSummarizer(
    model=model,
    # 如果不设置 check_summarizer_functions，默认也会有 set_summarizer_conversation_threshold(100) 这个函数
    # 当 check_summarizer_functions 中的检查函数返回 True，会触发总结
    # 当存在多个检查函数时，默认采用 AND 逻辑（所有函数都返回 True 时才总结）
    check_summarizer_functions=[
        set_summarizer_conversation_threshold(summarizer_count),  # 对话计数检查函数，即每 summarizer_count 轮后执行总结
        # set_summarizer_time_interval_threshold(10),              # 时间检查函数，即每10秒需要执行总结
        # set_summarizer_token_threshold(1000),                   # token检查函数，即每1000个token需要执行总结
        # set_summarizer_events_count_threshold(30),              # 事件数量检查函数，即每30个事件需要执行总结
        # set_summarizer_important_content_threshold(),            # 重要内容检查函数，即根据内容重要度来判断是否需要执行总结
        # set_summarizer_check_functions_by_and(                   # 组合检查函数，采用 AND 逻辑，当所有检查函数都返回 True 时，会触发总结
        #     set_summarizer_conversation_threshold(1),
        #     set_summarizer_time_interval_threshold(10),
        #     set_summarizer_token_threshold(1000),
        #     set_summarizer_important_content_threshold(),
        # ),
        # set_summarizer_check_functions_by_or(                    # 组合检查函数，采用 OR 逻辑，当任意一个检查函数返回 True 时，会触发总结
        #     set_summarizer_conversation_threshold(1),
        #     set_summarizer_time_interval_threshold(10),
        # )
    ],
    max_summary_length=600,      # 保留的总结文本长度，默认是 1000，超过该长度显示 ...
    keep_recent_count=4,         # 保留最近多少轮对话，默认是 10
)
```

---

### 2. 自动总结（SessionService 层面）

结合 `SummarizerSessionManager` 和 `SessionService` 使用，在 Runner 中自动总结。

**完整示例**：参考 [`examples/session_summarizer/run_agent.py`](../../../examples/session_summarizer/run_agent.py)

```python
from trpc_agent_sdk.sessions import SummarizerSessionManager, InMemorySessionService
from trpc_agent_sdk.runners import Runner

# 创建 SummarizerSessionManager
summarizer_manager = SummarizerSessionManager(
    model=model,
    summarizer=summarizer,
    auto_summarize=True,  # 默认是 True，如果设置为 False，则不会自动总结
)

# 在 SessionService 中使用
session_service = InMemorySessionService(summarizer_manager=summarizer_manager)

# 创建 Runner
runner = Runner(
    app_name=app_name,
    agent=agent,
    session_service=session_service
)

# 运行 Agent（总结会自动触发）
for i, user_input in enumerate(conversations):
    await run_agent(runner=runner, user_id=user_id, session_id=session_id, user_input=user_input)
    
    # 每 summarizer_count 轮对话后应该会触发总结
    if i % summarizer_count == 0:
        session = await session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id
        )
        if session:
            # 获取总结内容
            summary = await session_service.summarizer_manager.get_session_summary(session)
            if summary:
                print(f"   - 总结文本: {summary.summary_text[:100]}...")
                print(f"   - 原始事件数: {summary.original_event_count}")
                print(f"   - 压缩后事件数: {summary.compressed_event_count}")
                print(f"   - 压缩比例: {summary.get_compression_ratio():.1f}%")
```

**工作流程**：

1. **自动触发**：每 N 轮对话后，`SummarizerSessionManager` 自动检查是否需要总结
2. **生成总结**：使用 LLM 将历史对话压缩为简洁摘要
3. **事件压缩**：保留最近 N 轮对话，将更早的对话替换为总结文本
4. **更新会话**：更新 Session 中的事件列表

**总结内容使用**：

每次执行总结后的内容为 `summary.summary_text`，该内容会在后面的对话中加入到对应的请求 prompt 中，这个过程对用户无感知。

---

### 3. 手动会话总结

**完整示例**：参考 [`examples/session_summarizer/run_agent.py`](../../../examples/session_summarizer/run_agent.py)

```python
import time

# 构建会话 session 中的 event
session = await create_test_session_with_events(session_service, app_name, user_id, session_id)

# 强制手动执行总结（force=True 表示不受触发条件限制）
await session_service.summarizer_manager.create_session_summary(session, force=True)

if session:
    summary = await session_service.summarizer_manager.get_session_summary(session)
    if summary:
        print(f"   - 总结文本: {summary.summary_text[:100]}...")
        print(f"   - 总结时间: {time.ctime(summary.summary_timestamp)}")
        print(f"   - 原始事件数: {summary.original_event_count}")
        print(f"   - 压缩后事件数: {summary.compressed_event_count}")
        print(f"   - 压缩比例: {summary.get_compression_ratio():.1f}%")
```

---

## 配置参数

### SessionSummarizer 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `model` | LLMModel | 必需 | 用于生成总结的 LLM 模型 |
| `check_summarizer_functions` | List[CheckSummarizerFunction] | `[set_summarizer_conversation_threshold(100)]` | 触发总结的检查函数列表，当存在多个检查函数时，默认采用 AND 操作，即所有函数都返回 True，才进行总结 |
| `max_summary_length` | int | 1000 | 生成总结的最大长度 |
| `keep_recent_count` | int | 10 | 压缩后保留的最近事件数量（按轮数计算，每轮通常包含 2 个事件：用户消息和助手回复） |
| `summarizer_prompt` | str | DEFAULT_SUMMARIZER_PROMPT | 自定义总结提示模板 |

### SummarizerSessionManager 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `model` | LLMModel | 必需 | 用于生成总结的 LLM 模型 |
| `summarizer` | SessionSummarizer | None | 总结器实例，如果不提供则使用默认配置创建 |
| `auto_summarize` | bool | True | 是否自动总结，如果设置为 False，则不会自动总结 |

### 配置建议

#### 高频率对话场景

```python
summarizer = SessionSummarizer(
    model=model,
    check_summarizer_functions=[set_summarizer_conversation_threshold(20)],  # 更频繁的总结
    keep_recent_count=5,       # 保留较少事件
)
```

#### 长对话场景

```python
summarizer = SessionSummarizer(
    model=model,
    check_summarizer_functions=[set_summarizer_conversation_threshold(50)],  # 更多事件后总结
    keep_recent_count=15,      # 保留更多上下文
    max_summary_length=1500,   # 更长的总结
)
```

#### 内存敏感场景

```python
summarizer = SessionSummarizer(
    model=model,
    check_summarizer_functions=[set_summarizer_events_count_threshold(15)],  # 快速总结
    keep_recent_count=3,       # 最小保留
)
```

---

## 高级功能

### 1. 跳过总结控制

某些事件可以标记为跳过总结：

```python
from trpc_agent_sdk.types import EventActions

# 创建跳过总结的事件
event = Event(
    invocation_id="inv_123",
    author="system",
    content=Content(parts=[Part.from_text("Debug information")]),
    actions=EventActions(skip_summarization=True)  # 跳过总结
)
```

### 2. 获取总结元数据

```python
# 获取总结器配置信息
metadata = summarizer.get_summary_metadata()
print(f"模型名称: {metadata['model_name']}")
print(f"保留事件数: {metadata['keep_recent_count']}")
```

### 3. 使用 SessionSummary 对象

```python
from trpc_agent_sdk.sessions import SessionSummary

# 获取总结对象
summary = await session_service.summarizer_manager.get_session_summary(session)

# 获取压缩比例
compression_ratio = summary.get_compression_ratio()
print(f"压缩比例: {compression_ratio:.1f}%")

# 转换为字典
summary_dict = summary.to_dict()
```

### 4. Agent 层面总结（Filter 方式）

多 Agent 场景下，总结的是所有的 Agent 产生的数据；但是不同的 Agent 产生的数据量不同，业务可能期望只总结指定 Agent 产生的数据。

**完整实现**：参考 [`examples/session_summarizer/agent/filters.py`](../../../examples/session_summarizer/agent/filters.py)

**使用方式**：

```python
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.sessions import SessionSummarizer
from trpc_agent_sdk.context import get_invocation_ctx

class AgentSessionSummarizerFilter(BaseFilter):
    """Agent session summarizer filter."""
    
    def __init__(self, model: OpenAIModel):
        super().__init__()
        # 创建总结器
        self.summarizer = SessionSummarizer(
            model=model,
            max_summary_length=600,
            keep_recent_count=4,  # 保留最近多少轮对话，默认是 10
        )
    
    async def _after_every_stream(self, ctx: AgentContext, req: Any, rsp: FilterResult) -> None:
        """每次流式返回后检查是否需要总结"""
        # 当前 agent 流式每次返回一个 event, rsp 是 FilterResult 类型, 这里的 rsp.rsp 是 Event 类型
        if not rsp.rsp.partial:
            events = ctx.metadata.get("events", [])
            conversation_text = self.summarizer._extract_conversation_text(events)
            # 当对话文本超过 12KB 时触发总结
            if len(conversation_text) > 12 * 1024:
                await self._do_summarize(ctx)
        
        # 将执行的 event 放在上下文中缓存
        if "events" not in ctx.metadata:
            ctx.metadata["events"] = []
        ctx.metadata["events"].append(rsp.rsp)
    
    async def _after(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        """整个 agent 执行完的后操作"""
        await self._do_summarize(ctx)
    
    async def _do_summarize(self, ctx: AgentContext):
        """执行总结操作"""
        invocation_ctx: InvocationContext = get_invocation_ctx()
        
        # 获取该 agent 执行产生的 event
        events = ctx.metadata.pop("events", [])
        
        # 如果是多个 agent 并发执行，这里需要加协程锁保证顺序
        # 异步网络操作可能会切出协程，导致顺序错乱
        
        # 在全局 session 中删除该 agent 保留的 events
        for event in events:
            if event in invocation_ctx.session.events:
                invocation_ctx.session.events.remove(event)
        
        session_id = invocation_ctx.session.id
        conversation_text = self.summarizer._extract_conversation_text(events)
        
        # 对这些 agent 产生的事件做总结
        # create_session_summary_by_events 是专门用于 Agent 层面总结的方法
        summary_text, compressed_events = await self.summarizer.create_session_summary_by_events(
            events, session_id, ctx=invocation_ctx
        )
        
        # 将压缩后的事件添加回 session
        if compressed_events:
            invocation_ctx.session.events.extend(compressed_events)

# 在 Agent 中使用
def create_agent():
    agent = LlmAgent(
        name="analyze",
        model=model,
        description="分析策略的工具",
        tools=[log_set, metric_set],
        filters=[AgentSessionSummarizerFilter(model)],  # 配置 filter
        # ...
    )
    return agent
```

**采用 Filter 的方式来做总结**：

1. **记录事件**：记录该 Agent 产生的数据 events
2. **事件隔离**：从全局 session 中删除这些 events（避免与 SessionService 层面的总结冲突）
3. **执行总结**：对 events 做总结
4. **事件替换**：将总结后的 events 追加到全局 session 中

**两种总结方式的对比**：

| 特性 | SessionService 层面总结 | Agent 层面总结 |
|-----|----------------------|--------------|
| **触发时机** | 每 N 轮对话后 | 每次 Agent 执行后或文本超过阈值 |
| **总结范围** | 整个 Session 的所有事件 | 单个 Agent 产生的事件 |
| **适用场景** | 单 Agent 场景 | 多 Agent 协作场景 |
| **配置方式** | SessionService 初始化 | Agent Filter 配置 |
| **优势** | 简单易用，自动管理 | 更细粒度控制，支持多 Agent |

---

## 工作流程

### 1. 总结触发条件

总结器会在**满足用户定义的触发条件**时触发，框架内置多种触发条件：

- **`set_summarizer_conversation_threshold(conversation_count)`**：设置会话次数阈值，即会话次数达到 `conversation_count` 后执行总结，默认 `conversation_count` 为 100
- **`set_summarizer_token_threshold(token_count)`**：设置会话 token 阈值数，即 token 次数达到 `token_count` 后执行总结
- **`set_summarizer_events_count_threshold(event_count)`**：设置 event 阈值数，即 event 达到 `event_count` 后执行总结，默认 `event_count` 为 30
- **`set_summarizer_time_interval_threshold(time_interval)`**：设置时间间隔阈值，即对话间隔达到 `time_interval` 后执行总结，默认 `time_interval` 为 300s（5分钟）
- **`set_summarizer_important_content_threshold(important_content_count)`**：设置对话重要内容次数，即会话内容空格数超过 `important_content_count` 后执行总结，默认 `important_content_count` 为 10
- **`set_summarizer_check_functions_by_and(funcs: list[CheckSummarizerFunction])`**：组合检查函数，当 `funcs` 里面所有的函数都返回 True，执行总结（AND 逻辑）
- **`set_summarizer_check_functions_by_or(funcs: list[CheckSummarizerFunction])`**：组合检查函数，当 `funcs` 里面存在任一函数返回 True，执行总结（OR 逻辑）

**触发逻辑**：

- 当存在多个检查函数时，**默认采用 AND 逻辑**，即所有函数都返回 True 时才进行总结
- 可以使用 `set_summarizer_check_functions_by_and` 或 `set_summarizer_check_functions_by_or` 显式指定逻辑

---

### 2. 总结生成

总结生成使用默认提示模板：

```
Please summarize the following conversation, focusing on:
1. Key decisions made
2. Important information shared
3. Actions taken or planned
4. Context that should be remembered for future interactions

Keep the summary concise but comprehensive. Focus on what would be most important to remember for continuing the conversation.

Conversation:
{conversation_text}

Summary:
```

**自定义提示模板**：

用户如果期望替换默认的提示模版，采用以下方式：

```python
from textwrap import dedent

your_summarizer_prompt = dedent("""\
请总结以下对话，重点关注：
1. 关键决策
2. 重要信息
3. 行动计划

对话内容：
{conversation_text}

总结：""")

# conversation_text 表示对话内容，必须带这个占位符
summarizer = SessionSummarizer(
    model=model,
    summarizer_prompt=your_summarizer_prompt,
    # ...
)
```

---

## 运行结果分析

**完整示例输出**：参考 [`examples/session_summarizer/README.md`](../../../examples/session_summarizer/README.md)

### 关键观察点

#### 1️⃣ **自动触发总结**

```
第 4 轮对话后 → 触发总结（配置了 set_summarizer_conversation_threshold(3)）
第 7 轮对话后 → 触发总结
第 13 轮对话后 → 触发总结
```

**说明**：
- 配置了 `set_summarizer_conversation_threshold(3)`，每 3 轮对话后触发总结
- 总结在对话轮数达到阈值时自动执行

#### 2️⃣ **事件压缩效果**

| 轮次 | 原始事件数 | 压缩后事件数 | 压缩比例 |
|-----|----------|------------|---------|
| 第 4 轮 | 8 | 5 | 37.5% |
| 第 7 轮 | 8 | 5 | 37.5% |
| 第 13 轮 | 13 | 5 | 61.5% |
| 手动总结 | 39 | 5 | 87.2% |

**说明**：
- `keep_recent_count=4` 配置保留最近 4 轮对话（8 个事件：4 轮 × 2 事件/轮）
- 更早的对话被压缩为总结文本
- 随着对话进行，压缩比例逐渐提高

#### 3️⃣ **总结内容质量**

总结文本包含：
- ✅ **关键决策**：用户的学习选择和计划
- ✅ **重要信息**：Python 概念和知识点
- ✅ **行动计划**：项目实践和学习路径

**说明**：
- LLM 生成的总结保留了对话的核心信息
- 总结文本格式清晰，便于后续检索和使用

---

## 最佳实践

### 1. 配置调优

- 根据对话频率调整 `set_summarizer_conversation_threshold` 中的对话次数
- 根据内存限制调整 `keep_recent_count`
- 根据模型能力调整 `max_summary_length`

### 2. 内容过滤

- 使用 `skip_summarization` 标记不重要的调试信息
- 在总结前过滤掉系统事件
- 保留用户意图和关键决策

### 3. 成本控制

- 选择合适的模型平衡质量和成本
- 实现总结缓存减少重复计算
- 监控 API 调用频率和成本

### 4. 多 Agent 场景

- 使用 Agent 层面的总结（Filter 方式）避免冲突
- 为不同 Agent 配置不同的总结策略
- 注意并发安全，必要时添加协程锁

---

## 常见问题

### Q: 总结会丢失重要信息吗？

A: 总结器专门设计为保留关键信息，包括决策、重要信息和上下文。建议通过 `keep_recent_count` 参数保留足够的最近事件。

### Q: 如何避免过度总结？

A: 调整 `set_summarizer_conversation_threshold` 参数控制总结频率，使用 `skip_summarization` 标记不需要总结的事件。

### Q: 总结失败怎么办？

A: 总结器包含错误处理机制，失败时会返回原始会话，不会影响正常对话流程。

### Q: 如何评估总结质量？

A: 可以通过压缩比例、信息覆盖率、用户反馈等指标评估总结质量。

### Q: API 调用失败

A: 做如下检查：
- 检查 API 密钥是否正确
- 确认网络连接正常
- 验证模型名称是否正确

### Q: 总结质量不佳

A: 解决方式：
- 调整 `max_summary_length` 参数
- 使用更高质量的模型（如 GPT-4）
- 检查对话内容是否包含足够信息
- 自定义 `summarizer_prompt` 提示模板

### Q: 压缩比例过低

A: 解决方式：
- 调整 `keep_recent_count` 参数
- 降低 `set_summarizer_conversation_threshold` 设置的对话总结次数阈值，以更频繁总结
- 检查是否有太多事件被标记为跳过总结

### Q: 总结指定 Agent 中的数据

A: 解决方式：参考高级功能中的 `4. Agent 层面总结（Filter 方式）`，使用 `AgentSessionSummarizerFilter` 在 Agent Filter 中总结。

---

## 参考实现

Session Summarizer 参考了 [Agno summarizer.py](https://github.com/agno-agi/agno/blob/main/libs/agno/agno/memory/v2/summarizer.py) 的实现，主要区别：

- **数据结构**：TRPC Agent 使用更复杂的 Event 结构
- **模型调用**：使用 LlmRequest 和 generate_async
- **集成方式**：与 Session Service 深度集成
- **配置选项**：提供更多自定义选项
- **多 Agent 支持**：支持 Agent 层面的总结（Filter 方式）

---

## 完整示例

查看完整的 summarize 使用示例：

- 📁 **示例代码**：[`examples/session_summarizer/run_agent.py`](../../../examples/session_summarizer/run_agent.py)
- 📁 **示例文档**：[`examples/session_summarizer/README.md`](../../../examples/session_summarizer/README.md)
- 📁 **Agent Filter 实现**：[`examples/session_summarizer/agent/filters.py`](../../../examples/session_summarizer/agent/filters.py)

示例展示了两种总结方式：

1. **SessionService 层面总结**：使用 `SummarizerSessionManager` 在会话服务层面自动总结
2. **Agent 层面总结**：使用 `AgentSessionSummarizerFilter` 在 Agent Filter 中总结

两种方式可以结合使用，根据实际需求选择最适合的方案。
