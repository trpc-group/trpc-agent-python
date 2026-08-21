# A2A Usage Guide

The trpc-agent-python SDK includes built-in Agent-to-Agent (A2A) protocol support, allowing you to expose a local Agent as a standard A2A service or act as a client to invoke remote A2A Agents.

In this guide, **0.3** and **1.x (1.0)** are a2a-sdk major versions: 0.3 is the legacy stack (extra `[a2a]`, package `trpc_agent_sdk.server.a2a`); 1.x is the current protocol and the recommended stack (extra `[a2a-v1]`, package `trpc_agent_sdk.server.a2a_v1`). The two extras cannot be installed together.

## 🚀 Key Benefits

- **Simple deployment**: Publish your Agent as an A2A HTTP service with a few lines of code
- **Streaming support**: Artifact-first streaming out of the box
- **Cancellation support**: Clients can cancel in-flight remote tasks at any time
- **Session continuity**: Multi-turn conversations automatically preserve context

---

## Installation

Choose an a2a-sdk version via extras (**they cannot be installed together**). **Prefer `[a2a-v1]` (a2a-sdk 1.x)**; use `[a2a]` only for existing 0.3 code that has not been upgraded.

```bash
uv pip install -e ".[a2a-v1]"   # recommended: a2a-sdk 1.x (create_a2a_application / 1.0 agent cards)
                             # import: trpc_agent_sdk.server.a2a_v1
uv pip install -e ".[a2a]"      # existing 0.3 code: a2a-sdk 0.3
                             # import: trpc_agent_sdk.server.a2a
```

Python 3.12 is required.

---

## Server Deployment

Both versions share the same Agent definition. **Use 1.0 for new services** (extra `[a2a-v1]`); keep 0.3 only for deployments that have not been upgraded.

### 1. Define the Agent

First, define a standard `LlmAgent`:

```python
# agent/agent.py
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool


def get_weather_report(city: str) -> dict:
    """Fetch weather information for the given city."""
    weather_data = {
        "Beijing": {"city": "Beijing", "temperature": "25C", "condition": "Sunny", "humidity": "60%"},
        "Shanghai": {"city": "Shanghai", "temperature": "28C", "condition": "Cloudy", "humidity": "70%"},
    }
    return weather_data.get(city, {"city": city, "temperature": "Unknown", "condition": "Data not available"})


# Weather query Agent with model, instructions, and tools
root_agent = LlmAgent(
    name="weather_agent",
    description="A professional weather query assistant.",
    model=OpenAIModel(model_name="your-model", api_key="your-key", base_url="your-url"),
    instruction="You are a professional weather query assistant.",
    tools=[FunctionTool(get_weather_report)],  # Wrap plain functions as tools callable by the Agent
)
```

### 2. Create an a2a 1.0 server (recommended)

Install `[a2a-v1]` and import from `trpc_agent_sdk.server.a2a_v1`. Wrap the Agent with `TrpcA2aAgentService`, then assemble the HTTP app with `create_a2a_application`.

#### Create the A2A service and start it

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
        rpc_url=f"http://{HOST}:{PORT}",  # advertised in the Agent Card for discovery clients
        executor_config=TrpcA2aAgentExecutorConfig(),
    )
    a2a_svc.initialize()  # Required: builds Agent Card and completes initialization
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

After startup, the service publishes the Agent Card at `/.well-known/agent-card.json`. Full example: [examples/a2a_v1](../../../examples/a2a_v1/README.md).

#### Agent Card URL

The server does not know its public address. `supported_interfaces[].url` on the Agent Card must be set by the deployer. There is one configuration entry point: `TrpcA2aAgentService(rpc_url=...)`, or a fully custom `agent_card`.

```python
# Option 1: fixed hostname (recommended behind a reverse proxy / domain)
svc = TrpcA2aAgentService(
    service_name="weather",
    agent=root_agent,
    rpc_url="https://agent.example.com/a2a",
)

# Option 2: local / no fixed hostname — use the listen address as rpc_url
svc = TrpcA2aAgentService(
    service_name="weather",
    agent=root_agent,
    rpc_url="http://127.0.0.1:18081",
)
```

Discovery clients need a reachable `supported_interfaces[].url`, otherwise they fail with `no compatible transports found`. An empty url still starts the server (JSON-RPC callers that skip the card are fine), but a warning is logged. `create_a2a_application()` takes no separate path argument; the JSON-RPC route is mounted at the path of the card url (`https://x.com/a2a` → `/a2a`, origin-only → `/`) so the advertised path and the real endpoint stay in sync.

#### Server essentials

| Topic | Description |
|------|------|
| `create_a2a_application` | Assembles Agent Card and JSON-RPC routes; you can also compose a2a-sdk `DefaultRequestHandler` / `create_agent_card_routes` / `create_jsonrpc_routes` yourself |
| `rpc_url` | Set this when the card is auto-built; it becomes `supported_interfaces[].url` and discovery clients depend on it |
| `TrpcA2aAgentService` | Implements the A2A SDK `AgentExecutor` interface |
| `agent_card` | Built automatically from the Agent’s name, description, tools, etc.; a custom card must use 1.0 `supported_interfaces[]` |
| `initialize()` | Must be called before use; builds the Agent Card and completes internal setup |
| `session_service` | Optional; defaults to `InMemorySessionService`; can be replaced with a persistent implementation |
| `executor_config` | Optional; configures `user_id_extractor`, `event_callback`, `cancel_wait_timeout`, and related behavior |

#### Accepting legacy 0.3 clients

A 1.0 server speaks 1.0 by default and publishes `/.well-known/agent-card.json`. a2a-sdk 0.3.22+ `A2ACardResolver` already fetches that path, so a legacy client **can download the card**, but validation fails without a top-level `url`, and the server does not decode 0.3 JSON-RPC. `/.well-known/agent.json` is 404 by default.

If you still have un-upgraded 0.3 clients in production, enable the switch so the server accepts both 1.0 and 0.3 traffic on the **same endpoint**:

```python
from trpc_agent_sdk.server.a2a_v1 import create_a2a_application

app = create_a2a_application(a2a_svc, enable_v0_3_compat=True)
```

With the switch on, the framework:

- appends a `protocol_version="0.3"` interface (reusing the same url) on the **card copy used for well-known discovery**, so serialization includes the top-level `url` that 0.3 clients require
- enables 0.3 JSON-RPC decoding
- also publishes the same card at `/.well-known/agent.json` (for clients that still hard-code the deprecated path)

Once that switch is on, legacy 0.3 clients need **no changes**. Without it, 0.3 clients cannot use the service.

Compat **requires** a reachable interface url (`TrpcA2aAgentService(rpc_url=...)` or a custom `agent_card`). The 0.3 well-known card's top-level `url` is copied from that interface; an empty url cannot be invented. `create_a2a_application(..., enable_v0_3_compat=True)` raises `ValueError` if every interface url is empty.

When this function builds the default `DefaultRequestHandler`, that handler receives the same patched copy. A **custom `request_handler` is not rewritten**: 0.3 JSON-RPC still works, but if the handler inspects its own `supported_interfaces` for versioning, **the caller must advertise a 0.3 interface on that card**.

### 3. Create an a2a 0.3 server

Install `[a2a]` and import from `trpc_agent_sdk.server.a2a`. Wrap the Agent with `TrpcA2aAgentService`, then run it over standard HTTP with the A2A SDK’s `A2AStarletteApplication`.

#### Create the A2A service and start it

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
    a2a_svc.initialize()  # Required: builds Agent Card and completes initialization
    return a2a_svc


def serve():
    a2a_svc = create_a2a_service()

    request_handler = DefaultRequestHandler(
        agent_executor=a2a_svc,
        task_store=InMemoryTaskStore(),
    )

    server = A2AStarletteApplication(
        agent_card=a2a_svc.agent_card,  # primary path /.well-known/agent-card.json; alias /.well-known/agent.json
        http_handler=request_handler,
    )

    print(f"Starting A2A server on http://{HOST}:{PORT}")
    print(f"Agent card: http://{HOST}:{PORT}/.well-known/agent-card.json")

    uvicorn.run(server.build(), host=HOST, port=PORT)


if __name__ == "__main__":
    serve()
```

After startup, the service publishes the Agent Card at `/.well-known/agent-card.json` (and the deprecated alias `/.well-known/agent.json`). v0.3 does not require a card url. Full example: [examples/a2a](../../../examples/a2a/README.md).

#### Server essentials

| Topic | Description |
|------|------|
| `A2AStarletteApplication` | a2a-sdk 0.3 HTTP assembly; start with `server.build()` |
| `TrpcA2aAgentService` | Implements the A2A SDK `AgentExecutor` interface and can be passed directly as the executor to `DefaultRequestHandler` |
| `agent_card` | Built automatically from the Agent’s name, description, tools, etc.; a custom card uses the 0.3 layout (top-level `url`, `preferredTransport`) |
| `initialize()` | Must be called before use; builds the Agent Card and completes internal setup |
| `session_service` | Optional; defaults to `InMemorySessionService`; can be replaced with a persistent implementation |
| `executor_config` | Optional; configures `user_id_extractor`, `event_callback`, `cancel_wait_timeout`, and related behavior |

---

## Client Usage

Prefer extra `[a2a-v1]` and import from `trpc_agent_sdk.server.a2a_v1`. A 0.3 client only needs to switch the import to `trpc_agent_sdk.server.a2a`; discovery is still `/.well-known/agent-card.json`.

### 1. Create a Remote Agent and Invoke It

Use `TrpcRemoteA2aAgent` to connect to a remote A2A service. Provide the service base URL; the client discovers the Agent Card and establishes the connection automatically:

```python
# test_a2a.py
import asyncio
import uuid

from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.server.a2a_v1 import TrpcRemoteA2aAgent
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, Part

# Remote A2A service URL (matches the server bind address)
AGENT_BASE_URL = "http://127.0.0.1:18081"


async def main():
    # Remote Agent with service URL; discovers Agent Card from /.well-known/agent-card.json
    remote_agent = TrpcRemoteA2aAgent(
        name="weather_agent",
        agent_base_url=AGENT_BASE_URL,
        description="Professional weather query assistant",
    )
    await remote_agent.initialize()  # Async init: discover Agent Card, create A2A client

    # Session service and Runner; same usage as with a local Agent
    session_service = InMemorySessionService()
    runner = Runner(app_name="a2a_demo", agent=remote_agent, session_service=session_service)

    user_id = "demo_user"
    session_id = str(uuid.uuid4())  # Unique ID per session; reuse the same ID across turns

    # Pass business parameters (e.g. user_id) to the server via metadata
    run_config = RunConfig(agent_run_config={
        "metadata": {"user_id": user_id},
    })

    user_content = Content(parts=[Part.from_text(text="What's the weather in Beijing?")])

    # Streaming invocation; handle remote Agent events one by one
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

### 2. Multi-Turn Conversations

Reuse the same `session_id` to preserve context:

```python
queries = [
    "Hello, my name is Alice.",
    "What's the weather in Beijing?",
    "What's my name and what did I just ask?",  # Agent can recall prior turns
]

for query in queries:
    # New Runner per turn, same session_service to keep session state
    runner = Runner(app_name="a2a_demo", agent=remote_agent, session_service=session_service)
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,  # Same session_id; server maintains context
        new_message=Content(parts=[Part.from_text(text=query)]),
        run_config=run_config,
    ):
        # Handle events...
        pass
```

### 3. Passing Custom Parameters

Send `metadata` and `configuration` to the remote service via `RunConfig.agent_run_config`:

```python
from trpc_agent_sdk.configs import RunConfig

# Metadata key-value pairs are forwarded with the A2A request
# Server can read them via user_id_extractor or RequestContext.metadata
run_config = RunConfig(
    agent_run_config={
        "metadata": {
            "user_id": "12345",           # User identifier; server may use for session isolation
            "session_type": "premium",    # Custom business fields
            "custom_field": "value",
        },
    }
)
```

The server can read this metadata in the `user_id_extractor` callback (see the configuration section below).

### 4. Client Essentials

| Topic | Description |
|------|------|
| `TrpcRemoteA2aAgent` | Extends `BaseAgent`; use with `Runner` like a local Agent |
| `agent_base_url` | HTTP base URL of the remote A2A service; client discovers the Agent Card from `/.well-known/agent-card.json` |
| `initialize()` | Async initialization: Agent Card discovery and client construction |
| `agent_card` / `a2a_client` | Optional; pass an existing AgentCard or A2AClient to skip auto-discovery |
| `RunConfig` | Business parameters (e.g. `user_id`) via `metadata`; server reads them in callbacks |

### 5. Talking to a 0.3 server (`force_v0_3`)

The default follows the AgentCard. A complete 1.0 card or a complete 0.3 card does **not** need this flag. After discovery, a 1.0 client fills an empty JSONRPC interface url from `agent_base_url`, so a legacy 0.3 server with an empty card url usually does not need the flag either.

Set `force_v0_3=True` only when you **know** the peer is a 0.3 server and following the card would fail (or the card should not be trusted) — for example the card does not match the process, and you know the peer accepts 0.3.

With `force_v0_3=True`, the transport posts to the card's JSONRPC url when present, otherwise `agent_base_url`.

```python
from trpc_agent_sdk.server.a2a_v1 import TrpcRemoteA2aAgent

remote_agent = TrpcRemoteA2aAgent(
    name="weather_agent",
    agent_base_url="http://127.0.0.1:18081",
    force_v0_3=True,  # the peer is a 0.3 server; force the legacy wire
)
```

### 6. Protocol combination matrix

| Scenario | Server | Client |
|---|---|---|
| **1.0 → 1.0** (recommended) | `create_a2a_application(a2a_svc)` | default |
| **0.3 client → 1.0 server** | `create_a2a_application(a2a_svc, enable_v0_3_compat=True)` | legacy 0.3 client, no changes |
| **1.0 client → 0.3 server** | legacy 0.3 server | complete 0.3 card: default; `force_v0_3=True` if the card cannot be trusted |

> On the client, `force_v0_3=True` means "the peer is 0.3; force the legacy wire". Leave it off in the usual case. Server `enable_v0_3_compat` still means "a 1.0 server also accepts 0.3 clients". Do not mix the two.

> Runnable example: [examples/a2a_v1](../../../examples/a2a_v1/README.md) — `A2A_V03_COMPAT` on the server and `A2A_FORCE_V03` on the client cover the three protocol combinations. The 0.3 example is [examples/a2a](../../../examples/a2a/README.md).

---

## Task Cancellation

The SDK supports cancelling tasks while the Agent runs, including during LLM streaming and tool execution.

### Server Configuration

Use `cancel_wait_timeout` to cap how long the server waits for the Agent to finish cancellation:

```python
from trpc_agent_sdk.server.a2a_v1 import TrpcA2aAgentExecutorConfig
from trpc_agent_sdk.server.a2a_v1 import TrpcA2aAgentService

executor_config = TrpcA2aAgentExecutorConfig(
    cancel_wait_timeout=3.0,  # Max seconds to wait for Agent teardown after a cancel request
)

a2a_svc = TrpcA2aAgentService(
    service_name="weather_agent_cancel_service",
    agent=root_agent,
    rpc_url="http://127.0.0.1:18081",
    executor_config=executor_config,  # Executor with cancel timeout
)
a2a_svc.initialize()
```

For 0.3, import from `trpc_agent_sdk.server.a2a` and omit `rpc_url`.

### Client Cancellation

Issue a cancel request with `runner.cancel_run_async()`:

```python
from trpc_agent_sdk.events import AgentCancelledEvent

# From another coroutine: sends cancel_task over A2A
success = await runner.cancel_run_async(
    user_id=user_id,
    session_id=session_id,
    timeout=3.0,  # Client-side wait for cancellation to complete
)

# The in-flight run_async iterator receives AgentCancelledEvent
async for event in runner.run_async(...):
    if isinstance(event, AgentCancelledEvent):
        print(f"Run was cancelled: {event.error_message}")
        break
    # Handle other events normally...
```

### Cancellation Flow

```text
Client                              Server
  │                                  │
  │── runner.run_async() ──────────→ │ Start Agent execution
  │← streaming events ←──────────────│
  │                                  │
  │── runner.cancel_run_async() ──→ │ cancel_task request
  │                                  │── wait cancel_wait_timeout
  │← AgentCancelledEvent ←──────────│
  │                                  │
  │── runner.run_async() (cont.) ──→ │ Continue conversation on same session
```

### Session Recovery After Cancellation

The same `session_id` remains usable after cancellation. The SDK automatically:

- Retains completed tool call results
- Clears incomplete tool calls
- Records cancellation state in the session

### Timeout Settings

| Location | Parameter | Default | Description |
|----------|------|--------|------|
| Server | `cancel_wait_timeout` | 1.0 | Server wait for backend Agent cancellation to finish |
| Client | `timeout` | 1.0 | Client wait for `cancel_run_async` to complete |

Use matching timeouts on both sides when possible.

---

## TrpcA2aAgentExecutorConfig Options

`TrpcA2aAgentExecutorConfig` configures server-side Agent executor behavior. Import from `trpc_agent_sdk.server.a2a_v1` (1.0) or `trpc_agent_sdk.server.a2a` (0.3):

| Parameter | Type | Default | Description |
|------|------|--------|------|
| `cancel_wait_timeout` | `float` | `1.0` | Maximum seconds to wait when cancelling a task |
| `user_id_extractor` | `Callable[[RequestContext], str \| Awaitable[str]] \| None` | `None` | Callback to derive `user_id` from A2A request context; if unset, default logic based on `context_id` is used |
| `event_callback` | `Callable[[Event, RequestContext], Event \| None \| Awaitable[Event \| None]] \| None` | `None` | Invoked for each Event before it is converted to an A2A protocol event. See [Event callback](#event-callback-event_callback). |

Example:

```python
from trpc_agent_sdk.server.a2a_v1 import TrpcA2aAgentExecutorConfig

# Full example: user_id extraction, event callback, and cancel timeout
executor_config = TrpcA2aAgentExecutorConfig(
    user_id_extractor=custom_user_id_extractor,  # Custom user_id extraction
    event_callback=custom_event_callback,          # Event interception
    cancel_wait_timeout=2.0,                       # Cancel wait timeout (seconds)
)
```

---

## Custom user_id Extraction

By default, `user_id` is derived from the A2A request’s `context_id`. To read `user_id` from client-supplied `metadata`, configure `user_id_extractor`:

```python
from a2a.server.agent_execution import RequestContext
from trpc_agent_sdk.server.a2a_v1 import TrpcA2aAgentExecutorConfig


def custom_user_id_extractor(request: RequestContext) -> str:
    """Extract user_id from A2A request metadata.

    Clients pass user_id via RunConfig metadata;
    this callback reads it on the server for session isolation and user identification.
    """
    if request and request.metadata:
        user_id = request.metadata.get("user_id")
        if user_id:
            return user_id
    # Fallback: default user_id from context_id
    return f"A2A_USER_{request.context_id}"


executor_config = TrpcA2aAgentExecutorConfig(
    user_id_extractor=custom_user_id_extractor,
)
```

Client passes `user_id` via `RunConfig`:

```python
# Client sends user_id; server custom_user_id_extractor can read it
run_config = RunConfig(agent_run_config={
    "metadata": {"user_id": "my_user_123"},
})
```

---

## Event callback (`event_callback`)

`event_callback` lets the server intercept each Event **before** it is converted to an A2A protocol event and pushed to the client—for logging, filtering, or modifying content.

### Callback signature

```python
from trpc_agent_sdk.events import Event
from a2a.server.agent_execution import RequestContext

def event_callback(event: Event, context: RequestContext) -> Event | None:
    ...
```

| Parameter | Description |
|------|------|
| `event` | The current `Event`, including `content` (text / function_call / function_response), `partial` (streaming chunk flag), `custom_metadata`, etc. |
| `context` | A2A `RequestContext` with `task_id`, `context_id`, `metadata`, etc. |
| **Return value** | Return an `Event` to continue processing; return `None` to drop the event (not sent to the client) |

> The callback may be `async def`; the framework will `await` it.

### Scenario 1: Logging

```python
def custom_event_callback(event: Event, context: RequestContext) -> Event | None:
    # Detect streaming tool-call events
    if event.is_streaming_tool_call():
        print(f"[Event Callback] Streaming tool call detected: task={context.task_id}")

    # Check streaming chunks for function_call
    if event.partial and event.content and event.content.parts:
        for part in event.content.parts:
            if part.function_call:
                print(f"[Event Callback] Tool invocation: {part.function_call.name}")

    return event  # Passthrough, no modification
```

### Scenario 2: Filtering events

Return `None` to skip specific events:

```python
def custom_event_callback(event: Event, context: RequestContext) -> Event | None:
    # Drop non-visible events; None means skip (client never sees them)
    if not event.visible:
        return None
    return event
```

### Scenario 3: Copy and modify the event

> **Important**: When mutating an event, **deep-copy first** to avoid mutating objects owned by the framework. `Event` is a Pydantic v2 BaseModel; use `model_copy(deep=True)` for a deep copy.

```python
def custom_event_callback(event: Event, context: RequestContext) -> Event | None:
    if event.custom_metadata is None:
        # Deep copy before mutating framework-held state
        modified_event = event.model_copy(deep=True)
        modified_event.custom_metadata = {
            "source": "a2a_server",
            "task_id": context.task_id,
        }
        return modified_event  # Return modified copy
    return event
```

### Notes

1. **Always deep-copy before mutating**: `event.model_copy(deep=True)` recursively copies nested objects so the original event is not accidentally modified
2. **Returning `None` drops the event**: It is not converted to an A2A protocol event and the client does not receive it
3. **Callback runs before protocol conversion**: The returned event replaces the original for subsequent A2A conversion
4. **Performance**: The callback runs per event; under streaming, event rate is high—keep the handler lightweight

---

## Upgrading from 0.3 to 1.0

If you want to upgrade a service from 0.3 to 1.0, the official a2a-sdk is not fully backward compatible, so some code changes are required.

Everyday client usage is the same on both stacks; usually you only switch the import (`trpc_agent_sdk.server.a2a` → `trpc_agent_sdk.server.a2a_v1`). The breaking changes are almost all on the server assembly path. The two extras cannot be installed together; upgrade with `uv pip install -e ".[a2a-v1]"`.

| 0.3 usage | 1.0 usage | Notes |
|---|---|---|
| `from a2a.server.apps import A2AStarletteApplication` + `server = A2AStarletteApplication(...)` | `from trpc_agent_sdk.server.a2a_v1 import create_a2a_application` + `app = create_a2a_application(a2a_svc)` | **`A2AStarletteApplication` was removed in 1.0**. The result is a Starlette app: `uvicorn.run(app, ...)`, **no** `server.build()` |
| `DefaultRequestHandler(agent_executor=..., task_store=...)` | Usually assembled inside `create_a2a_application`; a custom handler must pass `agent_card=...` | **`DefaultRequestHandler` gained a required `agent_card`** |
| `TrpcA2aAgentService(service_name=..., agent=...)` | When the card is auto-built, add `rpc_url=...` | See [Agent Card URL](#agent-card-url) |
| Hand-written / passed-in `AgentCard` (top-level `url`, `preferredTransport`) | `supported_interfaces[]` (`url`, `protocol_binding`, `protocol_version`) | A custom card must use the 1.0 layout; a top-level `url` alone is not enough |

a2a-sdk also removed other low-level APIs (`A2AClient` → `await create_client()`); they are hidden inside the SDK, so business code does not need to handle them. If you use other incompatible APIs, follow the latest official a2a-sdk usage.

To keep talking to peers that are still on 0.3, see [Accepting legacy 0.3 clients](#accepting-legacy-03-clients) and [Talking to a 0.3 server](#5-talking-to-a-03-server-force_v0_3).

---

## Architecture Overview

```text
┌────────────────────────────────────────────────┐
│                  Client                        │
│  ┌──────────────────────────────────────────┐  │
│  │        TrpcRemoteA2aAgent               │  │
│  │    (connects to remote A2A service)      │  │
│  └──────────────┬───────────────────────────┘  │
│                 │ A2A Protocol (HTTP)           │
└─────────────────┼──────────────────────────────┘
                  │
┌─────────────────▼──────────────────────────────┐
│                  Server                        │
│  ┌──────────────────────────────────────────┐  │
│  │  1.0: create_a2a_application (recommended)│  │
│  │  0.3: A2AStarletteApplication            │  │
│  │    └─ DefaultRequestHandler          │  │
│  │         └─ TrpcA2aAgentService       │  │
│  │              └─ LlmAgent (your Agent)│  │
│  └──────────────────────────────────────────┘  │
└────────────────────────────────────────────────┘
```

1.0 imports from `trpc_agent_sdk.server.a2a_v1` and uses `create_a2a_application`; 0.3 imports from `trpc_agent_sdk.server.a2a` and uses `A2AStarletteApplication`. The rest of the execution chain is the same.

---

## Full Examples

- **Basics (1.x, recommended)**: [examples/a2a_v1](../../../examples/a2a_v1/README.md) — `create_a2a_application` + a three-turn conversation
- **With cancellation (1.x)**: [examples/a2a_v1_with_cancel](../../../examples/a2a_v1_with_cancel/README.md)
- **Basics (0.3)**: [examples/a2a](../../../examples/a2a/README.md) — A2A server deployment + a three-turn conversation
- **With cancellation (0.3)**: [examples/a2a_with_cancel](../../../examples/a2a_with_cancel/README.md) — Cancel during LLM streaming and during tool execution
