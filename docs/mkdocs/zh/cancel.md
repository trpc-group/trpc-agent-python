# Agent Cancel 机制

Agent 执行到一半时，用户可能发现输出不符合预期，想先打断，补充一些要求（比如哪里不满意、接下来应该怎么做），再让 Agent 继续。

trpc-agent 为此提供了 Cancel 机制。Agent 在几个检查点检查有没有收到取消请求：LLM 正在流式输出时、一个工具调用结束之后等。检查到取消，就停下当前执行，把已经产生的部分内容（流式文本、已完成的工具结果等）写进会话历史。

框架自带的 Agent 都已经接入了这项能力；业务自己实现的 CustomAgent 也可以按同样的方式接入。

| 模块类型 | 模块名称 | Cancel 支持 | Steering 支持 | 说明 |
|---------|---------|-------------|---------------|------|
| Single Agent | `LlmAgent` | ✅ | ✅ | 在 LLM 流式输出、工具执行等位置设置检查点 |
| Single Agent | `LangGraphAgent` | ✅ | ✅ | Steering 由业务自定义节点消费 |
| Single Agent | `ClaudeAgent` | ✅ | ✅ | 在 Claude SDK 流式输出中设置检查点 |
| Single Agent | `TrpcRemoteA2aAgent` | ✅ | — | Steering 由远端 A2A 服务决定 |
| Multi Agent | `ChainAgent` | ✅ | ❌ | 多 Agent 顺序执行，插话目标不唯一 |
| Multi Agent | `ParallelAgent` | ✅ | ❌ | 多个 Agent 并发执行，插话目标不确定 |
| Multi Agent | `CycleAgent` | ✅ | ❌ | 循环中执行 Agent 不唯一 |
| Multi Agent | `TeamAgent` | ✅ | ❌ | Leader 和 Member 都可能执行，无法确定向谁插话 |
| Agent Service | `TrpcA2aAgentService` | ✅ | — | Steering 由承载的 Agent 决定 |
| Agent Service | `AgUiService` | ✅ | — | Steering 由承载的 Agent 决定 |

多 Agent 场景暂不支持 Steering。多 Agent 可能并发执行，用户插话时无法确定应该发给当前执行的 Agent，还是发给入口 Agent；不同目标会得到不同结果，还会引入额外的路由、优先级和并发消费规则。等具体需求明确后再支持。


## Agent Cancel 机制设计介绍

### 架构设计

从使用方式看，接入 Cancel 只需要和 Runner 打交道：用 `run_async()` 运行 Agent，用 `cancel_run_async()` 发起取消。Runner 负责登记本次 run、驱动 Agent 执行，并在取消时做收尾；Agent 只负责在 checkpoint 检查取消信号。

整体架构：

```text
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                           接入层：用户代码 / A2A / AG-UI / 自定义服务                            │
│                                                                                                  │
│  run_async()                     cancel_run_async()                  send_steering()             │
└─────────────────────────────────────────────────┬────────────────────────────────────────────────┘
                                                  │ 调用 Runner
                                                  ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                                                                                  │
│  ┌────────────────────────────────────────┐        ┌──────────────────────────────────────────┐  │
│  │                 Runner                 │        │                Agent 执行                │  │
│  │  register_run(SessionKey)              │驱动 ──►│  checkpoint：                            │  │
│  │  append_event / unregister_run         │        │    - raise_if_cancelled()                │  │
│  │  写 cancel flag / steering queue       │        │    - consume_steering()                  │  │
│  └────────────────────────────────────────┘        └──────────────────────────────────────────┘  │
│                                                                                                  │
└─────────────────────────────────────────────────┬────────────────────────────────────────────────┘
                                                  │ 写 / 读控制状态
                                                  ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                                                                                  │
│  ┌──────────────────────────────────────┐          ┌──────────────────────────────────────────┐  │
│  │ 单 worker：_LocalManager             │          │ 多 worker：Unix Socket 控制面            │  │
│  │ runs: dict[SessionKey, _LocalRun]    │          │ master：_UnixSocketServer                │  │
│  │  ├─ cancel_event: asyncio.Event      │          │  ├─ cancel flag                          │  │
│  │  └─ steering: deque[Content]         │          │  └─ steering queue                       │  │
│  └──────────────────────────────────────┘          │ worker：每个 run 一条 UDS 连接           │  │
│                                                    └──────────────────────────────────────────┘  │
│                                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

Cancel 和 Steering 都通过 Runner 接入。调用 `run_async()` 后，Runner 会登记本次执行，用 `(app_name, user_id, session_id)` 标识这个 run。

**Cancel 机制**

调用 `cancel_run_async()` 后，Runner 设置该次 run 的取消信号。单 worker 下，取消信号在进程内存里；多 worker 部署时，master 在 fork 之前调用 `init_unix_socket()`，各 worker 通过 Unix Socket 访问 master 进程的里在运行中 Agent 的状态。Agent 在 checkpoint 读到取消信号后抛出 `RunCancelledException`；Runner 接住后，把已经输出的文本、已经完成的工具结果写进 session，给没执行的工具补一条 skipped 结果，最后返回 `AgentCancelledEvent`。

**Steering 机制**

调用 `send_steering()` 后，消息进入该次 Agent Run 的 steering 队列。Agent 在下一轮 LLM 请求前取走队列里的所有消息，把消息作为一条不可见的 user 事件写进 session，模型下一轮就能看到。最后一次 LLM 请求期间才到达的消息，仍然有效。

**接入服务端机制**

- **A2A**：客户端调用 `cancel_task` 取消，框架会转成 `runner.cancel_run_async()`；需要 steering 时，因不属于A2A协议的范畴，需要用户自行注册的 `POST /steering`。多 worker 部署时，收到请求的 worker 通过 Unix Socket 把 cancel 和 steering 发给 master，运行该 run 的 worker 在 checkpoint 读到。
- **AG-UI**：客户端断开 SSE 连接，服务端检测到断连后调用 `runner.cancel_run_async()`；steering 走 `AgUiService` 提供的 `POST /steering`。多 worker 部署时机制同A2A。
- **业务自定义 Server**：推荐在连接断开时调用 `runner.cancel_run_async()`；需要 steering 时直接调用 `runner.send_steering()`。多 worker 部署机制同A2A。

### 会话管理

**LLM 流式输出时取消**

已经输出的部分文本会保留，后面再补一句取消提示，让模型知道这一轮被打断了。下一轮用户可以指出哪里不满意，让 Agent 改正。

**工具执行时取消**

假设模型一次要调工具 1 和工具 2，工具 1 执行期间用户取消：

- 工具 1 会跑完，结果保留；
- 工具 2 不再执行，但会话里会保留它的调用记录，并补一条 `status=cancelled` 的 skipped 结果。这样 tool_call 和 tool_response 仍然配对，下一轮请求不会因为缺结果而非法；
- 最后同样补一句取消提示。

下一轮模型能看到工具 2 被跳过，可以重新调用它。

## 简单用法

### 基本示例

```python
import asyncio
import uuid

from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, Part
from trpc_agent_sdk.events import AgentCancelledEvent

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
    runner2 = Runner(
        app_name="my_app",
        agent=my_agent,
        session_service=InMemorySessionService(),
    )
    success = await runner2.cancel_run_async(
        user_id=user_id,
        session_id=session_id,
    )
    print(f"\n取消请求结果: {success}")

    await task
    await runner.close()
    await runner2.close()

asyncio.run(main())
```

`cancel_run_async()` 返回 `bool`：`True` 表示取消信号已发出，`False` 表示没有找到活跃 run 或发送失败。失败原因会由 Runner 打进框架日志；需要拿到具体原因时，可以直接调用 `cancel.cancel_run()`，它返回 `Status`。

### 多 worker 取消

单 worker 不需要额外配置，Runner 直接用进程内的取消信号。

多 worker 部署时，master 在 fork 之前调用 `init_unix_socket()`，worker 退出后 master 调用 `release_unix_socket()` 并退出。

每次调用 runner.run_async 时，框架将会启动一个定时协程，按 `poll_interval` 通过 unix-socket 同步 Agent 的控制信息（是否取消、是否有Steering），在 runner.run_async 结束时，将会清理这个协程相关的资源。

```python
from trpc_agent_sdk.cancel import UnixSocketConfig
from trpc_agent_sdk.cancel import init_unix_socket
from trpc_agent_sdk.cancel import release_unix_socket

init_unix_socket(UnixSocketConfig(poll_interval=0.1, timeout=3.0))
try:
    server.serve()
finally:
    release_unix_socket()
```

| 配置项 | 默认值 | 说明 |
|-------|--------|------|
| `socket_path` | `/tmp/trpc-agent-py/cancel.sock` | master server 的 UDS 路径 |
| `poll_interval` | 0.1 | 每次 Agent Run 向 master 同步状态的时间间隔（秒） |
| `timeout` | 3.0 | unix-socket 读写操作的超时时间（秒） |


### 自定义 Cancel Manager

单进程时，默认使用进程内的 `_LocalManager`，多进程时，框架提供了 `_UnixSocketManager` 来将多进程接入 Cancel 机制。框架提供的默认实现，在大部分场景中已经够用，但如果业务需要接入 Redis、NATS 等外部系统，实现分布式的Cancel，需要业务自行实现 `CancelManager` 后调用 `set_cancel_manager()`，注意，实现时，需要根据自己的系统，解决好分布式问题（时序、消息同步异常等问题）并充分验证：

```python
import asyncio
from typing import List
from typing import Optional

from trpc_agent_sdk.cancel import CancelManager
from trpc_agent_sdk.cancel import SessionKey
from trpc_agent_sdk.cancel import Status
from trpc_agent_sdk.cancel import set_cancel_manager
from trpc_agent_sdk.types import Content


class MyCancelManager(CancelManager):

    async def register_run(self, key: SessionKey) -> None:
        # 登记一个 run；同一 key 已经存在时抛 RuntimeError。
        ...

    async def unregister_run(self, key: SessionKey) -> None:
        # 释放 run；必须幂等，并清空该 run 待消费的 steering。
        ...

    async def cancel_run(self, key: SessionKey, timeout: float = 1.0) -> Status:
        # 请求取消并等待 Agent 执行完毕。timeout 由 Runner.cancel_run_async 传入，
        # 其他根据情况，返回Status(ok=False, error_msg="xxx")
        # 只有Agent运行完毕才返回 Status(ok=True)。
        ...

    async def send_steering(self, key: SessionKey, content: Content) -> Status:
        # 成功返回 Status(ok=True)；空内容返回
        # Status(ok=False, error_msg="steering content is empty")，
        # 没有活跃 run 返回 Status(ok=False, error_msg="no active run")。
        ...

    async def drain_steering(self, key: SessionKey) -> List[Content]:
        # 取空并清空该 run 的 steering，按 FIFO 返回 list[Content]；
        # 没有消息返回空列表。
        ...

    async def check_cancel(self, key: SessionKey) -> bool:
        # 已取消返回 True；没有活跃 run 或未取消返回 False，不抛异常。
        ...

    async def get_cancel_event(self, key: SessionKey) -> Optional[asyncio.Event]:
        # 返回本进程该 run 的 asyncio.Event；没有 run 返回 None，
        # 取消到达时需要 set 这个 event。
        ...


# 在进程启动时，设置 cancel_manager
set_cancel_manager(MyCancelManager())
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

app = FastAPI()

# 创建 Agent 和 Session Service
agent = LlmAgent(name="my_agent", model=model, instruction="你是一个智能助手")
session_service = InMemorySessionService()


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
            # 无论正常结束还是连接断开，都写入取消信号
            # run 在下一个 checkpoint 停止，并保存部分结果
            await runner.cancel_run_async(user_id, session_id)

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

from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.sessions import InMemorySessionService

app = FastAPI()

agent = LlmAgent(name="my_agent", model=model, instruction="你是一个智能助手")
session_service = InMemorySessionService()

runner = Runner(
    app_name="my_app",
    agent=agent,
    session_service=session_service,
)

@app.post("/sessions/{user_id}/{session_id}/cancel")
async def cancel_session_run(user_id: str, session_id: str):
    """取消指定会话的运行"""
    success = await runner.cancel_run_async(
        user_id=user_id,
        session_id=session_id,
    )
    if success:
        return {"status": "cancellation_requested"}
    else:
        raise HTTPException(
            status_code=404,
            detail="未找到该会话的活跃运行"
        )
```

**注意**：`cancel_run_async` 先在本地进程查找活跃 run。多 worker 部署时，master 在 fork 前调用 `init_unix_socket()`，取消请求通过 Unix Socket 到达 master，不再需要 hash 路由。

## Steering

Steering 用来在 run 执行过程中补充用户输入。`send_steering()` 把消息放进当前 run 的队列；`LlmAgent` 在下一次 LLM 请求前取走队列，把它作为一条不可见的 user 消息写进 session。模型在本轮就能看到新的指示，客户端的事件流里看不到它。

```python
from trpc_agent_sdk.types import Content, Part

status = await runner.send_steering(
    user_id=user_id,
    session_id=session_id,
    content=Content(role="user", parts=[Part(text="顺便查一下北京")]),
)
if status.ok:
    print("steering queued")
else:
    print(f"steering rejected: {status.error_msg}")
```

| 结果 | 含义 |
|------|------|
| `status.ok == True` | 消息已进入活跃 run 的队列 |
| `status.ok == False` | 未入队，`status.error_msg` 给出原因 |
| `error_msg="no active run"` | 该 session 当前没有活跃 run |
| `error_msg="steering content is empty"` | 消息内容为空（没有 parts） |

自定义 manager 可以返回自己的 `error_msg`。

消息必须包含至少一个 part。多 worker 部署时，任意 worker 都可以通过 Unix Socket 发送；运行该 run 的 worker 会在下一个 checkpoint 取走。

## Agent Cancel 指引

### LlmAgent

LlmAgent在执行流程的关键位置设置了 Cancel 和 Steering 的检查点

**使用示例：**

```python
import asyncio

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, Part

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

# 运行并支持Cancel
async def run_with_cancel():
    task = asyncio.create_task(run_agent())
    await asyncio.sleep(1)
    await runner.cancel_run_async(user_id, session_id)
    await task

# 运行并支持 Steering
async def run_with_steering():
    task = asyncio.create_task(run_agent())
    await asyncio.sleep(1)
    status = await runner.send_steering(
        user_id=user_id,
        session_id=session_id,
        content=Content(role="user", parts=[Part(text="顺便查一下北京")]),
    )
    if status.ok:
        print("steering queued")
    else:
        print(f"steering rejected: {status.error_msg}")
    await task
```

**完整示例：**
- Cancel：[examples/llmagent_with_cancel](../../../examples/llmagent_with_cancel/README.md)
- Steering：[examples/llmagent_with_steering](../../../examples/llmagent_with_steering/README.md)

### LangGraphAgent

LangGraphAgent 将 LangGraph 封装为 trpc-agent 兼容的 Agent，支持 Cancel。Steering 需要业务在自己的图节点中消费，框架不会自动往图里注入消息。

**Cancel 使用示例：**

```python
import asyncio

from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.agents import LangGraphAgent
from langgraph.graph import StateGraph

# 构建 LangGraph
def build_graph():
    builder = StateGraph(State)
    builder.add_node("process", process_node)  # 用户自定义处理节点
    builder.add_node("respond", respond_node)  # 用户自定义响应节点
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

# 运行并支持 Cancel
async def run_with_cancel():
    task = asyncio.create_task(run_agent())
    await asyncio.sleep(1)
    await runner.cancel_run_async(user_id, session_id)
    await task
```

**Steering 使用示例：**

```python
from langchain_core.messages import HumanMessage
from langchain_core.runnables.config import RunnableConfig
from langgraph.graph import END
from langgraph.prebuilt import tools_condition
from langgraph.types import Command

from trpc_agent_sdk.agents import get_agent_context
from trpc_agent_sdk.cancel import mark_steering_content


async def _take_steering(config: RunnableConfig):
    ctx = get_agent_context(config)
    content = await ctx.consume_steering()
    if content is None:
        return None
    marked = mark_steering_content(content)
    text = "".join(part.text or "" for part in marked.parts)
    return HumanMessage(content=text)


async def steering_node(state, config: RunnableConfig):
    """工具执行后，检查下有无 steering 消息。"""
    message = await _take_steering(config)
    if message is None:
        return {}
    return {"messages": [message]}


async def tail_steering_node(state, config: RunnableConfig) -> Command:
    """Agent结束前，检查下有无 steering 消息。"""
    message = await _take_steering(config)
    if message is None:
        return Command(goto=END)
    return Command(update={"messages": [message]}, goto="chatbot")


def route_after_chatbot(state) -> str:
    return "tools" if tools_condition(state) == "tools" else "tail_steering"
```

构造图：

```python
builder.add_node("steering", steering_node)
builder.add_node("tail_steering", tail_steering_node, destinations=("chatbot", END))
builder.add_conditional_edges(
    "chatbot", route_after_chatbot,
    {"tools": "tools", "tail_steering": "tail_steering"},
)
builder.add_edge("tools", "steering")
builder.add_edge("steering", "chatbot")
```

**完整示例：**
- Cancel：[examples/langgraph_agent_with_cancel](../../../examples/langgraph_agent_with_cancel/README.md)
- Steering：[examples/langgraph_agent_with_steering](../../../examples/langgraph_agent_with_steering/README.md)

### ClaudeAgent

ClaudeAgent 基于 Claude SDK 运行，支持 Cancel 和 Steering，其中内部实现上，当检测到 Steering 消息到达时，会直接发送到 CLI。

**使用示例：**

```python
import asyncio

from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.server.agents.claude import ClaudeAgent, setup_claude_env
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import Content, Part

# 定义工具
async def get_weather(city: str) -> dict:
    """获取城市天气"""
    await asyncio.sleep(3)  # 模拟耗时操作
    return {"city": city, "temperature": "25°C", "condition": "晴"}

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
    tools=[FunctionTool(get_weather)],
)
agent.initialize()

runner = Runner(
    app_name="claude_app",
    agent=agent,
    session_service=InMemorySessionService(),
)

# 运行并支持 Cancel
async def run_with_cancel():
    task = asyncio.create_task(run_agent())
    await asyncio.sleep(1)
    await runner.cancel_run_async(user_id, session_id)
    await task

# 运行并支持 Steering
async def run_with_steering():
    task = asyncio.create_task(run_agent())
    await asyncio.sleep(1)
    status = await runner.send_steering(
        user_id=user_id,
        session_id=session_id,
        content=Content(role="user", parts=[Part(text="顺便查一下北京")]),
    )
    if status.ok:
        print("steering queued")
    else:
        print(f"steering rejected: {status.error_msg}")
    await task
```

**完整示例：**
- Cancel：[examples/claude_agent_with_cancel](../../../examples/claude_agent_with_cancel/README.md)
- Steering：[examples/claude_agent_with_steering](../../../examples/claude_agent_with_steering/README.md)

### TeamAgent

TeamAgent 在 Leader 规划和 Member 执行期间均支持 Cancel。

**Cancel 场景：**
1. **Leader 规划期间取消**：保存 Leader 的部分响应
2. **Member 执行期间取消**：保存 Member 的部分响应到团队记忆

**使用示例：**

```python
import asyncio

from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.teams import TeamAgent
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.models import OpenAIModel

model = OpenAIModel(model_name="deepseek-chat")

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
# 服务端等待 owner run 到达 checkpoint 的最大时间（秒）
CANCEL_WAIT_TIMEOUT = 3.0


def create_a2a_service() -> TrpcA2aAgentService:
    """创建带有 Cancel 支持的 A2A 服务"""
    from agent.agent import root_agent

    # cancel_wait_timeout 限定等待执行任务感知取消信号并完成 checkpoint 清理的时间，
    # 不会强杀 run
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
    success = await runner2.cancel_run_async(user_id=user_id, session_id=session_id)
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
    success = await runner2.cancel_run_async(user_id=user_id, session_id=session_id)
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
| 服务端 | `cancel_wait_timeout` | 1.0 | 写入取消信号后，服务端等待 owner run 到达 checkpoint 的最大时间；不会强杀 run。 |

多 worker 部署时，在 master 调用 `init_unix_socket()`，任意 worker 收到的 `CancelTask` 都能到达 owner。需要投递 steering 时，调用 `Runner.send_steering`（或 A2A 集成提供的 `POST /steering` 接口）。

**完整示例：**
- [examples/a2a_with_cancel](../../../examples/a2a_with_cancel/README.md)（a2a-sdk 0.3）
- [examples/a2a_v1_with_cancel](../../../examples/a2a_v1_with_cancel/README.md)（a2a-sdk 1.x）

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
│  │  │  Runner.cancel_run_async()          │  │  │
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
        # cancel_wait_timeout 限定等待 run 到达下一个 checkpoint 并落库部分结果的时间，
        # 不会强杀 run
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
| `cancel_wait_timeout` | 3.0 | 写入取消请求后，等待 run 到达下一个 checkpoint 的最大时间（秒）。不会强杀 run；配置过小可能在流式文本落库前返回。 |

跨 worker 取消时，在 master 启动 worker 前调用 `init_unix_socket()`。之后 `AgUiAgent.cancel_run()` 通过 Unix Socket 发到 master，从任意 worker 都可以取消。

**完整示例：**
- [examples/agui_with_cancel](../../../examples/agui_with_cancel/README.md)

## Agent 服务 Steering 指引

Steering 不在 A2A / AG-UI 协议里，框架没有现成的客户端指令。业务要在自己的服务里支持一个 steering 接口（例如 `POST /steering`），收到请求后调用同一个 `Runner.send_steering()`：

```python
from trpc_agent_sdk.types import Content, Part

status = await runner.send_steering(
    user_id=user_id,
    session_id=session_id,
    content=Content(role="user", parts=[Part(text=text)]),
)
if not status.ok:
    return {"error": status.error_msg}
```
