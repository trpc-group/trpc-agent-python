# Session Summarizer

As the number of conversation turns increases, events accumulated in a Session continue to grow, leading to excessively long context and increased token consumption. Session Summarizer intelligently compresses historical conversations into summaries, effectively controlling session size while preserving key context. It is an essential core component in TRPC Agent for long-conversation scenarios.

## Overview

Session Summarizer intelligently analyzes conversation history and summarizes older conversation events into concise summaries, thereby:

- **Session Compression**: Compresses long conversation history into concise summaries
- **Reduced Token Usage**: Reduces token consumption and saves costs
- **Preserves Important Context**: Retains key information and decisions
- **Improved Performance**: Reduces the number of events to process

## Core Components

### SessionSummarizer Class

The main summarizer class responsible for the core logic of session compression.

### SessionSummary Class

A data structure representing a session summary, containing summary information and metadata.

### SummarizerSessionManager Class

The session summary manager responsible for automatically triggering and managing summarization at the SessionService level.

---

## Basic Usage

### 1. Creating a SessionSummarizer

```python
from trpc_agent_sdk.sessions import SessionSummarizer
from trpc_agent_sdk.models import OpenAIModel

# Create an LLM model
model = OpenAIModel(
    model_name="deepseek-chat",
    api_key="your-api-key",
    base_url="https://api.deepseek.com/v1"
)

# Summarize after every summarizer_count conversation turns
# If summarizer_count is set to 3, summarization is performed after every 3 turns
summarizer_count = 3

# Create the summarizer
summarizer = SessionSummarizer(
    model=model,
    # If check_summarizer_functions is not set, the default is set_summarizer_conversation_threshold(100)
    # Summarization is triggered when the check functions in check_summarizer_functions return True
    # When multiple check functions exist, AND logic is used by default (summarization occurs only when all functions return True)
    check_summarizer_functions=[
        set_summarizer_conversation_threshold(summarizer_count),  # Conversation count check function, summarizes after every summarizer_count turns
        # set_summarizer_time_interval_threshold(10),              # Time check function, summarizes every 10 seconds
        # set_summarizer_token_threshold(1000),                   # Token check function, summarizes every 1000 tokens
        # set_summarizer_events_count_threshold(30),              # Event count check function, summarizes every 30 events
        # set_summarizer_important_content_threshold(),            # Important content check function, determines whether to summarize based on content importance
        # set_summarizer_check_functions_by_and(                   # Combined check function with AND logic, triggers summarization when all check functions return True
        #     set_summarizer_conversation_threshold(1),
        #     set_summarizer_time_interval_threshold(10),
        #     set_summarizer_token_threshold(1000),
        #     set_summarizer_important_content_threshold(),
        # ),
        # set_summarizer_check_functions_by_or(                    # Combined check function with OR logic, triggers summarization when any check function returns True
        #     set_summarizer_conversation_threshold(1),
        #     set_summarizer_time_interval_threshold(10),
        # )
    ],
    max_summary_length=600,      # Maximum length of the summary text, default is 1000, truncated with ... if exceeded
    keep_recent_count=4,         # Number of recent conversation turns to keep, default is 10
)
```

---

### 2. Automatic Summarization (SessionService Level)

Use `SummarizerSessionManager` with `SessionService` to enable automatic summarization in the Runner.

**Complete Example**: Refer to [`examples/session_summarizer/run_agent.py`](../../../examples/session_summarizer/run_agent.py)

```python
from trpc_agent_sdk.sessions import SummarizerSessionManager, InMemorySessionService
from trpc_agent_sdk.runners import Runner

# Create SummarizerSessionManager
summarizer_manager = SummarizerSessionManager(
    model=model,
    summarizer=summarizer,
    auto_summarize=True,  # Default is True; if set to False, automatic summarization is disabled
)

# Use with SessionService
session_service = InMemorySessionService(summarizer_manager=summarizer_manager)

# Create Runner
runner = Runner(
    app_name=app_name,
    agent=agent,
    session_service=session_service
)

# Run the Agent (summarization is triggered automatically)
for i, user_input in enumerate(conversations):
    await run_agent(runner=runner, user_id=user_id, session_id=session_id, user_input=user_input)
    
    # Summarization should be triggered after every summarizer_count turns
    if i % summarizer_count == 0:
        session = await session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id
        )
        if session:
            # Get the summary content
            summary = await session_service.summarizer_manager.get_session_summary(session)
            if summary:
                print(f"   - Summary text: {summary.summary_text[:100]}...")
                print(f"   - Original event count: {summary.original_event_count}")
                print(f"   - Compressed event count: {summary.compressed_event_count}")
                print(f"   - Compression ratio: {summary.get_compression_ratio():.1f}%")
```

**Workflow**:

1. **Automatic Triggering**: After every N conversation turns, `SummarizerSessionManager` automatically checks whether summarization is needed
2. **Summary Generation**: Uses the LLM to compress historical conversations into concise summaries
3. **Event Compression**: Retains the most recent N conversation turns and replaces older conversations with summary text
4. **Session Update**: Updates the event list in the Session

**Summary Content Usage**:

After each summarization, the content `summary.summary_text` is injected into the corresponding request prompt in subsequent conversations. This process is transparent to the user.

---

### 3. Manual Session Summarization

**Complete Example**: Refer to [`examples/session_summarizer/run_agent.py`](../../../examples/session_summarizer/run_agent.py)

```python
import time

# Build events in the session
session = await create_test_session_with_events(session_service, app_name, user_id, session_id)

# Force manual summarization (force=True bypasses trigger conditions)
await session_service.summarizer_manager.create_session_summary(session, force=True)

if session:
    summary = await session_service.summarizer_manager.get_session_summary(session)
    if summary:
        print(f"   - Summary text: {summary.summary_text[:100]}...")
        print(f"   - Summary time: {time.ctime(summary.summary_timestamp)}")
        print(f"   - Original event count: {summary.original_event_count}")
        print(f"   - Compressed event count: {summary.compressed_event_count}")
        print(f"   - Compression ratio: {summary.get_compression_ratio():.1f}%")
```

---

## Configuration Parameters

### SessionSummarizer Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model` | LLMModel | Required | LLM model used for generating summaries |
| `check_summarizer_functions` | List[CheckSummarizerFunction] | `[set_summarizer_conversation_threshold(100)]` | List of check functions that trigger summarization. When multiple check functions exist, AND logic is used by default, meaning summarization occurs only when all functions return True |
| `max_summary_length` | int | 1000 | Maximum length of the generated summary |
| `keep_recent_count` | int | 10 | Number of recent events to keep after compression (counted by turns; each turn typically contains 2 events: a user message and an assistant response) |
| `summarizer_prompt` | str | DEFAULT_SUMMARIZER_PROMPT | Custom summary prompt template |

### SummarizerSessionManager Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model` | LLMModel | Required | LLM model used for generating summaries |
| `summarizer` | SessionSummarizer | None | Summarizer instance; if not provided, one is created with default configuration |
| `auto_summarize` | bool | True | Whether to enable automatic summarization; if set to False, automatic summarization is disabled |

### Configuration Recommendations

#### High-Frequency Conversation Scenario

```python
summarizer = SessionSummarizer(
    model=model,
    check_summarizer_functions=[set_summarizer_conversation_threshold(20)],  # More frequent summarization
    keep_recent_count=5,       # Keep fewer events
)
```

#### Long Conversation Scenario

```python
summarizer = SessionSummarizer(
    model=model,
    check_summarizer_functions=[set_summarizer_conversation_threshold(50)],  # Summarize after more events
    keep_recent_count=15,      # Keep more context
    max_summary_length=1500,   # Longer summary
)
```

#### Memory-Sensitive Scenario

```python
summarizer = SessionSummarizer(
    model=model,
    check_summarizer_functions=[set_summarizer_events_count_threshold(15)],  # Quick summarization
    keep_recent_count=3,       # Minimum retention
)
```

---

## Advanced Features

### 1. Skip Summarization Control

Certain events can be marked to skip summarization:

```python
from trpc_agent_sdk.types import EventActions

# Create an event that skips summarization
event = Event(
    invocation_id="inv_123",
    author="system",
    content=Content(parts=[Part.from_text("Debug information")]),
    actions=EventActions(skip_summarization=True)  # Skip summarization
)
```

### 2. Retrieving Summary Metadata

```python
# Get summarizer configuration info
metadata = summarizer.get_summary_metadata()
print(f"Model name: {metadata['model_name']}")
print(f"Retained event count: {metadata['keep_recent_count']}")
```

### 3. Using the SessionSummary Object

```python
from trpc_agent_sdk.sessions import SessionSummary

# Get the summary object
summary = await session_service.summarizer_manager.get_session_summary(session)

# Get the compression ratio
compression_ratio = summary.get_compression_ratio()
print(f"Compression ratio: {compression_ratio:.1f}%")

# Convert to dictionary
summary_dict = summary.to_dict()
```

### 4. Agent-Level Summarization (Filter Approach)

In multi-Agent scenarios, summarization covers data produced by all Agents. However, different Agents may produce different volumes of data, and the business logic may require summarizing only data produced by a specific Agent.

**Complete Implementation**: Refer to [`examples/session_summarizer/agent/filters.py`](../../../examples/session_summarizer/agent/filters.py)

**Usage**:

```python
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.sessions import SessionSummarizer
from trpc_agent_sdk.context import get_invocation_ctx

class AgentSessionSummarizerFilter(BaseFilter):
    """Agent session summarizer filter."""
    
    def __init__(self, model: OpenAIModel):
        super().__init__()
        # Create the summarizer
        self.summarizer = SessionSummarizer(
            model=model,
            max_summary_length=600,
            keep_recent_count=4,  # Number of recent conversation turns to keep, default is 10
        )
    
    async def _after_every_stream(self, ctx: AgentContext, req: Any, rsp: FilterResult) -> None:
        """Check whether summarization is needed after each streaming response"""
        # The current agent stream returns one event per response; rsp is of type FilterResult, where rsp.rsp is of type Event
        if not rsp.rsp.partial:
            events = ctx.metadata.get("events", [])
            conversation_text = self.summarizer._extract_conversation_text(events)
            # Trigger summarization when conversation text exceeds 12KB
            if len(conversation_text) > 12 * 1024:
                await self._do_summarize(ctx)
        
        # Cache the executed events in the context
        if "events" not in ctx.metadata:
            ctx.metadata["events"] = []
        ctx.metadata["events"].append(rsp.rsp)
    
    async def _after(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        """Post-processing after the entire agent execution completes"""
        await self._do_summarize(ctx)
    
    async def _do_summarize(self, ctx: AgentContext):
        """Perform summarization"""
        invocation_ctx: InvocationContext = get_invocation_ctx()
        
        # Get the events produced by this agent
        events = ctx.metadata.pop("events", [])
        
        # In multi-Agent concurrent execution, a coroutine lock is needed here to ensure ordering
        # Async network operations may yield the coroutine, causing ordering issues
        
        # Remove the events retained by this agent from the global session
        for event in events:
            if event in invocation_ctx.session.events:
                invocation_ctx.session.events.remove(event)
        
        session_id = invocation_ctx.session.id
        conversation_text = self.summarizer._extract_conversation_text(events)
        
        # Summarize the events produced by this agent
        # create_session_summary_by_events is specifically designed for Agent-level summarization
        summary_text, compressed_events = await self.summarizer.create_session_summary_by_events(
            events, session_id, ctx=invocation_ctx
        )
        
        # Add the compressed events back to the session
        if compressed_events:
            invocation_ctx.session.events.extend(compressed_events)

# Use in an Agent
def create_agent():
    agent = LlmAgent(
        name="analyze",
        model=model,
        description="Tool for analyzing strategies",
        tools=[log_set, metric_set],
        filters=[AgentSessionSummarizerFilter(model)],  # Configure filter
        # ...
    )
    return agent
```

**Summarization using the Filter approach**:

1. **Record Events**: Record the events produced by this Agent
2. **Event Isolation**: Remove these events from the global session (to avoid conflicts with SessionService-level summarization)
3. **Perform Summarization**: Summarize the events
4. **Event Replacement**: Append the summarized events back to the global session

**Comparison of the Two Summarization Approaches**:

| Feature | SessionService-Level Summarization | Agent-Level Summarization |
|---------|-----------------------------------|--------------------------|
| **Trigger Timing** | After every N conversation turns | After each Agent execution or when text exceeds a threshold |
| **Summarization Scope** | All events in the entire Session | Events produced by a single Agent |
| **Applicable Scenarios** | Single-Agent scenarios | Multi-Agent collaboration scenarios |
| **Configuration Method** | SessionService initialization | Agent Filter configuration |
| **Advantages** | Simple to use, automatically managed | Finer-grained control, supports multiple Agents |

---

## Workflow

### 1. Summarization Trigger Conditions

The summarizer is triggered when **user-defined trigger conditions are met**. The framework provides several built-in trigger conditions:

- **`set_summarizer_conversation_threshold(conversation_count)`**: Sets the conversation count threshold. Summarization is performed after the conversation count reaches `conversation_count`. Default `conversation_count` is 100
- **`set_summarizer_token_threshold(token_count)`**: Sets the session token threshold. Summarization is performed after the token count reaches `token_count`
- **`set_summarizer_events_count_threshold(event_count)`**: Sets the event count threshold. Summarization is performed after the event count reaches `event_count`. Default `event_count` is 30
- **`set_summarizer_time_interval_threshold(time_interval)`**: Sets the time interval threshold. Summarization is performed after the conversation interval reaches `time_interval`. Default `time_interval` is 300s (5 minutes)
- **`set_summarizer_important_content_threshold(important_content_count)`**: Sets the important content count. Summarization is performed after the number of spaces in conversation content exceeds `important_content_count`. Default `important_content_count` is 10
- **`set_summarizer_check_functions_by_and(funcs: list[CheckSummarizerFunction])`**: Combined check function. Summarization is performed when all functions in `funcs` return True (AND logic)
- **`set_summarizer_check_functions_by_or(funcs: list[CheckSummarizerFunction])`**: Combined check function. Summarization is performed when any function in `funcs` returns True (OR logic)

**Trigger Logic**:

- When multiple check functions exist, **AND logic is used by default**, meaning summarization occurs only when all functions return True
- You can explicitly specify the logic using `set_summarizer_check_functions_by_and` or `set_summarizer_check_functions_by_or`

---

### 2. Summary Generation

Summary generation uses the default prompt template:

```
Please summarize the following conversation, focusing on:
1. Key decisions made
2. Important information shared
3. Actions taken or planned
4. Context that should be remembered for future interactions

Keep the summary concise but comprehensive. Focus on what would be most important to remember for continuing the conversation.

Conversation:
{conversation_text}

Summary:
```

**Custom Prompt Template**:

To replace the default prompt template, use the following approach:

```python
from textwrap import dedent

your_summarizer_prompt = dedent("""\
Please summarize the following conversation, focusing on:
1. Key decisions
2. Important information
3. Action plans

Conversation:
{conversation_text}

Summary:""")

# conversation_text represents the conversation content; this placeholder is required
summarizer = SessionSummarizer(
    model=model,
    summarizer_prompt=your_summarizer_prompt,
    # ...
)
```

---

## Output Analysis

**Complete Example Output**: Refer to [`examples/session_summarizer/README.md`](../../../examples/session_summarizer/README.md)

### Key Observations

#### 1️⃣ **Automatic Summarization Triggering**

```
After turn 4 → Summarization triggered (configured with set_summarizer_conversation_threshold(3))
After turn 7 → Summarization triggered
After turn 13 → Summarization triggered
```

**Explanation**:
- Configured with `set_summarizer_conversation_threshold(3)`, summarization is triggered after every 3 conversation turns
- Summarization is automatically performed when the conversation turn count reaches the threshold

#### 2️⃣ **Event Compression Results**

| Turn | Original Event Count | Compressed Event Count | Compression Ratio |
|------|---------------------|----------------------|-------------------|
| Turn 4 | 8 | 5 | 37.5% |
| Turn 7 | 8 | 5 | 37.5% |
| Turn 13 | 13 | 5 | 61.5% |
| Manual Summary | 39 | 5 | 87.2% |

**Explanation**:
- `keep_recent_count=4` is configured to retain the most recent 4 conversation turns (8 events: 4 turns × 2 events/turn)
- Older conversations are compressed into summary text
- The compression ratio gradually increases as the conversation progresses

#### 3️⃣ **Summary Content Quality**

The summary text contains:
- ✅ **Key Decisions**: User's learning choices and plans
- ✅ **Important Information**: Python concepts and knowledge points
- ✅ **Action Plans**: Project practice and learning paths

**Explanation**:
- The LLM-generated summary retains the core information of the conversation
- The summary text is clearly formatted, facilitating subsequent retrieval and usage

---

## Best Practices

### 1. Configuration Tuning

- Adjust the conversation count in `set_summarizer_conversation_threshold` based on conversation frequency
- Adjust `keep_recent_count` based on memory constraints
- Adjust `max_summary_length` based on model capabilities

### 2. Content Filtering

- Use `skip_summarization` to mark unimportant debug information
- Filter out system events before summarization
- Preserve user intent and key decisions

### 3. Cost Control

- Choose an appropriate model to balance quality and cost
- Implement summary caching to reduce redundant computation
- Monitor API call frequency and costs

### 4. Multi-Agent Scenarios

- Use Agent-level summarization (Filter approach) to avoid conflicts
- Configure different summarization strategies for different Agents
- Ensure concurrency safety by adding coroutine locks when necessary

---

## FAQ

### Q: Will summarization lose important information?

A: The summarizer is specifically designed to preserve key information, including decisions, important data, and context. It is recommended to retain sufficient recent events via the `keep_recent_count` parameter.

### Q: How to avoid over-summarization?

A: Adjust the `set_summarizer_conversation_threshold` parameter to control summarization frequency, and use `skip_summarization` to mark events that should not be summarized.

### Q: What happens if summarization fails?

A: The summarizer includes error handling mechanisms. On failure, it returns the original session without affecting the normal conversation flow.

### Q: How to evaluate summary quality?

A: Summary quality can be evaluated using metrics such as compression ratio, information coverage, and user feedback.

### Q: API call failure

A: Perform the following checks:
- Verify that the API key is correct
- Confirm that the network connection is functioning properly
- Verify that the model name is correct

### Q: Poor summary quality

A: Solutions:
- Adjust the `max_summary_length` parameter
- Use a higher-quality model (e.g., GPT-4)
- Ensure the conversation content contains sufficient information
- Customize the `summarizer_prompt` template

### Q: Low compression ratio

A: Solutions:
- Adjust the `keep_recent_count` parameter
- Lower the conversation summarization threshold set by `set_summarizer_conversation_threshold` for more frequent summarization
- Check whether too many events are marked to skip summarization

### Q: Summarizing data from a specific Agent

A: Solution: Refer to `4. Agent-Level Summarization (Filter Approach)` in the Advanced Features section. Use `AgentSessionSummarizerFilter` for summarization within an Agent Filter.

---

## Reference Implementation

Session Summarizer is inspired by [Agno summarizer.py](https://github.com/agno-agi/agno/blob/main/libs/agno/agno/memory/v2/summarizer.py), with the following key differences:

- **Data Structure**: TRPC Agent uses a more complex Event structure
- **Model Invocation**: Uses LlmRequest and generate_async
- **Integration**: Deep integration with Session Service
- **Configuration Options**: Provides more customization options
- **Multi-Agent Support**: Supports Agent-level summarization (Filter approach)

---

## Complete Examples

See the complete summarization usage examples:

- 📁 **Example Code**: [`examples/session_summarizer/run_agent.py`](../../../examples/session_summarizer/run_agent.py)
- 📁 **Example Documentation**: [`examples/session_summarizer/README.md`](../../../examples/session_summarizer/README.md)
- 📁 **Agent Filter Implementation**: [`examples/session_summarizer/agent/filters.py`](../../../examples/session_summarizer/agent/filters.py)

The examples demonstrate two summarization approaches:

1. **SessionService-Level Summarization**: Uses `SummarizerSessionManager` for automatic summarization at the session service level
2. **Agent-Level Summarization**: Uses `AgentSessionSummarizerFilter` for summarization within an Agent Filter

Both approaches can be combined. Choose the most suitable approach based on your actual requirements.
