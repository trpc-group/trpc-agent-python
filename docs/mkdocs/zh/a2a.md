# A2A 使用文档

trpc-agent SDK 内置了 Agent-to-Agent (A2A) 协议支持，让你可以将本地 Agent 发布为标准 A2A 服务，也可以作为客户端远程调用其他 A2A Agent。

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

使用 `TrpcA2aAgentService` 将 Agent 包装为 A2A 服务，再通过 `create_a2a_application`（封装了 a2a-sdk 1.x 路由工厂）组装 Starlette 应用：

```python
# run_server.py
import uvicorn

# SDK 提供的 A2A 服务封装与便利层应用组装
from trpc_agent_sdk.server.a2a import TrpcA2aAgentService
from trpc_agent_sdk.server.a2a import TrpcA2aAgentExecutorConfig
from trpc_agent_sdk.server.a2a import create_a2a_application

HOST = "127.0.0.1"
PORT = 18081


def create_a2a_service() -> TrpcA2aAgentService:
    from agent.agent import root_agent

    # 执行器配置（可选），可在此配置 user_id_extractor、event_callback 等
    executor_config = TrpcA2aAgentExecutorConfig()

    # 将 Agent 包装为 A2A 服务，实现了 A2A SDK 的 AgentExecutor 接口。
    # rpc_url 是写入 Agent Card 的对外地址，依赖卡片发现的客户端会调用它。
    a2a_svc = TrpcA2aAgentService(
        service_name="weather_agent_service",  # 服务名称，用于标识服务
        agent=root_agent,                      # 要部署的 Agent
        rpc_url=f"http://{HOST}:{PORT}",       # Agent Card 中声明的对外地址
        executor_config=executor_config,
    )
    a2a_svc.initialize()  # 必须调用，完成 Agent Card 构建等初始化
    return a2a_svc


def serve():
    a2a_svc = create_a2a_service()

    # 组装 Starlette 应用，自动注册 Agent Card 和 JSON-RPC 端点
    app = create_a2a_application(a2a_svc)

    print(f"Starting A2A server on http://{HOST}:{PORT}")
    print(f"Agent card: http://{HOST}:{PORT}/.well-known/agent-card.json")

    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    serve()
```

启动后，服务会自动发布 Agent Card 到 `/.well-known/agent-card.json`，客户端可通过该地址发现并调用 Agent。

### 3. 服务端关键要点

| 要点 | 说明 |
|------|------|
| `TrpcA2aAgentService` | 实现了 A2A SDK 的 `AgentExecutor` 接口，可直接作为 `create_a2a_application` 的执行器 |
| `rpc_url` | 写入 `supported_interfaces[].url` 的对外地址；服务端知道自己地址时配置（见 [Agent Card URL](#agent-card-url)） |
| `agent_card` | 自动根据 Agent 的 name、description、tools 等信息构建，也可手动传入 |
| `initialize()` | 必须在使用前调用，完成 Agent Card 构建和内部初始化 |
| `create_a2a_application()` | 便利层，把 Agent Card 与 JSON-RPC 路由挂载成 Starlette 应用。可选：需要完全控制时可直接用 a2a-sdk 的 `create_agent_card_routes` / `create_jsonrpc_routes` 自己拼 |
| `enable_v0_3_compat` | `create_a2a_application(..., enable_v0_3_compat=True)` 在同一端点同时接受旧版 0.3 客户端 |
| `session_service` | 可选，默认使用 `InMemorySessionService`；可替换为持久化实现 |
| `executor_config` | 可选，用于配置 `user_id_extractor`、`event_callback`、`cancel_wait_timeout` 等行为 |

#### Agent Card URL

服务端**不知道自己的对外地址**，因此 `supported_interfaces[].url` 默认留空，需要你提供一个。url 只有**一个配置入口**：`TrpcA2aAgentService(rpc_url=...)`（或完全自定义的 `agent_card`）：

```python
# 该 url 会原样写入 Agent Card
svc = TrpcA2aAgentService(
    service_name="weather",
    agent=root_agent,
    rpc_url="https://agent.example.com/a2a",
)
```

`create_a2a_application()` 会从这个 url 推导 JSON-RPC 的挂载路径（`https://agent.example.com/a2a` → `/a2a`，纯域名则 `/`），保证"卡片声明的路径"与"实际挂载路径"永不不一致。若任何地方都没配置 url，服务仍能启动——JSON-RPC 直连的客户端不读卡片——但会打出一条 warning，因为依赖卡片发现的客户端无法调用该 Agent。

---

## 从 v0.3 升级

SDK 底层协议从 a2a 0.3 升级到 1.0，对应用层主要有两方面：**代码写法要迁移**（下节），**运行时可平滑兼容**（兼容开关）。**卡片路径不变**：0.3 和 1.0 的 Agent Card 都发布在 `/.well-known/agent-card.json`，发现机制无需迁移。

### 代码写法迁移（0.3 → 1.0）

a2a-sdk 从 0.3 到 1.0 是一次架构重写，业务代码里几处关键写法要改：

| 0.3 写法 | 1.0 写法 | 说明 |
|---|---|---|
| `from a2a.server.apps import A2AStarletteApplication` + `server = A2AStarletteApplication(agent_card=..., http_handler=...)` | `from trpc_agent_sdk.server.a2a import create_a2a_application` + `app = create_a2a_application(a2a_svc)` | **`A2AStarletteApplication` 在 1.0 已删除**，改用 SDK 便利层装配 |
| `DefaultRequestHandler(agent_executor=..., task_store=...)` | 无需手拼（`create_a2a_application` 内部构造）；需自定义时才 `DefaultRequestHandler(agent_executor=..., task_store=..., agent_card=...)` | **`DefaultRequestHandler` 新增必填 `agent_card`** |
| `TrpcA2aAgentService(service_name=..., agent=..., executor_config=...)` | 增加 `rpc_url=...` | **必须配 `rpc_url`**，见下文 |
| 卡片顶层 `url` | `supported_interfaces[].url` | 卡片布局变了；0.3 客户端发现用顶层 `url`，1.0 用 `supported_interfaces` |

> 上表是**业务代码**要改的。此外 a2a-sdk 底层还有 `A2AClient`（已删除 → `await create_client()`）等 API 变化，但都被封装在 SDK 内部，业务代码无需处理。业务代码通常只需：服务端加 `rpc_url` + 改用 `create_a2a_application`；客户端用 `TrpcRemoteA2aAgent`。

### 服务端：开启 `enable_v0_3_compat` 兼容旧客户端

1.0 服务端默认只接受 1.0 客户端。如果线上仍有旧版 0.3 客户端（尚未升级），服务端在**同一端点**同时接受 1.0 和 0.3 报文：

```python
app = create_a2a_application(a2a_svc, enable_v0_3_compat=True)
```

框架会自动往 Agent Card 追加一个 `protocol_version="0.3"` 的接口（复用同一个 url），使 0.3 客户端能正确发现并调用。旧 0.3 客户端**无需改动**。

### 客户端：`enable_v0_3_compat=True` 兼容旧服务端

当远端可能是纯 0.3 老服务端时（老卡片没有 `supportedInterfaces` 或接口 url 为空，1.0 默认发现会报 `no compatible transports found`），开启兼容模式让客户端**自动协商**：能读到 1.0 接口就走 1.0，读到 0.3 接口就走 0.3 报文：

```python
remote_agent = TrpcRemoteA2aAgent(
    name="weather_agent",
    agent_base_url="http://127.0.0.1:18081",
    enable_v0_3_compat=True,  # 兼容旧服务端：按卡片自动协商 1.0/0.3
)
```

### 最重要的变化：必须配置 `rpc_url`

0.3 对卡片 url 不做强制要求，旧服务端不填 url 仍能工作；**1.0 的 Agent Card 必须携带可达的 `supported_interfaces[].url`**，否则发现型客户端会报 `no compatible transports found`。升级时**务必**在 `TrpcA2aAgentService` 构造时配置 `rpc_url`（或提供自定义 `agent_card`），详见上文 [Agent Card URL](#agent-card-url)。

### 三种协议组合对照

| 场景 | 服务端 | 客户端 |
|---|---|---|
| **1.0 → 1.0**（推荐） | `create_a2a_application(a2a_svc)` | 默认 |
| **0.3 客户端 → 1.0 服务端** | `create_a2a_application(a2a_svc, enable_v0_3_compat=True)` | 旧 0.3 客户端，无需改动 |
| **1.0 客户端 → 0.3 服务端** | 旧 0.3 服务端 | `TrpcRemoteA2aAgent(..., enable_v0_3_compat=True)` |

> **`enable_v0_3_compat=True` 自动适应**：客户端优先按卡片自动协商协议——读到 1.0 接口走 1.0 报文，读到 0.3 接口走 0.3 报文。当**卡片拉不到、没有 `supportedInterfaces`、或接口 url 为空**（纯 0.3 老服务端，0.3 布局把地址留给客户端）时，直接走 0.3 报文。所以它能同时调 1.0 服务端和纯 0.3 老服务端，无需手动切换。

> 完整可运行示例见 [examples/a2a](../../../examples/a2a/README.md)（同一个 example 通过 `A2A_V03_COMPAT` 环境变量覆盖三种组合）。

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
    # 创建远程 Agent，指定服务 URL；客户端会自动从 /.well-known/agent-card.json 发现 Agent Card
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
| `agent_base_url` | 远程 A2A 服务的 HTTP 地址，客户端会自动从 `/.well-known/agent-card.json` 发现 Agent Card |
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
│  │  create_a2a_application (trpc-agent) │  │
│  │    └─ DefaultRequestHandler          │  │
│  │         └─ TrpcA2aAgentService       │  │
│  │              └─ LlmAgent (你的 Agent)│  │
│  └──────────────────────────────────────────┘  │
└────────────────────────────────────────────────┘
```

---

## 完整示例

- **基本使用**：[examples/a2a](../../../examples/a2a/README.md) — A2A 服务部署 + 3 轮多轮对话
- **支持任务取消**：[examples/a2a_with_cancel](../../../examples/a2a_with_cancel/README.md) — LLM 流式阶段取消 + 工具执行阶段取消
