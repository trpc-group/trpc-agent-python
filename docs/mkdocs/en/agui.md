# AG-UI Usage Guide

[AG-UI](https://github.com/ag-ui-protocol/ag-ui) is a protocol for Agent–frontend interaction: it is event-driven and pushes tool invocations, model output, and other behaviors to the frontend as distinct Events.

- **Live status display**: the frontend can observe and render the Agent’s current execution state  
- **Streaming output and progress**: supports streaming text, tool-call progress, and other real-time presentation  
- **Human-in-the-loop**: execution can pause to wait for user confirmation or feedback in the frontend  

[CopilotKit](https://github.com/CopilotKit/CopilotKit) currently provides multiple UI components that interact with Agents via AG-UI.

This repository exposes an AG-UI server bridge under `server/ag_ui`: `AgUiAgent` aligns a single AG-UI request with the internal `Runner.run_async`; `EventTranslator` maps framework events to AG-UI standard events; `AgUiService` registers URIs and a POST streaming endpoint; `AgUiManager` aggregates multiple services and listens externally with **FastAPI + Uvicorn**. The frontend handles presentation and interaction (CopilotKit is optional) and connects to the AG-UI service through an AG-UI event stream (e.g. SSE).

## Installation

From the repository root after cloning (enable the `ag-ui` optional extra):

```bash
pip install -e ".[ag-ui]"
```

Python 3.12 is required. Core dependencies include `ag-ui-protocol` and `FastAPI/Uvicorn`.

## Quick Start

Mount `AgUiAgent` with `AgUiService`, pass the same FastAPI application to `AgUiManager`, then call `run(host, port)`.

- `AgUiService(service_name, app=fastapi_app)`: registers each Agent’s POST route on the supplied `app`.  
- `add_agent("/your_uri", agui_agent)`: the Agent URI must not conflict with other custom routes on the same application.  
- To add business APIs on the same FastAPI app, register routes on that `app` before calling `manager.run` (the sample adds `GET /health` via `AguiRunner`).

Below is a minimal version consistent with the `_agui_runner.py` / `run_server.py` examples (omitting `/health` and similar details): create `FastAPI` and `AgUiManager`, pass the same `app` to `AgUiService`, register the Agent, then `set_app` and `run`.

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

# AgUiManager aggregates multiple AgUiService instances and starts the FastAPI app with Uvicorn
manager = AgUiManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan: release background execution state held by manager on shutdown."""
    logger.info("AG-UI server starting")
    yield
    logger.info("AG-UI server shutting down")
    await manager.close()


app = FastAPI(title="AG-UI demo", lifespan=lifespan)


def serve():
    from agent.agent import root_agent  # Your custom root Agent (e.g. LlmAgent)

    app_name = "weather_app"
    service_name = "weather_agent_service"
    uri = "/weather_agent"  # Frontend POSTs to this path to trigger Agent execution

    # In-memory sessions; suitable for dev/debug; replace with RedisSessionService in production
    session_service = InMemorySessionService()

    # AgUiService : binds to the FastAPI app; add_agent registers POST routes automatically
    agui_service = AgUiService(service_name, app=app)

    # Create AgUiAgent : first positional arg is a BaseAgent instance; remaining args are keyword-only
    agui_agent = AgUiAgent(
        root_agent,
        app_name=app_name,
        session_service=session_service,
    )

    # Mount the Agent at the given URI
    agui_service.add_agent(uri, agui_agent)
    # Register the service with the manager
    manager.register_service(service_name, agui_service)
    # After set_app, manager.run invokes uvicorn.run(app, host, port) internally
    manager.set_app(app)
    manager.run(HOST, PORT)


if __name__ == "__main__":
    serve()
```

For a more complete, runnable layout (including `await manager.close()` in the FastAPI lifespan), see:

- [examples/agui/run_server.py](../../../examples/agui/run_server.py)  
- [examples/agui/_agui_runner.py](../../../examples/agui/_agui_runner.py)  

Implementation details and directory layout are described in the [README](../../../trpc_agent_sdk/server/ag_ui/README.md) under the repository’s AG-UI server implementation.

## Advanced Usage

### `AgUiAgent` configuration overview

#### Application name and user ID

Supports static values or dynamic resolution from `RunAgentInput` for session and application scoping.

```python
from ag_ui.core import RunAgentInput

from trpc_agent_sdk.server.ag_ui import AgUiAgent

# Option 1: static values — all requests share the same app_name / user_id
agui_agent = AgUiAgent(
    weather_agent,
    app_name="weather_app",  # Fixed application name
    user_id="user_123",      # Fixed user ID
)

# Option 2: dynamic extraction — parse from each request’s RunAgentInput
# RunAgentInput is defined by ag-ui-protocol and includes thread_id, state, messages, etc.
def extract_app_name(inp: RunAgentInput) -> str:
    # inp.state is a custom state dict sent from the frontend with the request
    return inp.state.get("app_name", "default_app")

def extract_user_id(inp: RunAgentInput) -> str:
    return inp.state.get("user_id", f"thread_user_{inp.thread_id}")

# Note: app_name and app_name_extractor cannot both be set (same for user_id)
agui_agent = AgUiAgent(
    weather_agent,
    app_name_extractor=extract_app_name,
    user_id_extractor=extract_user_id,
)
```

#### Sessions and memory (storage)

In-memory sessions are the default; production can use Redis or similar (you supply the Redis URL and credentials).

```python
from trpc_agent_sdk.memory import RedisMemoryService
from trpc_agent_sdk.server.ag_ui import AgUiAgent
from trpc_agent_sdk.sessions import RedisSessionService

redis_url = "redis://localhost:6379/0"

# Redis-backed sessions and memory (recommended for production)
# use_in_memory_services=False prevents the framework from auto-creating in-memory services for omitted deps
agui_agent = AgUiAgent(
    weather_agent,
    app_name="weather_app",
    session_service=RedisSessionService(db_url=redis_url),
    memory_service=RedisMemoryService(db_url=redis_url, enabled=True),
    use_in_memory_services=False,
)

# Session timeout and cleanup (managed internally by SessionManager)
# session_timeout_seconds: sessions idle longer than this are marked expired
# cleanup_interval_seconds: interval for periodic cleanup of expired sessions
agui_agent = AgUiAgent(
    weather_agent,
    app_name="weather_app",
    session_timeout_seconds=3600,      # 1 hour
    cleanup_interval_seconds=600,       # 10 minutes
)
```

#### Execution and tool timeouts, concurrency

```python
agui_agent = AgUiAgent(
    weather_agent,
    app_name="weather_app",
    execution_timeout_seconds=1200,    # Max single Agent run: 20 minutes (default 600s)
    tool_timeout_seconds=600,          # Max single tool call: 10 minutes (default 300s)
    max_concurrent_executions=20,      # Max concurrent executions (default 10)
)
```

#### Accessing the HTTP request from Agent / callbacks

The framework stores the current request’s HTTP object in the invocation’s `run_config`; use `get_agui_http_req` in a custom Agent or callback to read it (e.g. auth headers, tenant ID, request ID).

In a custom `BaseAgent` subclass:

```python
from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.server.ag_ui import get_agui_http_req


class MyCustomAgent(BaseAgent):
    async def _run_async_impl(self, ctx: InvocationContext):
        # get_agui_http_req reads the HTTP Request from ctx.run_config; may be None
        request = get_agui_http_req(ctx)
        auth_token = request.headers.get("authorization", "") if request else ""
        tenant_id = request.headers.get("x-tenant-id", "") if request else ""
        ...
```

In callbacks:

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.server.ag_ui import get_agui_http_req


async def before_agent_callback(context: InvocationContext):
    # Callbacks and custom Agents use the same API to obtain the HTTP Request
    request = get_agui_http_req(context)
    request_id = request.headers.get("x-request-id", "") if request else ""
    tenant_id = request.headers.get("x-tenant-id", "") if request else ""
    print(f"request_id={request_id}, tenant_id={tenant_id}")
    # Return None to not intercept; continue execution
    return None


agent = LlmAgent(
    # ...
    before_agent_callback=before_agent_callback,
)
```

Usage matches Callbacks in [filter.md](./filter.md); it also applies to `after_agent_callback`, `before_model_callback`, `before_tool_callback`, etc.

For CustomAgent, see [CustomAgent](./custom_agent.md).

#### User feedback (human-in-the-loop)

`user_feedback_handler` runs after the frontend submits tool-related feedback; use it for logging, updating session state, or rewriting the tool result text passed to the Agent.

```python
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.server.ag_ui import AgUiAgent
from trpc_agent_sdk.server.ag_ui import AgUiUserFeedBack


async def user_feedback_handler(feedback: AgUiUserFeedBack):
    """Invoked after the frontend submits a tool result and before the result is passed to the Agent."""
    logger.info("User feedback received")
    logger.info(f"   Tool: {feedback.tool_name}")
    logger.info(f"   Message: {feedback.tool_message}")

    # feedback.session is the current Session instance; you may mutate the state dict directly
    feedback.session.state["last_tool"] = feedback.tool_name
    feedback.session.state["user_approval"] = feedback.tool_message
    # After mutating the session, call this so the framework persists changes to storage
    feedback.mark_session_modified()

    # You may also change tool_message to alter the final tool result text sent to the Agent
    # feedback.tool_message = "Modified message"


agui_agent = AgUiAgent(
    weather_agent,
    app_name="weather_app",
    user_feedback_handler=user_feedback_handler,
)
```

**Notes:**

- If you modify `feedback.session`, call `feedback.mark_session_modified()` for changes to be persisted.  
- Changing `feedback.tool_message` alters the tool result subsequently passed to the Agent.  
- The handler runs after the tool result is submitted and before it enters the Agent.  

### Customizing `AgUiAgent.run`

Subclasses may override `run` to preprocess inputs or postprocess output events:

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
        # _preprocess_input / _postprocess_event are custom hooks;
        # the base AgUiAgent does not define them — implement them in the subclass.
        modified_input = await self._preprocess_input(input)

        # Delegate to the parent run to execute the Agent and emit the AG-UI event stream
        async for event in super().run(modified_input, http_request=http_request):
            modified_event = await self._postprocess_event(event)
            if modified_event:
                yield modified_event

    # ---- Example placeholders for subclass-defined hooks ----
    async def _preprocess_input(self, input: RunAgentInput) -> RunAgentInput:
        """Preprocess request input, e.g. inject extra state or filter messages."""
        return input

    async def _postprocess_event(self, event: BaseEvent) -> BaseEvent | None:
        """Postprocess emitted events; return None to drop an event."""
        return event
```

### Cancel and SSE disconnect

When the client closes the SSE connection, the server can cooperatively cancel the run and checkpoint partial results. The `cancel_wait_timeout` setting (default `3.0` seconds) is how long to wait for cancellation to finish; if it is too short, streamed content may not be fully persisted to the session.

Full details and a client `abort` example: [examples/agui_with_cancel/README.md](../../../examples/agui_with_cancel/README.md); wiring: [examples/agui_with_cancel/_agui_runner.py](../../../examples/agui_with_cancel/_agui_runner.py).

## AG-UI server module exports

Public symbols exported from the `server.ag_ui` submodule include:

- `AgUiAgent`  
- `AgUiUserFeedBack`  
- `get_agui_http_req`  
- `AgUiManager`  
- `AgUiService`  
- `get_agui_service_registry`  

## Complete examples

- AGUI basic streaming and tool calls example: [examples/agui/README.md](../../../examples/agui/README.md)  
- AGUI cancel support example: [examples/agui_with_cancel/README.md](../../../examples/agui_with_cancel/README.md)  
