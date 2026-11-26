# Agent Cancel Mechanism

During Agent execution, the output may sometimes not meet the user's requirements. In such cases, users often interrupt the Agent execution, provide partial feedback (indicating which outputs before the interruption were unsatisfactory and what should be done next), and then let the Agent continue execution.

For this scenario, the trpc-agent-python framework provides a Cancel mechanism that allows cancelling an Agent's ongoing operations while preserving partial content (content being streamed by the LLM, tool execution results in progress, etc.). This mechanism is based on a checkpoint design. During execution, each Agent checks at checkpoint locations (after an LLM streaming output chunk, after a tool call completes, etc.) whether the current Agent should be terminated. If termination is required, an exception is thrown, and the framework records and saves the partial information to the session history.

This capability has been integrated into all Agents provided by the framework. Custom Agents implemented by other services can also be easily integrated.

| Module Type | Module Name | Cancel Support | Description |
|---------|---------|-------------|------|
| Single Agent | `LlmAgent` | ✅ | Checkpoints set at LLM streaming output, tool execution, and other locations |
| Single Agent | `LangGraphAgent` | ✅ | Checkpoints set during LangGraph streaming output |
| Single Agent | `ClaudeAgent` | ✅ | Checkpoints set during claude-sdk streaming output |
| Single Agent | `TrpcRemoteA2aAgent` | ✅ | Checkpoints set during HTTP streaming output |
| Multi Agent | `ChainAgent` | ✅ | Exception propagated from its sub-Agents |
| Multi Agent | `ParallelAgent` | ✅ | Execution cancelled when any sub-Agent throws an exception |
| Multi Agent | `CycleAgent` | ✅ | Exception propagated from its sub-Agents |
| Multi Agent | `TeamAgent` | ✅ | Cancellable during both Leader and Member execution |
| Agent Service | `TrpcA2aAgentService` | ✅ | Cancels remote Agent execution via the A2A protocol's cancel_task interface |
| Agent Service | `AgUiService` | ✅ | Agent automatically cancels execution upon SSE connection disconnect detection |


## Agent Cancel Mechanism Design Overview

### Architecture Design

Cancel and Steering are both integrated through the Runner. `run_async()` runs the Agent, `cancel_run_async()` requests cancellation, and `send_steering()` adds user input while a run is active. The Runner registers the run, drives the Agent, and performs cleanup on cancellation; the Agent only checks the control state at checkpoints.

Overall architecture:

```text
┌─────────────────────────────────────────────────────────────────────────┐
│   Entry layer: user code / A2A / AG-UI / custom services                │
│                                                                         │
│   run_async()          cancel_run_async()          send_steering()      │
└───────────────────────────────────┬─────────────────────────────────────┘
│                                   │ calls the Runner                    │
│                                   ▼                                     │
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                         │
│   ┌──────────────────────────┐           ┌────────────────────────┐     │
│   │ Runner                   │           │ Agent execution        │     │
│   │ register_run(SessionKey) │ drives ─► │ checkpoints:           │     │
│   │ append_event             │           │   raise_if_cancelled() │     │
│   │ unregister_run           │           │   consume_steering()   │     │
│   └──────────────────────────┘           └────────────────────────┘     │
│                                                                         │
└───────────────────────────────────┬─────────────────────────────────────┘
│                                   │ read / write control state          │
│                                   ▼                                     │
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                         │
│   ┌──────────────────────┐           ┌───────────────────────────┐      │
│   │ Single worker        │           │ Multi-worker              │      │
│   │ _LocalManager        │           │ Unix Socket control plane │      │
│   │   ├─ cancel_event    │           │ master: _UnixSocketServer │      │
│   │   └─ steering: deque │           │ worker: one UDS per run   │      │
│   └──────────────────────┘           └───────────────────────────┘      │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

After `run_async()` is called, the Runner registers the execution and identifies it with `(app_name, user_id, session_id)`.

**Cancel mechanism**

After `cancel_run_async()` is called, the Runner sets the cancel signal for that run. With a single worker, the signal lives in process memory. In a multi-worker deployment, the master calls `init_unix_socket()` before forking, and each worker reaches the master's state over Unix Socket. When the Agent observes the signal at a checkpoint it raises `RunCancelledException`; the Runner catches it, persists the streamed text and completed tool results, appends a skipped result for tools that did not run, and finally returns `AgentCancelledEvent`.

**Steering mechanism**

After `send_steering()` is called, the message enters the steering queue of that run. Before the next LLM request, the Agent drains the queue and writes the messages into the session as a hidden user event, so the model sees them on the next request. Messages that arrive during the final LLM request are still delivered.

**Server integration**

- **A2A**: the client calls `cancel_task`, which the framework maps to `runner.cancel_run_async()`. Steering is outside the A2A protocol, so register your own `POST /steering` route. In a multi-worker deployment, the worker that receives the request forwards cancel and steering to the master over Unix Socket; the worker that owns the run reads them at its next checkpoint.
- **AG-UI**: the client closes the SSE connection, and the server calls `runner.cancel_run_async()` after detecting the disconnect. Steering is outside the AG-UI protocol; expose your own route and call `runner.send_steering()`. Multi-worker behavior is the same as A2A.
- **Custom server**: call `runner.cancel_run_async()` when the connection disconnects, and `runner.send_steering()` directly for steering. Multi-worker behavior is the same as A2A.

### Session Management

When an Agent is cancelled, different session management strategies are applied depending on the scenario:

**Scenario 1: Cancellation during LLM streaming output**
- Session management: Messages from the start of the LLM response to the point of interruption are preserved. After this partial streamed text, a message "User cancel the agent execution." is appended to make the Agent aware of the cancellation event
- Effect: In the next conversation turn, the user can indicate which text was unsatisfactory, and the Agent will correct its output

**Scenario 2: Cancellation during tool execution**
- If the model requested tool 1 and tool 2, and the user cancels while tool 1 is running, tool 1 finishes and its result is preserved.
- Tool 2 is not executed, but its call record stays in the session with a skipped result whose `status` is `cancelled`. This keeps tool calls and responses paired so the next request remains valid.
- A cancellation notice is appended to make the Agent aware of the cancellation event.
- Effect: In the next conversation turn, the Agent can see that tool 2 was skipped and may call it again.

## Basic Usage

### Basic Example

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

    # Run Agent in a background task
    async def run_agent():
        user_content = Content(parts=[Part.from_text("Please describe the history of artificial intelligence in detail")])
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=user_content,
        ):
            # Check if a cancel event is received
            if isinstance(event, AgentCancelledEvent):  # AgentCancelledEvent
                print(f"Run cancelled: {event.error_message}")
                continue # After continue, runner.run_async will terminate

            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)

    task = asyncio.create_task(run_agent())

    # Wait for a period then cancel
    await asyncio.sleep(2)

    # Cancel the run using the same user_id and session_id
    runner2 = Runner(
        app_name="my_app",
        agent=my_agent,
        session_service=InMemorySessionService(),
    )
    success = await runner2.cancel_run_async(
        user_id=user_id,
        session_id=session_id,
        timeout=3.0,  # Timeout for waiting the Agent cancel operation to complete
    )
    print(f"\nCancel request result: {success}")

    await task
    await runner.close()
    await runner2.close()

asyncio.run(main())
```

`cancel_run_async()` returns `bool`: `True` means the cancel signal was delivered, `False` means no active run was found or delivery failed. The Runner logs the failure reason. If you need the detailed reason, call `cancel.cancel_run()` directly; it returns `Status`.

### Multi-worker Cancellation

Single-worker deployments need no extra configuration; the Runner uses the in-process cancel signal directly.

In a multi-worker deployment, the master calls `init_unix_socket()` before forking; after the workers exit, the master calls `release_unix_socket()` and exits.

Each call to `runner.run_async()` starts a periodic coroutine that syncs the Agent's control state (whether the run is cancelled and whether steering is pending) from the master over Unix Socket at `poll_interval`. When `runner.run_async()` finishes, its resources are cleaned up.

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

| Option | Default | Description |
|-------|--------|------|
| `socket_path` | `/tmp/trpc-agent-py/cancel.sock` | UDS path of the master server |
| `poll_interval` | 0.1 | Interval (seconds) at which each Agent run syncs state from the master |
| `timeout` | 3.0 | Timeout (seconds) for Unix Socket read/write operations |

### Custom Cancel Manager

Single-process deployments use the in-process `_LocalManager` by default. For multiple processes, the framework provides `_UnixSocketManager` to connect them to the Cancel mechanism. The built-in implementations cover most scenarios. If you need to integrate an external system such as Redis or NATS for distributed cancellation, implement `CancelManager` and install it with `set_cancel_manager()`. You are responsible for handling distributed concerns such as ordering and messaging failures, and for validating the implementation thoroughly:

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
        # Register a run; raise RuntimeError when the same key is already active.
        ...

    async def unregister_run(self, key: SessionKey) -> None:
        # Release the run; must be idempotent and drop its pending steering.
        ...

    async def cancel_run(self, key: SessionKey, timeout: float = 1.0) -> Status:
        # Request cancellation. timeout is supplied by Runner.cancel_run_async;
        # A2A and AG-UI pass their own cancel_wait_timeout.
        # A local implementation may wait up to this long for cleanup; a remote
        # implementation may ignore it.
        # Return Status(ok=False, error_msg="no active run") when no active run
        # matches, and Status(ok=True) after the cancel signal is delivered.
        ...

    async def send_steering(self, key: SessionKey, content: Content) -> Status:
        # Return Status(ok=True) on success. Return
        # Status(ok=False, error_msg="steering content is empty") for empty content
        # and Status(ok=False, error_msg="no active run") when no run matches.
        ...

    async def drain_steering(self, key: SessionKey) -> List[Content]:
        # Drain and clear the run's steering queue in FIFO order; return an empty
        # list when nothing is pending.
        ...

    async def check_cancel(self, key: SessionKey) -> bool:
        # Return True when cancelled; False when no run matches or not cancelled.
        ...

    async def get_cancel_event(self, key: SessionKey) -> Optional[asyncio.Event]:
        # Return this process's asyncio.Event for the run; return None when no run
        # matches. Set the event when cancellation arrives.
        ...


# Install the cancel manager at process startup
set_cancel_manager(MyCancelManager())
```

### Agent Custom Service Examples

#### Approach 1: Connection-disconnect-based cancellation (Recommended)

In long-connection scenarios such as SSE/WebSocket, it is recommended to automatically trigger cancellation by detecting connection disconnection. This approach has low implementation cost — users simply disconnect to trigger cancellation without requiring a separate cancel interface.

The following is an example based on FastAPI SSE:

```python
import asyncio

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, Part

app = FastAPI()

# Create Agent and Session Service
agent = LlmAgent(name="my_agent", model=model, instruction="You are an intelligent assistant")
session_service = InMemorySessionService()

# Cancel wait timeout configuration
CANCEL_WAIT_TIMEOUT = 3.0


@app.post("/chat/{user_id}/{session_id}")
async def chat_endpoint(user_id: str, session_id: str, message: str, request: Request):
    """SSE chat endpoint with automatic cancellation on disconnect"""

    app_name = "my_app"

    async def event_generator():
        # Create a Runner for each request
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
                # Detect if the client has disconnected
                if await request.is_disconnected():
                    break

                # Send SSE events
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.text:
                            yield f"data: {part.text}\n\n"

        except asyncio.CancelledError:
            # Connection closed by the client
            raise
        finally:
            # Trigger cancel operation regardless of normal completion or disconnection
            # This ensures the Agent execution is properly terminated and partial results are saved
            await runner.cancel_run_async(
                user_id=user_id,
                session_id=session_id,
                timeout=CANCEL_WAIT_TIMEOUT,
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )
```

This pattern is already implemented in the AG-UI service. See [trpc_agent_sdk/server/ag_ui/_plugin/_utils.py](../../../trpc_agent_sdk/server/ag_ui/_plugin/_utils.py)

#### Approach 2: Explicit cancel interface

If a standalone cancel interface (e.g., REST API) is needed, note the following considerations. You can use this approach:

```python
from fastapi import FastAPI, HTTPException

from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.sessions import InMemorySessionService

app = FastAPI()

agent = LlmAgent(name="my_agent", model=model, instruction="You are an intelligent assistant")
session_service = InMemorySessionService()

runner = Runner(
    app_name="my_app",
    agent=agent,
    session_service=session_service,
)

@app.post("/sessions/{user_id}/{session_id}/cancel")
async def cancel_session_run(user_id: str, session_id: str):
    """Cancel the run for a specified session"""
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
            detail="No active run found for this session"
        )
```

**Note**: `cancel_run_async` first looks for an active run in the local process. In a multi-worker deployment, the master calls `init_unix_socket()` before forking, and cancel requests reach the master over Unix Socket; hash-based routing is no longer required.

## Steering

Steering lets the user add input while a run is executing. `send_steering()` puts the message into the current run's queue; `LlmAgent` drains the queue before its next LLM request and writes it into the session as a hidden user message. The model sees the new instruction in the same run, while the client event stream does not expose it.

```python
from trpc_agent_sdk.types import Content, Part

status = await runner.send_steering(
    user_id=user_id,
    session_id=session_id,
    content=Content(role="user", parts=[Part(text="also check Beijing")]),
)
if status.ok:
    print("steering queued")
else:
    print(f"steering rejected: {status.error_msg}")
```

| Result | Meaning |
|------|------|
| `status.ok == True` | The message entered the active run's queue |
| `status.ok == False` | Not queued; `status.error_msg` explains why |
| `error_msg="no active run"` | The session has no active run |
| `error_msg="steering content is empty"` | The message has no parts |

A custom manager can return its own `error_msg`.

The message must contain at least one part. In a multi-worker deployment, any worker can send steering over Unix Socket; the worker that owns the run drains it at the next checkpoint.

## Agent Cancel Guide

### LlmAgent

LlmAgent has checkpoints set at critical positions in the execution flow:

**Checkpoint locations:**
- At the beginning of each conversation turn
- Before LLM API calls
- During LLM streaming output (each chunk)
- Before and after tool execution

**Usage example:**

```python
import asyncio

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService

# Define tools
async def get_weather(city: str) -> dict:
    """Get city weather"""
    await asyncio.sleep(3)  # Simulate time-consuming operation
    return {"city": city, "temperature": "25°C", "condition": "Sunny"}

# Create Agent
agent = LlmAgent(
    name="weather_agent",
    model=OpenAIModel(model_name="deepseek-chat"),
    instruction="You are a weather query assistant",
    tools=[FunctionTool(get_weather)],
)

# Create Runner
runner = Runner(
    app_name="weather_app",
    agent=agent,
    session_service=InMemorySessionService(),
)

# Run with cancel support
async def run_with_cancel():
    task = asyncio.create_task(run_agent())
    await asyncio.sleep(1)
    await runner.cancel_run_async(user_id, session_id)
    await task
```

**Full example:**
- [examples/llmagent_with_cancel](../../../examples/llmagent_with_cancel/README.md)

### LangGraphAgent

LangGraphAgent wraps LangGraph as a trpc-agent-python compatible Agent, and also supports the Cancel mechanism.

**Checkpoint locations:**
- Before and after graph node execution
- During streaming output

**Usage example:**

```python
import asyncio

from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.agents import LangGraphAgent
from langgraph.graph import StateGraph

# Build LangGraph
def build_graph():
    builder = StateGraph(State)
    builder.add_node("process", process_node) # User-defined processing node
    builder.add_node("respond", respond_node) # User-defined response node
    builder.set_entry_point("process")
    builder.add_edge("process", "respond")
    return builder.compile()

# Create LangGraphAgent
agent = LangGraphAgent(
    name="langgraph_agent",
    description="LangGraph-powered Agent",
    graph=build_graph(),
)

runner = Runner(
    app_name="langgraph_app",
    agent=agent,
    session_service=InMemorySessionService(),
)

# Cancel usage is the same as LlmAgent
await runner.cancel_run_async(user_id, session_id)
```

**Full example:**
- [examples/langgraph_agent_with_cancel](../../../examples/langgraph_agent_with_cancel/README.md)

### ClaudeAgent

ClaudeAgent runs on the Claude SDK and supports both Cancel and Steering. Internally, when a steering message arrives, it is sent directly to the CLI.

**Usage example:**

```python
import asyncio

from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.server.agents.claude import ClaudeAgent, setup_claude_env
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import Content, Part

# Define a tool
async def get_weather(city: str) -> dict:
    """Get the weather for a city"""
    await asyncio.sleep(3)  # Simulate a slow call
    return {"city": city, "temperature": "25°C", "condition": "Sunny"}

model = OpenAIModel(model_name="deepseek-chat")

# Set up Claude environment
setup_claude_env(
    proxy_host="0.0.0.0",
    proxy_port=8082,
    claude_models={"all": model},
)

# Create ClaudeAgent
agent = ClaudeAgent(
    name="claude_agent",
    model=model,
    instruction="You are an intelligent assistant",
    tools=[FunctionTool(get_weather)],
)
agent.initialize()

runner = Runner(
    app_name="claude_app",
    agent=agent,
    session_service=InMemorySessionService(),
)

# Run with cancel support
async def run_with_cancel():
    task = asyncio.create_task(run_agent())
    await asyncio.sleep(1)
    await runner.cancel_run_async(user_id, session_id)
    await task

# Run with steering support
async def run_with_steering():
    task = asyncio.create_task(run_agent())
    await asyncio.sleep(1)
    status = await runner.send_steering(
        user_id=user_id,
        session_id=session_id,
        content=Content(role="user", parts=[Part(text="also check Beijing")]),
    )
    if status.ok:
        print("steering queued")
    else:
        print(f"steering rejected: {status.error_msg}")
    await task
```

**Full examples:**
- Cancel: [examples/claude_agent_with_cancel](../../../examples/claude_agent_with_cancel/README.md)
- Steering: [examples/claude_agent_with_steering](../../../examples/claude_agent_with_steering/README.md)

### TeamAgent

TeamAgent supports Cancel during both Leader planning and Member execution phases.

**Cancel scenarios:**
1. **Cancellation during Leader planning**: Saves the Leader's partial response
2. **Cancellation during Member execution**: Saves the Member's partial response to team memory

**Usage example:**

```python
import asyncio

from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.teams import TeamAgent
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.models import OpenAIModel

model = OpenAIModel(model_name="deepseek-chat")

# Create team members
researcher = LlmAgent(
    name="researcher",
    model=model,
    description="Research expert",
    instruction="Responsible for information retrieval",
    tools=[FunctionTool(search_web)],
)

writer = LlmAgent(
    name="writer",
    model=model,
    description="Writing expert",
    instruction="Responsible for content creation",
)

# Create team
team = TeamAgent(
    name="content_team",
    model=model,
    members=[researcher, writer],
    instruction="Coordinate research and writing tasks",
    share_member_interactions=True,
)

runner = Runner(
    app_name="team_app",
    agent=team,
    session_service=InMemorySessionService(),
)

# Cancel will interrupt the currently executing Leader or Member
await runner.cancel_run_async(user_id, session_id)
```

**Full example:**
- [examples/team_with_cancel](../../../examples/team_with_cancel/README.md)

## Agent Service Cancel Guide

### A2A

Agent services deployed via the A2A protocol support remote Cancel.

**Architecture:**

```
┌─────────────────────────────────────────────────┐
│                   Client                         │
│  ┌───────────────────────────────────────────┐  │
│  │         TrpcRemoteA2aAgent                │  │
│  │     (Connect to remote A2A service)       │  │
│  └─────────────┬─────────────────────────────┘  │
│                │ A2A Protocol                    │
│                │ (Supports Cancel)               │
└────────────────┼────────────────────────────────┘
                 │
                 │ HTTP
                 │
┌────────────────▼────────────────────────────────┐
│                   Server                         │
│  ┌───────────────────────────────────────────┐  │
│  │      TrpcA2aAgentService                  │  │
│  │  ┌─────────────────────────────────────┐  │  │
│  │  │          LlmAgent                   │  │  │
│  │  │     (Cancel-enabled Agent)          │  │  │
│  │  └─────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

**Server configuration:**

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
# Timeout (seconds) for waiting the Agent to complete cancellation, recommended to keep consistent with client timeout
CANCEL_WAIT_TIMEOUT = 3.0


def create_a2a_service() -> TrpcA2aAgentService:
    """Create an A2A service with Cancel support"""
    from agent.agent import root_agent

    # Key configuration: cancel_wait_timeout controls how long the server waits
    # for the backend Agent to complete the cancellation after receiving cancel_task
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
    """Start the A2A service"""
    a2a_svc = create_a2a_service()

    # Assemble the service with a2a-sdk components
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

**Client usage:**

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

# A2A server address, must match the configuration in run_server.py
AGENT_BASE_URL = "http://127.0.0.1:18082"
# Client timeout (seconds) for waiting cancellation to complete, recommended to keep consistent with server cancel_wait_timeout
CANCEL_TIMEOUT = 3.0


async def run_remote_agent(
    runner: Runner,
    user_id: str,
    session_id: str,
    query: str,
    tool_call_callback: Optional[Callable[[], Awaitable[None]]] = None,
    event_count_callback: Optional[Callable[[int], Awaitable[None]]] = None,
) -> None:
    """Run remote Agent and process the event stream"""
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

            # Received cancel event, indicating the Agent was successfully cancelled
            if isinstance(event, AgentCancelledEvent):
                print(f"\n❌ Run was cancelled: {event.error_message}")
                break

            if not event.content or not event.content.parts:
                continue

            # Process streaming output (partial=True indicates a streaming chunk)
            if event.partial:
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
                continue

            # Process complete events (tool calls, tool results, etc.)
            for part in event.content.parts:
                if part.thought:
                    continue
                if part.function_call:
                    print(f"\n🔧 [Invoke Tool: {part.function_call.name}({part.function_call.args})]")
                    # Trigger callback when tool call is detected, used to initiate cancellation during tool execution
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
    """Create a Runner instance bound to the remote A2A Agent"""
    return Runner(app_name=app_name, agent=remote_agent, session_service=session_service)


# ============================================================
# Scenario 1: Cancel during LLM streaming output
# After receiving 10 streaming events, send a cancel request to the remote service via cancel_run_async
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
        # Trigger cancel signal when the 10th event is received
        if count == 10:
            print(f"\n⏳ [Received {count} events, triggering cancellation...]")
            event_threshold_reached.set()

    # Runner for running the Agent
    runner1 = create_runner(app_name, session_service, remote_agent)

    async def run_query1() -> None:
        await run_remote_agent(runner1, user_id, session_id, query1, event_count_callback=on_event_count)

    # Run Agent in a background task
    task = asyncio.create_task(run_query1())

    print("⏳ Waiting for first 10 events...")
    await event_threshold_reached.wait()

    # Use another Runner to send the cancel request (simulating an independent cancel caller)
    runner2 = create_runner(app_name, session_service, remote_agent)
    print("\n⏸️  Requesting cancellation after 10 events...")
    # cancel_run_async sends a cancel_task request to the remote A2A service
    success = await runner2.cancel_run_async(user_id=user_id, session_id=session_id, timeout=CANCEL_TIMEOUT)
    print(f"✓ Cancellation requested: {success}")

    await task

    print()
    print("💡 Result: The partial response was saved to session with cancellation message")
    print()

    # Continue conversation in the same session after cancellation to verify session context is maintained
    query2 = "what happens?"
    print(f"📝 User Query 2: {query2}")
    print()

    runner3 = create_runner(app_name, session_service, remote_agent)
    await run_remote_agent(runner3, user_id, session_id, query2)

    print("💡 Result: Agent can still respond with session context maintained")
    print("-" * 80)
    print()


# ============================================================
# Scenario 2: Cancel during tool execution
# Initiate cancellation after detecting a function_call event, while the tool is still executing on the server
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
        # Set signal when tool call is detected to trigger cancellation
        print("⏳ [Tool call detected...]")
        tool_call_detected.set()

    runner1 = create_runner(app_name, session_service, remote_agent)

    async def run_query1() -> None:
        await run_remote_agent(runner1, user_id, session_id, query1, tool_call_callback=on_tool_call)

    task = asyncio.create_task(run_query1())

    print("⏳ Waiting for tool call to be detected...")
    await tool_call_detected.wait()

    # Initiate cancellation during tool execution; completed tool results are preserved, incomplete calls are cleaned up
    runner2 = create_runner(app_name, session_service, remote_agent)
    print("\n⏸️  Tool call detected! Requesting cancellation during tool execution...")
    success = await runner2.cancel_run_async(user_id=user_id, session_id=session_id, timeout=CANCEL_TIMEOUT)
    print(f"✓ Cancellation requested: {success}")

    await task

    print()
    print("💡 Result: Incomplete function calls were cleaned up from session")
    print()

    # Continue conversation after cancellation to verify session recovery
    query2 = "what happens?"
    print(f"📝 User Query 2: {query2}")
    print()

    runner3 = create_runner(app_name, session_service, remote_agent)
    await run_remote_agent(runner3, user_id, session_id, query2)

    print("💡 Result: Agent can still respond with session context maintained")
    print("-" * 80)
    print()


async def main():
    # Create a remote A2A Agent connected to the service started by run_server.py
    remote_agent = TrpcRemoteA2aAgent(
        name="weather_agent",
        agent_base_url=AGENT_BASE_URL,
        description="Professional weather query assistant with cancel support",
    )
    await remote_agent.initialize()

    # Run the two cancel scenarios in sequence
    await scenario_1_cancel_during_streaming(remote_agent)

    await scenario_2_cancel_during_tool_execution(remote_agent)


if __name__ == "__main__":
    asyncio.run(main())
```

**Configuration reference:**

| Configuration Location | Parameter | Default | Description |
|----------|------|--------|------|
| Server | `cancel_wait_timeout` | 1.0 | Timeout for the server to wait for the backend Agent to complete cancellation |
| Client | `timeout` | 1.0 | Timeout for the client to wait for the local RemoteA2aAgent to complete cancellation |

It is recommended to configure the same timeout value for both.

In a multi-worker deployment, call `init_unix_socket()` in the master so a `CancelTask` received by any worker reaches the owner. To deliver steering, call `Runner.send_steering` from your own endpoint, for example a `POST /steering` route on the service.

**Full example:**
- [examples/a2a_with_cancel](../../../examples/a2a_with_cancel/README.md) (a2a-sdk 0.3)
- [examples/a2a_v1_with_cancel](../../../examples/a2a_v1_with_cancel/README.md) (a2a-sdk 1.x)

### AG-UI

Agent services deployed via the AG-UI protocol automatically trigger Cancel when the client closes the SSE connection.

**Architecture:**

```
┌─────────────────────────────────────────────────┐
│                   Client                         │
│  ┌───────────────────────────────────────────┐  │
│  │        @ag-ui/client                      │  │
│  │    agent.abortRun() closes connection     │  │
│  └─────────────┬─────────────────────────────┘  │
│                │ AG-UI Protocol (SSE)            │
└────────────────┼────────────────────────────────┘
                 │ HTTP
                 │ ⚡ Connection disconnected
                 │
┌────────────────▼────────────────────────────────┐
│                   Server                         │
│  ┌───────────────────────────────────────────┐  │
│  │      AgUiService (detects disconnect)     │  │
│  │  ┌─────────────────────────────────────┐  │  │
│  │  │  AgUiAgent.cancel_run()             │  │  │
│  │  │    ↓                                │  │  │
│  │  │  Runner.cancel_run_async()          │  │  │
│  │  │    ↓                                │  │  │
│  │  │  Agent (stops at checkpoint)        │  │  │
│  │  └─────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

**Server configuration:**

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
    """Start the AG-UI service, register Agent and bind routes"""
    service_name = "weather_agent_cancel_service"
    uri = "/weather_agent"  # AG-UI endpoint path, clients connect via this path
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
    """AG-UI Runner: manages AgUiManager, FastAPI app, and service registration"""

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
    """Create AgUiAgent with cancel_wait_timeout configuration"""
    agui_agent = AgUiAgent(
        trpc_agent=root_agent,
        app_name=name,
        # Key configuration: timeout for waiting the Agent to complete cancellation after SSE disconnect
        # If configured too short, Cancel may not complete and streamed text cannot be saved to the session
        cancel_wait_timeout=3.0,
        **kwargs,
    )
    return agui_agent


def create_agui_runner(app_name: str, service_name: str, uri: str, **kwargs: Any) -> AguiRunner:
    """Assemble AG-UI service: create Runner -> create Service -> register Agent route"""
    ag_ui_runner: AguiRunner = AguiRunner(app_name)
    agui_service = AgUiService(service_name, app=ag_ui_runner.app)
    agui_agent = _create_agui_agent(app_name, **kwargs)
    # Register the Agent to the specified URI path, clients connect via this path
    agui_service.add_agent(uri, agui_agent)
    ag_ui_runner.register_service(service_name, agui_service)
    return ag_ui_runner
```

**Client usage (JavaScript):**

client_js/main.js:

```javascript
import { HttpAgent } from '@ag-ui/client';

// Connect to the AG-UI server, path must match the uri registered in run_server.py
const agent = new HttpAgent({
  url: 'http://127.0.0.1:18080/weather_agent',
  debug: false
});

let chunkCount = 0;
const ABORT_AFTER_CHUNKS = 5;  // Trigger cancellation after receiving 5 text chunks

// Subscribe to AG-UI event stream
const subscription = agent.subscribe({
  onTextMessageStartEvent: ({ event }) => {
    process.stdout.write('\n🤖 Assistant: ');
  },
  onTextMessageContentEvent: ({ event }) => {
    process.stdout.write(event.delta ?? '');
    chunkCount++;
    // After reaching threshold, call abortRun() to close SSE connection, triggering server-side Cancel
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

// Send user message and start the Agent
await agent.addMessage({
  role: 'user',
  content: 'Please introduce yourself in detail and tell me what you can do.',
  id: 'user_123'
});

await agent.runAgent();

subscription.unsubscribe?.();
```

**Cancel trigger mechanism:**
- Client calls `agent.abortRun()` to close the SSE connection
- Server detects the disconnection (`asyncio.CancelledError`)
- Automatically invokes `cancel_run()` to trigger cooperative cancellation
- Agent stops execution at the checkpoint
- Partial responses and session state are saved

**Configuration reference:**

| Parameter | Default | Description |
|------|--------|------|
| `cancel_wait_timeout` | 3.0 | Timeout (in seconds) for waiting the Cancel operation to complete. If this value is not properly configured, the Cancel operation may fail to execute successfully, causing streamed text to not be saved to the session. |

In a multi-worker deployment, call `init_unix_socket()` before starting the workers. `AgUiAgent.cancel_run()` then sends the cancel request to the master over Unix Socket, so any worker can cancel the run.

**Full example:**
- [examples/agui_with_cancel](../../../examples/agui_with_cancel/README.md)
