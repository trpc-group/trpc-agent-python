# A2A 使用文档

trpc-agent-python SDK 内置了 Agent-to-Agent (A2A) 协议支持，让你可以将本地 Agent 发布为标准 A2A 服务，也可以作为客户端远程调用其他 A2A Agent。

## 🚀 核心优势

- **部署简单**：几行代码即可将 Agent 发布为 A2A HTTP 服务
- **流式支持**：开箱即用的 artifact-first 流式传输
- **取消支持**：客户端可随时取消正在执行的远程任务
- **会话保持**：多轮对话自动维护上下文

---

## 安装

```bash
pip install -e ".[a2a]"
```

需要使用 Python 3.12。

---

## 服务端部署

### 1. 定义 Agent

首先定义一个标准的 `LlmAgent`：

```python
# agent/agent.py
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool


def get_weather_report(city: str) -> dict:
    """获取指定城市的天气信息"""
    weather_data = {
        "Beijing": {"city": "Beijing", "temperature": "25C", "condition": "Sunny", "humidity": "60%"},
        "Shanghai": {"city": "Shanghai", "temperature": "28C", "condition": "Cloudy", "humidity": "70%"},
    }
    return weather_data.get(city, {"city": city, "temperature": "Unknown", "condition": "Data not available"})


# 创建一个天气查询 Agent，配置模型、提示词和工具
root_agent = LlmAgent(
    name="weather_agent",
    description="A professional weather query assistant.",
    model=OpenAIModel(model_name="your-model", api_key="your-key", base_url="your-url"),
    instruction="You are a professional weather query assistant.",
    tools=[FunctionTool(get_weather_report)],  # 将普通函数包装为 Agent 可调用的工具
)
```

### 2. 创建 A2A 服务并启动

使用 `TrpcA2aAgentService` 将 Agent 包装为 A2A 服务，然后通过 A2A SDK 的 `A2AStarletteApplication` 以标准 HTTP 方式运行：

```python
# run_server.py
import uvicorn

# A2A SDK 提供的 HTTP 服务框架组件
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore

# SDK 提供的 A2A 服务封装
from trpc_agent_sdk.server.a2a import TrpcA2aAgentService
from trpc_agent_sdk.server.a2a import TrpcA2aAgentExecutorConfig

HOST = "127.0.0.1"
PORT = 18081


def create_a2a_service() -> TrpcA2aAgentService:
    from agent.agent import root_agent

    # 执行器配置（可选），可在此配置 user_id_extractor、event_callback 等
    executor_config = TrpcA2aAgentExecutorConfig()

    # 将 Agent 包装为 A2A 服务，实现了 A2A SDK 的 AgentExecutor 接口
    a2a_svc = TrpcA2aAgentService(
        service_name="weather_agent_service",  # 服务名称，用于标识服务
        agent=root_agent,                      # 要部署的 Agent
        executor_config=executor_config,
    )
    a2a_svc.initialize()  # 必须调用，完成 Agent Card 构建等初始化
    return a2a_svc


def serve():
    a2a_svc = create_a2a_service()

    # 使用 A2A SDK 的 DefaultRequestHandler 处理 A2A 协议请求
    request_handler = DefaultRequestHandler(
        agent_executor=a2a_svc,        # 传入我们的 A2A 服务作为执行器
        task_store=InMemoryTaskStore(), # 任务存储，生产环境可替换为持久化实现
    )

    # 构建 Starlette HTTP 应用，自动注册 Agent Card 和 A2A 协议端点
    server = A2AStarletteApplication(
        agent_card=a2a_svc.agent_card,  # Agent Card 会发布到 /.well-known/agent.json
        http_handler=request_handler,
    )

    print(f"Starting A2A server on http://{HOST}:{PORT}")
    print(f"Agent card: http://{HOST}:{PORT}/.well-known/agent.json")

    uvicorn.run(server.build(), host=HOST, port=PORT)


if __name__ == "__main__":
    serve()
```

启动后，服务会自动发布 Agent Card 到 `/.well-known/agent.json`，客户端可通过该地址发现并调用 Agent。

### 3. 服务端关键要点

| 要点 | 说明 |
|------|------|
| `TrpcA2aAgentService` | 实现了 A2A SDK 的 `AgentExecutor` 接口，可直接作为 `DefaultRequestHandler` 的执行器 |
| `agent_card` | 自动根据 Agent 的 name、description、tools 等信息构建，也可手动传入 |
| `initialize()` | 必须在使用前调用，完成 Agent Card 构建和内部初始化 |
| `session_service` | 可选，默认使用 `InMemorySessionService`；可替换为持久化实现 |
| `executor_config` | 可选，用于配置 `user_id_extractor`、`event_callback`、`cancel_wait_timeout` 等行为 |

---

## 客户端调用

### 1. 创建远程 Agent 并发起调用

使用 `TrpcRemoteA2aAgent` 连接远程 A2A 服务。只需提供服务 URL，客户端会自动发现 Agent Card 并建立连接：

```python
# test_a2a.py
import asyncio
import uuid

from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.server.a2a import TrpcRemoteA2aAgent
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, Part

# 远程 A2A 服务地址（对应服务端启动的地址）
AGENT_BASE_URL = "http://127.0.0.1:18081"


async def main():
    # 创建远程 Agent，指定服务 URL；客户端会自动从 /.well-known/agent.json 发现 Agent Card
    remote_agent = TrpcRemoteA2aAgent(
        name="weather_agent",
        agent_base_url=AGENT_BASE_URL,
        description="Professional weather query assistant",
    )
    await remote_agent.initialize()  # 异步初始化：发现 Agent Card、创建 A2A 客户端

    # 创建会话服务和 Runner，使用方式与本地 Agent 完全一致
    session_service = InMemorySessionService()
    runner = Runner(app_name="a2a_demo", agent=remote_agent, session_service=session_service)

    user_id = "demo_user"
    session_id = str(uuid.uuid4())  # 每个会话使用唯一 ID，多轮对话复用同一 ID

    # 通过 metadata 向服务端传递业务参数（如 user_id）
    run_config = RunConfig(agent_run_config={
        "metadata": {"user_id": user_id},
    })

    user_content = Content(parts=[Part.from_text(text="What's the weather in Beijing?")])

    # 发起流式调用，逐事件处理远程 Agent 的响应
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=user_content,
        run_config=run_config,
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    print(part.text, end="", flush=True)

    print()


if __name__ == "__main__":
    asyncio.run(main())
```

### 2. 多轮对话

复用同一个 `session_id` 即可保持上下文：

```python
queries = [
    "Hello, my name is Alice.",
    "What's the weather in Beijing?",
    "What's my name and what did I just ask?",  # Agent 能回忆前两轮内容
]

for query in queries:
    # 每轮创建新的 Runner 实例，但复用同一个 session_service 以保持会话状态
    runner = Runner(app_name="a2a_demo", agent=remote_agent, session_service=session_service)
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,  # 复用同一 session_id，服务端自动维护上下文
        new_message=Content(parts=[Part.from_text(text=query)]),
        run_config=run_config,
    ):
        # 处理事件...
        pass
```

### 3. 传递自定义参数

通过 `RunConfig` 的 `agent_run_config` 向远程服务传递 `metadata` 和 `configuration`：

```python
from trpc_agent_sdk.configs import RunConfig

# metadata 中的键值对会随 A2A 请求传递到服务端
# 服务端可通过 user_id_extractor 或 RequestContext.metadata 读取
run_config = RunConfig(
    agent_run_config={
        "metadata": {
            "user_id": "12345",           # 用户标识，服务端可提取用于会话隔离
            "session_type": "premium",    # 业务自定义字段
            "custom_field": "value",
        },
    }
)
```

服务端可通过 `user_id_extractor` 回调读取这些 metadata（见下文配置章节）。

### 4. 客户端关键要点

| 要点 | 说明 |
|------|------|
| `TrpcRemoteA2aAgent` | 继承 `BaseAgent`，可像本地 Agent 一样通过 `Runner` 使用 |
| `agent_base_url` | 远程 A2A 服务的 HTTP 地址，客户端会自动从 `/.well-known/agent.json` 发现 Agent Card |
| `initialize()` | 异步初始化，完成 Agent Card 发现和客户端创建 |
| `agent_card` / `a2a_client` | 可选参数，如果已有 AgentCard 或 A2AClient 实例可直接传入，跳过自动发现 |
| `RunConfig` | 通过 `metadata` 字段传递业务参数（如 `user_id`），服务端可通过回调读取 |

---

## 任务取消

SDK 支持在 Agent 执行过程中取消任务，包括 LLM 流式生成阶段和工具执行阶段。

### 服务端配置

通过 `cancel_wait_timeout` 控制服务端等待 Agent 完成取消的超时时间：

```python
from trpc_agent_sdk.server.a2a import TrpcA2aAgentService
from trpc_agent_sdk.server.a2a import TrpcA2aAgentExecutorConfig

executor_config = TrpcA2aAgentExecutorConfig(
    cancel_wait_timeout=3.0,  # 收到 cancel 请求后，等待 Agent 完成取消清理的最大秒数
)

a2a_svc = TrpcA2aAgentService(
    service_name="weather_agent_cancel_service",
    agent=root_agent,
    executor_config=executor_config,  # 传入带取消超时配置的执行器
)
a2a_svc.initialize()
```

### 客户端取消

通过 `runner.cancel_run_async()` 发起取消请求：

```python
from trpc_agent_sdk.events import AgentCancelledEvent

# 在另一个协程中发起取消请求，会通过 A2A 协议发送 cancel_task 到服务端
success = await runner.cancel_run_async(
    user_id=user_id,
    session_id=session_id,
    timeout=3.0,  # 客户端等待取消完成的超时时间
)

# 正在运行的 run_async 迭代中会收到 AgentCancelledEvent
async for event in runner.run_async(...):
    if isinstance(event, AgentCancelledEvent):
        print(f"Run was cancelled: {event.error_message}")
        break
    # 正常处理其他事件...
```

### 取消流程

```text
客户端                              服务端
  │                                  │
  │── runner.run_async() ──────────→ │ 开始执行 Agent
  │← 流式事件 ←─────────────────── │
  │                                  │
  │── runner.cancel_run_async() ──→ │ cancel_task 请求
  │                                  │── 等待 cancel_wait_timeout
  │← AgentCancelledEvent ←──────── │
  │                                  │
  │── runner.run_async() (续) ────→ │ 同 session 继续对话
```

### 取消后会话恢复

取消后同一 `session_id` 仍可继续使用。SDK 会自动：

- 保留已完成的工具调用结果
- 清理未完成的工具调用
- 在会话中记录取消状态

### 超时配置

| 配置位置 | 参数 | 默认值 | 说明 |
|----------|------|--------|------|
| 服务端 | `cancel_wait_timeout` | 1.0 | 服务端等待后端 Agent 取消完成的超时时间 |
| 客户端 | `timeout` | 1.0 | 客户端等待 `cancel_run_async` 完成的超时时间 |

建议两端配置相同的超时时间。

---

## TrpcA2aAgentExecutorConfig 配置项

`TrpcA2aAgentExecutorConfig` 用于配置服务端 Agent 执行器的行为，从 `trpc_agent_sdk.server.a2a` 导入：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `cancel_wait_timeout` | `float` | `1.0` | 取消任务时最大等待秒数 |
| `user_id_extractor` | `Callable[[RequestContext], str \| Awaitable[str]] \| None` | `None` | 从 A2A 请求上下文提取 `user_id` 的回调；不设置时使用基于 `context_id` 的默认逻辑 |
| `event_callback` | `Callable[[Event, RequestContext], Event \| None \| Awaitable[Event \| None]] \| None` | `None` | 事件回调，在每个 Event 转换为 A2A 协议事件之前调用。详见 [事件回调](#事件回调event_callback) |

示例：

```python
from trpc_agent_sdk.server.a2a import TrpcA2aAgentExecutorConfig

# 完整配置示例：同时设置 user_id 提取、事件回调和取消超时
executor_config = TrpcA2aAgentExecutorConfig(
    user_id_extractor=custom_user_id_extractor,  # 自定义 user_id 提取逻辑
    event_callback=custom_event_callback,          # 事件拦截回调
    cancel_wait_timeout=2.0,                       # 取消等待超时（秒）
)
```

---

## 自定义 user_id 提取

默认情况下，`user_id` 基于 A2A 请求的 `context_id` 生成。如果需要从客户端传递的 `metadata` 中提取 `user_id`，可配置 `user_id_extractor`：

```python
from a2a.server.agent_execution import RequestContext
from trpc_agent_sdk.server.a2a import TrpcA2aAgentExecutorConfig


def custom_user_id_extractor(request: RequestContext) -> str:
    """从 A2A 请求的 metadata 中提取 user_id。

    客户端通过 RunConfig 的 metadata 传入 user_id，
    服务端在此回调中读取，用于会话隔离和用户识别。
    """
    if request and request.metadata:
        user_id = request.metadata.get("user_id")
        if user_id:
            return user_id
    # 兜底：基于 context_id 生成默认 user_id
    return f"A2A_USER_{request.context_id}"


executor_config = TrpcA2aAgentExecutorConfig(
    user_id_extractor=custom_user_id_extractor,
)
```

客户端通过 `RunConfig` 传递 `user_id`：

```python
# 客户端传递 user_id，服务端的 custom_user_id_extractor 即可读取到
run_config = RunConfig(agent_run_config={
    "metadata": {"user_id": "my_user_123"},
})
```

---

## 事件回调（event_callback）

`event_callback` 允许在服务端对每个 Event 进行拦截处理——在事件被转换为 A2A 协议事件并推送给客户端**之前**，你可以进行日志记录、过滤或内容修改。

### 回调签名

```python
from trpc_agent_sdk.events import Event
from a2a.server.agent_execution import RequestContext

def event_callback(event: Event, context: RequestContext) -> Event | None:
    ...
```

| 参数 | 说明 |
|------|------|
| `event` | 当前产生的 `Event` 对象，包含 `content`（文本 / function_call / function_response）、`partial`（是否流式片段）、`custom_metadata` 等字段 |
| `context` | A2A 请求上下文 `RequestContext`，包含 `task_id`、`context_id`、`metadata` 等信息 |
| **返回值** | 返回 `Event` 对象继续处理；返回 `None` 则跳过该事件（不发送给客户端） |

> 回调也可以声明为 `async def`，框架会自动 `await`。

### 场景 1：日志记录

```python
def custom_event_callback(event: Event, context: RequestContext) -> Event | None:
    # 检测流式工具调用事件
    if event.is_streaming_tool_call():
        print(f"[Event Callback] Streaming tool call detected: task={context.task_id}")

    # 检查流式片段中是否包含 function_call
    if event.partial and event.content and event.content.parts:
        for part in event.content.parts:
            if part.function_call:
                print(f"[Event Callback] Tool invocation: {part.function_call.name}")

    return event  # 原样返回，不做修改
```

### 场景 2：过滤事件

返回 `None` 可跳过特定事件：

```python
def custom_event_callback(event: Event, context: RequestContext) -> Event | None:
    # 过滤掉不可见事件，返回 None 表示跳过（客户端不会收到）
    if not event.visible:
        return None
    return event
```

### 场景 3：拷贝并修改事件

> **重要**：修改事件时务必**先深拷贝再修改**，避免污染框架内部持有的原始事件对象。`Event` 是 Pydantic v2 BaseModel，使用 `model_copy(deep=True)` 进行深拷贝。

```python
def custom_event_callback(event: Event, context: RequestContext) -> Event | None:
    if event.custom_metadata is None:
        # 先深拷贝，避免修改框架内部持有的原始对象
        modified_event = event.model_copy(deep=True)
        modified_event.custom_metadata = {
            "source": "a2a_server",
            "task_id": context.task_id,
        }
        return modified_event  # 返回修改后的副本
    return event
```

### 注意事项

1. **必须深拷贝后再修改**：`event.model_copy(deep=True)` 会递归复制所有嵌套对象，确保原始事件不被意外修改
2. **返回 `None` = 丢弃事件**：该事件不会被转换为 A2A 协议事件，客户端不会收到
3. **回调在协议转换之前执行**：修改后的事件会替代原始事件进入后续的 A2A 事件转换流程
4. **性能考虑**：回调在每个事件上执行，流式场景下事件频率较高，建议保持回调逻辑轻量

---

## 架构概览

```text
┌────────────────────────────────────────────────┐
│                  客户端                         │
│  ┌──────────────────────────────────────────┐  │
│  │        TrpcRemoteA2aAgent               │  │
│  │    (连接远程 A2A 服务)                    │  │
│  └──────────────┬───────────────────────────┘  │
│                 │ A2A Protocol (HTTP)           │
└─────────────────┼──────────────────────────────┘
                  │
┌─────────────────▼──────────────────────────────┐
│                  服务端                         │
│  ┌──────────────────────────────────────────┐  │
│  │  A2AStarletteApplication (a2a-sdk)      │  │
│  │    └─ DefaultRequestHandler             │  │
│  │         └─ TrpcA2aAgentService          │  │
│  │              └─ LlmAgent (你的 Agent)    │  │
│  └──────────────────────────────────────────┘  │
└────────────────────────────────────────────────┘
```

---

## 完整示例

- **基本使用**：[examples/a2a](../../../examples/a2a/README.md) — A2A 服务部署 + 3 轮多轮对话
- **支持任务取消**：[examples/a2a_with_cancel](../../../examples/a2a_with_cancel/README.md) — LLM 流式阶段取消 + 工具执行阶段取消
