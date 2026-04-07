# AG-UI 使用文档

[AG-UI](https://github.com/ag-ui-protocol/ag-ui) 是用于 Agent 与前端页面交互的协议：基于事件驱动，将工具调用、模型输出等行为以不同 Event 推送给前端。

- **实时状态展示**：前端可感知并展示 Agent 当前运行状态  
- **流式输出与进度**：支持流式文本、工具调用进度等实时呈现  
- **人机协作（Human-In-The-Loop）**：执行过程中可暂停，等待用户在前端确认或反馈  

当前 [CopilotKit](https://github.com/CopilotKit/CopilotKit) 已提供多种通过 AG-UI 与 Agent 交互的 UI 组件。

本仓库在 `server/ag_ui` 中提供 AG-UI 服务端桥接：`AgUiAgent` 将一次 AG-UI 请求与内部 `Runner.run_async` 对齐；`EventTranslator` 把框架事件转为 AG-UI 标准事件；`AgUiService` 注册 URI 与 POST 流式端点；`AgUiManager` 聚合多个服务并用 **FastAPI + Uvicorn** 对外监听。前端负责展示与交互（可选用 CopilotKit），与 AG-UI 服务之间通过 AG-UI 事件流（如 SSE）连接。

## 安装

在克隆后的仓库根目录执行（启用 `ag-ui` 可选依赖）：

```bash
pip install -e ".[ag-ui]"
```

要求使用 Python 3.12。核心依赖包含 `ag-ui-protocol` 与 `FastAPI/Uvicorn`。

## 快速上手

用 `AgUiService` 挂载 `AgUiAgent`，把同一 FastAPI 应用交给 `AgUiManager`，再调用 `run(host, port)`。

- `AgUiService(service_name, app=fastapi_app)`：在传入的 `app` 上注册各 Agent 的 POST 路由。  
- `add_agent("/your_uri", agui_agent)`：Agent 的 URI 不要与同应用上其它自定义路由冲突。  
- 若要在同一 FastAPI 应用上增加业务接口，在调用 `manager.run` 之前向该 `app` 注册路由即可（示例里通过 `AguiRunner` 增加了 `GET /health`）。

下面是与示例 `_agui_runner.py` / `run_server.py` 思路一致的精简写法（省略 `/health` 等细节）：先创建 `FastAPI` 与 `AgUiManager`，再把同一 `app` 交给 `AgUiService`，注册 Agent 后 `set_app` 并 `run`。

```python
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

from trpc_agent_sdk.log import logger
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.server.ag_ui import AgUiAgent
from trpc_agent_sdk.server.ag_ui import AgUiManager
from trpc_agent_sdk.server.ag_ui import AgUiService

load_dotenv()

HOST = "127.0.0.1"
PORT = 18080

# AgUiManager 聚合多个 AgUiService，并通过 Uvicorn 启动 FastAPI 应用
manager = AgUiManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 生命周期：在应用关闭时释放 manager 持有的后台执行状态。"""
    logger.info("AG-UI server starting")
    yield
    logger.info("AG-UI server shutting down")
    await manager.close()


app = FastAPI(title="AG-UI demo", lifespan=lifespan)


def serve():
    from agent.agent import root_agent  # 你自定义的根 Agent（如 LlmAgent）

    app_name = "weather_app"
    service_name = "weather_agent_service"
    uri = "/weather_agent"  # 前端 POST 到此路径即可触发 Agent 执行

    # 内存 Session，适合开发调试；生产可替换为 RedisSessionService
    session_service = InMemorySessionService()

    # AgUiService : 绑定 FastAPI app，后续 add_agent 时会自动注册 POST 路由
    agui_service = AgUiService(service_name, app=app)

    # 创建 AgUiAgent : 第一个位置参数为 BaseAgent 实例，其余均为 keyword-only
    agui_agent = AgUiAgent(
        root_agent,
        app_name=app_name,
        session_service=session_service,
    )

    # 将 Agent 挂载到指定 URI
    agui_service.add_agent(uri, agui_agent)
    # 将 service 注册到 manager
    manager.register_service(service_name, agui_service)
    # set_app 后 manager.run 内部会调用 uvicorn.run(app, host, port)
    manager.set_app(app)
    manager.run(HOST, PORT)


if __name__ == "__main__":
    serve()
```

更完整、可直接运行的组织方式（含 `FastAPI` 生命周期里 `await manager.close()`）见：

- [examples/agui/run_server.py](../../../examples/agui/run_server.py)  
- [examples/agui/_agui_runner.py](../../../examples/agui/_agui_runner.py)  

实现细节与目录结构说明见仓库内 AG-UI 服务端实现目录的 [README](../../../trpc_agent_sdk/server/ag_ui/README.md)。

## 进阶用法

### `AgUiAgent` 配置概要

#### 应用名与用户 ID

支持静态或从 `RunAgentInput` 动态解析，用于会话与应用维度隔离。

```python
from ag_ui.core import RunAgentInput

from trpc_agent_sdk.server.ag_ui import AgUiAgent

# 方式一：静态值 —— 所有请求共用同一 app_name / user_id
agui_agent = AgUiAgent(
    weather_agent,
    app_name="weather_app",  # 固定应用名
    user_id="user_123",      # 固定用户 ID
)

# 方式二：动态提取 —— 从每次请求的 RunAgentInput 中解析
# RunAgentInput 由 ag-ui-protocol 定义，包含 thread_id、state、messages 等字段
def extract_app_name(inp: RunAgentInput) -> str:
    # inp.state 是前端随请求传入的自定义状态字典
    return inp.state.get("app_name", "default_app")

def extract_user_id(inp: RunAgentInput) -> str:
    return inp.state.get("user_id", f"thread_user_{inp.thread_id}")

# 注意：app_name 与 app_name_extractor 不可同时指定（user_id 同理）
agui_agent = AgUiAgent(
    weather_agent,
    app_name_extractor=extract_app_name,
    user_id_extractor=extract_user_id,
)
```

#### 会话与记忆（Storage）

默认可使用内存会话；生产环境可接入 Redis 等实现（需自行提供 Redis 地址与权限）。

```python
from trpc_agent_sdk.memory import RedisMemoryService
from trpc_agent_sdk.server.ag_ui import AgUiAgent
from trpc_agent_sdk.sessions import RedisSessionService

redis_url = "redis://localhost:6379/0"

# 使用 Redis 持久化会话与记忆（生产环境推荐）
# use_in_memory_services=False 禁止框架为未传入的服务自动创建内存实现
agui_agent = AgUiAgent(
    weather_agent,
    app_name="weather_app",
    session_service=RedisSessionService(db_url=redis_url),
    memory_service=RedisMemoryService(db_url=redis_url, enabled=True),
    use_in_memory_services=False,
)

# 会话超时与清理（由内部 SessionManager 管理）
# session_timeout_seconds：会话空闲超过此时间将被标记为过期
# cleanup_interval_seconds：定期清理过期会话的间隔
agui_agent = AgUiAgent(
    weather_agent,
    app_name="weather_app",
    session_timeout_seconds=3600,      # 1 小时
    cleanup_interval_seconds=600,       # 10 分钟
)
```

#### 执行与工具超时、并发

```python
agui_agent = AgUiAgent(
    weather_agent,
    app_name="weather_app",
    execution_timeout_seconds=1200,    # 单次 Agent 执行上限 20 分钟（默认 600s）
    tool_timeout_seconds=600,          # 单次工具调用上限 10 分钟（默认 300s）
    max_concurrent_executions=20,      # 最大并发执行数（默认 10）
)
```

#### 在 Agent / 回调中获取 HTTP Request

框架会把当前请求的 HTTP 对象写入本次调用的 `run_config`，可通过 `get_agui_http_req` 在自定义 Agent 或回调里读取（例如鉴权头、租户 ID、请求 ID）。

在自定义 `BaseAgent` 子类中：

```python
from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.server.ag_ui import get_agui_http_req


class MyCustomAgent(BaseAgent):
    async def _run_async_impl(self, ctx: InvocationContext):
        # get_agui_http_req 从 ctx.run_config 中提取 HTTP Request，可能为 None
        request = get_agui_http_req(ctx)
        auth_token = request.headers.get("authorization", "") if request else ""
        tenant_id = request.headers.get("x-tenant-id", "") if request else ""
        ...
```

在回调中：

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.server.ag_ui import get_agui_http_req


async def before_agent_callback(context: InvocationContext):
    # 回调与自定义 Agent 使用同一接口获取 HTTP Request
    request = get_agui_http_req(context)
    request_id = request.headers.get("x-request-id", "") if request else ""
    tenant_id = request.headers.get("x-tenant-id", "") if request else ""
    print(f"request_id={request_id}, tenant_id={tenant_id}")
    # 返回 None 表示不拦截，继续执行
    return None


agent = LlmAgent(
    # ...
    before_agent_callback=before_agent_callback,
)
```

用法与 [filter.md](./filter.md) 中的 Callback 一致，也可用于 `after_agent_callback`、`before_model_callback`、`before_tool_callback` 等。

关于 CustomAgent，见 [CustomAgent](./custom_agent.md)。

#### 用户反馈（Human-In-The-Loop）

`user_feedback_handler` 在前端提交工具相关反馈后调用，可用于日志、更新会话状态或改写传给 Agent 的工具结果文案。

```python
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.server.ag_ui import AgUiAgent
from trpc_agent_sdk.server.ag_ui import AgUiUserFeedBack


async def user_feedback_handler(feedback: AgUiUserFeedBack):
    """在前端提交工具结果后、结果传入 Agent 之前被调用。"""
    logger.info("User feedback received")
    logger.info(f"   Tool: {feedback.tool_name}")
    logger.info(f"   Message: {feedback.tool_message}")

    # feedback.session 是当前 Session 实例，可直接修改 state 字典
    feedback.session.state["last_tool"] = feedback.tool_name
    feedback.session.state["user_approval"] = feedback.tool_message
    # 修改 session 后必须调用此方法，框架才会将变更写回存储
    feedback.mark_session_modified()

    # 也可修改 tool_message 来改变最终传给 Agent 的工具结果文本
    # feedback.tool_message = "Modified message"


agui_agent = AgUiAgent(
    weather_agent,
    app_name="weather_app",
    user_feedback_handler=user_feedback_handler,
)
```

**注意：**

- 若修改了 `feedback.session`，需调用 `feedback.mark_session_modified()` 才会写回存储。  
- 可通过修改 `feedback.tool_message` 改变后续传给 Agent 的工具结果。  
- Handler 在工具结果提交、结果进入 Agent 之前执行。  

### 自定义 `AgUiAgent.run`

子类可重写 `run`，对输入或输出事件做前后处理：

```python
from typing import AsyncGenerator

from ag_ui.core import BaseEvent
from ag_ui.core import RunAgentInput
from starlette.requests import Request

from trpc_agent_sdk.server.ag_ui import AgUiAgent


class CustomAgUiAgent(AgUiAgent):
    async def run(
        self,
        input: RunAgentInput,
        http_request: Request | None = None,
    ) -> AsyncGenerator[BaseEvent, None]:
        # _preprocess_input / _postprocess_event 为自定义方法，
        # 基类 AgUiAgent 不包含这两个方法，需在子类中自行实现。
        modified_input = await self._preprocess_input(input)

        # 调用父类 run 执行 Agent 并产出 AG-UI 事件流
        async for event in super().run(modified_input, http_request=http_request):
            modified_event = await self._postprocess_event(event)
            if modified_event:
                yield modified_event

    # ---- 以下为子类自定义的示例占位 ----
    async def _preprocess_input(self, input: RunAgentInput) -> RunAgentInput:
        """对请求输入做预处理，例如注入额外 state 或过滤消息。"""
        return input

    async def _postprocess_event(self, event: BaseEvent) -> BaseEvent | None:
        """对产出的事件做后处理，返回 None 可跳过该事件。"""
        return event
```

### 取消（Cancel）与 SSE 断开

当客户端关闭 SSE 连接时，服务端可协作式取消运行，并在检查点保存部分结果。配置项 `cancel_wait_timeout`（默认 `3.0` 秒）表示等待取消完成的超时；若过短可能导致流式内容未完整落会话。

完整说明与客户端 `abort` 示例见 [examples/agui_with_cancel/README.md](../../../examples/agui_with_cancel/README.md)，组装方式见 [examples/agui_with_cancel/_agui_runner.py](../../../examples/agui_with_cancel/_agui_runner.py)。

## AG-UI 服务端模块导出

自 `server.ag_ui` 子模块导出的公开符号主要包括：

- `AgUiAgent`  
- `AgUiUserFeedBack`  
- `get_agui_http_req`  
- `AgUiManager`  
- `AgUiService`  
- `get_agui_service_registry`  

## 完整示例

- AGUI 基本流式与工具调用示例：[examples/agui/README.md](../../../examples/agui/README.md)  
- AGUI 支持取消示例：[examples/agui_with_cancel/README.md](../../../examples/agui_with_cancel/README.md)  
