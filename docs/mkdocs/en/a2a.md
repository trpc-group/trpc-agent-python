# A2A Usage Guide

The trpc-agent-python SDK includes built-in Agent-to-Agent (A2A) protocol support, allowing you to expose a local Agent as a standard A2A service or act as a client to invoke remote A2A Agents.

## 🚀 Key Benefits

- **Simple deployment**: Publish your Agent as an A2A HTTP service with a few lines of code
- **Streaming support**: Artifact-first streaming out of the box
- **Cancellation support**: Clients can cancel in-flight remote tasks at any time
- **Session continuity**: Multi-turn conversations automatically preserve context

---

## Installation

```bash
pip install -e ".[a2a]"
```

Python 3.12 is required.

---

## Server Deployment

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

### 2. Create the A2A Service and Start It

Use `TrpcA2aAgentService` to wrap the Agent as an A2A service, then assemble a Starlette app with `create_a2a_application` (which wraps the a2a-sdk 1.x route factories):

```python
# run_server.py
import uvicorn

# A2A service wrapper and convenience app assembly from the SDK
from trpc_agent_sdk.server.a2a import TrpcA2aAgentService
from trpc_agent_sdk.server.a2a import TrpcA2aAgentExecutorConfig
from trpc_agent_sdk.server.a2a import create_a2a_application

HOST = "127.0.0.1"
PORT = 18081


def create_a2a_service() -> TrpcA2aAgentService:
    from agent.agent import root_agent

    # Executor configuration (optional); configure user_id_extractor, event_callback, etc.
    executor_config = TrpcA2aAgentExecutorConfig()

    # Wrap the Agent as an A2A service implementing the A2A SDK AgentExecutor interface.
    # rpc_url is the public address advertised in the agent card; discovery-based
    # clients call this url.
    a2a_svc = TrpcA2aAgentService(
        service_name="weather_agent_service",  # Service identifier
        agent=root_agent,                      # Agent to deploy
        rpc_url=f"http://{HOST}:{PORT}",       # Public address for the agent card
        executor_config=executor_config,
    )
    a2a_svc.initialize()  # Required: builds Agent Card and completes initialization
    return a2a_svc


def serve():
    a2a_svc = create_a2a_service()

    # Assemble a Starlette app with the agent-card and JSON-RPC routes.
    app = create_a2a_application(a2a_svc)

    print(f"Starting A2A server on http://{HOST}:{PORT}")
    print(f"Agent card: http://{HOST}:{PORT}/.well-known/agent-card.json")

    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    serve()
```

After startup, the service publishes the Agent Card at `/.well-known/agent-card.json`; clients discover and invoke the Agent from that URL.

### 3. Server Essentials

| Topic | Description |
|------|------|
| `TrpcA2aAgentService` | Implements the A2A SDK `AgentExecutor` interface and can be passed directly as the executor to `create_a2a_application` |
| `rpc_url` | The public address advertised in `supported_interfaces[].url`; set it when the server knows its own address (see [Agent Card URL](#agent-card-url)) |
| `agent_card` | Built automatically from the Agent’s name, description, tools, etc.; can also be supplied manually |
| `initialize()` | Must be called before use; builds the Agent Card and completes internal setup |
| `create_a2a_application()` | Convenience wrapper that mounts the agent-card and JSON-RPC routes into a Starlette app. Optional: for full control, compose a2a-sdk’s `create_agent_card_routes` / `create_jsonrpc_routes` yourself |
| `enable_v0_3_compat` | Default `False`: 1.0-only; `/.well-known/agent.json` returns 404. Set `True` to also accept 0.3 clients on the same endpoint **and** publish the legacy card path |
| `session_service` | Optional; defaults to `InMemorySessionService`; can be replaced with a persistent implementation |
| `executor_config` | Optional; configures `user_id_extractor`, `event_callback`, `cancel_wait_timeout`, and related behavior |

#### Agent Card URL

The server does not know its own public address, so `supported_interfaces[].url` is left empty unless you provide one. The single configuration point is `TrpcA2aAgentService(rpc_url=...)` (or a fully custom `agent_card`):

```python
# The url is written into the agent card as-is.
svc = TrpcA2aAgentService(
    service_name="weather",
    agent=root_agent,
    rpc_url="https://agent.example.com/a2a",
)
```

`create_a2a_application()` derives the JSON-RPC mount path from that url (`https://agent.example.com/a2a` → `/a2a`, a bare origin → `/`), so the advertised path and the mounted path can never diverge. If no url is configured anywhere, a 1.0-only app still starts — direct JSON-RPC callers never read the card — but a warning is logged because discovery-based clients cannot call the agent. With `enable_v0_3_compat=True`, a missing url is a `ValueError`: 0.3 discovery cannot work without a top-level `url`.

---

## Upgrading from v0.3

The SDK moved from the a2a 0.3 protocol to 1.0. Two things matter for application code: **code changes** (below) and **runtime compatibility** (the compat switches). **Default discovery is 1.0-only**: the server publishes the Agent Card at `/.well-known/agent-card.json`. Legacy 0.3 clients (`A2ACardResolver`) fetch `/.well-known/agent.json`, which is **not** served (404) unless you set `enable_v0_3_compat=True`.

### Code migration (0.3 → 1.0)

a2a-sdk 0.3 → 1.0 was an architectural rewrite; several key call sites in business code must change:

| 0.3 usage | 1.0 usage | Notes |
|---|---|---|
| `from a2a.server.apps import A2AStarletteApplication` + `server = A2AStarletteApplication(agent_card=..., http_handler=...)` | `from trpc_agent_sdk.server.a2a import create_a2a_application` + `app = create_a2a_application(a2a_svc)` | **`A2AStarletteApplication` was removed in 1.0**; use the SDK convenience layer |
| `DefaultRequestHandler(agent_executor=..., task_store=...)` | Not needed (assembled inside `create_a2a_application`); only for custom handlers: `DefaultRequestHandler(agent_executor=..., task_store=..., agent_card=...)` | **`DefaultRequestHandler` gained a required `agent_card`** |
| `TrpcA2aAgentService(service_name=..., agent=..., executor_config=...)` | When the card is auto-built, add `rpc_url=...`; if you pass `agent_card` yourself, write 1.0 fields on that card (next row) | **`rpc_url` is required for auto-built cards**, see below |
| Hand-written / passed-in `AgentCard` (top-level `url`, `preferredTransport`) | `supported_interfaces[]` (`url`, `protocol_binding`, `protocol_version`) | **A custom `agent_card=` must use the 1.0 layout** as well; a top-level `url` alone is not enough. 0.3 clients still discover via top-level `url`; 1.0 uses `supported_interfaces` |

> The table above is what **business code** must change. a2a-sdk also removed other low-level APIs (`A2AClient` → `await create_client()`, `ClientFactory` sync → async), but they are hidden inside the SDK, so business code does not need to handle them. Business code usually only needs: add `rpc_url` (or rewrite a custom `agent_card` to the 1.0 layout) + switch to `create_a2a_application` on the server, and use `TrpcRemoteA2aAgent` on the client.

### Server: enable `enable_v0_3_compat` for legacy clients

A 1.0 server accepts only 1.0 clients by default (`/.well-known/agent-card.json` only; `/.well-known/agent.json` is 404). If you still have un-upgraded 0.3 clients in production, enable the switch so the server accepts both 1.0 and 0.3 traffic on the **same endpoint**:

```python
app = create_a2a_application(a2a_svc, enable_v0_3_compat=True)
```

With the switch on, the framework:

- publishes the same card at `/.well-known/agent.json` (the path 0.3 `A2ACardResolver` uses by default)
- appends a `protocol_version="0.3"` interface (reusing the same url) on the **card copy used for well-known discovery**
- enables 0.3 JSON-RPC decoding

Once that switch is on, legacy 0.3 clients need **no changes**. Without it, 0.3 clients cannot discover the service.

Compat **requires** a reachable interface url (`TrpcA2aAgentService(rpc_url=...)` or a custom `agent_card`). The 0.3 well-known card's top-level `url` is copied from that interface; an empty url cannot be invented. `create_a2a_application(..., enable_v0_3_compat=True)` raises `ValueError` if every interface url is empty, instead of starting in a silently undiscoverable state.

When this function builds the default `DefaultRequestHandler`, that handler receives the same patched copy. A **custom `request_handler` is not rewritten**: 0.3 JSON-RPC still works, but if the handler inspects its own `supported_interfaces` for versioning, **the caller must advertise a 0.3 interface on that card**.

### Client: `force_v0_3` (usually leave off)

The default follows the AgentCard. A complete 1.0 card or a complete 0.3 card does **not** need this flag.

Set `force_v0_3=True` only when you **know** the peer is a 0.3 server and following the card would fail (or the card should not be trusted), for example:

- A legacy 0.3 server with an empty card url (allowed in 0.3), so discovery yields no usable JSONRPC address
- The card does not match the process, and you know the peer accepts 0.3

The 0.3 transport posts to the card's JSONRPC url when present, otherwise `agent_base_url`.

```python
remote_agent = TrpcRemoteA2aAgent(
    name="weather_agent",
    agent_base_url="http://127.0.0.1:18081",
    force_v0_3=True,  # the peer is a 0.3 server; force the legacy wire
)
```

### The most important change: configure `rpc_url`

v0.3 did not require a card url, so old servers worked without one; **a 1.0 Agent Card must carry a reachable `supported_interfaces[].url`**, otherwise discovery-based clients fail with `no compatible transports found`. On upgrade, **make sure** to configure `rpc_url` when constructing `TrpcA2aAgentService` (or provide a custom `agent_card`) — see [Agent Card URL](#agent-card-url) above.

### Protocol combination matrix

| Scenario | Server | Client |
|---|---|---|
| **1.0 → 1.0** (recommended) | `create_a2a_application(a2a_svc)` | default |
| **0.3 client → 1.0 server** | `create_a2a_application(a2a_svc, enable_v0_3_compat=True)` | legacy 0.3 client, no changes |
| **1.0 client → 0.3 server** | legacy 0.3 server | complete 0.3 card: default; otherwise `force_v0_3=True` |

> On the client, `force_v0_3=True` means "the peer is 0.3; force the legacy wire". Leave it off in the usual case. Server `enable_v0_3_compat` still means "a 1.0 server also accepts 0.3 clients". Do not mix the two.

> Runnable example: [examples/a2a](../../../examples/a2a/README.md) — the same example covers all three combinations via two environment variables: `A2A_V03_COMPAT` on the server and `A2A_FORCE_V03` on the client.

---

## Client Usage

### 1. Create a Remote Agent and Invoke It

Use `TrpcRemoteA2aAgent` to connect to a remote A2A service. Provide the service base URL; the client discovers the Agent Card and establishes the connection automatically:

```python
# test_a2a.py
import asyncio
import uuid

from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.server.a2a import TrpcRemoteA2aAgent
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

---

## Task Cancellation

The SDK supports cancelling tasks while the Agent runs, including during LLM streaming and tool execution.

### Server Configuration

Use `cancel_wait_timeout` to cap how long the server waits for the Agent to finish cancellation:

```python
from trpc_agent_sdk.server.a2a import TrpcA2aAgentService
from trpc_agent_sdk.server.a2a import TrpcA2aAgentExecutorConfig

executor_config = TrpcA2aAgentExecutorConfig(
    cancel_wait_timeout=3.0,  # Max seconds to wait for Agent teardown after a cancel request
)

a2a_svc = TrpcA2aAgentService(
    service_name="weather_agent_cancel_service",
    agent=root_agent,
    executor_config=executor_config,  # Executor with cancel timeout
)
a2a_svc.initialize()
```

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

`TrpcA2aAgentExecutorConfig` configures server-side Agent executor behavior. Import from `trpc_agent_sdk.server.a2a`:

| Parameter | Type | Default | Description |
|------|------|--------|------|
| `cancel_wait_timeout` | `float` | `1.0` | Maximum seconds to wait when cancelling a task |
| `user_id_extractor` | `Callable[[RequestContext], str \| Awaitable[str]] \| None` | `None` | Callback to derive `user_id` from A2A request context; if unset, default logic based on `context_id` is used |
| `event_callback` | `Callable[[Event, RequestContext], Event \| None \| Awaitable[Event \| None]] \| None` | `None` | Invoked for each Event before it is converted to an A2A protocol event. See [Event callback](#event-callback-event_callback). |

Example:

```python
from trpc_agent_sdk.server.a2a import TrpcA2aAgentExecutorConfig

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
from trpc_agent_sdk.server.a2a import TrpcA2aAgentExecutorConfig


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
│  │  create_a2a_application (trpc-agent) │  │
│  │    └─ DefaultRequestHandler          │  │
│  │         └─ TrpcA2aAgentService       │  │
│  │              └─ LlmAgent (your Agent)│  │
│  └──────────────────────────────────────────┘  │
└────────────────────────────────────────────────┘
```

---

## Full Examples

- **Basics**: [examples/a2a](../../../examples/a2a/README.md) — A2A server deployment + a three-turn conversation
- **With cancellation**: [examples/a2a_with_cancel](../../../examples/a2a_with_cancel/README.md) — Cancel during LLM streaming and during tool execution
