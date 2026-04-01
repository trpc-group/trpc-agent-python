# Agent Cancel Mechanism

During Agent execution, the output may sometimes not meet the user's requirements. In such cases, the user often interrupts the Agent execution, provides partial feedback (indicating which outputs before the interruption were unsatisfactory and what should be done next), and then lets the Agent continue execution.

For this scenario, the trpc-agent framework provides a Cancel mechanism that allows cancelling an Agent's ongoing operation while preserving partial content (content being streamed by the LLM, tool execution results in progress, etc.). This mechanism is based on a checkpoint design. In their implementations, each Agent checks at checkpoint locations (after an LLM streaming output chunk, after a tool call completes, etc.) whether the current Agent should be terminated. If termination is required, an exception is thrown, and the framework records and saves the partial information into the session history.

This capability has been integrated into all Agents provided by the framework. Custom Agents implemented by other services can also be easily integrated.

| Module Type | Module Name | Cancel Support | Description |
|---------|---------|-------------|------|
| Single Agent | `LlmAgent` | ✅ | Checkpoints set at LLM streaming output, tool execution, and other locations |
| Single Agent | `LangGraphAgent` | ✅ | Checkpoints set in LangGraph streaming output |
| Single Agent | `ClaudeAgent` | ✅ | Checkpoints set in Claude SDK streaming output |
| Single Agent | `TrpcRemoteA2aAgent` | ✅ | Checkpoints set in HTTP streaming output |
| Multi Agent | `ChainAgent` | ✅ | Exception propagated from sub-Agents |
| Multi Agent | `ParallelAgent` | ✅ | Execution cancelled when any sub-Agent throws an exception |
| Multi Agent | `CycleAgent` | ✅ | Exception propagated from sub-Agents |
| Multi Agent | `TeamAgent` | ✅ | Cancellable during both Leader and Member execution |
| Agent Service | `TrpcA2aAgentService` | ✅ | Cancels remote Agent execution via the A2A protocol's cancel_task API |
| Agent Service | `AgUiService` | ✅ | Agent automatically cancels execution upon SSE connection disconnect detection |


## Agent Cancel Mechanism Design Overview

### Architecture Design

As shown in the architecture below:
- When the framework starts, it creates a global `_RunCancellationManager` object to manage Agent cancellation signals.
- Users run and interrupt Agent execution through the Runner.
    - The user executes an Agent via `run_async`. Before Agent execution, the Runner registers the current run information with the Manager through `register_run`. The SessionKey is a triplet of (app_name, user_id, session_id).
    - The user cancels Agent execution via `cancel_run_async`. The Runner receives the `RunCancelledException` thrown by the Agent, completes the post-cancel processing (injecting partial streaming messages and partial tool call content into the Agent's session). After processing, the Runner generates an `AgentCancelledEvent` to convey the termination information, and the cancellation reason can be obtained through its error_message field.
- Agents embed checkpoints during execution to integrate the Cancel capability.
    - During Agent execution, `ctx.raise_if_cancelled` is used in the `_run_async_impl` implementation to check at each checkpoint (after an LLM streaming output chunk, after a tool call, etc.) whether the current execution has been cancelled. If `runner.cancel_run_async` has been called, the Agent's execution will be marked as cancelled, and `raise_if_cancelled` will throw a `RunCancelledException`.
    - Generally, common checkpoints include: during LLM streaming output, after tool calls. Cancellation during tool call execution is not currently supported.
- Agent services automatically call `runner.cancel_run_async` through their interfaces and obtain cancellation details from the AgentCancelledEvent returned by the Runner.
    - For AG-UI services, since the protocol does not natively support cancellation, the client cancels Agent execution by disconnecting. The Agent service detects the connection disconnect exception and automatically calls `runner.cancel_run_async` to support this capability.
    - For A2A services, the protocol natively supports cancellation through the `cancel_task` API. The framework already supports this API and adapts it to `runner.cancel_run_async`, but it needs to be used with hash-based routing. In multi-node deployment scenarios, configuring hash-based routing can be cumbersome. A simpler approach, similar to AG-UI, is for the Agent service to automatically detect the connection disconnect and call `runner.cancel_run_async`. However, due to the current underlying implementation of the a2a-sdk, the Agent will continue to execute after the connection is disconnected, so hash-based routing is temporarily required to complete the cancel operation.
    - For custom services, it is recommended to implement cancellation logic triggered by connection disconnect. This approach has a low implementation cost, does not require hash-based routing, and the client simply needs to disconnect from the remote Agent.

<p align="center">
  <img src="../assets/imgs/agent_cancel.png" alt="Agent Cancel" />
</p>

### Session Management

When an Agent is cancelled, different session management strategies are applied depending on the scenario:

**Scenario 1: Cancellation during LLM streaming output**
- Session management: Messages from the start of the LLM response to the point of interruption are all preserved. After the streamed text of this portion, a message "User cancel the agent execution." is appended to make the Agent aware of the cancellation event.
- Effect: In the next conversation turn, the user can point out which text was unreasonable, and the Agent will correct its output.

**Scenario 2: Cancellation during tool execution**
- Session management: For scenarios where an Agent needs to call multiple tools, e.g., tool 1 and tool 2, if the user cancels Agent execution while tool 1 is being called, the execution will skip tool 2's call after tool 1 completes and terminate. The call information for tool 2 in the current turn will be removed from the session history, as if the Agent never executed tool 2 in this turn. Similarly, after tool 1's call response, a message "User cancel the agent execution." is appended to make the Agent aware of the cancellation event.
- Effect: In the next conversation turn, the Agent can sense that tool 2 was not called and may call tool 2.

### Limitations

> **⚠️ The current Cancel mechanism only supports single-node scenarios**

`_RunCancellationManager` uses in-process storage (`Dict`) to track active runs. This means:

1. **Cancel requests must be sent to the same node running the Agent**
2. **Cross-node cancellation is not supported**
3. **Applicable scenarios**:
   - Single-node deployment
   - Client communicates with the Agent through the same connection (WebSocket, SSE)
   - Cancellation is automatically triggered upon connection disconnect

## Basic Usage

### Basic Example

```python
import asyncio
import uuid
from trpc_agent.runners import Runner
from trpc_agent.sessions import InMemorySessionService
from trpc_agent.types import Content, Part

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

    # Wait for a while then cancel
    await asyncio.sleep(2)

    # Cancel the run using the same user_id and session_id
    runner2 = Runner(xxxx)
    success = await runner2.cancel_run_async(
        user_id=user_id,
        session_id=session_id,
        timeout=3.0,  # Timeout for waiting the Agent cancel action to complete
    )
    print(f"\nCancel request result: {success}")

    await task
    await runner.close()
    await runner2.close()

asyncio.run(main())
```

### Agent Custom Service Examples

#### Method 1: Cancellation based on connection disconnect (Recommended)

In long-connection scenarios such as SSE/WebSocket, it is recommended to automatically trigger cancellation by detecting connection disconnects. This approach has a low implementation cost — the user simply disconnects to trigger the cancellation without requiring a separate cancel API.

The following is an example based on FastAPI SSE:

```python
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from trpc_agent.runners import Runner
from trpc_agent.agents import LlmAgent
from trpc_agent.sessions import InMemorySessionService
from trpc_agent.types import Content, Part
from trpc_agent import cancel

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
            # Connection closed by client
            raise
        finally:
            # Trigger cancel regardless of normal completion or disconnect
            # This ensures Agent execution is properly terminated and partial results are saved
            cleanup_event = await cancel.cancel_run(app_name, user_id, session_id)

            if cleanup_event is not None:
                try:
                    # Wait for the cancel operation to complete
                    await asyncio.wait_for(cleanup_event.wait(), timeout=CANCEL_WAIT_TIMEOUT)
                except asyncio.TimeoutError:
                    pass  # Continue after timeout, the Agent may still be running

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )
```

This pattern is already implemented in the AG-UI service. See [trpc_agent_ecosystem/ag_ui/_plugin/_ag_ui_handler.py](../../../trpc_agent_ecosystem/ag_ui/_plugin/_ag_ui_handler.py) for reference.

#### Method 2: Explicit cancel API

If a separate cancel API (e.g., REST API) is needed, note the following. You can use this approach:

```python
from fastapi import FastAPI, HTTPException

app = FastAPI()
runner = Runner(...)

@app.post("/sessions/{user_id}/{session_id}/cancel")
async def cancel_session_run(user_id: str, session_id: str):
    """Cancel the run for the specified session"""
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

**Note**: This approach requires that the cancel request be sent to the same node running the Agent. In multi-node deployment scenarios, hash-based routing must be used to ensure that the cancel request is routed to the node executing the Agent.

## Agent Cancel Guide

### LlmAgent

LlmAgent has checkpoints set at key positions in the execution flow:

**Checkpoint locations:**
- At the start of each conversation turn
- Before an LLM API call
- During LLM streaming output (each chunk)
- Before and after tool execution

**Usage example:**

```python
from trpc_agent.agents import LlmAgent
from trpc_agent.models import OpenAIModel
from trpc_agent.tools import FunctionTool

# Define tools
async def get_weather(city: str) -> dict:
    """Get weather for a city"""
    await asyncio.sleep(3)  # Simulate a time-consuming operation
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

LangGraphAgent wraps LangGraph as a trpc-agent compatible Agent, and also supports the Cancel mechanism.

**Checkpoint locations:**
- Before and after graph node execution
- During streaming output

**Usage example:**

```python
from trpc_agent.agents import LangGraphAgent
from langgraph.graph import StateGraph

# Build LangGraph
def build_graph():
    builder = StateGraph(State)
    builder.add_node("process", process_node)
    builder.add_node("respond", respond_node)
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

ClaudeAgent runs in a subprocess mode using the Claude SDK. When cancelled, it terminates the subprocess.

**Cancel implementation:**
- When a cancel request is detected, a termination signal is sent to the Claude SDK subprocess
- After the subprocess exits, partial responses are saved to the session

**Usage example:**

```python
from trpc_agent_ecosystem.agents.claude import ClaudeAgent, setup_claude_env
from trpc_agent.models import OpenAIModel

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
    tools=[FunctionTool(some_tool)],
)
agent.initialize()

runner = Runner(
    app_name="claude_app",
    agent=agent,
    session_service=InMemorySessionService(),
)

# Cancel usage is the same
await runner.cancel_run_async(user_id, session_id)
```

**Notes:**
- Cancellation will cause the Claude SDK subprocess to be terminated. You may see `ProcessError` logs, which is expected behavior.
- After the subprocess is terminated, partial responses will be saved to the session.

**Full example:**
- [examples/claude_agent_with_cancel](../../../examples/claude_agent_with_cancel/README.md)

### TeamAgent

TeamAgent supports Cancel during both Leader planning and Member execution.

**Cancel scenarios:**
1. **Cancellation during Leader planning**: Saves the Leader's partial response
2. **Cancellation during Member execution**: Saves the Member's partial response to team memory

**Usage example:**

```python
from trpc_agent.agents import LlmAgent
from trpc_agent.teams import TeamAgent
from trpc_agent.tools import FunctionTool

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
│                   Client                        │
│  ┌───────────────────────────────────────────┐  │
│  │         TrpcRemoteA2aAgent                │  │
│  │     (Connect to remote A2A service)       │  │
│  └─────────────┬─────────────────────────────┘  │
│                │ A2A Protocol                   │
│                │ (Supports Cancel)              │
└────────────────┼────────────────────────────────┘
                 │
                 │ HTTP
                 │
┌────────────────▼────────────────────────────────┐
│                   Server                        │
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

```python
from trpc_agent_ecosystem.a2a import TrpcA2aAgentService
from trpc_agent_ecosystem.a2a._core.executor import A2aAgentExecutorConfig

# Configure cancel wait timeout
executor_config = A2aAgentExecutorConfig(
    cancel_wait_timeout=3.0,  # Default: 1.0 seconds
)

a2a_service = TrpcA2aAgentService(
    service_name="trpc.a2a.agent.weather_with_cancel",
    agent=agent,
    executor_config=executor_config,
)
```

**Client usage:**

```python
from trpc_agent_ecosystem.a2a.agent import TrpcRemoteA2aAgent

# Create remote Agent
remote_agent = TrpcRemoteA2aAgent(
    name="weather_agent",
    service_name="trpc.a2a.agent.weather_with_cancel",
    description="Remote weather query service",
)
await remote_agent.initialize()

runner = Runner(
    app_name="client_app",
    agent=remote_agent,
    session_service=InMemorySessionService(),
)

# Cancel sends a cancellation request to the remote service
success = await runner.cancel_run_async(
    user_id=user_id,
    session_id=session_id,
    timeout=3.0,
)
```

**Configuration reference:**

| Configuration Location | Parameter | Default | Description |
|----------|------|--------|------|
| Server | `cancel_wait_timeout` | 1.0 | Timeout for the server to wait for the backend Agent to complete cancellation |
| Client | `timeout` | 1.0 | Timeout for the client to wait for the local RemoteA2aAgent to complete cancellation |

It is recommended to configure the same timeout value for both.

**Full example:**
- [examples/trpc_a2a_with_cancel](../../../examples/trpc_a2a_with_cancel/README.md)

### AG-UI

Agent services deployed via the AG-UI protocol automatically trigger Cancel when the client closes the SSE connection.

**Architecture:**

```
┌─────────────────────────────────────────────────┐
│                   Client                        │
│  ┌───────────────────────────────────────────┐  │
│  │        @ag-ui/client                      │  │
│  │    agent.abortRun() closes connection     │  │
│  └─────────────┬─────────────────────────────┘  │
│                │ AG-UI Protocol (SSE)           │
└────────────────┼────────────────────────────────┘
                 │ HTTP
                 │ ⚡ Connection disconnected
                 │
┌────────────────▼────────────────────────────────┐
│                   Server                        │
│  ┌───────────────────────────────────────────┐  │
│  │      AgUiService (detect disconnect)      │  │
│  │  ┌─────────────────────────────────────┐  │  │
│  │  │  AgUiAgent.cancel_run()             │  │  │
│  │  │    ↓                                │  │  │
│  │  │  Cancellation Manager               │  │  │
│  │  │  (cancel.cancel_run)                │  │  │
│  │  │    ↓                                │  │  │
│  │  │  Agent (stops at checkpoint)        │  │  │
│  │  └─────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

**Server configuration:**

```python
from trpc_agent_ecosystem.agui import AgUiAgent, AgUiService

# Create AG-UI Agent
agui_agent = AgUiAgent(
    trpc_agent=agent,
    app_name="weather_app",
    cancel_wait_timeout=3.0,  # Cancel wait timeout, default: 3.0 seconds
)

# Create service
agui_service = AgUiService(agents=[agui_agent])

# Start service
await agui_service.start(host="0.0.0.0", port=18080)
```

**Client usage (JavaScript):**

```javascript
import { AgentClient } from '@anthropic-ai/agent-ui-client';

const agent = new AgentClient({
  url: 'http://localhost:18080',
});

// Start run
const runId = await agent.startRun({
  userId: 'user1',
  sessionId: 'session1',
  message: 'What is the weather?',
});

// Subscribe to events
agent.onEvent((event) => {
  console.log('Event:', event);
});

// Cancel run (close SSE connection)
agent.abortRun();
```

**Cancel trigger mechanism:**
- Client calls `agent.abortRun()` to close the SSE connection
- Server detects the connection disconnect (`asyncio.CancelledError`)
- Automatically invokes `cancel_run()` to trigger cooperative cancellation
- Agent stops execution at the checkpoint
- Partial responses and session state are saved

**Configuration reference:**

| Parameter | Default | Description |
|------|--------|------|
| `cancel_wait_timeout` | 3.0 | Timeout (in seconds) for waiting for the Cancel operation to complete. If this value is improperly configured, the Cancel operation may fail to execute successfully, causing streamed text to not be saved to the session. |

**Full example:**
- [examples/trpc_agui_with_cancel](../../../examples/trpc_agui_with_cancel/README.md)
