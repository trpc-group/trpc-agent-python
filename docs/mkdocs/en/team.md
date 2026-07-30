
# Team Agent

TeamAgent is a component in the trpc-agent framework for implementing multi-agent collaboration. It implements a Coordinate pattern similar to the Agno framework. TeamAgent has an internal team leader (Leader) responsible for decomposing user requests, delegating tasks to appropriate member agents, tracking task completion, and synthesizing member responses to generate the final answer.

Unlike the deterministic orchestration patterns (Chain, Parallel, Cycle) in [Multi Agents](./multi_agents.md), TeamAgent uses a LeaderAgent to decompose subtasks, delegate tasks, track task completion, and re-plan when task processing fails, making it more suitable for complex scenarios that require intelligent coordination.

## Why Team?
A single agent typically excels at only one role. In real-world applications, we often need multiple roles to collaborate, such as:
- Researching background information
- Writing code
- Reviewing and error correction

The goal of Team is to combine these roles through a small and clear API without introducing hard-to-use "multi-layer abstractions."
Here, API refers to Application Programming Interface.

## Design Overview

Core design principles of TeamAgent:

- **Leader-Member architecture**: The internal Leader Agent uses an LLM to decide which member to delegate tasks to
- **Tool-driven delegation**: The Leader delegates tasks by calling the `delegate_to_member` tool
- **Message isolation control**: Through the `override_messages` mechanism, TeamAgent fully controls the message context each member sees
- **State persistence**: Uses `TeamRunContext` stored in `session.state`, supporting multi-turn conversations

### Execution Flow

```
User request → Leader analyzes task → Leader calls delegate_to_member(member, task)
    → TeamAgent intercepts signal → Executes target member
    → Collects member response → Updates TeamRunContext
    → Leader continues processing or synthesizes final response → Returns to user
```

## Simple Example

Below is a complete content creation team example demonstrating TeamAgent usage. The built-in Leader in TeamAgent delegates appropriate tasks to members and tracks task completion. If completed, it finishes. Team members (researcher and writer) share the Team's history (share_member_interactions=True), so the writer can write based on the researcher's content.

```python
import asyncio
import os
import uuid

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.teams import TeamAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import Content, Part


# Define member tools
async def search_web(query: str) -> str:
    """Search the web for information."""
    return f"Search results for '{query}': Found relevant information..."

async def check_grammar(text: str) -> str:
    """Check grammar of the text."""
    return f"Grammar check completed: Text quality is good."


def create_team():
    model = OpenAIModel(
        model_name="deepseek-chat",
        api_key=os.environ.get("TRPC_AGENT_API_KEY", ""),
        base_url="https://api.deepseek.com/v1",
    )

    # Researcher - responsible for information search
    researcher = LlmAgent(
        name="researcher",
        model=model,
        description="Research expert",
        instruction="""You are a research expert. When receiving a topic:
1. Use the search_web tool to search for information
2. Provide comprehensive factual information
Keep your response concise.""",
        tools=[FunctionTool(search_web)],
    )

    # Writer - responsible for content creation
    writer = LlmAgent(
        name="writer",
        model=model,
        description="Writing expert",
        instruction="""You are a professional writer. When receiving information:
1. Transform research into engaging content
2. Use the check_grammar tool to verify quality
Keep your response concise.""",
        tools=[FunctionTool(check_grammar)],
    )

    # Create team
    return TeamAgent(
        name="content_team",
        model=model,
        members=[researcher, writer],
        instruction="""You are the content team editor. Your role is:
1. First delegate tasks to the researcher to gather information
2. Then have the writer create content based on research
3. Synthesize the final response for the user""",
        share_member_interactions=True,  # Allow members to share interaction information
    )


async def main():
    APP_NAME = "content_team_demo"
    USER_ID = "demo_user"
    session_id = str(uuid.uuid4())

    team = create_team()
    session_service = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=team, session_service=session_service)

    query = "Please write a short article about AI"
    user_message = Content(parts=[Part.from_text(text=query)])

    async for event in runner.run_async(
            user_id=USER_ID,
            session_id=session_id,
            new_message=user_message,
    ):
        if event.content and event.content.parts and event.partial:
            for part in event.content.parts:
                if part.text:
                    print(part.text, end="", flush=True)

    await runner.close()


if __name__ == "__main__":
    asyncio.run(main())
```

**Full example:**
- [examples/team/run_agent.py](../../../examples/team/run_agent.py) - Basic team collaboration example

## Configuring Skills for the Leader

TeamAgent supports configuring Agent Skills for the Leader to extend the Leader's capabilities (such as executing skill scripts, generating intermediate materials, reading skill documentation). Usage is shown in the example below:

```python
from trpc_agent_sdk.skills import create_default_skill_repository
from trpc_agent_sdk.code_executors import create_local_workspace_runtime


workspace_runtime = create_local_workspace_runtime()
repository = create_default_skill_repository("./skills", workspace_runtime=workspace_runtime)

TeamAgent(
    name="content_team_with_skill",
    model=model,
    members=[researcher, writer],
    instruction="""xxx""",
    tools=[skill_tool_set],  # SkillToolSet instance, providing skill search, read, and execution tool capabilities
    skill_repository=repository,
    share_member_interactions=True,
)
```

For more on Skill usage, see [skill.md](./skill.md).

**Full example:**
- [examples/team_with_skill/run_agent.py](../../../examples/team_with_skill/run_agent.py) - TeamAgent example with Leader integrated Skills
- *Note: In this example's instruction, the Leader is forced to call the skills tool series for demonstration purposes. This is not required in actual usage.*

## Team History Session Message Management

TeamAgent provides multiple parameters to control history information sharing:

### share_member_interactions

Controls whether interaction information between members in the current turn is shared with other members:

- `share_member_interactions=True`: Later-executing members can see the tasks and responses of earlier-executing members, facilitating collaboration and information continuity between members.
- `share_member_interactions=False` (default): Members are isolated from each other, only seeing the task assigned by the Leader, unaware of other members' execution.

```python
team = TeamAgent(
    name="team",
    model=model,
    members=[agent1, agent2],
    share_member_interactions=True,  # Members can see other members' tasks and responses
)
```

When enabled, later-executing members can see the tasks and responses of earlier-executing members, injected in the following format:
```
<member_interaction_context>
See below interactions with other team members.
Member: researcher
Task: Search for AI information
Response: Found relevant AI research...
</member_interaction_context>
```

### num_member_history_runs

Controls whether members see their own historical interaction records (within the same turn and across turns):

```python
team = TeamAgent(
    name="team",
    model=model,
    members=[agent1, agent2],
    num_member_history_runs=0,  # Default, disables member self-history
)
```

- `num_member_history_runs=0`: Does not inject the member's own history.
- `num_member_history_runs=1`: Injects the most recent 1 turn of member self-history, suitable for scenarios where "the Leader delegates to the same member multiple times within the same turn."
- `num_member_history_runs>1`: Injects the most recent N turns of member self-history, supporting cross-turn context continuation.

Example (preserving member self-history within the same turn):

```python
team = TeamAgent(
    name="team",
    model=model,
    members=[agent1, agent2],
    share_member_interactions=True,  # Still sharing other members' interactions
    num_member_history_runs=1,       # Member sees its own most recent turn history
)
```

When enabled, members receive self-history context in the following format:

```
<member_self_history_context>
See below your previous interactions in this team.
Task: ...
Response: ...
</member_self_history_context>
```

### share_team_history

Controls whether team-level conversation history is shared with members:

```python
team = TeamAgent(
    name="team",
    model=model,
    members=[agent1, agent2],
    share_team_history=True,        # Share team history with members
    num_team_history_runs=3,        # Share the most recent 3 turns of history
)
```

### add_history_to_leader

Controls whether the Leader includes past conversation history (supports multi-turn conversations):

```python
team = TeamAgent(
    name="team",
    model=model,
    members=[agent1, agent2],
    add_history_to_leader=True,     # Leader includes history (enabled by default)
    num_history_runs=3,             # Include the most recent 3 turns of history
)
```

## Controlling Member Messages Injected into the Team Session

When a member finishes execution, all messages it produced (including intermediate tool calls, tool return results, final text replies, etc.) are injected back into the Leader's context as the response part of the delegation record. By default, all messages are retained (`keep_all_member_message`), but when a member has many execution steps, this may cause the Leader's context to become too long, affecting inference efficiency and token consumption.

Therefore, the framework provides `member_message_filter` to filter or summarize member messages, controlling which content is ultimately passed to the Leader. The filter receives `List[Content]` produced during member execution and returns a `str` as the response text in the delegation record. Three configuration methods are supported:

- **Global configuration**: Pass a single filter function that applies to all members
- **Per-member configuration**: Pass a `Dict[str, filter]` to specify different filter strategies for different members; unspecified members use the default `keep_all_member_message`
- **Custom function**: Implement a synchronous or asynchronous function with the signature `(List[Content]) -> str`

### Built-in Filters

```python
from trpc_agent_sdk.teams import keep_all_member_message, keep_last_member_message

# Keep all messages (default behavior)
team = TeamAgent(
    name="team",
    model=model,
    members=[analyst],
    member_message_filter=keep_all_member_message,
)

# Keep only the last message (suitable for multi-step members where only the final result matters)
team = TeamAgent(
    name="team",
    model=model,
    members=[analyst],
    member_message_filter=keep_last_member_message,
)
```

### Per-Member Configuration

```python
team = TeamAgent(
    name="team",
    model=model,
    members=[researcher, writer],
    member_message_filter={
        "researcher": keep_all_member_message,   # Researcher keeps all messages
        "writer": keep_last_member_message,      # Writer keeps only the last message
    },
)
```

### Custom Filter

```python
from typing import List
from trpc_agent_sdk.types import Content

async def custom_filter(messages: List[Content]) -> str:
    """Custom message filter"""
    # Extract only text content
    texts = []
    for msg in messages:
        if msg.parts:
            for part in msg.parts:
                if part.text:
                    texts.append(part.text)
    return "\n".join(texts[-2:])  # Keep only the last two

team = TeamAgent(
    name="team",
    model=model,
    members=[analyst],
    member_message_filter=custom_filter,
)
```

**Full example:**
- [examples/team_member_message_filter/run_agent.py](../../../examples/team_member_message_filter/run_agent.py) - Member message filtering example

## Human-in-the-Loop (HITL)

TeamAgent supports Human-in-the-Loop, but **only the Leader can trigger it**. Member agents cannot be configured with `LongRunningFunctionTool`; if a member attempts to trigger HITL, a `RuntimeError` will be raised.

For more details on HITL, see [human_in_the_loop.md](./human_in_the_loop.md).

**Important restrictions**:
- `LongRunningFunctionTool` can only be configured in TeamAgent's `tools` parameter (used by the Leader)
- Member agents (including nested TeamAgents) cannot be configured with `LongRunningFunctionTool`
- If a member triggers a `LongRunningEvent`, a `RuntimeError` will be raised

```python
from trpc_agent_sdk.tools import LongRunningFunctionTool, FunctionTool
from trpc_agent_sdk.events import LongRunningEvent
from trpc_agent_sdk.types import FunctionResponse

# Define a tool that requires human approval
async def request_approval(content: str, reason: str) -> dict:
    """Request human approval before proceeding."""
    return {
        "status": "pending",
        "content": content,
        "reason": reason,
    }

# Create long-running tool
approval_tool = LongRunningFunctionTool(request_approval)

# ❌ Wrong: Members cannot be configured with LongRunningFunctionTool
# assistant_wrong = LlmAgent(
#     name="assistant",
#     model=model,
#     tools=[approval_tool],  # ❌ Will raise RuntimeError at runtime
# )

# ✅ Correct: Members should only be configured with regular tools
assistant = LlmAgent(
    name="assistant",
    model=model,
    tools=[FunctionTool(some_normal_tool)],  # ✅ Regular tool
)

# ✅ Correct: HITL tools are configured in TeamAgent's tools (used by the Leader)
team = TeamAgent(
    name="approval_team",
    model=model,
    members=[assistant],
    instruction="""When user requests to publish content,
use request_approval tool to get human approval first.""",
    tools=[approval_tool],  # ✅ Leader can use HITL tools
)
```

**Handling HITL at runtime**:

```python
async for event in runner.run_async(...):
    if isinstance(event, LongRunningEvent):
        print(f"Waiting for human approval: {event.function_call.args}")

        # Simulate human approval
        response_data = {"status": "approved", "approved_by": "admin"}

        # Build resume content
        resume_response = FunctionResponse(
            id=event.function_response.id,
            name=event.function_response.name,
            response=response_data,
        )
        resume_content = Content(
            role="user",
            parts=[Part(function_response=resume_response)]
        )

        # Resume by calling runner.run_async again with the resume message
        async for resume_event in runner.run_async(
            user_id=USER_ID,
            session_id=session_id,
            new_message=resume_content,
        ):
            # Handle post-resume events
            pass
```

**Full example:**
- [examples/team_human_in_the_loop/run_agent.py](../../../examples/team_human_in_the_loop/run_agent.py) - HITL example

## Various Member Types

TeamAgent members are not limited to LlmAgent. Any agent that inherits from BaseAgent and supports `override_messages` can serve as a member.

**Note: Currently, member agents only support: LlmAgent, ClaudeAgent, LangGraphAgent, RemoteA2aAgent. For support of other agent types, please contact us.**

### ClaudeAgent as a Member

```python
from trpc_agent_sdk.server.agents.claude import ClaudeAgent, setup_claude_env

# Set up Claude environment
setup_claude_env(proxy_host="0.0.0.0", proxy_port=8083, claude_models={"all": model})

# Create Claude member
claude_agent = ClaudeAgent(
    name="claude_expert",
    model=model,
    description="Expert powered by Claude",
    instruction="You are an expert assistant.",
    tools=[FunctionTool(some_tool)],
)
claude_agent.initialize()

# As a team member
team = TeamAgent(
    name="hybrid_team",
    model=model,
    members=[claude_agent],
    instruction="Delegate expert tasks to claude_expert.",
)
```

**Full example:**
- [examples/team_member_agent_claude/run_agent.py](../../../examples/team_member_agent_claude/run_agent.py) - ClaudeAgent member example

### LangGraphAgent as a Member

```python
from trpc_agent_sdk.agents import LangGraphAgent

# Build LangGraph
graph = build_your_langgraph()

# Create LangGraph member
langgraph_agent = LangGraphAgent(
    name="langgraph_expert",
    description="Expert powered by LangGraph",
    graph=graph,
    instruction="You are a calculation expert.",
)

# As a team member
team = TeamAgent(
    name="hybrid_team",
    model=model,
    members=[langgraph_agent],
    instruction="Delegate calculation tasks to langgraph_expert.",
)
```

**Full example:**
- [examples/team_member_agent_langgraph/run_agent.py](../../../examples/team_member_agent_langgraph/run_agent.py) - LangGraphAgent member example

### Remote A2A Agent as a Member

```python
from trpc_agent_sdk.server.a2a.agent import TrpcRemoteA2aAgent

# Create remote A2A member
remote_agent = TrpcRemoteA2aAgent(
    name="remote_service",
    service_name="trpc.agent.team_a2a.weather",  # Or use resolver_result
    description="Remote weather service agent",
)
await remote_agent.initialize()

# As a team member
team = TeamAgent(
    name="distributed_team",
    model=model,
    members=[remote_agent],
    instruction="Delegate weather queries to remote_service.",
)
```

**Full example:**
- examples/team_member_agent_remote_a2a/run_agent.py - Remote A2A member example (example to be added)

### TeamAgent as a Member (Nested Teams)

TeamAgent itself can also serve as a member of another TeamAgent, enabling hierarchical team structures. This pattern is suitable for complex organizational architectures, such as: Project Manager → Development Team → [Backend Developer, Frontend Developer].

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.teams import TeamAgent

# === Bottom tier: Members inside the dev team (LlmAgent) ===
backend_dev = LlmAgent(
    name="backend_dev",
    model=model,
    description="Backend development expert",
    instruction="You are a backend developer. Design APIs and server-side logic.",
    tools=[FunctionTool(design_api)],
)

frontend_dev = LlmAgent(
    name="frontend_dev",
    model=model,
    description="Frontend development expert",
    instruction="You are a frontend developer. Design UI components.",
    tools=[FunctionTool(design_ui)],
)

# === Middle tier: Nested development team (TeamAgent) ===
dev_team = TeamAgent(
    name="dev_team",
    model=model,
    description="Development team for technical implementation",
    members=[backend_dev, frontend_dev],
    instruction="""You are the dev team leader. Coordinate:
1. Backend tasks → delegate to backend_dev
2. Frontend tasks → delegate to frontend_dev
Then integrate the technical deliverables.""",
    share_member_interactions=True,
)

# === Middle tier: Documentation writer (LlmAgent), peer of dev_team ===
doc_writer = LlmAgent(
    name="doc_writer",
    model=model,
    description="Technical documentation writer",
    instruction="You are a technical writer. Create clear documentation.",
    tools=[FunctionTool(format_docs)],
)

# === Top layer: Project manager (contains TeamAgent as a member) ===
project_manager = TeamAgent(
    name="project_manager",
    model=model,
    members=[dev_team, doc_writer],  # dev_team is a TeamAgent!
    instruction="""You are the project manager. For each request:
1. Delegate technical tasks to dev_team
2. Delegate documentation to doc_writer
3. Synthesize the final deliverables""",
    share_member_interactions=True,
)
```

**Execution flow**:
```
User request → project_manager (top-level TeamAgent)
    → Delegates to dev_team (nested TeamAgent)
        → dev_team's Leader delegates to backend_dev
        → dev_team's Leader delegates to frontend_dev
        → dev_team returns integrated results
    → Delegates to doc_writer
    → project_manager synthesizes the final response
```

```python
# Wrong example: Nested team members using HITL tools will cause errors
inner_agent = LlmAgent(
    name="inner_agent",
    tools=[LongRunningFunctionTool(approval_func)],  # ❌ Will raise RuntimeError
)

inner_team = TeamAgent(
    name="inner_team",
    members=[inner_agent],
)

outer_team = TeamAgent(
    name="outer_team",
    members=[inner_team],  # inner_team as a member
    tools=[LongRunningFunctionTool(approval_func)],  # ✅ Only the top-level Leader can use HITL
)
```

**Full example:**
- [examples/team_member_agent_team/run_agent.py](../../../examples/team_member_agent_team/run_agent.py) - Nested TeamAgent example

## Other Configuration Options

### parallel_execution

Controls whether multiple delegations are executed in parallel. When the Leader delegates to multiple members in a single turn, you can choose sequential or parallel execution:

```
Sequential execution (parallel_execution=False, default):
  Leader -> analyst1 (1s) -> analyst2 (1s) -> analyst3 (1s)
  Total time: 3 seconds

Parallel execution (parallel_execution=True):
  Leader -> [analyst1 | analyst2 | analyst3] (run simultaneously)
  Total time: ~1 second (depends on the longest individual execution time)
```

Usage example:

```python
# Create multiple analyst members
market_analyst = LlmAgent(
    name="market_analyst",
    model=model,
    description="Market trends analysis expert",
    instruction="Analyze market trends for the given topic.",
    tools=[FunctionTool(analyze_market_trends)],
)

competitor_analyst = LlmAgent(
    name="competitor_analyst",
    model=model,
    description="Competitor analysis expert",
    instruction="Analyze competitors for the given topic.",
    tools=[FunctionTool(analyze_competitor)],
)

risk_analyst = LlmAgent(
    name="risk_analyst",
    model=model,
    description="Risk assessment expert",
    instruction="Assess risks for the given topic.",
    tools=[FunctionTool(analyze_risks)],
)

# Create a team with parallel execution enabled
team = TeamAgent(
    name="analysis_team",
    model=model,
    members=[market_analyst, competitor_analyst, risk_analyst],
    instruction="""You are a strategic analysis team leader.
When asked for comprehensive analysis, delegate to ALL THREE analysts
SIMULTANEOUSLY in a single response to enable parallel execution.
After receiving all results, synthesize them into a strategic recommendation.""",
    parallel_execution=True,  # Enable parallel execution
    share_member_interactions=True,
)
```
**Applicable scenarios**:
- Multiple members' tasks are independent and do not depend on each other's output
- Need to obtain parallel analysis results from multiple experts
- Want to reduce total execution time

**Full example:**
- [examples/team_parallel_execution/run_agent.py](../../../examples/team_parallel_execution/run_agent.py) - Parallel execution example

### max_iterations

Prevents infinite delegation loops:

```python
team = TeamAgent(
    name="team",
    model=model,
    members=[agent1, agent2],
    max_iterations=20,  # Maximum delegation iterations (default 20)
)
```

## Implementing Other Team Patterns

trpc-agent's TeamAgent implements the Coordinate pattern from Agno. Other team patterns from the Agno framework can be implemented by combining trpc-agent's Multi Agents components.

For detailed descriptions of each pattern below, see [Agno Team Delegation Patterns](https://docs.agno.com/basics/teams/delegation).

### Members Respond Directly

In Agno, setting `respond_directly=True` allows a member's response to be returned directly to the user without going through the Leader's synthesis.

In trpc-agent, a similar effect can be achieved using ChainAgent:

```python
from trpc_agent_sdk.agents import LlmAgent, ChainAgent

# Intent recognition agent - analyzes user requests and builds task descriptions
intent_agent = LlmAgent(
    name="intent_agent",
    model=model,
    instruction="""You are an intent analyzer. Analyze user input and:
1. Identify the user's intent (technical issue or sales inquiry)
2. Extract key information from the request
3. Formulate a clear task description for the next agent

Output format:
Intent: [technical/sales]
Task: [clear task description]""",
    output_key="analyzed_task",
)

# Router agent - delegates to the corresponding sub-agent based on intent
router = LlmAgent(
    name="router",
    model=model,
    instruction="""You are a router. Based on the analyzed task:

{analyzed_task}

Route to the appropriate agent:
- If Intent is technical → transfer to technical_support
- If Intent is sales → transfer to sales_consultant

Just route with the task, don't answer yourself.""",
    sub_agents=[technical_support, sales_consultant],  # Sub-agents respond directly
)

# Compose: Intent recognition → Routing (sub-agents respond directly)
respond_directly_pipeline = ChainAgent(
    name="respond_directly_pipeline",
    sub_agents=[intent_agent, router],
)
```

**Core idea**:
1. Use `intent_agent` to analyze user requests and build task descriptions
2. Use `router` to delegate tasks to the corresponding sub-agent based on intent
3. The sub-agent's response is the final response, without additional synthesis

### Send Input Directly to Members

In Agno, setting `determine_input_for_members=False` passes the user's raw input directly to members without the Leader rewriting it.

In trpc-agent, this can be implemented as follows:

```python
from trpc_agent_sdk.agents import LlmAgent, ChainAgent

# Router agent - only decides the target, does not rewrite the input
router = LlmAgent(
    name="router",
    model=model,
    instruction="""You are a router. Just decide which agent to use:
- English questions → transfer to english_agent
- Japanese questions → transfer to japanese_agent
Do NOT modify or rephrase the user's original question.""",
    sub_agents=[english_agent, japanese_agent],
)

# Summarizer agent (if needed)
summarizer = LlmAgent(
    name="summarizer",
    model=model,
    instruction="""Summarize the response from: {previous_response}""",
    output_key="final_response",
)

# Compose the pipeline
pipeline = ChainAgent(
    name="raw_input_pipeline",
    sub_agents=[router, summarizer],
)
```

**Core idea**: Explicitly instruct the router agent not to rewrite user input in the instruction, and directly forward using `transfer_to_agent`.

### Passthrough Teams

In Agno, setting both `respond_directly=True` and `determine_input_for_members=False` creates a passthrough for user requests — the Leader only routes without processing input or synthesizing output.

In trpc-agent:

```python
from trpc_agent_sdk.agents import LlmAgent

# Pure router agent - passthrough mode
passthrough_router = LlmAgent(
    name="passthrough_router",
    model=model,
    instruction="""You are a pure router. Based on the question type:
- Big questions → transfer to big_question_agent
- Small questions → transfer to small_question_agent
Do NOT answer, just route. Do NOT modify the question.""",
    sub_agents=[big_question_agent, small_question_agent],
)

# Use passthrough_router directly as the entry point
runner = Runner(app_name="app", agent=passthrough_router, ...)
```

**Core idea**: An LlmAgent configured with `sub_agents` inherently supports passthrough mode — just explicitly instruct it to only route in the instruction.

### Delegate to All Members

In Agno, setting `delegate_to_all_members=True` delegates the task to all members simultaneously.

In trpc-agent, use ChainAgent + ParallelAgent combination:

```python
from trpc_agent_sdk.agents import LlmAgent, ParallelAgent, ChainAgent

# Multiple expert agents
reddit_researcher = LlmAgent(
    name="reddit_researcher",
    model=model,
    instruction="Research the topic on Reddit.",
    output_key="reddit_result",
)

hackernews_researcher = LlmAgent(
    name="hackernews_researcher",
    model=model,
    instruction="Research the topic on HackerNews.",
    output_key="hackernews_result",
)

academic_researcher = LlmAgent(
    name="academic_researcher",
    model=model,
    instruction="Research academic papers on this topic.",
    output_key="academic_result",
)

# Execute all researchers in parallel
parallel_research = ParallelAgent(
    name="parallel_research",
    sub_agents=[reddit_researcher, hackernews_researcher, academic_researcher],
)

# Summarizer agent
summarizer = LlmAgent(
    name="summarizer",
    model=model,
    instruction="""Synthesize research results from multiple sources:

Reddit findings: {reddit_result}
HackerNews findings: {hackernews_result}
Academic findings: {academic_result}

Create a comprehensive summary.""",
    output_key="final_summary",
)

# Compose: Parallel research → Summarization
research_team = ChainAgent(
    name="research_team",
    sub_agents=[parallel_research, summarizer],
)
```

**Core idea**:
1. Use `ParallelAgent` to execute all members in parallel
2. Each member saves results via `output_key`
3. Use a subsequent agent to reference and synthesize results via template variables `{output_key}`

### Pattern Comparison Summary

| Agno Pattern | trpc-agent Implementation |
|-----------|---------------------|
| Coordinate (default) | `TeamAgent` |
| `respond_directly=True` | `LlmAgent` + `sub_agents` (sub-agents respond directly) |
| `determine_input_for_members=False` | `LlmAgent` + `sub_agents` + instruction to not rewrite |
| Passthrough | `LlmAgent` + `sub_agents` (pure routing instruction) |
| `delegate_to_all_members=True` | `ChainAgent` + `ParallelAgent` + summarizer agent |

For more Multi Agents orchestration patterns, see: [Multi Agents Documentation](./multi_agents.md)
