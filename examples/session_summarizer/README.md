# Session Summarizer 示例

## 示例简介

本示例演示如何使用 **Session Summarizer** 功能来压缩长对话历史，减少 token 消耗并保持重要上下文。示例展示了两种总结方式：

1. **SessionService 层面总结**：使用 `SummarizerSessionManager` 在会话服务层面自动总结
2. **Agent 层面总结**：使用 `AgentSessionSummarizerFilter` 在 Agent Filter 中总结（在 `agent/filters.py` 中实现）

### 核心特性

- ✅ **自动会话压缩**：智能压缩长对话历史，减少 token 使用
- ✅ **多种触发条件**：支持对话轮数、时间间隔、token 数量、重要内容等多种触发条件
- ✅ **保留重要上下文**：压缩时保留关键信息和决策
- ✅ **灵活配置**：可配置总结频率、保留轮数、总结长度等参数
- ✅ **两种总结方式**：支持 SessionService 层面和 Agent 层面的总结

## 环境要求

- Python 3.10+（强烈建议使用 3.12）

## 安装和运行

### 1. 下载并安装 trpc-agent

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 2. 配置环境变量

在 `.env` 文件中设置 LLM 相关变量：

```bash
TRPC_AGENT_API_KEY=your_api_key
TRPC_AGENT_BASE_URL=http://v2.open.venus.woa.com/llmproxy
TRPC_AGENT_MODEL_NAME=deepseek-v3-local-II
```

### 3. 运行示例

```bash
cd examples/session_summarizer/
python3 run_agent.py
```

---

## 代码说明

### SessionService 层面总结（默认方式）

示例使用 `SummarizerSessionManager` 在 SessionService 层面进行自动总结：

```python
def create_summarizer_manager(model: OpenAIModel) -> SummarizerSessionManager:
    """创建 SummarizerSessionManager"""

    # 创建总结器
    summarizer = SessionSummarizer(
        model=model,
        # 触发条件：每 3 轮对话后执行总结
        check_summarizer_functions=[
            set_summarizer_conversation_threshold(3),
        ],
        max_summary_length=600,      # 保留的总结文本长度
        keep_recent_count=4,          # 保留最近 4 轮对话
    )

    # 创建 SummarizerSessionManager
    summarizer_manager = SummarizerSessionManager(
        model=model,
        summarizer=summarizer,
        auto_summarize=True,          # 自动总结
    )
    return summarizer_manager

# 在 SessionService 中使用
session_service = InMemorySessionService(summarizer_manager=summarizer_manager)
```

**工作流程**：

1. **自动触发**：每 3 轮对话后，`SummarizerSessionManager` 自动检查是否需要总结
2. **生成总结**：使用 LLM 将历史对话压缩为简洁摘要
3. **事件压缩**：保留最近 4 轮对话，将更早的对话替换为总结文本
4. **更新会话**：更新 Session 中的事件列表

---

### Agent 层面总结（Filter 方式）

在 `agent/filters.py` 中实现了 `AgentSessionSummarizerFilter`，可以在 Agent 层面进行总结：

```python
class AgentSessionSummarizerFilter(BaseFilter):
    """Agent session summarizer filter."""

    def __init__(self, model: OpenAIModel):
        self.summarizer = SessionSummarizer(
            model=model,
            max_summary_length=600,
        )

    async def _after_every_stream(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        """每次流式返回后检查是否需要总结"""
        if not rsp.rsp.partial:
            events = ctx.metadata.get("events", [])
            conversation_text = self.summarizer._extract_conversation_text(events)
            # 当对话文本超过 12KB 时触发总结
            if len(conversation_text) > 12 * 1024:
                await self._do_summarize(ctx)

        # 缓存事件
        if "events" not in ctx.metadata:
            ctx.metadata["events"] = []
        ctx.metadata["events"].append(rsp.rsp)

    async def _after(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        """Agent 执行完成后执行总结"""
        await self._do_summarize(ctx)

    async def _do_summarize(self, ctx: AgentContext):
        """执行总结操作"""
        invocation_ctx = get_invocation_ctx()
        events = ctx.metadata.pop("events", [])

        # 从全局 session 中移除这些事件
        for event in events:
            if event in invocation_ctx.session.events:
                invocation_ctx.session.events.remove(event)

        # 对 Agent 产生的事件进行总结
        summary_text, compressed_events = await self.summarizer.create_session_summary_by_events(
            events, invocation_ctx.session.id, ctx=invocation_ctx
        )

        # 将压缩后的事件添加回 session
        if compressed_events:
            invocation_ctx.session.events.extend(compressed_events)
```

**使用方式**：

在 `agent/agent.py` 中启用 Filter：

```python
def create_agent() -> LlmAgent:
    agent = LlmAgent(
        name="python_tutor",
        model=_create_model(),
        instruction=INSTRUCTION,
        # 启用 Agent 层面的总结
        filters=[AgentSessionSummarizerFilter(_create_model())],
    )
    return agent
```

**两种方式的对比**：

| 特性 | SessionService 层面总结 | Agent 层面总结 |
|-----|----------------------|--------------|
| **触发时机** | 每 N 轮对话后 | 每次 Agent 执行后或文本超过阈值 |
| **总结范围** | 整个 Session 的所有事件 | 单个 Agent 产生的事件 |
| **适用场景** | 单 Agent 场景 | 多 Agent 协作场景 |
| **配置方式** | SessionService 初始化 | Agent Filter 配置 |
| **优势** | 简单易用，自动管理 | 更细粒度控制，支持多 Agent |

---

## 运行结果分析

### 完整输出

```txt
============================================================
示例2：LlmAgent + SummarizerSessionManager 集成演示
============================================================
📊 会话信息: llm_summarizer_manager_demo/user_005/c482b44c-0e10-431d-a08f-8d5e94082749

💬 开始多轮对话 (14 轮)...

--- 第 1 轮对话 ---
你好！很高兴你想学习Python编程。当然可以帮你！Python是一门非常适合初学者的编程语言...

--- 第 2 轮对话 ---
当然可以！变量是编程中最基础也最重要的概念之一。让我用一个生活中的例子来解释...

--- 第 3 轮对话 ---
很高兴你理解了变量的概念！Python中有几种主要的数据类型，我来为你详细介绍一下...

--- 第 4 轮对话 ---
很好的问题！控制流(Control Flow)指的是程序执行时的顺序控制...

📊 第 4 轮后会话状态:
   - 总结文本: **Summary of the Python Learning Conversation:**

1. **Key Decisions Made:**
   - User decided to start learning Python programming
   - User chose to begin with basic syntax
   - User wants to practice with small projects
   - User asked about variables, data types, and control flow

2. **Important Information Shared:**
   - Python is beginner-friendly
   - Variables are containers for storing data
   - Main data types: int, float, str, bool, list, dict
   - Control flow includes if-elif-else, for/while loops

3. **Actions Taken or Planned:**
   - User plans to do small projects after learning basics
   - User wants to practice with calculator project

   - 原始事件数: 8
   - 压缩后事件数: 5
   - 压缩比例: 37.5%

----------------------------------------

--- 第 5 轮对话 ---
太棒了！实践是巩固知识的最好方式。根据你目前掌握的内容（变量、数据类型、控制流）...

📊 第 7 轮后会话状态:
   - 总结文本: **Summary of the Python Learning Conversation:**
   - 原始事件数: 8
   - 压缩后事件数: 5
   - 压缩比例: 37.5%

----------------------------------------

📊 第 13 轮后会话状态:
   - 总结文本: ### Conversation Summary: Python Learning Progress

1. **Key Decisions Made**
   - User chose to learn Python programming
   - User decided to start with basic syntax
   - User wants to practice with projects
   - User wants to learn advanced concepts

2. **Important Information Shared**
   - Variables, data types, control flow concepts
   - Functions, OOP, exception handling
   - File operations and project examples

3. **Actions Taken or Planned**
   - Calculator project completed
   - Library management system project planned

   - 原始事件数: 13
   - 压缩后事件数: 5
   - 压缩比例: 61.5%

----------------------------------------

--- 开始手动创建总结 ---
[2026-02-05 10:22:24][INFO] Generated summary for session c482b44c-0e10-431d-a08f-8d5e94082749: 603 characters
[2026-02-05 10:22:24][INFO] Compressed session c482b44c-0e10-431d-a08f-8d5e94082749: 39 events -> 5 events
   - 总结文本: ### Conversation Summary: Python Learning Journey

1. **Key Decisions Made**
   - User chose to start learning Python programming
   - User decided to begin with basic syntax
   - User wants to practice with small projects
   - User wants to learn advanced concepts (functions, OOP, exception handling)
   - User wants to build a library management system

2. **Important Information Shared**
   - Python basics: variables, data types, control flow
   - Advanced concepts: functions, OOP, exception handling, file operations
   - Project examples: calculator, library management system

3. **Actions Taken or Planned**
   - Completed calculator project
   - Planned library management system project
   - Learned file operations for data persistence

   - 总结时间: Thu Feb  5 10:22:24 2026
   - 原始事件数: 39
   - 压缩后事件数: 5
   - 压缩比例: 87.2%
```

### 关键观察点

#### 1️⃣ **自动触发总结**

```
第 4 轮对话后 → 触发总结（4 % 3 = 1，但实际是第 4 轮）
第 7 轮对话后 → 触发总结（7 % 3 = 1）
第 13 轮对话后 → 触发总结（13 % 3 = 1）
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

#### 4️⃣ **手动强制总结**

```python
# 手动强制创建总结
await session_service.summarizer_manager.create_session_summary(session, force=True)
```

**说明**：
- 可以手动触发总结，不受触发条件限制
- 适用于需要立即压缩长对话的场景

---

## 功能特性详解

### 1. 多种触发条件

```python
check_summarizer_functions=[
    # 对话轮数触发（每 N 轮）
    set_summarizer_conversation_threshold(3),

    # 时间间隔触发（每 N 秒）
    # set_summarizer_time_interval_threshold(10),

    # Token 数量触发（每 N 个 token）
    # set_summarizer_token_threshold(1000),

    # 重要内容触发（根据内容重要度）
    # set_summarizer_important_content_threshold(),

    # 组合条件（AND 逻辑）
    # set_summarizer_check_functions_by_and(...),

    # 组合条件（OR 逻辑）
    # set_summarizer_check_functions_by_or(...),
]
```

### 2. 可配置参数

| 参数 | 说明 | 默认值 | 示例值 |
|-----|------|--------|--------|
| `max_summary_length` | 总结文本最大长度 | 1000 | 600 |
| `keep_recent_count` | 保留最近 N 轮对话 | 10 | 4 |
| `auto_summarize` | 是否自动总结 | True | True |

### 3. 总结效果

- **减少 Token 消耗**：长对话压缩为简短摘要，显著减少 token 使用
- **保持上下文**：保留关键信息和决策，不影响后续对话
- **提升性能**：减少处理的事件数量，提升响应速度

---

## Agent 层面总结（Filter 方式）

### 实现说明

在 `agent/filters.py` 中实现了 `AgentSessionSummarizerFilter`，提供了基于 Agent Filter 的总结方式。

#### 核心机制

1. **事件收集**：在 `_after_every_stream` 中收集 Agent 产生的所有事件
2. **触发检查**：检查对话文本长度是否超过阈值（12KB）
3. **事件隔离**：从全局 Session 中移除 Agent 的事件，避免重复总结
4. **总结压缩**：使用 `create_session_summary_by_events` 对 Agent 事件进行总结
5. **事件替换**：将压缩后的事件添加回 Session

#### 使用场景

- **多 Agent 协作**：每个 Agent 独立总结自己的对话历史
- **细粒度控制**：可以针对不同 Agent 配置不同的总结策略
- **避免冲突**：Agent 层面的总结不会与 SessionService 层面的总结冲突

#### 配置示例

```python
# 在 agent/agent.py 中启用
def create_agent() -> LlmAgent:
    agent = LlmAgent(
        name="python_tutor",
        model=_create_model(),
        instruction=INSTRUCTION,
        # 启用 Agent 层面的总结 Filter
        filters=[AgentSessionSummarizerFilter(_create_model())],
    )
    return agent
```

#### 工作流程

```
用户输入 → Agent 处理 → 生成响应
    ↓
_after_every_stream: 收集事件到 ctx.metadata["events"]
    ↓
检查对话文本长度 > 12KB？
    ↓ 是
_do_summarize:
  - 从 Session 中移除 Agent 事件
  - 调用 create_session_summary_by_events 总结
  - 将压缩后的事件添加回 Session
    ↓
_after: Agent 执行完成后再次检查总结
```

#### 关键方法说明

**`_after_every_stream`**：
- 每次流式返回后调用
- 收集事件到 `ctx.metadata["events"]`
- 检查对话文本长度，超过阈值时触发总结

**`_after`**：
- Agent 执行完成后调用
- 确保所有事件都被处理

**`_do_summarize`**：
- 执行实际的总结操作
- 使用 `create_session_summary_by_events` 方法
- 处理事件隔离和替换

#### 注意事项

1. **并发安全**：如果多个 Agent 并发执行，需要添加协程锁保证顺序
2. **事件隔离**：确保 Agent 的事件不会与 SessionService 的总结冲突
3. **性能考虑**：总结操作会调用 LLM，注意控制频率

---

## 总结

本示例展示了 Session Summarizer 的两种使用方式：

1. **SessionService 层面总结**（推荐用于单 Agent 场景）
   - 简单易用，自动管理
   - 配置灵活，支持多种触发条件
   - 适合大多数单 Agent 应用场景

2. **Agent 层面总结**（推荐用于多 Agent 场景）
   - 细粒度控制，每个 Agent 独立总结
   - 避免多 Agent 协作时的冲突
   - 适合复杂的多 Agent 系统

两种方式可以结合使用，根据实际需求选择最适合的方案。
