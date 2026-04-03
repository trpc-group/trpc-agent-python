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

这种模式已在 AG-UI 服务中实现，可参考 [trpc_agent_sdk/server/ag_ui/_plugin/_utils.py](../../../trpc_agent_sdk/server/ag_ui/_plugin/_utils.py)

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

run_server.py:

```python
import uvicorn
from dotenv import load_dotenv

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore

from trpc_agent_sdk.server.a2a import TrpcA2aAgentExecutorConfig
from trpc_agent_sdk.server.a2a import TrpcA2aAgentService

load_dotenv()

HOST = "127.0.0.1"
PORT = 18082
# 等待 Agent 取消完成的超时时间（秒），建议与客户端 timeout 保持一致
CANCEL_WAIT_TIMEOUT = 3.0


def create_a2a_service() -> TrpcA2aAgentService:
    """创建带有 Cancel 支持的 A2A 服务"""
    from agent.agent import root_agent

    # 关键配置：cancel_wait_timeout 控制服务端收到 cancel_task 后，
    # 等待后端 Agent 完成取消操作的最大时间
    executor_config = TrpcA2aAgentExecutorConfig(
        cancel_wait_timeout=CANCEL_WAIT_TIMEOUT,
    )

    a2a_svc = TrpcA2aAgentService(
        service_name="weather_agent_cancel_service",
        agent=root_agent,
        executor_config=executor_config,
    )
    a2a_svc.initialize()

    return a2a_svc


def serve():
    """启动 A2A 服务"""
    a2a_svc = create_a2a_service()

    # 使用 a2a-sdk 标准组件组装服务
    request_handler = DefaultRequestHandler(
        agent_executor=a2a_svc,
        task_store=InMemoryTaskStore(),
    )

    server = A2AStarletteApplication(
        agent_card=a2a_svc.agent_card,
        http_handler=request_handler,
    )

    uvicorn.run(server.build(), host=HOST, port=PORT)


if __name__ == "__main__":
    serve()
```

**客户端使用：**

test_a2a_cancel.py:

```python
import asyncio
import uuid
from typing import Awaitable
from typing import Callable
from typing import Optional

from dotenv import load_dotenv
from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.events import AgentCancelledEvent
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.server.a2a import TrpcRemoteA2aAgent
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()

# A2A 服务端地址，需与 run_server.py 中配置一致
AGENT_BASE_URL = "http://127.0.0.1:18082"
# 客户端等待取消完成的超时时间（秒），建议与服务端 cancel_wait_timeout 一致
CANCEL_TIMEOUT = 3.0


async def run_remote_agent(
    runner: Runner,
    user_id: str,
    session_id: str,
    query: str,
    tool_call_callback: Optional[Callable[[], Awaitable[None]]] = None,
    event_count_callback: Optional[Callable[[int], Awaitable[None]]] = None,
) -> None:
    """运行远程 Agent 并处理事件流"""
    user_content = Content(parts=[Part.from_text(text=query)])

    run_config = RunConfig(agent_run_config={
        "metadata": {
            "user_id": user_id,
        },
    })

    print("🤖 Remote Agent: ", end="", flush=True)
    event_count = 0
    try:
        async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=user_content,
                run_config=run_config,
        ):
            event_count += 1
            if event_count_callback:
                await event_count_callback(event_count)

            # 收到取消事件，说明 Agent 已成功被取消
            if isinstance(event, AgentCancelledEvent):
                print(f"\n❌ Run was cancelled: {event.error_message}")
                break

            if not event.content or not event.content.parts:
                continue

            # 处理流式输出（partial=True 表示流式 chunk）
            if event.partial:
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
                continue

            # 处理完整事件（工具调用、工具结果等）
            for part in event.content.parts:
                if part.thought:
                    continue
                if part.function_call:
                    print(f"\n🔧 [Invoke Tool: {part.function_call.name}({part.function_call.args})]")
                    # 检测到工具调用时触发回调，用于在工具执行期间发起取消
                    if tool_call_callback:
                        await tool_call_callback()
                elif part.function_response:
                    print(f"📊 [Tool Result: {part.function_response.response}]")

    except Exception as e:
        print(f"\n⚠️ Error: {e}")

    print()


def create_runner(
    app_name: str,
    session_service: InMemorySessionService,
    remote_agent: TrpcRemoteA2aAgent,
) -> Runner:
    """创建 Runner 实例，绑定远程 A2A Agent"""
    return Runner(app_name=app_name, agent=remote_agent, session_service=session_service)


# ============================================================
# 场景 1：LLM 流式输出阶段取消
# 收到 10 个流式事件后，通过 cancel_run_async 向远程服务发送取消请求
# ============================================================
async def scenario_1_cancel_during_streaming(remote_agent: TrpcRemoteA2aAgent) -> None:
    print("📋 Scenario 1: Cancel During LLM Streaming (Remote A2A)")
    print("-" * 80)

    app_name = "a2a_cancel_demo"
    user_id = "demo_user"
    session_id = str(uuid.uuid4())
    session_service = InMemorySessionService()

    query1 = "Introduce yourself in detail, what can you do as a weather assistant."
    print(f"🆔 Session ID: {session_id[:8]}...")
    print(f"📝 User Query 1: {query1}")
    print()

    event_threshold_reached = asyncio.Event()

    async def on_event_count(count: int) -> None:
        # 收到第 10 个事件时，触发取消信号
        if count == 10:
            print(f"\n⏳ [Received {count} events, triggering cancellation...]")
            event_threshold_reached.set()

    # 用于运行 Agent 的 Runner
    runner1 = create_runner(app_name, session_service, remote_agent)

    async def run_query1() -> None:
        await run_remote_agent(runner1, user_id, session_id, query1, event_count_callback=on_event_count)

    # 在后台 task 中运行 Agent
    task = asyncio.create_task(run_query1())

    print("⏳ Waiting for first 10 events...")
    await event_threshold_reached.wait()

    # 用另一个 Runner 发起取消请求（模拟独立的取消调用方）
    runner2 = create_runner(app_name, session_service, remote_agent)
    print("\n⏸️  Requesting cancellation after 10 events...")
    # cancel_run_async 会向远程 A2A 服务发送 cancel_task 请求
    success = await runner2.cancel_run_async(user_id=user_id, session_id=session_id, timeout=CANCEL_TIMEOUT)
    print(f"✓ Cancellation requested: {success}")

    await task

    print()
    print("💡 Result: The partial response was saved to session with cancellation message")
    print()

    # 取消后在同一 session 继续对话，验证会话上下文保持
    query2 = "what happens?"
    print(f"📝 User Query 2: {query2}")
    print()

    runner3 = create_runner(app_name, session_service, remote_agent)
    await run_remote_agent(runner3, user_id, session_id, query2)

    print("💡 Result: Agent can still respond with session context maintained")
    print("-" * 80)
    print()


# ============================================================
# 场景 2：工具执行阶段取消
# 检测到 function_call 事件后发起取消，此时工具仍在服务端执行中
# ============================================================
async def scenario_2_cancel_during_tool_execution(remote_agent: TrpcRemoteA2aAgent) -> None:
    print("📋 Scenario 2: Cancel During Tool Execution (Remote A2A)")
    print("-" * 80)

    app_name = "a2a_cancel_demo"
    user_id = "demo_user"
    session_id = str(uuid.uuid4())
    session_service = InMemorySessionService()

    query1 = "What's the current weather in Shanghai and Beijing?"
    print(f"🆔 Session ID: {session_id[:8]}...")
    print(f"📝 User Query 1: {query1}")
    print()

    tool_call_detected = asyncio.Event()

    async def on_tool_call() -> None:
        # 检测到工具调用时设置信号，触发取消
        print("⏳ [Tool call detected...]")
        tool_call_detected.set()

    runner1 = create_runner(app_name, session_service, remote_agent)

    async def run_query1() -> None:
        await run_remote_agent(runner1, user_id, session_id, query1, tool_call_callback=on_tool_call)

    task = asyncio.create_task(run_query1())

    print("⏳ Waiting for tool call to be detected...")
    await tool_call_detected.wait()

    # 工具执行中发起取消，已完成的工具结果会保留，未完成的调用会被清理
    runner2 = create_runner(app_name, session_service, remote_agent)
    print("\n⏸️  Tool call detected! Requesting cancellation during tool execution...")
    success = await runner2.cancel_run_async(user_id=user_id, session_id=session_id, timeout=CANCEL_TIMEOUT)
    print(f"✓ Cancellation requested: {success}")

    await task

    print()
    print("💡 Result: Incomplete function calls were cleaned up from session")
    print()

    # 取消后继续对话，验证会话可恢复
    query2 = "what happens?"
    print(f"📝 User Query 2: {query2}")
    print()

    runner3 = create_runner(app_name, session_service, remote_agent)
    await run_remote_agent(runner3, user_id, session_id, query2)

    print("💡 Result: Agent can still respond with session context maintained")
    print("-" * 80)
    print()


async def main():
    # 创建远程 A2A Agent，连接到 run_server.py 启动的服务
    remote_agent = TrpcRemoteA2aAgent(
        name="weather_agent",
        agent_base_url=AGENT_BASE_URL,
        description="Professional weather query assistant with cancel support",
    )
    await remote_agent.initialize()

    # 依次运行两个取消场景
    await scenario_1_cancel_during_streaming(remote_agent)

    await scenario_2_cancel_during_tool_execution(remote_agent)


if __name__ == "__main__":
    asyncio.run(main())
```

**配置说明：**

| 配置位置 | 参数 | 默认值 | 说明 |
|----------|------|--------|------|
| 服务端 | `cancel_wait_timeout` | 1.0 | 服务端等待后端 Agent 取消完成的超时时间 |
| 客户端 | `timeout` | 1.0 | 客户端等待本端 RemoteA2aAgent 取消完成的超时时间 |

建议两者配置相同的超时时间。

**完整示例：**
- [examples/a2a_with_cancel](../../../examples/a2a_with_cancel/README.md)

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

run_server.py:

```python
from dotenv import load_dotenv

from trpc_agent_sdk.sessions import InMemorySessionService

from _agui_runner import create_agui_runner

load_dotenv()

HOST = "127.0.0.1"
PORT = 18080

app_name = "agui_cancel_demo"


def serve():
    """启动 AG-UI 服务，注册 Agent 并绑定路由"""
    service_name = "weather_agent_cancel_service"
    uri = "/weather_agent"  # AG-UI 端点路径，客户端通过此路径连接
    from agent.agent import root_agent
    session_service = InMemorySessionService()
    agui_runner = create_agui_runner(app_name,
                                     service_name,
                                     uri,
                                     root_agent=root_agent,
                                     session_service=session_service)
    agui_runner.run(HOST, PORT)


if __name__ == "__main__":
    serve()
```

_agui_runner.py:

```python
from contextlib import asynccontextmanager
from typing import Any

from ag_ui.core import RunAgentInput
from fastapi import FastAPI
from pydantic import BaseModel

from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.server.ag_ui import AgUiAgent
from trpc_agent_sdk.server.ag_ui import AgUiManager
from trpc_agent_sdk.server.ag_ui import AgUiService


class HealthResponse(BaseModel):
    status: str = "ok"
    app_name: str
    version: str = "1.0.0"


class AguiRunner:
    """AG-UI Runner：管理 AgUiManager、FastAPI 应用和服务注册"""

    def __init__(
        self,
        app_name: str,
    ) -> None:
        self._app_name = app_name
        self._agui_manager = AgUiManager()
        self._app = self._create_app()

    @property
    def app(self) -> FastAPI:
        return self._app

    def register_service(self, service_name: str, service: AgUiService) -> None:
        self._agui_manager.register_service(service_name, service)

    def run(self, host: str, port: int, **kwargs: Any) -> None:
        self._app.get("/health", response_model=HealthResponse, tags=["meta"])(self.health)
        self._agui_manager.set_app(self._app)
        self._agui_manager.run(host, port, **kwargs)

    @asynccontextmanager
    async def _lifespan(self, app: FastAPI):
        logger.info("TRPC AG-UI Server (with cancel) starting up.")
        yield
        logger.info("TRPC AG-UI Server (with cancel) shutting down.")
        await self._agui_manager.close()

    def _create_app(self) -> FastAPI:
        app = FastAPI(
            title="TRPC AG-UI Server (Cancel Demo)",
            description="HTTP API for TRPC AG-UI Server with Cancel support",
            version="1.0.0",
            lifespan=self._lifespan,
        )
        return app

    async def health(self) -> HealthResponse:
        return HealthResponse(app_name=self._app_name)


def _create_agui_agent(name: str, root_agent: BaseAgent, **kwargs) -> AgUiAgent:
    """创建 AgUiAgent，配置 cancel_wait_timeout"""
    agui_agent = AgUiAgent(
        trpc_agent=root_agent,
        app_name=name,
        # 关键配置：SSE 连接断开后，等待 Agent 完成取消的超时时间
        # 如果配置过短，Cancel 可能未完成，流式文本无法保存到会话
        cancel_wait_timeout=3.0,
        **kwargs,
    )
    return agui_agent


def create_agui_runner(app_name: str, service_name: str, uri: str, **kwargs: Any) -> AguiRunner:
    """组装 AG-UI 服务：创建 Runner → 创建 Service → 注册 Agent 路由"""
    ag_ui_runner: AguiRunner = AguiRunner(app_name)
    agui_service = AgUiService(service_name, app=ag_ui_runner.app)
    agui_agent = _create_agui_agent(app_name, **kwargs)
    # 将 Agent 注册到指定的 URI 路径，客户端通过该路径连接
    agui_service.add_agent(uri, agui_agent)
    ag_ui_runner.register_service(service_name, agui_service)
    return ag_ui_runner
```

**客户端使用（JavaScript）：**

client_js/main.js:

```javascript
import { HttpAgent } from '@ag-ui/client';

// 连接 AG-UI 服务端，路径需与 run_server.py 中注册的 uri 一致
const agent = new HttpAgent({
  url: 'http://127.0.0.1:18080/weather_agent',
  debug: false
});

let chunkCount = 0;
const ABORT_AFTER_CHUNKS = 5;  // 收到 5 个文本 chunk 后触发取消

// 订阅 AG-UI 事件流
const subscription = agent.subscribe({
  onTextMessageStartEvent: ({ event }) => {
    process.stdout.write('\n🤖 Assistant: ');
  },
  onTextMessageContentEvent: ({ event }) => {
    process.stdout.write(event.delta ?? '');
    chunkCount++;
    // 达到阈值后调用 abortRun() 关闭 SSE 连接，触发服务端 Cancel
    if (chunkCount === ABORT_AFTER_CHUNKS) {
      process.stdout.write('\n\n⏸️  Aborting run after receiving ' + ABORT_AFTER_CHUNKS + ' text chunks...\n');
      agent.abortRun();
    }
  },
  onTextMessageEndEvent: ({ event }) => {
    process.stdout.write('\n');
  },
  onToolCallStartEvent: ({ event }) => {
    process.stdout.write(`\n🔧 Call Tool ${event.toolCallName}: `);
  },
  onToolCallArgsEvent: ({ event }) => {
    process.stdout.write(event.delta ?? '');
  },
  onToolCallResultEvent: ({ event }) => {
    process.stdout.write(`\n✅ Tool result: ${event.content}`);
  },
  onRunStartedEvent: ({ event }) => {
    process.stdout.write(`\n⚙️  Run started: ${event.runId}`);
  },
  onRunFinishedEvent: ({ result }) => {
    if (result !== undefined) {
      process.stdout.write(`⚙️  Run finished, result: ${result}\n`);
    } else {
      process.stdout.write('⚙️  Run finished\n');
    }
  },
  onRunFailedEvent: ({ error }) => {
    process.stdout.write(`❌ Run failed: ${error}\n`);
  }
});

// 发送用户消息并启动 Agent
await agent.addMessage({
  role: 'user',
  content: 'Please introduce yourself in detail and tell me what you can do.',
  id: 'user_123'
});

await agent.runAgent();

subscription.unsubscribe?.();
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
- [examples/agui_with_cancel](../../../examples/agui_with_cancel/README.md)
