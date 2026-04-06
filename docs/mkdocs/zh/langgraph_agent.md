# LangGraph Agent

LangGraphAgent 封装了基于 LangGraph 图编排的 AI Agent 实现。它使用有向图定义复杂工作流，支持多步骤处理、条件分支、并行执行等高级能力。

## 与LlmAgent的对比

| 特性 | LlmAgent | LangGraphAgent |
|------|----------|----------------|
| **适用场景** | 简单对话、工具调用 | 复杂工作流、多步骤处理 |
| **执行模式** | LLM主导决策 | 图结构预定义流程 |
| **控制粒度** | 粗粒度（依赖LLM推理） | 细粒度（精确控制每个步骤） |
| **并行处理** | 不支持 | 原生支持 |
| **条件分支** | LLM自主判断 | 显式定义分支逻辑 |
| **状态管理** | 简单会话状态 | 复杂图状态管理 |
| **可预测性** | 低（依赖LLM） | 高（预定义流程） |

## 场景推荐

**使用LangGraphAgent的场景：**
- 需要多步骤、有序处理的复杂任务
- 需要条件分支和并行执行的工作流
- 对执行流程有精确控制要求
- 需要状态在多个节点间传递和转换
- RAG、代码生成、数据处理等复杂pipeline

**使用LlmAgent的场景：**
- 简单对话和问答
- 基础工具调用
- 灵活性要求高于可预测性的场景

## 安装 LangGraph

建议先创建独立虚拟环境，再按项目仓库`requirements-pypi.txt`当前依赖版本安装

### 单独安装
```bash
# 创建 Python 虚拟环境
python3 -m venv .venv
# 激活当前目录下的虚拟环境
source .venv/bin/activate
# 通过项目 requirements 中使用的镜像源安装指定版本的 LangGraph
pip install "langgraph==0.6.0"
```

### 使用requirements安装

如果你希望安装与仓库当前开发环境一致的依赖，推荐直接使用根目录下的 `requirements.txt` 和 `requirements-pypi.txt`。

```bash
# 创建 Python 虚拟环境
python3 -m venv venv
# 激活当前目录下的虚拟环境
source venv/bin/activate
# 先安装 PyPI 侧依赖
pip install -r requirements-pypi.txt
# 再安装项目基础依赖
pip install -r requirements.txt
```

## 核心概念

在本框架中，`LangGraphAgent` 的典型使用方式是：先用 LangGraph 构建工作流图，再将 `compile()` 之后得到的图对象传给 `LangGraphAgent`。理解下面几个核心概念后，基本就能看懂大部分示例。

### 图（Graph）

图是工作流的核心结构，由节点和边组成，用来描述“从哪里开始、经过哪些步骤、在什么条件下流向哪里”。示例代码通常直接使用 LangGraph 原生的 `StateGraph` 来构图：

```python
from langgraph.graph import StateGraph, START, END

graph_builder = StateGraph(MyState)
graph_builder.add_node("chatbot", chatbot_node)
graph_builder.add_node("tools", tool_node)

graph_builder.add_edge(START, "chatbot")
graph_builder.add_edge("tools", "chatbot")
graph_builder.add_edge("chatbot", END)
```

补充说明：

- `START` 和 `END` 是 LangGraph 提供的特殊节点标识，分别表示图的起点和终点
- 它们属于框架内置概念，不需要像普通业务节点一样单独实现函数
- 图本身只负责描述执行拓扑，真正的运行逻辑由各个节点函数完成

### 节点（Node）

节点表示工作流中的一个处理步骤。一个节点通常对应一个函数，可以是普通处理节点、LLM 节点，或者工具执行节点。

在本框架中，常见节点写法如下：

```python
from trpc_agent_sdk.agents import langgraph_llm_node

@langgraph_llm_node
def chatbot_node(state: MyState, config):
    return {"messages": [llm.invoke(state["messages"])]}
```

如果是工具节点，则通常配合 `@tool` 和 `@tool_node` 使用：

```python
from langchain_core.tools import tool
from trpc_agent_sdk.agents.langgraph_agent import tool_node

@tool
@tool_node
def calculate(operation: str, a: float, b: float) -> str:
    return f"{operation}: {a}, {b}"
```

补充说明：

- 节点的输入通常是当前 `state`
- 节点的返回值通常是一个字典，用于更新图状态
- `@langgraph_llm_node` 会为 LLM 节点补充 tracing 和事件记录能力
- `@tool_node` 会为工具节点补充工具调用链路记录能力

### 状态（State）

状态是在节点之间流转的数据容器。框架中的 LangGraph 示例通常使用 `TypedDict` 来定义状态结构；如果某个字段需要在多轮节点执行中持续累积，还可以配合 `Annotated[..., add_messages]` 这类 reducer 使用。

```python
from typing import Annotated
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages

class MyState(TypedDict):
    messages: Annotated[list, add_messages]
    user_input: str
    result: str
```

补充说明：

- `messages` 往往用于保存对话消息历史，是 LangGraph 对话类工作流里最常见的状态字段
- 自定义字段如 `user_input`、`result`、`status` 等可用于存放业务数据
- 节点通过读取 `state` 获取上游结果，并通过返回字典写回新的状态值

### 状态模式（State Schema）

所谓“状态模式”通常就是传给 `StateGraph(...)` 的状态类型定义。最常见的做法就是上面的 `TypedDict`。

```python
graph_builder = StateGraph(MyState)
```

它的作用是：

- 约束图状态中有哪些字段
- 约定每个字段的数据类型
- 为部分字段指定合并策略，例如消息列表追加而不是直接覆盖

### 编译后的图（Compiled Graph）

`LangGraphAgent` 真正接收的不是 `StateGraph` 构建器本身，而是 `compile()` 之后的图对象。框架中的 `LangGraphAgent` 明确要求传入已编译图。

```python
from trpc_agent_sdk.agents.langgraph_agent import LangGraphAgent

graph = graph_builder.compile()

agent = LangGraphAgent(
    name="workflow_agent",
    graph=graph,
    instruction="You are a workflow assistant.",
)
```

补充说明：

- `compile()` 会把前面定义的节点、边和条件路由整理成可执行图
- `LangGraphAgent` 在运行时会调用该图的流式执行能力，并把 LangGraph 的输出转换为 `trpc_agent` 的 `Event`
- 因此可以把 `StateGraph` 理解为“定义阶段”，把编译后的 graph 理解为“执行阶段”

更多有关图的概念可以参考：[Graph](./graph.md)

## 创建 LangGraphAgent

提供 LangGraph 的 Compiled Graph 即可创建 LangGraphAgent，如下所示：

相关参数遵循LlmAgent的参数说明。

```python
from trpc_agent_sdk.agents.langgraph_agent import LangGraphAgent

# 假设已经构建了 LangGraph
graph = build_your_langgraph()

agent = LangGraphAgent(
    name="workflow_agent",
    description="A complex workflow processing agent",
    graph=graph,
    instruction="You are a workflow assistant that processes tasks step by step.",
)
```

## 构建 LangGraph 工作流

### 基础图结构

```python
from langchain.chat_models import init_chat_model
from langchain_core.tools import tool
from langgraph.graph import StateGraph, START
from langgraph.graph.message import add_messages
from langgraph.prebuilt import tools_condition, ToolNode
from typing_extensions import TypedDict
from typing import Annotated

# 定义状态结构
class State(TypedDict):
    messages: Annotated[list, add_messages]

# 初始化模型
model = init_chat_model("deepseek:deepseek-chat", api_key="your-api-key", api_base="https://api.deepseek.com/v1")

def build_graph():
    graph_builder = StateGraph(State)
    
    # 添加节点
    graph_builder.add_node("chatbot", chatbot_node)
    graph_builder.add_node("tools", tool_node)
    
    # 添加边
    graph_builder.add_edge(START, "chatbot")
    graph_builder.add_conditional_edges("chatbot", tools_condition)
    graph_builder.add_edge("tools", "chatbot")
    
    return graph_builder.compile()
```

### 接入 trpc_agent

框架提供了两个重要装饰器，用于将 LangGraph 的 Node 接入 `trpc_agent`：

#### @langgraph_llm_node 装饰器

用于装饰调用LLM的节点，自动记录LLM调用信息：

```python
from trpc_agent_sdk.agents import langgraph_llm_node

@langgraph_llm_node
def chatbot_node(state: State):
    """通用LLM节点，调用LLM生成回复"""
    return {"messages": [llm_with_tools.invoke(state["messages"])]}

# 自定义输入输出键的用法
@langgraph_llm_node(input_key="conversation", output_key="response")  
def custom_chatbot(state: CustomState):
    """自定义LLM输入与输出字段的LLM节点"""
    return {"response": [llm.invoke(state["conversation"])]}
```

#### @tool_node 装饰器

用于装饰工具执行节点，自动记录工具调用信息。注意：`@tool_node` 必须放在 LangGraph 的 `@tool` 装饰器之后：

```python
from trpc_agent_sdk.agents.langgraph_agent import tool_node
from langchain_core.tools import tool

@tool
@tool_node  
def calculate(operation: str, a: float, b: float) -> str:
    """执行数学计算的工具"""
    if operation == "add":
        return f"Result: {a} + {b} = {a + b}"
    # ... 其他操作
```

## Human-In-The-Loop 能力

详见 [Human-In-The-Loop](./human_in_the_loop.md)。

## 高级配置

### LangGraph 配置

框架提供 `RunConfig` 用于配置 LangGraph 的运行时参数，如下所示：
- `input`：用于传递用户自定义输入，会与 Agent 内部的 `{"messages": [xxx]}` 合并后，作为 `langgraph.astream` 的输入；
- [Stream Mode](https://langchain-ai.github.io/langgraph/how-tos/streaming/)：用于控制 LangGraph 输出。框架内置 `updates`、`custom`、`messages` 三种流模式，用户可按需扩展；
- [RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html)：LangGraph 的运行配置，用户可按需设置；
- 其他配置如有需要，也可通过 `RunConfig` 传入。当前框架只透传 `stream_mode` 和 `runnable_config` 两项，如需扩展欢迎提 issue。

```python
from trpc_agent_sdk.agents.run_config import RunConfig

run_config = RunConfig(
    agent_run_config={
        "input": {"user_input": {"custom1": "xxx"}},
        "stream_mode": ["values"],
        "runnable_config": {
            "configurable": {"xxx": "xxx"}
        }
    }
)

runner.run_async(..., run_config=run_config)
```

### 获取 LangGraph 原始数据

获取 `Event` 后，可以进一步读取 LangGraph 的原始响应数据，如下所示：

```python
from trpc_agent_sdk.agents.langgraph_agent import get_langgraph_payload

async for event in runner.run_async(...):
    langgraph_payload = get_langgraph_payload(event)
    if langgraph_payload:
        stream_mode = langgraph_payload["stream_mode"] 
        chunk = langgraph_payload["chunk"]
        # 处理原始 LangGraph 数据
```

### 在节点中访问 Agent 上下文

在 LangGraph 节点中可以访问 `trpc_agent` 的上下文信息：

```python
from trpc_agent_sdk.agents.langgraph_agent import get_langgraph_agent_context

@langgraph_llm_node
def context_aware_node(state: State, config: RunnableConfig):
    """可访问 Agent 上下文的节点"""
    ctx = get_langgraph_agent_context(config)
    user_id = ctx.session.user_id
    session_state = ctx.session.state
    
    # 基于上下文进行处理
    response = llm.invoke(build_context_prompt(state, user_id, session_state))
    return {"messages": [response]}
```

## 内存管理建议

LangGraphAgent 支持两种内存管理方式：

1. **使用trpc_agent的SessionService**（推荐）：
   ```python
   # 不使用 checkpointer，trpc_agent 将托管会话信息
   graph = graph_builder.compile()
   ```

2. **使用 LangGraph 的 checkpointer**：
   ```python
   from langgraph.checkpoint.memory import MemorySaver
   
   memory = MemorySaver()
   graph = graph_builder.compile(checkpointer=memory)
   ```

推荐使用第一种方式，因为它与 `trpc_agent` 的多 Agent 对话管理集成得更好。

## 自定义事件发送

### LangGraphEventWriter 使用

在 LangGraph 节点中，支持使用 `LangGraphEventWriter` 发送自定义事件，这些事件可以被事件转换器（如 AG-UI 的事件转换器）捕获并转换为协议特定的事件。

#### 基础用法

```python
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter
from trpc_agent_sdk.agents.utils import LangGraphEventWriter

def custom_node(
    state: State,
    *,
    config: RunnableConfig,
    writer: StreamWriter,
):
    """在节点中使用 LangGraphEventWriter 发送事件

    重要提示：config 和 writer 必须是关键字参数（在 * 之后），
    这样 LangGraph 才能正确注入它们。

    Args:
        state: 图状态
        config: 运行配置（关键字参数，由 LangGraph 注入）
        writer: 流写入器（关键字参数，由 LangGraph 注入）
    """
    # 从 config 创建 event writer
    event_writer = LangGraphEventWriter.from_config(writer, config)

    # 发送文本事件
    event_writer.write_text("Processing data...")

    # 发送自定义事件（结构化数据）
    event_writer.write_custom({
        "stage": "processing",
        "progress": 50,
        "status": "in_progress",
    })

    return {}
```

#### 文本事件

`write_text()` 方法用于发送文本消息：

```python
# 发送普通文本
event_writer.write_text("正在处理数据...")

# 发送思考文本（thought）
event_writer.write_text("让我分析一下这个问题...", thought=True)

# 发送完整消息（非流式）
event_writer.write_text("处理完成！", partial=False)
```

参数说明：
- `text`: 文本内容
- `partial`: 是否为流式/部分事件（默认 `True`）
- `thought`: 是否为思考/推理文本（默认 `False`）

#### 自定义事件

`write_custom()` 方法用于发送结构化数据：

```python
# 发送进度信息
event_writer.write_custom({
    "stage": "initialization",
    "progress": 0,
    "status": "starting",
})

# 发送任意结构化数据
event_writer.write_custom({
    "metric": "accuracy",
    "value": 0.95,
    "timestamp": time.time(),
})
```

### 构建 AG-UI 协议消息

要将 LangGraph 事件转换为 AG-UI 协议消息，需要创建自定义事件转换器。

#### 创建自定义转换器

```python
from typing import AsyncGenerator
from ag_ui.core import BaseEvent, EventType, CustomEvent
from trpc_agent_sdk.events import Event as TrpcEvent
from trpc_agent_sdk.agents.utils import LangGraphEventType, get_event_type
from trpc_agent_sdk.server.ag_ui._plugin._langgraph_event_translator import (
    AgUiLangGraphEventTranslator,
    AgUiTranslationContext,
)

class CustomAgUiEventTranslator(AgUiLangGraphEventTranslator):
    """自定义 AG-UI 事件转换器"""

    async def translate(
        self,
        event: TrpcEvent,
        context: AgUiTranslationContext,
    ) -> AsyncGenerator[BaseEvent, None]:
        """将 LangGraph trpc Event 转换为 AG-UI 事件

        Args:
            event: trpc Event 对象
            context: AG-UI 转换上下文（包含 thread_id 和 run_id）

        Yields:
            AG-UI BaseEvent 对象
        """
        # 获取事件类型（使用枚举）
        event_type = get_event_type(event)

        if event_type == LangGraphEventType.TEXT:
            # 处理文本事件
            yield await self._translate_text_event(event, context)
        elif event_type == LangGraphEventType.CUSTOM:
            # 处理自定义事件
            yield await self._translate_custom_event(event, context)

    async def _translate_text_event(
        self,
        event: TrpcEvent,
        context: AgUiTranslationContext,
    ) -> CustomEvent:
        """转换文本事件为 AG-UI CustomEvent"""
        # 提取文本内容
        text_content = ""
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    text_content += part.text

        # 返回 AG-UI CustomEvent
        return CustomEvent(
            type=EventType.CUSTOM,
            timestamp=int(event.timestamp * 1000),  # 转换为毫秒
            raw_event=None,
            name="progress_text",  # 自定义事件名称
            value={"text": text_content},
        )

    async def _translate_custom_event(
        self,
        event: TrpcEvent,
        context: AgUiTranslationContext,
    ) -> CustomEvent:
        """转换自定义事件为 AG-UI CustomEvent"""
        # 提取自定义数据
        custom_data = {}
        if event.custom_metadata:
            custom_data = event.custom_metadata.get("data", {})

        # 返回 AG-UI CustomEvent
        return CustomEvent(
            type=EventType.CUSTOM,
            timestamp=int(event.timestamp * 1000),
            raw_event=None,
            name="analysis_progress",  # 自定义事件名称
            value=custom_data,  # 传递自定义数据
        )
```

#### 使用自定义转换器

在创建 `AgUiAgent` 时注入自定义转换器：

```python
from trpc_agent_sdk.server.ag_ui import AgUiAgent

def create_agui_agent() -> AgUiAgent:
    """创建带有自定义事件转换器的 AgUiAgent"""
    from agent.event_translator import CustomAgUiEventTranslator

    # 创建自定义转换器实例
    custom_translator = CustomAgUiEventTranslator()

    agui_agent = AgUiAgent(
        trpc_agent=your_langgraph_agent,
        app_name="your_app",
        event_translator=custom_translator,  # 注入自定义转换器
    )
    return agui_agent
```

## 完整示例

完整的 LangGraph Agent 示例见：
- 基础示例：[examples/langgraph_agent](../../../examples/langgraph_agent/README.md)
- AG-UI 自定义事件示例：examples/trpc_agui_with_langgraph_custom（示例待补充）
