# Agent Cancel 机制

Agent 在执行过程中，有的时候输出不满足用户的要求，这个时候用户常常会中断 Agent 执行，给出部分信息之后（对中断前哪些输出不满意，后面应该怎么做），再让 Agent 继续执行。

对于此场景，trpc-agent 框架提供了 Cancel 机制，允许取消 Agent 正在执行的操作，保存部分内容（LLM正在流式输出的内容、正在执行的工具内容等）。该机制基于检查点（checkpoint）设计，各 Agent 在实现中，会在检查点处（LLM流式输出chunk、一个工具调用结束后等）检查当前 Agent 是否应该终止，如果终止，则会抛出异常，框架会记录并保存部分信息到会话历史中。

当前已经在框架提供的所有 Agent 中接入了此能力，其他业务自己实现的 CustomAgent 也可轻松接入进来。

| 模块类型 | 模块名称 | Cancel 支持 | 说明 |
|---------|---------|-------------|------|
| Single Agent | `LlmAgent` | ✅ | 在 LLM 流式输出、工具执行等位置设置检查点 |
| Single Agent | `LangGraphAgent` | ✅ | 在LangGraph的流式输出中设了置检查点 |
| Single Agent | `ClaudeAgent` | ✅ | 在使用claude-sdk流式输出中设置了检查点 |
| Single Agent | `TrpcRemoteA2aAgent` | ✅ | 在http流式输出中设置了检查点 |
| Multi Agent | `ChainAgent` | ✅ | 从其子Agent中抛出异常 |
| Multi Agent | `ParallelAgent` | ✅ | 任一子 Agent 抛出异常，则取消执行 |
| Multi Agent | `CycleAgent` | ✅ | 从其子Agent中抛出异常 |
| Multi Agent | `TeamAgent` | ✅ | Leader 和 Member 执行期间均可被取消 |
| Agent Service | `TrpcA2aAgentService` | ✅ | A2A 协议的 cancel_task 接口取消远程Agent的执行 |
| Agent Service | `AgUiService` | ✅ | 通过 SSE 连接断开检测，Agent自动取消执行 |


## Agent Cancel 机制设计介绍

### 架构设计

如下架构所示：
- 框架启动时，将会创建一个 `_RunCancellationManager` 的全局对象，用于管理Agent的取消信号
- 用户通过Runner来运行、打断Agent执行
    - 用户通过 `run_async` 执行 Agent，Runner会在执行Agent执行前，通过 `register_run` 注册本次执行的信息到 Manager，SessionKey 是 (app_name, user_id, session_id) 的三元组
    - 用户通过 `cancel_run_async` 取消 Agent 的执行，Runner 收到 Agent 抛出的 `RunCancelledException`，完成Cancel的后置处理（注入部分流式消息、部分工具调用的内容到Agent的会话中），在处理后，由 Runner 生成 `AgentCancelledEvent` 传递终止信息，可以通过其 error_message 字段获得中断的原因
- Agent在执行过程中，埋入检查点，以接入Cancel的能力
    - 在 Agent 的执行过程中，通过在 `_run_async_impl` 实现中，使用 `ctx.raise_if_cancelled` 在各个检查点（LLM流式输出chunk后、工具调用后等）检查当前的执行是否被取消，如果 `runner.cancel_run_async` 被调用过，则Agent的执行会被标记为取消，raise_if_cancelled执行会抛出 `RunCancelledException` 的异常
    - 一般来说，常见的检查点有这些：LLM流式输出过程中，工具调用之后，工具调用过程中取消暂时不支持
- Agent服务，通过接口自动调用 `runner.cancel_run_async`，通过Runner返回的AgentCancelledEvent事件获取取消的细节
    - 对AG-UI服务，其协议未原生支持取消，客户端通过断开连接来取消Agent的执行，Agent服务通过感知到连接断开的异常，自动调用了 `runner.cancel_run_async` 以支持此能力
    - 对A2A服务，其协议原生支持取消，通过调用接口 `cancel_task` 来取消，框架已经支持了此接口，适配了 `runner.cancel_run_async`，但需要配合hash寻址来使用。在多节点部署场景，配比hash寻址比较麻烦，一个更简单的方法是类似AG-UI一样，Agent服务自动感知到连接断开，调用 `runner.cancel_run_async`，但受限于目前 a2a-sdk 的底层实现，连接断开后，Agent仍然会继续执行，暂时需要使用hash寻址来完成取消操作。
    - 对自定义服务，推荐实现基于连接断开触发Agent取消执行的逻辑，这种方式实现成本很低，不需要依赖hash寻址，客户端直接断开与远程Agent的连接即可。

<p align="center">
  <img src="../assets/imgs/agent_cancel.png" alt="Agent Cancel" />
</p>

### 会话管理

Agent被Cancel时，将会根据场景进行不同的会话管理：

**场景 1：LLM 流式输出期间取消**
- 会话管理：LLM回复开始到被打断区间的消息，均会被保留，这部分的流式文本后，将会追加一个消息 "User cancel the agent execution." 让Agent感知到取消事件的发生
- 效果：下一轮对话，用户能指出哪些文本不合理，Agent将会纠正输出

**场景 2：工具执行期间取消**
- 会话管理：针对Agent需要调用多个工具的场景，比如需要调用工具1和工具2，在调用工具1时，用户取消Agent执行，在等到工具1调用结束后，将会跳过工具2的调用而结束，本轮工具2的调用信息将会从历史会话中移除，就像Agent本轮从未执行过工具2一样，同样，在工具1的调用响应后，将会追加一个消息 "User cancel the agent execution." 让Agent感知到取消事件的发生
- 效果：下一轮对话，Agent能够感知到工具2没有调用，可能会调用工具2。

### 限制

> **⚠️ 当前 Cancel 机制仅支持单节点场景**

`_RunCancellationManager` 使用进程内存储（`Dict`）来追踪活跃的运行。这意味着：

1. **Cancel 请求必须发送到运行 Agent 的同一节点**
2. **不支持跨节点取消**
3. **适用场景**：
   - 单节点部署
   - 客户端通过同一连接（WebSocket、SSE）与 Agent 通信
   - 连接断开时自动触发取消

## 简单用法

### 基本示例

```python
import asyncio
import uuid
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, Part

async def main():
    runner = Runner(
        app_name="my_app",
        agent=my_agent,
        session_service=InMemorySessionService(),
    )

    user_id = "demo_user"
    session_id = str(uuid.uuid4())

    # 在后台任务中运行 Agent
    async def run_agent():
        user_content = Content(parts=[Part.from_text("请详细介绍人工智能的发展历史")])
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=user_content,
        ):
            # 检查是否收到取消事件
            if isinstance(event, AgentCancelledEvent):  # AgentCancelledEvent
                print(f"运行已取消: {event.error_message}")
                continue # continue后，runner.run_async将会结束

            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)

    task = asyncio.create_task(run_agent())

    # 等待一段时间后取消
    await asyncio.sleep(2)

    # 使用相同的 user_id 和 session_id 取消运行
    runner2 = Runner(xxxx)
    success = await runner2.cancel_run_async(
        user_id=user_id,
        session_id=session_id,
        timeout=3.0,  # 等待Agent取消动作完成的超时时间
    )
    print(f"\n取消请求结果: {success}")

    await task
    await runner.close()
    await runner2.close()

asyncio.run(main())
```

### Agent自定义服务示例

#### 方式一：基于连接断开的取消（推荐）

在 SSE/WebSocket 等长连接场景下，推荐通过检测连接断开来自动触发取消。这种方式实现成本低，用户只需断开连接即可触发取消，无需额外的取消接口。

以下是基于 FastAPI SSE 的示例：

```python
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, Part
from trpc_agent_sdk import cancel

app = FastAPI()

# 创建 Agent 和 Session Service
agent = LlmAgent(name="my_agent", model=model, instruction="你是一个智能助手")
session_service = InMemorySessionService()

# Cancel 等待超时配置
CANCEL_WAIT_TIMEOUT = 3.0


@app.post("/chat/{user_id}/{session_id}")
async def chat_endpoint(user_id: str, session_id: str, message: str, request: Request):
    """SSE 聊天端点，支持连接断开自动取消"""

    app_name = "my_app"

    async def event_generator():
        # 为每次请求创建 Runner
        runner = Runner(
            app_name=app_name,
            agent=agent,
            session_service=session_service,
        )

        try:
            user_content = Content(parts=[Part.from_text(message)])

            async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=user_content,
            ):
                # 检测客户端是否断开连接
                if await request.is_disconnected():
                    break

                # 发送 SSE 事件
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.text:
                            yield f"data: {part.text}\n\n"

        except asyncio.CancelledError:
            # 连接被客户端关闭
            raise
        finally:
            # 无论正常结束还是连接断开，都触发取消操作
            # 这确保了 Agent 执行被正确终止，部分结果被保存
            cleanup_event = await cancel.cancel_run(app_name, user_id, session_id)

            if cleanup_event is not None:
                try:
                    # 等待取消操作完成
                    await asyncio.wait_for(cleanup_event.wait(), timeout=CANCEL_WAIT_TIMEOUT)
                except asyncio.TimeoutError:
                    pass  # 超时后继续，Agent 可能仍在运行

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )
```

这种模式已在 AG-UI 服务中实现，可参考 [trpc_agent_sdk/server/ag_ui/_plugin/_ag_ui_handler.py](../../../trpc_agent_sdk/server/ag_ui/_plugin/_ag_ui_handler.py)

#### 方式二：显式取消接口

如果需要提供独立的取消接口（如 REST API），但需要注意，可以使用以下方式：

```python
from fastapi import FastAPI, HTTPException

app = FastAPI()
runner = Runner(...)

@app.post("/sessions/{user_id}/{session_id}/cancel")
async def cancel_session_run(user_id: str, session_id: str):
    """取消指定会话的运行"""
    success = await runner.cancel_run_async(
        user_id=user_id,
        session_id=session_id,
        timeout=3.0,
    )
    if success:
        return {"status": "cancellation_requested"}
    else:
        raise HTTPException(
            status_code=404,
            detail="未找到该会话的活跃运行"
        )
```

**注意**：此方式要求取消请求必须发送到运行 Agent 的同一节点，在多节点部署场景下需要配合 hash 路由使用，确保cancel请求发到执行Agent的节点上。

## Agent Cancel 指引

### LlmAgent

LlmAgent在执行流程的关键位置设置了检查点：

**检查点位置：**
- 每轮对话开始时
- LLM API 调用前
- LLM 流式输出期间（每个 chunk）
- 工具执行前后

**使用示例：**

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

# 定义工具
async def get_weather(city: str) -> dict:
    """获取城市天气"""
    await asyncio.sleep(3)  # 模拟耗时操作
    return {"city": city, "temperature": "25°C", "condition": "晴"}

# 创建 Agent
agent = LlmAgent(
    name="weather_agent",
    model=OpenAIModel(model_name="deepseek-chat"),
    instruction="你是一个天气查询助手",
    tools=[FunctionTool(get_weather)],
)

# 创建 Runner
runner = Runner(
    app_name="weather_app",
    agent=agent,
    session_service=InMemorySessionService(),
)

# 运行并支持取消
async def run_with_cancel():
    task = asyncio.create_task(run_agent())
    await asyncio.sleep(1)
    await runner.cancel_run_async(user_id, session_id)
    await task
```

**完整示例：**
- [examples/llmagent_with_cancel](../../../examples/llmagent_with_cancel/README.md)

### LangGraphAgent

LangGraphAgent 将 LangGraph 封装为 trpc-agent 兼容的 Agent，同样支持 Cancel 机制。

**检查点位置：**
- 图节点执行前后
- 流式输出期间

**使用示例：**

```python
from trpc_agent_sdk.agents import LangGraphAgent
from langgraph.graph import StateGraph

# 构建 LangGraph
def build_graph():
    builder = StateGraph(State)
    builder.add_node("process", process_node)
    builder.add_node("respond", respond_node)
    builder.set_entry_point("process")
    builder.add_edge("process", "respond")
    return builder.compile()

# 创建 LangGraphAgent
agent = LangGraphAgent(
    name="langgraph_agent",
    description="LangGraph 驱动的 Agent",
    graph=build_graph(),
)

runner = Runner(
    app_name="langgraph_app",
    agent=agent,
    session_service=InMemorySessionService(),
)

# Cancel 用法与 LlmAgent 相同
await runner.cancel_run_async(user_id, session_id)
```

**完整示例：**
- [examples/langgraph_agent_with_cancel](../../../examples/langgraph_agent_with_cancel/README.md)

### ClaudeAgent

ClaudeAgent 使用 Claude SDK 的子进程模式运行，Cancel 时会终止子进程。

**Cancel 实现：**
- 检测到取消请求时，向 Claude SDK 子进程发送终止信号
- 子进程退出后，保存部分响应到会话

**使用示例：**

```python
from trpc_agent_sdk.server.agents.claude import ClaudeAgent, setup_claude_env
from trpc_agent_sdk.models import OpenAIModel

model = OpenAIModel(model_name="deepseek-chat")

# 设置 Claude 环境
setup_claude_env(
    proxy_host="0.0.0.0",
    proxy_port=8082,
    claude_models={"all": model},
)

# 创建 ClaudeAgent
agent = ClaudeAgent(
    name="claude_agent",
    model=model,
    instruction="你是一个智能助手",
    tools=[FunctionTool(some_tool)],
)
agent.initialize()

runner = Runner(
    app_name="claude_app",
    agent=agent,
    session_service=InMemorySessionService(),
)

# Cancel 用法相同
await runner.cancel_run_async(user_id, session_id)
```

**注意事项：**
- Cancel 会导致 Claude SDK 子进程被终止，可能会看到 `ProcessError` 日志，这是正常行为
- 子进程终止后，部分响应会被保存到会话

**完整示例：**
- [examples/claude_agent_with_cancel](../../../examples/claude_agent_with_cancel/README.md)

### TeamAgent

TeamAgent 在 Leader 规划和 Member 执行期间均支持 Cancel。

**Cancel 场景：**
1. **Leader 规划期间取消**：保存 Leader 的部分响应
2. **Member 执行期间取消**：保存 Member 的部分响应到团队记忆

**使用示例：**

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.teams import TeamAgent
from trpc_agent_sdk.tools import FunctionTool

# 创建团队成员
researcher = LlmAgent(
    name="researcher",
    model=model,
    description="研究专家",
    instruction="负责信息搜索",
    tools=[FunctionTool(search_web)],
)

writer = LlmAgent(
    name="writer",
    model=model,
    description="写作专家",
    instruction="负责内容创作",
)

# 创建团队
team = TeamAgent(
    name="content_team",
    model=model,
    members=[researcher, writer],
    instruction="协调研究和写作任务",
    share_member_interactions=True,
)

runner = Runner(
    app_name="team_app",
    agent=team,
    session_service=InMemorySessionService(),
)

# Cancel 会中断当前执行的 Leader 或 Member
await runner.cancel_run_async(user_id, session_id)
```

**完整示例：**
- [examples/team_with_cancel](../../../examples/team_with_cancel/README.md)

## Agent 服务 Cancel 指引

### A2A

通过 A2A 协议部署的 Agent 服务支持远程 Cancel。

**架构：**

```
┌─────────────────────────────────────────────────┐
│                   客户端                         │
│  ┌───────────────────────────────────────────┐  │
│  │         TrpcRemoteA2aAgent                │  │
│  │     (连接远程 A2A 服务)                     │  │
│  └─────────────┬─────────────────────────────┘  │
│                │ A2A Protocol                   │
│                │ (支持 Cancel)                  │
└────────────────┼────────────────────────────────┘
                 │
                 │ HTTP
                 │
┌────────────────▼────────────────────────────────┐
│                   服务端                         │
│  ┌───────────────────────────────────────────┐  │
│  │      TrpcA2aAgentService                  │  │
│  │  ┌─────────────────────────────────────┐  │  │
│  │  │          LlmAgent                   │  │  │
│  │  │     (支持 Cancel 的 Agent)          │  │  │
│  │  └─────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

**服务端配置：**

```python
from trpc_agent_sdk.server.a2a import TrpcA2aAgentService
from trpc_agent_sdk.server.a2a._core.executor import A2aAgentExecutorConfig

# 配置 Cancel 等待超时
executor_config = A2aAgentExecutorConfig(
    cancel_wait_timeout=3.0,  # 默认 1.0 秒
)

a2a_service = TrpcA2aAgentService(
    service_name="trpc.a2a.agent.weather_with_cancel",
    agent=agent,
    executor_config=executor_config,
)
```

**客户端使用：**

```python
from trpc_agent_sdk.server.a2a.agent import TrpcRemoteA2aAgent

# 创建远程 Agent
remote_agent = TrpcRemoteA2aAgent(
    name="weather_agent",
    service_name="trpc.a2a.agent.weather_with_cancel",
    description="远程天气查询服务",
)
await remote_agent.initialize()

runner = Runner(
    app_name="client_app",
    agent=remote_agent,
    session_service=InMemorySessionService(),
)

# Cancel 会向远程服务发送取消请求
success = await runner.cancel_run_async(
    user_id=user_id,
    session_id=session_id,
    timeout=3.0,
)
```

**配置说明：**

| 配置位置 | 参数 | 默认值 | 说明 |
|----------|------|--------|------|
| 服务端 | `cancel_wait_timeout` | 1.0 | 服务端等待后端 Agent 取消完成的超时时间 |
| 客户端 | `timeout` | 1.0 | 客户端等待本端 RemoteA2aAgent 取消完成的超时时间 |

建议两者配置相同的超时时间。

**完整示例：**
- examples/trpc_a2a_with_cancel（示例待补充）

### AG-UI

通过 AG-UI 协议部署的 Agent 服务，当客户端关闭 SSE 连接时自动触发 Cancel。

**架构：**

```
┌─────────────────────────────────────────────────┐
│                   客户端                         │
│  ┌───────────────────────────────────────────┐  │
│  │        @ag-ui/client                      │  │
│  │    agent.abortRun() 关闭连接               │  │
│  └─────────────┬─────────────────────────────┘  │
│                │ AG-UI Protocol (SSE)           │
└────────────────┼────────────────────────────────┘
                 │ HTTP
                 │ ⚡ 连接断开
                 │
┌────────────────▼────────────────────────────────┐
│                   服务端                         │
│  ┌───────────────────────────────────────────┐  │
│  │      AgUiService (检测断开)                │  │
│  │  ┌─────────────────────────────────────┐  │  │
│  │  │  AgUiAgent.cancel_run()             │  │  │
│  │  │    ↓                                │  │  │
│  │  │  取消管理器 (cancel.cancel_run)       │  │  │
│  │  │    ↓                                │  │  │
│  │  │  Agent (在检查点处停止)               │  │  │
│  │  └─────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

**服务端配置：**

```python
from trpc_agent_sdk.server.ag_ui import AgUiAgent, AgUiService

# 创建 AG-UI Agent
agui_agent = AgUiAgent(
    trpc_agent=agent,
    app_name="weather_app",
    cancel_wait_timeout=3.0,  # Cancel 等待超时，默认 3.0 秒
)

# 创建服务
agui_service = AgUiService(agents=[agui_agent])

# 启动服务
await agui_service.start(host="0.0.0.0", port=18080)
```

**客户端使用（JavaScript）：**

```javascript
import { AgentClient } from '@anthropic-ai/agent-ui-client';

const agent = new AgentClient({
  url: 'http://localhost:18080',
});

// 开始运行
const runId = await agent.startRun({
  userId: 'user1',
  sessionId: 'session1',
  message: 'What is the weather?',
});

// 订阅事件
agent.onEvent((event) => {
  console.log('Event:', event);
});

// 取消运行（关闭 SSE 连接）
agent.abortRun();
```

**Cancel 触发机制：**
- 客户端调用 `agent.abortRun()` 关闭 SSE 连接
- 服务端检测到连接断开（`asyncio.CancelledError`）
- 自动调用 `cancel_run()` 触发协作式取消
- Agent 在检查点处停止执行
- 保存部分响应和会话状态

**配置说明：**

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `cancel_wait_timeout` | 3.0 | 等待 Cancel 操作完成的超时时间（秒）。如果此值配置不当，Cancel 操作可能无法成功执行，导致流式文本无法保存到会话中。 |

**完整示例：**
- examples/trpc_agui_with_cancel（示例待补充）
