# A2A 使用文档

trpc-agent SDK 内置了 Agent-to-Agent (A2A) 协议支持，让你可以将本地 Agent 发布为标准 A2A 服务，也可以作为客户端远程调用其他 A2A Agent。

本文中的 **0.3** 和 **1.x（1.0）** 指底层 a2a-sdk 的大版本：0.3 是旧版（extra `[a2a]`，包 `trpc_agent_sdk.server.a2a`），1.x 是现行协议与推荐版本（extra `[a2a-v1]`，包 `trpc_agent_sdk.server.a2a_v1`），二者不能同时安装。

## 🚀 核心优势

- **部署简单**：几行代码即可将 Agent 发布为 A2A HTTP 服务
- **流式支持**：开箱即用的 artifact-first 流式传输
- **取消支持**：客户端可随时取消正在执行的远程任务
- **会话保持**：多轮对话自动维护上下文

---

## 安装

框架通过 extra 选择 a2a-sdk 版本（**不能同时安装**）。**推荐 `[a2a-v1]`（a2a-sdk 1.x）**；`[a2a]` 仅用于尚未升级的 0.3 代码。

```bash
uv pip install -e ".[a2a-v1]"   # 推荐：a2a-sdk 1.x（create_a2a_application / 1.0 Agent Card）
                             # import: trpc_agent_sdk.server.a2a_v1
uv pip install -e ".[a2a]"      # 现有 0.3 代码：a2a-sdk 0.3
                             # import: trpc_agent_sdk.server.a2a
```

需要使用 Python3.12。

---

## 服务端部署

两端共用同一份 Agent 定义。**新服务请用 1.0**（extra `[a2a-v1]`）；0.3 只给还没升级的现有部署。

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

### 2. 创建 a2a 1.0 服务端（推荐）

安装 `[a2a-v1]`，从 `trpc_agent_sdk.server.a2a_v1` 导入。用 `TrpcA2aAgentService` 包装 Agent，再用 `create_a2a_application` 装配 HTTP 服务。

#### 创建 A2A 服务并启动

```python
# run_server.py
import uvicorn

from trpc_agent_sdk.server.a2a_v1 import TrpcA2aAgentExecutorConfig
from trpc_agent_sdk.server.a2a_v1 import TrpcA2aAgentService
from trpc_agent_sdk.server.a2a_v1 import create_a2a_application

HOST = "127.0.0.1"
PORT = 18081


def create_a2a_service() -> TrpcA2aAgentService:
    from agent.agent import root_agent

    a2a_svc = TrpcA2aAgentService(
        service_name="weather_agent_service",
        agent=root_agent,
        rpc_url=f"http://{HOST}:{PORT}",  # 写入 Agent Card，供发现型客户端调用
        executor_config=TrpcA2aAgentExecutorConfig(),
    )
    a2a_svc.initialize()  # 必须调用，完成 Agent Card 构建等初始化
    return a2a_svc


def serve():
    a2a_svc = create_a2a_service()
    app = create_a2a_application(a2a_svc)

    print(f"Starting A2A server on http://{HOST}:{PORT}")
    print(f"Agent card: http://{HOST}:{PORT}/.well-known/agent-card.json")

    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    serve()
```

启动后，服务会发布 Agent Card 到 `/.well-known/agent-card.json`。完整示例见 [examples/a2a_v1](../../../examples/a2a_v1/README.md)。

#### Agent Card URL

服务端不知道自己的对外地址。Agent Card 里 `supported_interfaces[].url` 由部署方指定，配置入口只有一个：`TrpcA2aAgentService(rpc_url=...)`，或传入自定义 `agent_card`。

```python
# 方式 1：固定域名（推荐，有反代/域名时）
svc = TrpcA2aAgentService(
    service_name="weather",
    agent=root_agent,
    rpc_url="https://agent.example.com/a2a",
)

# 方式 2：本地/无固定域名，直接把监听地址当 rpc_url
svc = TrpcA2aAgentService(
    service_name="weather",
    agent=root_agent,
    rpc_url="http://127.0.0.1:18081",
)
```

发现型客户端需要可达的 `supported_interfaces[].url`，否则会报 `no compatible transports found`。url 为空仍能启动（JSON-RPC 直连不读卡），但会打 warning。`create_a2a_application()` 不另收路径参数；JSON-RPC 路由挂到卡片 url 的 path（`https://x.com/a2a` → `/a2a`，无路径则 `/`），保证卡片声明和实际端点一致。

#### 服务端关键要点

| 要点 | 说明 |
|------|------|
| `create_a2a_application` | 装配 Agent Card 与 JSON-RPC 路由；也可自行用 a2a-sdk 的 `DefaultRequestHandler` / `create_agent_card_routes` / `create_jsonrpc_routes` |
| `rpc_url` | 自动建卡时应配置，写入 `supported_interfaces[].url`；发现型客户端依赖它 |
| `TrpcA2aAgentService` | 实现了 A2A SDK 的 `AgentExecutor` 接口 |
| `agent_card` | 自动根据 Agent 的 name、description、tools 等信息构建；自定义时用 1.0 的 `supported_interfaces[]` |
| `initialize()` | 必须在使用前调用，完成 Agent Card 构建和内部初始化 |
| `session_service` | 可选，默认使用 `InMemorySessionService`；可替换为持久化实现 |
| `executor_config` | 可选，用于配置 `user_id_extractor`、`event_callback`、`cancel_wait_timeout` 等行为 |

#### 兼容旧 0.3 客户端

1.0 服务端默认按 1.0 报文工作，并发布 `/.well-known/agent-card.json`。a2a-sdk 0.3.22+ 的 `A2ACardResolver` 本来就拉这条路径，因此旧客户端**能拉到卡**，但会因缺少顶层 `url` 校验失败，且服务端默认不解码 0.3 JSON-RPC。`/.well-known/agent.json` 默认 404。

如果线上仍有旧版 0.3 客户端，在**同一端点**同时接受 1.0 和 0.3 报文：

```python
from trpc_agent_sdk.server.a2a_v1 import create_a2a_application

app = create_a2a_application(a2a_svc, enable_v0_3_compat=True)
```

开启开关后，框架会：

- 往 **well-known 发现用的卡片副本**追加 `protocol_version="0.3"` 接口（复用同一个 url），使序列化结果带上 0.3 必填的顶层 `url`
- 打开 JSON-RPC 的 0.3 解码
- 额外在 `/.well-known/agent.json` 发布同一张卡（给仍硬编码废弃路径的客户端）

**仅在该开关开启时**，旧 0.3 客户端**无需改动**。默认关闭时，0.3 客户端无法正常使用该服务。

Compat **要求**卡片上有可达 url（`TrpcA2aAgentService(rpc_url=...)` 或自定义 `agent_card`）。0.3 well-known 卡的顶层 `url` 从该接口复制而来，空 url 无法凭空补上。`create_a2a_application(..., enable_v0_3_compat=True)` 在所有接口 url 都为空时会 **raise `ValueError`**。

未传入自定义 `request_handler` 时，内部构造的 `DefaultRequestHandler` 使用同一份带 0.3 接口的副本。传入自定义 handler 时，开关**不会**改写 `handler.agent_card`：JSON-RPC 仍可按 0.3 报文工作，但若 handler 自己读 `supported_interfaces` 做版本判断，**须由调用方在该卡上自行声明 0.3 接口**。

### 3. 创建 a2a 0.3 服务端

安装 `[a2a]`，从 `trpc_agent_sdk.server.a2a` 导入。用 `TrpcA2aAgentService` 包装 Agent，再通过 A2A SDK 的 `A2AStarletteApplication` 以标准 HTTP 方式运行。

#### 创建 A2A 服务并启动

```python
# run_server.py
import uvicorn

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore

from trpc_agent_sdk.server.a2a import TrpcA2aAgentExecutorConfig
from trpc_agent_sdk.server.a2a import TrpcA2aAgentService

HOST = "127.0.0.1"
PORT = 18081


def create_a2a_service() -> TrpcA2aAgentService:
    from agent.agent import root_agent

    executor_config = TrpcA2aAgentExecutorConfig()

    a2a_svc = TrpcA2aAgentService(
        service_name="weather_agent_service",
        agent=root_agent,
        executor_config=executor_config,
    )
    a2a_svc.initialize()  # 必须调用，完成 Agent Card 构建等初始化
    return a2a_svc


def serve():
    a2a_svc = create_a2a_service()

    request_handler = DefaultRequestHandler(
        agent_executor=a2a_svc,
        task_store=InMemoryTaskStore(),
    )

    server = A2AStarletteApplication(
        agent_card=a2a_svc.agent_card,  # 主路径 /.well-known/agent-card.json；兼容别名 /.well-known/agent.json
        http_handler=request_handler,
    )

    print(f"Starting A2A server on http://{HOST}:{PORT}")
    print(f"Agent card: http://{HOST}:{PORT}/.well-known/agent-card.json")

    uvicorn.run(server.build(), host=HOST, port=PORT)


if __name__ == "__main__":
    serve()
```

启动后，服务会发布 Agent Card 到 `/.well-known/agent-card.json`（同时兼容废弃路径 `/.well-known/agent.json`）。0.3 对卡片 url 不做强制要求。完整示例见 [examples/a2a](../../../examples/a2a/README.md)。

#### 服务端关键要点

| 要点 | 说明 |
|------|------|
| `A2AStarletteApplication` | a2a-sdk 0.3 的 HTTP 装配；启动时调用 `server.build()` |
| `TrpcA2aAgentService` | 实现了 A2A SDK 的 `AgentExecutor` 接口，可直接作为 `DefaultRequestHandler` 的执行器 |
| `agent_card` | 自动根据 Agent 的 name、description、tools 等信息构建，也可手动传入（0.3 布局：顶层 `url`、`preferredTransport`） |
| `initialize()` | 必须在使用前调用，完成 Agent Card 构建和内部初始化 |
| `session_service` | 可选，默认使用 `InMemorySessionService`；可替换为持久化实现 |
| `executor_config` | 可选，用于配置 `user_id_extractor`、`event_callback`、`cancel_wait_timeout` 等行为 |

---

## 客户端调用

推荐 extra `[a2a-v1]`，从 `trpc_agent_sdk.server.a2a_v1` 导入。0.3 客户端把 import 换成 `trpc_agent_sdk.server.a2a` 即可；发现路径同样是 `/.well-known/agent-card.json`。

### 1. 创建远程 Agent 并发起调用

使用 `TrpcRemoteA2aAgent` 连接远程 A2A 服务。只需提供服务 URL，客户端会自动发现 Agent Card 并建立连接：

```python
# test_a2a.py
import asyncio
import uuid

from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.server.a2a_v1 import TrpcRemoteA2aAgent
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

### 5. 对接 0.3 服务端（`force_v0_3`）

默认跟 AgentCard 走：完整的 1.0 卡、完整的 0.3 卡都不必开这个开关。1.0 客户端发现后若 JSONRPC 接口 url 为空，会用 `agent_base_url` 补上，所以「旧 0.3 服务没填卡片 url」通常也不必开。

只有你**明确知道对端是 0.3 服务端**，且默认跟卡走不通（或不该信这张卡）时再开，例如卡片声明和进程不一致、你确认对端收的是 0.3 报文。

`force_v0_3=True` 时，POST 地址优先用卡片上的 JSONRPC url，没有时才回退 `agent_base_url`。

```python
from trpc_agent_sdk.server.a2a_v1 import TrpcRemoteA2aAgent

remote_agent = TrpcRemoteA2aAgent(
    name="weather_agent",
    agent_base_url="http://127.0.0.1:18081",
    force_v0_3=True,  # 明确对端是 0.3，强制旧报文
)
```

### 6. 三种协议组合对照

| 场景 | 服务端 | 客户端 |
|---|---|---|
| **1.0 → 1.0**（推荐） | `create_a2a_application(a2a_svc)` | 默认 |
| **0.3 客户端 → 1.0 服务端** | `create_a2a_application(a2a_svc, enable_v0_3_compat=True)` | 旧 0.3 客户端，无需改动 |
| **1.0 客户端 → 0.3 服务端** | 旧 0.3 服务端 | 完整 0.3 卡用默认；卡片不可信或跟卡走不通时 `force_v0_3=True` |

> 客户端 `force_v0_3=True` 表示「我确认对端是 0.3，强制旧报文」，通常不用开。服务端 `enable_v0_3_compat` 仍表示「1.0 服务同时收 0.3 客户端」，两者不要混用。

> 完整可运行示例见 [examples/a2a_v1](../../../examples/a2a_v1/README.md)（服务端 `A2A_V03_COMPAT` 和客户端 `A2A_FORCE_V03` 覆盖三种协议组合）。0.3 示例见 [examples/a2a](../../../examples/a2a/README.md)。

---

## 任务取消

SDK 支持在 Agent 执行过程中取消任务，包括 LLM 流式生成阶段和工具执行阶段。

### 服务端配置

通过 `cancel_wait_timeout` 控制服务端等待 Agent 完成取消的超时时间：

```python
from trpc_agent_sdk.server.a2a_v1 import TrpcA2aAgentExecutorConfig
from trpc_agent_sdk.server.a2a_v1 import TrpcA2aAgentService

executor_config = TrpcA2aAgentExecutorConfig(
    cancel_wait_timeout=3.0,  # 收到 cancel 请求后，等待 Agent 完成取消清理的最大秒数
)

a2a_svc = TrpcA2aAgentService(
    service_name="weather_agent_cancel_service",
    agent=root_agent,
    rpc_url="http://127.0.0.1:18081",
    executor_config=executor_config,  # 传入带取消超时配置的执行器
)
a2a_svc.initialize()
```

0.3 从 `trpc_agent_sdk.server.a2a` 导入，且不需要 `rpc_url`。

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

`TrpcA2aAgentExecutorConfig` 用于配置服务端 Agent 执行器的行为。1.0 从 `trpc_agent_sdk.server.a2a_v1` 导入；0.3 从 `trpc_agent_sdk.server.a2a` 导入：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `cancel_wait_timeout` | `float` | `1.0` | 取消任务时最大等待秒数 |
| `user_id_extractor` | `Callable[[RequestContext], str \| Awaitable[str]] \| None` | `None` | 从 A2A 请求上下文提取 `user_id` 的回调；不设置时使用基于 `context_id` 的默认逻辑 |
| `event_callback` | `Callable[[Event, RequestContext], Event \| None \| Awaitable[Event \| None]] \| None` | `None` | 事件回调，在每个 Event 转换为 A2A 协议事件之前调用。详见 [事件回调](#事件回调event_callback) |

示例：

```python
from trpc_agent_sdk.server.a2a_v1 import TrpcA2aAgentExecutorConfig

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
from trpc_agent_sdk.server.a2a_v1 import TrpcA2aAgentExecutorConfig


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

## 从 0.3 升级到 1.0

如果服务想从 0.3 版本升级到 1.0，由于官方 a2a sdk 没有完全兼容旧版本，所以需要进行一些代码的改造。

客户端日常用法两边一致，一般只换 import（`trpc_agent_sdk.server.a2a` → `trpc_agent_sdk.server.a2a_v1`）。不兼容主要出在服务端装配。两个 extra 不能同时安装，升级时改为 `uv pip install -e ".[a2a-v1]"`。

| 0.3 写法 | 1.0 写法 | 说明 |
|---|---|---|
| `from a2a.server.apps import A2AStarletteApplication` + `server = A2AStarletteApplication(...)` | `from trpc_agent_sdk.server.a2a_v1 import create_a2a_application` + `app = create_a2a_application(a2a_svc)` | **`A2AStarletteApplication` 在 1.0 已删除**。装配结果是 Starlette app，直接 `uvicorn.run(app, ...)`，**没有** `server.build()` |
| `DefaultRequestHandler(agent_executor=..., task_store=...)` | 通常无需手拼（`create_a2a_application` 内部构造）；自定义时必须加 `agent_card=...` | **`DefaultRequestHandler` 新增必填 `agent_card`** |
| `TrpcA2aAgentService(service_name=..., agent=...)` | 自动建卡时增加 `rpc_url=...` | 见 [Agent Card URL](#agent-card-url) |
| 手写 / 传入的 `AgentCard`（顶层 `url`、`preferredTransport`） | `supported_interfaces[]`（`url`、`protocol_binding`、`protocol_version`） | 自定义卡必须改成 1.0 布局，不能只填顶层 `url` |

a2a-sdk 底层还有 `A2AClient`（已删除 → `await create_client()`）等 API 变化，都被封装在 SDK 内部，业务代码无需处理。若还用到了其他不兼容的 API，请参考官方 a2a-sdk 的最新用法修改。

若升级后仍要和未升级的对端互通，见上文 [兼容旧 0.3 客户端](#兼容旧-03-客户端) 和 [对接 0.3 服务端](#5-对接-03-服务端force_v0_3)。

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
│  │  1.0: create_a2a_application（推荐）     │  │
│  │  0.3: A2AStarletteApplication            │  │
│  │    └─ DefaultRequestHandler          │  │
│  │         └─ TrpcA2aAgentService       │  │
│  │              └─ LlmAgent (你的 Agent)│  │
│  └──────────────────────────────────────────┘  │
└────────────────────────────────────────────────┘
```

1.0 从 `trpc_agent_sdk.server.a2a_v1` 导入并用 `create_a2a_application`；0.3 从 `trpc_agent_sdk.server.a2a` 导入并用 `A2AStarletteApplication`。后面的执行链路相同。

---

## 完整示例

- **基本使用（1.x，推荐）**：[examples/a2a_v1](../../../examples/a2a_v1/README.md) — `create_a2a_application` + 3 轮多轮对话
- **支持任务取消（1.x）**：[examples/a2a_v1_with_cancel](../../../examples/a2a_v1_with_cancel/README.md)
- **基本使用（0.3）**：[examples/a2a](../../../examples/a2a/README.md) — A2A 服务部署 + 3 轮多轮对话
- **支持任务取消（0.3）**：[examples/a2a_with_cancel](../../../examples/a2a_with_cancel/README.md) — LLM 流式阶段取消 + 工具执行阶段取消
