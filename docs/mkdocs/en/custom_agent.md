# Custom Agent

When the framework's preset multi-agent modes (Chain/Parallel/Cycle) and their combinations cannot meet your requirements, you can directly inherit `BaseAgent` and implement custom control flow to define **arbitrary multi-agent orchestration logic**.

Custom Agents are also suitable for **complex orchestration scenarios** that the framework's preset multi-agent modes (Chain / Parallel / Cycle) and their combinations cannot handle: conditional routing, dynamic agent selection, complex state management, and more can all be freely implemented within a single `_run_async_impl`.


## Use Cases
Custom Agents are suitable for complex scenarios that the framework's built-in Multi Agents cannot handle, as illustrated below:

- **Conditional Logic**: Execute different sub-agents or take different paths based on runtime conditions or results from the previous step. For example, an intelligent diagnostic system first lets TriageAgent analyze symptoms — if "fever + cough" is detected, it invokes RespiratoryAgent; if "chest pain + shortness of breath" is detected, it invokes CardiacAgent. Agents on different paths use entirely different consultation strategies.
- **Complex State Management**: Implement complex state maintenance and update logic throughout the workflow that goes beyond simple sequential passing. For example, in a multi-round debate system, ArgumentAgent proposes a viewpoint, CriticAgent refutes it and updates the `argument_strength` state, then DefenderAgent decides based on the strength value whether to invoke ReinforcementAgent to strengthen the argument or proceed directly to ConclusionAgent.
- **External System Integration**: Directly integrate calls to external APIs, databases, or custom libraries within the orchestration control flow. For example, an intelligent news writing system lets DataCollectorAgent call a news API to gather material, decides based on the API response status whether to let FactCheckerAgent verify the information, and finally lets WriterAgent choose different writing styles based on the verification results.
- **Dynamic Agent Selection**: Choose which sub-agent to run next based on dynamic evaluation of the situation or input. For example, a code review system lets ComplexityAnalyzerAgent analyze code complexity — if complexity_score > 8, it invokes SeniorReviewerAgent and SecurityAuditorAgent; if < 3, it only invokes BasicReviewerAgent.
- **Custom Business Workflows**: Implement orchestration logic that does not conform to standard sequential, parallel, or loop structures. For example, an AI game strategy system forms a triangular loop among StrategyAgent, TacticsAgent, and ExecutionAgent, where each agent can hand control to either of the other two agents based on changes in the game situation.

## Implementation Overview

Inherit `BaseAgent` and implement the `_run_async_impl` method:

```python
from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from typing import AsyncGenerator

class MyCustomAgent(BaseAgent):
    """Custom Agent example"""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        # Implement your custom logic here
        ...
```
## Core Method Details

The `_run_async_impl` method is the core of a Custom Agent. You need to implement the following within it:

1. **Run sub_agents**: Use `sub_agent.run_async(ctx)` to execute sub-agents and propagate events
2. **Manage state**: Read and write the state dictionary via `ctx.session.state` to pass data between agent invocations
3. **Implement control flow**: Use standard Python constructs (`if`/`elif`/`else`, `for`/`while` loops, `try`/`except`) to create complex conditional or iterative workflows


## Implementing Custom Logic
### Core Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `_run_async_impl` | `(ctx: InvocationContext) -> AsyncGenerator[Event, None]` | **Must implement**. Core business logic that produces an event stream via `yield` |
| `run_async` | `(parent_context: InvocationContext) -> AsyncGenerator[Event, None]` | Public entry point inherited from `BaseAgent`. Automatically manages callbacks and context, internally calls `_run_async_impl` |

### Core Patterns

- Read and write state via `ctx.session.state` to pass data between agent invocations
- Use `sub_agent.run_async(ctx)` to run sub-agents, and yield events one by one with `async for event in ...`
- Use `create_text_event(ctx, text)` to create custom text events
- Determine whether to terminate early via `ctx.actions.escalate` or `ctx.session.state`

### Running Sub-Agents

Choose when to run sub-agents as needed within `_run_async_impl`. Sub-agents can be `LlmAgent`, `ChainAgent`, `ParallelAgent`, or even another custom agent.

```python
async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
    async for event in self.some_sub_agent.run_async(ctx):
        yield event
```

### State Management and Conditional Control Flow

It is recommended to manage runtime state through the framework's State, allowing agents to flexibly participate in conditional control flows.

```python
async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
    # Read data written by the previous agent (field is set by the agent's output_key)
    previous_result = ctx.session.state.get("some_key")

    if previous_result == "value_a":
        async for event in self.agent_a.run_async(ctx):
            yield event
    else:
        async for event in self.agent_b.run_async(ctx):
            yield event
```

### Terminating Agent Execution

In certain scenarios (e.g., Cycle loops), you may need to terminate the agent application execution early. You can use `ctx.actions.escalate` and similar mechanisms to achieve early termination.

```python
async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
    async for event in self.some_sub_agent.run_async(ctx):
        if ctx.actions.escalate:
            return
        if ctx.session.state.get("process_complete"):
            return
        yield event
```

### Controlling Event Visibility

In certain scenarios, an agent may produce information during execution that should not be visible externally (e.g., critical reasoning processes). You can control whether an event is returned by the Runner by setting the event's `visible` field.

The framework also provides the `create_text_event` utility function for conveniently creating text events:

```python
from trpc_agent_sdk.events import create_text_event

async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
    async for event in self.analyzer.run_async(ctx):
        if should_hide(event):
            event.visible = False
        yield event

    # Create an invisible internal log event
    yield create_text_event(
        ctx=ctx,
        text="Internal processing: analyzing document type...",
        visible=False,
    )
```

## Example: Intelligent Document Processing Agent

The following is a practical Custom Agent example that demonstrates dynamically selecting a processing pipeline based on document type:

```python
from trpc_agent_sdk.agents import BaseAgent, LlmAgent, ChainAgent
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from pydantic import ConfigDict
from typing import AsyncGenerator

class SmartDocumentProcessor(BaseAgent):
    """Intelligent Document Processing Agent
    
    Dynamically selects a processing strategy based on document type and content complexity:
    - Simple documents: process directly
    - Complex documents: analyze → process → validate
    - Technical documents: specialized processing pipeline
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
    )

    def __init__(self, **kwargs):
        # Define various processing agents
        self.document_analyzer = LlmAgent(
            name="document_analyzer",
            model="deepseek-chat", 
            instruction="Analyze the document type and complexity, output: simple/complex/technical",
            output_key="doc_type"
        )
        
        self.simple_processor = LlmAgent(
            name="simple_processor",
            model="deepseek-chat",
            instruction="Process simple document: {user_input}",
            output_key="processed_content"
        )
        
        # Complex document processing chain: analyze → process
        complex_analyzer = LlmAgent(
            name="complex_analyzer", 
            model="deepseek-chat",
            instruction="Perform in-depth analysis of complex document structure and key points: {user_input}",
            output_key="complex_analysis"
        )
        
        complex_processor = LlmAgent(
            name="complex_processor",
            model="deepseek-chat", 
            instruction="Process complex document based on analysis: {complex_analysis}",
            output_key="processed_content"
        )
        
        # Wrap the complex document processing pipeline using ChainAgent
        self.complex_processor_chain = ChainAgent(
            name="complex_processor_chain",
            description="Complex document processing: analyze → process", 
            sub_agents=[complex_analyzer, complex_processor]
        )
        
        self.technical_processor = LlmAgent(
            name="technical_processor",
            model="deepseek-chat",
            instruction="Process using the technical document specialized pipeline: {user_input}",
            output_key="processed_content"
        )
        
        self.quality_validator = LlmAgent(
            name="quality_validator",
            model="deepseek-chat",
            instruction="Validate processing quality: {processed_content}, output suggestions if issues are found",
            output_key="quality_feedback"
        )
        
        # Add all agents to sub_agents
        sub_agents = [
            self.document_analyzer,
            self.simple_processor,
            self.complex_processor_chain,  # Complex document processing pipeline wrapped with ChainAgent
            self.technical_processor,
            self.quality_validator
        ]
        
        super().__init__(sub_agents=sub_agents, **kwargs)
    
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Implement the custom logic for intelligent document processing"""
        
        # Step 1: Analyze document type
        async for event in self.document_analyzer.run_async(ctx):
            yield event
        
        doc_type = ctx.session.state.get("doc_type", "simple")
        
        # Step 2: Select processing strategy based on document type
        if doc_type == "simple":
            # Process simple documents directly
            async for event in self.simple_processor.run_async(ctx):
                yield event
                
        elif doc_type == "complex":
            # Complex documents: use ChainAgent to execute analyze → process pipeline
            async for event in self.complex_processor_chain.run_async(ctx):
                yield event
                
            # Complex documents require quality validation
            async for event in self.quality_validator.run_async(ctx):
                yield event
                
        elif doc_type == "technical":
            # Technical documents use a specialized pipeline
            async for event in self.technical_processor.run_async(ctx):
                yield event
                
            # Technical documents also require validation
            async for event in self.quality_validator.run_async(ctx):
                yield event
        
        # Additional conditional logic can be added here, for example:
        # - Check processing result quality to decide whether to reprocess
        # - Determine whether to execute additional steps based on user permissions
        # - Adjust the processing pipeline based on external system status
```

## Complete Example

For the complete Custom Agent example, see: [examples/llmagent_with_custom_agent/run_agent.py](../../../examples/llmagent_with_custom_agent/run_agent.py)


## Extension Recommendations

| Direction | Approach |
|-----------|----------|
| **Integrate Tools** | Configure sub-agents with the `tools` parameter, such as `FunctionTool` to connect databases / HTTP / internal services |
| **Add Validation** | Perform parameter validation, risk control, and feature flag checks before branching |
| **Progressive Evolution** | When if-else branches become excessive or collaboration is needed, smoothly transition to `ChainAgent` / `ParallelAgent` or `Graph` |
