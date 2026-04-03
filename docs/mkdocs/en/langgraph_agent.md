# LangGraph Agent

LangGraphAgent wraps an AI Agent implementation based on LangGraph graph orchestration. It uses directed graphs to define complex workflows, supporting multi-step processing, conditional branching, parallel execution, and other advanced features.

## Comparison with LlmAgent

| Feature | LlmAgent | LangGraphAgent |
|---------|----------|----------------|
| **Use Cases** | Simple conversations, tool calling | Complex workflows, multi-step processing |
| **Execution Mode** | LLM-driven decision making | Graph-structured predefined workflows |
| **Control Granularity** | Coarse-grained (relies on LLM reasoning) | Fine-grained (precise control over each step) |
| **Parallel Processing** | Not supported | Natively supported |
| **Conditional Branching** | LLM autonomous judgment | Explicitly defined branching logic |
| **State Management** | Simple session state | Complex graph state management |
| **Predictability** | Low (depends on LLM) | High (predefined workflows) |

## Scenario Recommendations

**Use LangGraphAgent for:**
- Complex tasks requiring multi-step, sequential processing
- Workflows requiring conditional branching and parallel execution
- Scenarios requiring precise control over the execution flow
- State passing and transformation across multiple nodes
- Complex pipelines such as RAG, code generation, and data processing

**Use LlmAgent for:**
- Simple conversations and Q&A
- Basic tool calling
- Scenarios where flexibility is more important than predictability

## Installing LangGraph

It is recommended to create an isolated virtual environment first, then install dependencies according to the versions specified in the project repository's `requirements-pypi.txt`

### Standalone Installation
```bash
# Create a Python virtual environment
python3 -m venv .venv
# Activate the virtual environment in the current directory
source .venv/bin/activate
# Install the specified version of LangGraph via the mirror source used in the project requirements
pip install "langgraph==0.6.0"
```

### Installation via requirements

If you want to install dependencies consistent with the current development environment of the repository, it is recommended to use the `requirements.txt` and `requirements-pypi.txt` files in the root directory directly.

```bash
# Create a Python virtual environment
python3 -m venv venv
# Activate the virtual environment in the current directory
source venv/bin/activate
# Install PyPI-side dependencies first
pip install -r requirements-pypi.txt
# Then install project base dependencies
pip install -r requirements.txt
```

## Core Concepts

In this framework, the typical usage of `LangGraphAgent` is: first build a workflow graph using LangGraph, then pass the graph object obtained after `compile()` to `LangGraphAgent`. Understanding the following core concepts will help you understand most examples.

### Graph

A graph is the core structure of a workflow, composed of nodes and edges, used to describe "where to start, what steps to go through, and under what conditions to flow where." Example code typically uses LangGraph's native `StateGraph` to construct graphs:

```python
from langgraph.graph import StateGraph, START, END

graph_builder = StateGraph(MyState)
graph_builder.add_node("chatbot", chatbot_node)
graph_builder.add_node("tools", tool_node)

graph_builder.add_edge(START, "chatbot")
graph_builder.add_edge("tools", "chatbot")
graph_builder.add_edge("chatbot", END)
```

Additional notes:

- `START` and `END` are special node identifiers provided by LangGraph, representing the entry point and exit point of the graph respectively
- They are built-in framework concepts and do not need to be implemented as separate functions like regular business nodes
- The graph itself is only responsible for describing the execution topology; the actual runtime logic is handled by each node function

### Node

A node represents a processing step in the workflow. A node typically corresponds to a function, which can be a regular processing node, an LLM node, or a tool execution node.

In this framework, the common node implementation pattern is as follows:

```python
from trpc_agent_sdk.agents import langgraph_llm_node

@langgraph_llm_node
def chatbot_node(state: MyState, config):
    return {"messages": [llm.invoke(state["messages"])]}
```

For tool nodes, they are typically used with the `@tool` and `@tool_node` decorators:

```python
from langchain_core.tools import tool
from trpc_agent_sdk.agents.langgraph_agent import tool_node

@tool
@tool_node
def calculate(operation: str, a: float, b: float) -> str:
    return f"{operation}: {a}, {b}"
```

Additional notes:

- The input to a node is typically the current `state`
- The return value of a node is typically a dictionary used to update the graph state
- `@langgraph_llm_node` adds tracing and event recording capabilities to LLM nodes
- `@tool_node` adds tool invocation chain recording capabilities to tool nodes

### State

State is the data container that flows between nodes. LangGraph examples in this framework typically use `TypedDict` to define the state structure; if a field needs to accumulate across multiple node executions, it can be combined with reducers like `Annotated[..., add_messages]`.

```python
from typing import Annotated
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages

class MyState(TypedDict):
    messages: Annotated[list, add_messages]
    user_input: str
    result: str
```

Additional notes:

- `messages` is typically used to store conversation message history and is the most common state field in LangGraph conversational workflows
- Custom fields such as `user_input`, `result`, `status`, etc. can be used to store business data
- Nodes read upstream results via `state` and write back new state values by returning a dictionary

### State Schema

The "state schema" is typically the state type definition passed to `StateGraph(...)`. The most common approach is the `TypedDict` shown above.

```python
graph_builder = StateGraph(MyState)
```

Its purpose is to:

- Constrain which fields exist in the graph state
- Define the data type for each field
- Specify merge strategies for certain fields, e.g., appending to a message list instead of overwriting

### Compiled Graph

What `LangGraphAgent` actually accepts is not the `StateGraph` builder itself, but the graph object after `compile()`. The framework's `LangGraphAgent` explicitly requires a compiled graph as input.

```python
from trpc_agent_sdk.agents.langgraph_agent import LangGraphAgent

graph = graph_builder.compile()

agent = LangGraphAgent(
    name="workflow_agent",
    graph=graph,
    instruction="You are a workflow assistant.",
)
```

Additional notes:

- `compile()` organizes the previously defined nodes, edges, and conditional routes into an executable graph
- `LangGraphAgent` invokes the streaming execution capability of this graph at runtime and converts LangGraph's output into `trpc_agent` `Event` objects
- Therefore, `StateGraph` can be understood as the "definition phase," and the compiled graph as the "execution phase"

For more graph-related concepts, refer to: [Graph](./graph.md)

## Creating a LangGraphAgent

A LangGraphAgent can be created by providing LangGraph's Compiled Graph, as shown below:

The relevant parameters follow the parameter descriptions of LlmAgent.

```python
from trpc_agent_sdk.agents.langgraph_agent import LangGraphAgent

# Assume the LangGraph has already been built
graph = build_your_langgraph()

agent = LangGraphAgent(
    name="workflow_agent",
    description="A complex workflow processing agent",
    graph=graph,
    instruction="You are a workflow assistant that processes tasks step by step.",
)
```

## Building a LangGraph Workflow

### Basic Graph Structure

```python
from langchain.chat_models import init_chat_model
from langchain_core.tools import tool
from langgraph.graph import StateGraph, START
from langgraph.graph.message import add_messages
from langgraph.prebuilt import tools_condition, ToolNode
from typing_extensions import TypedDict
from typing import Annotated

# Define the state structure
class State(TypedDict):
    messages: Annotated[list, add_messages]

# Initialize the model
model = init_chat_model("deepseek:deepseek-chat", api_key="your-api-key", api_base="https://api.deepseek.com/v1")

def build_graph():
    graph_builder = StateGraph(State)
    
    # Add nodes
    graph_builder.add_node("chatbot", chatbot_node)
    graph_builder.add_node("tools", tool_node)
    
    # Add edges
    graph_builder.add_edge(START, "chatbot")
    graph_builder.add_conditional_edges("chatbot", tools_condition)
    graph_builder.add_edge("tools", "chatbot")
    
    return graph_builder.compile()
```

### Integrating with trpc_agent

The framework provides two important decorators to integrate LangGraph nodes with trpc_agent:

#### @langgraph_llm_node Decorator

Used to decorate nodes that call LLMs, automatically recording LLM invocation information:

```python
from trpc_agent_sdk.agents import langgraph_llm_node

@langgraph_llm_node
def chatbot_node(state: State):
    """General LLM node that calls the LLM to generate responses"""
    return {"messages": [llm_with_tools.invoke(state["messages"])]}

# Usage with custom input/output keys
@langgraph_llm_node(input_key="conversation", output_key="response")  
def custom_chatbot(state: CustomState):
    """LLM node with custom input and output fields"""
    return {"response": [llm.invoke(state["conversation"])]}
```

#### @tool_node Decorator

Used to decorate tool execution nodes, automatically recording tool invocation information. Note that @tool_node must be placed after LangGraph's @tool decorator:

```python
from trpc_agent_sdk.agents.langgraph_agent import tool_node
from langchain_core.tools import tool

@tool
@tool_node  
def calculate(operation: str, a: float, b: float) -> str:
    """A tool for performing mathematical calculations"""
    if operation == "add":
        return f"Result: {a} + {b} = {a + b}"
    # ... other operations
```

## Human-In-The-Loop Capability

See [Human-In-The-Loop](./human_in_the_loop.md).

## Advanced Configuration

### LangGraph Configuration

The framework provides RunConfig for configuring some LangGraph runtime settings, as shown below:
- `input`: Used to pass user-defined input, which will be merged with the Agent's internal `{"messages": [xxx]}` as the input for the `langgraph.astream` call;
- [Stream Mode](https://langchain-ai.github.io/langgraph/how-tos/streaming/) controls LangGraph's output. The framework has built-in support for `updates`, `custom`, and `messages` stream modes, and users can add more;
- [RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html) is LangGraph's runtime configuration, which users can configure as needed;
- Other configurations can also be passed through RunConfig if needed. Currently, only `stream_mode` and `runnable_config` are supported. If additional configuration items are needed, feel free to open an issue.

```python
from trpc_agent_sdk.agents.run_config import RunConfig

run_config = RunConfig(
    agent_run_config={
        "input": {"user_input": {"custom1": "xxx"}},
        "stream_mode": ["values"],
        "runnable_config": {
            "configurable": {"xxx": "xxx"}
        }
    }
)

runner.run_async(..., run_config=run_config)
```

### Retrieving Raw LangGraph Data

After obtaining an Event, you can access the raw response data from LangGraph, as shown below:

```python
from trpc_agent_sdk.agents.langgraph_agent import get_langgraph_payload

async for event in runner.run_async(...):
    langgraph_payload = get_langgraph_payload(event)
    if langgraph_payload:
        stream_mode = langgraph_payload["stream_mode"] 
        chunk = langgraph_payload["chunk"]
        # Process raw LangGraph data
```

### Accessing Agent Context within Nodes

In LangGraph nodes, you can access the trpc_agent context information:

```python
from trpc_agent_sdk.agents.langgraph_agent import get_langgraph_agent_context

@langgraph_llm_node
def context_aware_node(state: State, config: RunnableConfig):
    """A node that can access the Agent context"""
    ctx = get_langgraph_agent_context(config)
    user_id = ctx.session.user_id
    session_state = ctx.session.state
    
    # Process based on context
    response = llm.invoke(build_context_prompt(state, user_id, session_state))
    return {"messages": [response]}
```

## Memory Management Recommendations

LangGraphAgent supports two memory management approaches:

1. **Using trpc_agent's SessionService** (Recommended):
   ```python
   # Do not use a checkpointer; trpc_agent will manage session information
   graph = graph_builder.compile()
   ```

2. **Using LangGraph's checkpointer**:
   ```python
   from langgraph.checkpoint.memory import MemorySaver
   
   memory = MemorySaver()
   graph = graph_builder.compile(checkpointer=memory)
   ```

The first approach is recommended because it integrates better with trpc_agent's multi-Agent conversation management.

## Custom Event Emission

### LangGraphEventWriter Usage

Within LangGraph nodes, `LangGraphEventWriter` can be used to emit custom events. These events can be captured by event translators (such as AG-UI's event translator) and converted into protocol-specific events.

#### Basic Usage

```python
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter
from trpc_agent_sdk.agents.utils import LangGraphEventWriter

def custom_node(
    state: State,
    *,
    config: RunnableConfig,
    writer: StreamWriter,
):
    """Emit events within a node using LangGraphEventWriter

    Important: config and writer must be keyword-only arguments (after *),
    so that LangGraph can inject them correctly.

    Args:
        state: Graph state
        config: Runtime configuration (keyword argument, injected by LangGraph)
        writer: Stream writer (keyword argument, injected by LangGraph)
    """
    # Create an event writer from config
    event_writer = LangGraphEventWriter.from_config(writer, config)

    # Emit a text event
    event_writer.write_text("Processing data...")

    # Emit a custom event (structured data)
    event_writer.write_custom({
        "stage": "processing",
        "progress": 50,
        "status": "in_progress",
    })

    return {}
```

#### Text Events

The `write_text()` method is used to emit text messages:

```python
# Emit plain text
event_writer.write_text("Processing data...")

# Emit thought text
event_writer.write_text("Let me analyze this problem...", thought=True)

# Emit a complete message (non-streaming)
event_writer.write_text("Processing complete!", partial=False)
```

Parameter descriptions:
- `text`: Text content
- `partial`: Whether this is a streaming/partial event (default `True`)
- `thought`: Whether this is thought/reasoning text (default `False`)

#### Custom Events

The `write_custom()` method is used to emit structured data:

```python
# Emit progress information
event_writer.write_custom({
    "stage": "initialization",
    "progress": 0,
    "status": "starting",
})

# Emit arbitrary structured data
event_writer.write_custom({
    "metric": "accuracy",
    "value": 0.95,
    "timestamp": time.time(),
})
```

### Building AG-UI Protocol Messages

To convert LangGraph events into AG-UI protocol messages, you need to create a custom event translator.

#### Creating a Custom Translator

```python
from typing import AsyncGenerator
from ag_ui.core import BaseEvent, EventType, CustomEvent
from trpc_agent_sdk.events import Event as TrpcEvent
from trpc_agent_sdk.agents.utils import LangGraphEventType, get_event_type
from trpc_agent_sdk.server.ag_ui._plugin._langgraph_event_translator import (
    AgUiLangGraphEventTranslator,
    AgUiTranslationContext,
)

class CustomAgUiEventTranslator(AgUiLangGraphEventTranslator):
    """Custom AG-UI event translator"""

    async def translate(
        self,
        event: TrpcEvent,
        context: AgUiTranslationContext,
    ) -> AsyncGenerator[BaseEvent, None]:
        """Convert a LangGraph trpc Event to AG-UI events

        Args:
            event: trpc Event object
            context: AG-UI translation context (contains thread_id and run_id)

        Yields:
            AG-UI BaseEvent objects
        """
        # Get the event type (using enum)
        event_type = get_event_type(event)

        if event_type == LangGraphEventType.TEXT:
            # Handle text events
            yield await self._translate_text_event(event, context)
        elif event_type == LangGraphEventType.CUSTOM:
            # Handle custom events
            yield await self._translate_custom_event(event, context)

    async def _translate_text_event(
        self,
        event: TrpcEvent,
        context: AgUiTranslationContext,
    ) -> CustomEvent:
        """Convert a text event to an AG-UI CustomEvent"""
        # Extract text content
        text_content = ""
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    text_content += part.text

        # Return an AG-UI CustomEvent
        return CustomEvent(
            type=EventType.CUSTOM,
            timestamp=int(event.timestamp * 1000),  # Convert to milliseconds
            raw_event=None,
            name="progress_text",  # Custom event name
            value={"text": text_content},
        )

    async def _translate_custom_event(
        self,
        event: TrpcEvent,
        context: AgUiTranslationContext,
    ) -> CustomEvent:
        """Convert a custom event to an AG-UI CustomEvent"""
        # Extract custom data
        custom_data = {}
        if event.custom_metadata:
            custom_data = event.custom_metadata.get("data", {})

        # Return an AG-UI CustomEvent
        return CustomEvent(
            type=EventType.CUSTOM,
            timestamp=int(event.timestamp * 1000),
            raw_event=None,
            name="analysis_progress",  # Custom event name
            value=custom_data,  # Pass custom data
        )
```

#### Using the Custom Translator

Inject the custom translator when creating an `AgUiAgent`:

```python
from trpc_agent_sdk.server.ag_ui import AgUiAgent

def create_agui_agent() -> AgUiAgent:
    """Create an AgUiAgent with a custom event translator"""
    from agent.event_translator import CustomAgUiEventTranslator

    # Create the custom translator instance
    custom_translator = CustomAgUiEventTranslator()

    agui_agent = AgUiAgent(
        trpc_agent=your_langgraph_agent,
        app_name="your_app",
        event_translator=custom_translator,  # Inject the custom translator
    )
    return agui_agent
```

## Complete Examples

For complete LangGraph Agent examples, see:
- Basic example: [examples/langgraph_agent](../../../examples/langgraph_agent/README.md)
- AG-UI custom event example: examples/trpc_agui_with_langgraph_custom (example to be added)
