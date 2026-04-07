[English](README.md) | [中文](README.zh_CN.md)

# tRPC-Agent-Python

[![PyPI Version](https://img.shields.io/pypi/v/trpc-agent-py.svg)](https://pypi.org/project/trpc-agent-py/)
[![Python Versions](https://img.shields.io/pypi/pyversions/trpc-agent-py.svg)](https://pypi.org/project/trpc-agent-py/)
[![LICENSE](https://img.shields.io/badge/license-Apache--2.0-green.svg)](https://github.com/trpc-group/trpc-agent-python/blob/main/LICENSE)
[![Releases](https://img.shields.io/github/release/trpc-group/trpc-agent-python.svg?style=flat-square)](https://github.com/trpc-group/trpc-agent-python/releases)
[![Coverage](https://codecov.io/gh/trpc-group/trpc-agent-python/branch/main/graph/badge.svg)](https://app.codecov.io/gh/trpc-group/trpc-agent-python/tree/main)
[![Documentation](https://img.shields.io/badge/Docs-Website-blue.svg)](https://trpc-group.github.io/trpc-agent-python/)

**A production-grade Agent framework deeply integrated with the Python AI ecosystem.**  
tRPC-Agent-Python provides an end-to-end foundation for agent building, orchestration, tool integration, session and long-term memory, service deployment, and observability, so you can ship reliable and extensible AI applications faster.

## Why Choose tRPC-Agent-Python

- **Multi-paradigm agent orchestration**: Built-in orchestration supports `ChainAgent` / `ParallelAgent` / `CycleAgent` / `TransferAgent`, with `GraphAgent` for graph-based orchestration.
- **Graph orchestration capability (`GraphAgent`)**: Use DSL to orchestrate `Agent` / `Tool` / `MCP` / `Knowledge` / `CodeExecutor` in one unified flow.
- **Efficient integration with Python AI ecosystems**: Agent ecosystem extensions (`claude-agent-sdk` / `LangGraph`, etc.) / Tool ecosystem extensions (`mcp`, etc.) / Knowledge ecosystem extensions (`LangChain`, etc.) / Model ecosystem extensions (`LiteLLM`, etc.) / Memory ecosystem extensions (`Mem0`, etc.).
- **Agent ecosystem extensions**: Supports `LangGraphAgent` / `ClaudeAgent` / `TeamAgent` (Agno-Like).
- **Tool ecosystem extensions**: `FunctionTool` / File tools / `MCPToolset` / LangChain Tool / Agent-as-Tool.
- **Complete memory capability (`Session` / `Memory`)**: `Session` manages messages and state within a single session, while `Memory` manages cross-session long-term memory and personalization. Persistence supports `InMemory` / `Redis` / `SQL`; `Memory` also supports `Mem0`.
- **Production-grade knowledge capability**: Built on LangChain components with first-class RAG support.
- **CodeExecutor extension capability**: Supports local / container executors for code execution and task grounding.
- **Skills extension capability**: Supports `SKILL.md`-based skill systems for reusable capabilities and dynamic tooling.
- **Connect to multiple LLM providers**: OpenAI-like / Anthropic / LiteLLM routing.
- **Serving and observability**: Expose HTTP / A2A / AG-UI services through FastAPI, with built-in OpenTelemetry tracing.
- **trpc-claw (OpenClaw-like personal agent)**: Built on [nanobot](https://github.com/HKUDS/nanobot), tRPC-Agent ships trpc-claw so you can quickly build an OpenClaw-like personal AI agent with Telegram, WeCom, and other channel support.

## Use Cases

- Intelligent customer support and knowledge QA (RAG + session memory)
- Code generation and engineering automation (`ClaudeAgent`)
- Code execution and automated task grounding (`CodeExecutor`)
- Agent Skills for reusable capabilities
- Multi-role collaborative workflows (`TeamAgent` / multi-agent)
- Cross-protocol agent service integration (`A2A` / `AG-UI`)
- MCP tool protocol integration and tool ecosystem expansion
- Unified gateway access and protocol conversion
- Component-based workflow orchestration using `GraphAgent`
- Reusing existing LangGraph workflows in this runtime
- Build an OpenClaw-like personal AI agent quickly with trpc-claw

## Table of Contents

- [tRPC-Agent-Python](#trpc-agent-python)
  - [Why Choose tRPC-Agent-Python](#why-choose-trpc-agent-python)
  - [Use Cases](#use-cases)
  - [Table of Contents](#table-of-contents)
  - [Quick Start](#quick-start)
  - [trpc-claw Usage](#trpc-claw-usage)
  - [Documentation](#documentation)
  - [Examples](#examples)
    - [1. Getting Started and Basic Agents](#1-getting-started-and-basic-agents)
    - [2. Preset Multi-Agent Orchestration](#2-preset-multi-agent-orchestration)
    - [3. Team Collaboration](#3-team-collaboration)
    - [4. Graph Orchestration](#4-graph-orchestration)
    - [5. Agent Ecosystem Extensions](#5-agent-ecosystem-extensions)
    - [6. Tools and MCP](#6-tools-and-mcp)
    - [7. Skills](#7-skills)
    - [8. CodeExecutor](#8-codeexecutor)
    - [9. Session, Memory, and Knowledge](#9-session-memory-and-knowledge)
    - [10. Serving and Protocols](#10-serving-and-protocols)
    - [11. Filters and Execution Control](#11-filters-and-execution-control)
    - [12. Advanced LlmAgent Capabilities](#12-advanced-llmagent-capabilities)
    - [13. LlmAgent Tool Calling and Interaction](#13-llmagent-tool-calling-and-interaction)
  - [Architecture Overview](#architecture-overview)
  - [Contributing](#contributing)
  - [Acknowledgements](#acknowledgements)

## Quick Start

### Prerequisites

- Python 3.10+ (Python 3.12 recommended)
- Available model API key (OpenAI-like / Anthropic, or route via LiteLLM)

### Installation

```bash
pip install trpc-agent-py
```

Install optional capabilities as needed:

```bash
pip3 install -e '.[a2a,ag-ui,knowledge,agent-claude,mem0,langfuse]'
```

### Develop Weather Agent

```python
import asyncio
import os
import uuid

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import Content, Part


async def get_weather_report(city: str) -> dict:
    return {"city": city, "temperature": "25°C", "condition": "Sunny", "humidity": "60%"}


async def main():
    model = OpenAIModel(
        model_name=os.environ["TRPC_AGENT_MODEL_NAME"],
        api_key=os.environ["TRPC_AGENT_API_KEY"],
        base_url=os.environ.get("TRPC_AGENT_BASE_URL", ""),
    )

    agent = LlmAgent(
        name="assistant",
        description="A helpful assistant",
        model=model,
        instruction="You are a helpful assistant.",
        tools=[FunctionTool(get_weather_report)],
    )

    session_service = InMemorySessionService()
    runner = Runner(app_name="demo_app", agent=agent, session_service=session_service)

    user_id = "demo_user"
    session_id = str(uuid.uuid4())
    user_content = Content(parts=[Part.from_text(text="What's the weather in Beijing?")])

    async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=user_content):
        if not event.content or not event.content.parts:
            continue
        for part in event.content.parts:
            if part.text and event.partial:
                print(part.text, end="", flush=True)
            elif part.function_call:
                print(f"\n🔧 [{part.function_call.name}({part.function_call.args})]", flush=True)
            elif part.function_response:
                print(f"📊 [{part.function_response.response}]", flush=True)

    print()

if __name__ == "__main__":
    asyncio.run(main())
```

### Run the Agent

```bash
export TRPC_AGENT_API_KEY=xxx
export TRPC_AGENT_BASE_URL=xxxx
export TRPC_AGENT_MODEL_NAME=xxxx
python quickstart.py
```

## trpc-claw Usage

tRPC-Agent ships trpc-claw (`trpc_agent_cmd openclaw`), built on [nanobot](https://github.com/HKUDS/nanobot), so you can quickly build an OpenClaw-like personal AI agent. Start it with a single command and it runs 24/7 — chat through Telegram, WeCom, or any other IM, or use it locally via CLI / UI.

For full configuration and advanced features, see: [openclaw.md](./docs/mkdocs/en/openclaw.md)

### Quick Start

**1. Generate config**

```bash
mkdir -p ~/.trpc_claw
trpc_agent_cmd openclaw conf_temp > ~/.trpc_claw/config.yaml
```

**2. Set environment variables**

```bash
export TRPC_AGENT_API_KEY=your_api_key
export TRPC_AGENT_BASE_URL=your_base_url
export TRPC_AGENT_MODEL_NAME=your_model
```

**3. Run locally**

```bash
# Force local CLI mode
trpc_agent_cmd openclaw chat -c ~/.trpc_claw/config.yaml

# Local UI
trpc_agent_cmd openclaw ui -c ~/.trpc_claw/config.yaml
```

**4. Connect WeCom / Telegram**

Enable the channel in `config.yaml`, then launch with `run`:

```yaml
channels:
  wecom:
    enabled: true
    bot_id: ${WECOM_BOT_ID}
    secret: ${WECOM_BOT_SECRET}
  # or Telegram:
  # telegram:
  #   enabled: true
  #   token: ${TELEGRAM_BOT_TOKEN}
```

```bash
trpc_agent_cmd openclaw run -c ~/.trpc_claw/config.yaml
```

If no channel is available, `run` automatically falls back to local CLI for easy debugging.

## Documentation

- See directory: [`docs/mkdocs/en`](./docs/mkdocs/en)

## Examples

All examples in the `examples` directory are runnable. The groups below organize recommended starting points by capability, with short guidance so you can quickly pick what to read first for your scenario.

### 1. Getting Started and Basic Agents

Recommended first:

- [examples/quickstart](./examples/quickstart/README.md) - Minimal runnable demo
- [examples/llmagent](./examples/llmagent/README.md) - Basic `LlmAgent` usage
- [examples/litellm](./examples/litellm/README.md) - LiteLLM backend routing with `LiteLLMModel`
- [examples/llmagent_with_custom_prompt](./examples/llmagent_with_custom_prompt/README.md) - Custom prompts
- [examples/llmagent_with_schema](./examples/llmagent_with_schema/README.md) - Structured outputs

Related docs: [llm_agent.md](./docs/mkdocs/en/llm_agent.md) / [model.md](./docs/mkdocs/en/model.md)

This group helps you:

- Run a full end-to-end path from user input to tool call to model output
- Understand how to consume `function_call` / `function_response` events in streaming output
- Learn baseline patterns for prompts and structured responses

Start with this snippet (`Runner` + streaming events):

```python
runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)
async for event in runner.run_async(user_id=user_id, session_id=current_session_id, new_message=user_content):
    if event.partial and event.content:
        ...
```

### 2. Preset Multi-Agent Orchestration

Recommended first:

- [examples/multi_agent_chain](./examples/multi_agent_chain/README.md) - `ChainAgent`
- [examples/multi_agent_parallel](./examples/multi_agent_parallel/README.md) - `ParallelAgent`
- [examples/multi_agent_cycle](./examples/multi_agent_cycle/README.md) - `CycleAgent`
- [examples/transfer_agent](./examples/transfer_agent/README.md) - `TransferAgent` handoff
- [examples/multi_agent_subagent](./examples/multi_agent_subagent/README.md) - Sub-agent delegation
- [examples/multi_agent_compose](./examples/multi_agent_compose/README.md) - Composed orchestration
- [examples/multi_agent_start_from_last](./examples/multi_agent_start_from_last/README.md) - Resume from last agent state

Related docs: [multi_agents.md](./docs/mkdocs/en/multi_agents.md)

This group helps you:

- Understand the role differences among Chain / Parallel / Cycle / Transfer
- Pick serial, parallel, loop, or handoff orchestration by task shape
- Learn how to resume and compose flows from existing outputs

Start with this snippet (`ChainAgent`):

```python
pipeline = ChainAgent(
    name="document_processor",
    sub_agents=[extractor_agent, translator_agent],
)
```

### 3. Team Collaboration

Recommended first:

- [examples/team](./examples/team/README.md) - Team coordination mode
- [examples/team_parallel_execution](./examples/team_parallel_execution/README.md) - Team parallel execution
- [examples/team_with_skill](./examples/team_with_skill/README.md) - Team + Skills
- [examples/team_human_in_the_loop](./examples/team_human_in_the_loop/README.md) - Team with human-in-the-loop
- [examples/team_as_sub_agent](./examples/team_as_sub_agent/README.md) - Team as a sub-agent
- [examples/team_member_message_filter](./examples/team_member_message_filter/README.md) - Team member message filtering
- [examples/team_member_agent_claude](./examples/team_member_agent_claude/README.md) - Team member using `ClaudeAgent`
- [examples/team_member_agent_langgraph](./examples/team_member_agent_langgraph/README.md) - Team member using `LangGraphAgent`
- [examples/team_member_agent_team](./examples/team_member_agent_team/README.md) - Nested Team members
- [examples/team_with_cancel](./examples/team_with_cancel/README.md) - Team task cancellation

Related docs: [team.md](./docs/mkdocs/en/team.md) / [human_in_the_loop.md](./docs/mkdocs/en/human_in_the_loop.md) / [cancel.md](./docs/mkdocs/en/cancel.md)

This group helps you:

- Understand the Leader / Member collaboration model in Team
- Combine Skills, sub-teams, and external agents in one workflow
- Cover practical concerns like filtering, human approval, and cancellation

Start with this snippet (`TeamAgent`):

```python
content_team = TeamAgent(
    name="content_team",
    model=model,
    members=[researcher, writer],
    instruction=LEADER_INSTRUCTION,
    share_member_interactions=True,
)
```

### 4. Graph Orchestration

Recommended first:

- [examples/graph](./examples/graph/README.md) - `GraphAgent` with function / llm / agent / code / mcp / knowledge nodes
- [examples/graph_multi_turns](./examples/graph_multi_turns/README.md) - Multi-turn graph execution
- [examples/graph_with_interrupt](./examples/graph_with_interrupt/README.md) - Graph execution interruption
- [examples/dsl](./examples/dsl/README.md) - DSL orchestration basics
- [examples/dsl/classifier_mcp](./examples/dsl/classifier_mcp/README.md) - DSL + MCP classification routing

Related docs: [graph.md](./docs/mkdocs/en/graph.md) / [dsl.md](./docs/mkdocs/en/dsl.md)

This group helps you:

- Build explicit, controllable workflows (branching, merging, interruption, resuming)
- Mix `Agent` / `Tool` / `MCP` / `CodeExecutor` / `Knowledge` in a single graph
- Use DSL for workflows that stay readable and maintainable

Start with this snippet (conditional routing):

```python
graph.add_conditional_edges(
    "decide",
    create_route_choice(set(path_map.keys())),
    path_map,
)
```

### 5. Agent Ecosystem Extensions

Recommended first:

- [examples/langgraph_agent](./examples/langgraph_agent/README.md) - Integrate pre-built and compiled LangGraph workflows
- [examples/langgraph_agent_with_cancel](./examples/langgraph_agent_with_cancel/README.md) - `LangGraphAgent` cancellation
- [examples/langgraphagent_with_human_in_the_loop](./examples/langgraphagent_with_human_in_the_loop/README.md) - `LangGraphAgent` human-in-the-loop
- [examples/claude_agent](./examples/claude_agent/README.md) - `ClaudeAgent` basics
- [examples/claude_agent_with_streaming_tool](./examples/claude_agent_with_streaming_tool/README.md) - `ClaudeAgent` streaming tools
- [examples/claude_agent_with_skills](./examples/claude_agent_with_skills/README.md) - `ClaudeAgent` + Skills
- [examples/claude_agent_with_code_writer](./examples/claude_agent_with_code_writer/README.md) - `ClaudeAgent` for code generation
- [examples/claude_agent_with_travel_planner](./examples/claude_agent_with_travel_planner/README.md) - `ClaudeAgent` task planning
- [examples/claude_agent_with_cancel](./examples/claude_agent_with_cancel/README.md) - `ClaudeAgent` cancellation

Related docs: [langgraph_agent.md](./docs/mkdocs/en/langgraph_agent.md) / [claude_agent.md](./docs/mkdocs/en/claude_agent.md) / [human_in_the_loop.md](./docs/mkdocs/en/human_in_the_loop.md) / [cancel.md](./docs/mkdocs/en/cancel.md)

This group helps you:

- Reuse existing LangGraph assets in the current runtime with `LangGraphAgent`
- Use `ClaudeAgent` for code generation, engineering automation, and streaming tools
- Cover production-ready patterns like human-in-the-loop and cancellation

Start with this snippet (`ClaudeAgent`):

```python
root_agent = ClaudeAgent(
    name="claude_weather_agent",
    model=_create_model(),
    instruction=INSTRUCTION,
    tools=[FunctionTool(get_weather)],
    enable_session=True,
)
```

### 6. Tools and MCP

Recommended first:

- [examples/function_tools](./examples/function_tools/README.md) - `FunctionTool`
- [examples/file_tools](./examples/file_tools/README.md) - File tools
- [examples/tools](./examples/tools/README.md) - Basic tool combinations
- [examples/toolsets](./examples/toolsets/README.md) - ToolSet composition
- [examples/streaming_tools](./examples/streaming_tools/README.md) - Streaming tool calling
- [examples/mcp_tools](./examples/mcp_tools/README.md) - `MCPToolset` (`stdio` / `sse` / `streamable-http`)
- [examples/langchain_tools](./examples/langchain_tools/README.md) - LangChain tools integration
- [examples/agent_tools](./examples/agent_tools/README.md) - Agent as a Tool

Related docs: [tool.md](./docs/mkdocs/en/tool.md)

This group helps you:

- Cover the full tool access path from function tools to MCP to composed toolsets
- Learn advanced modes such as streaming tools and Agent-as-Tool
- Reuse existing tool implementations in multi-agent scenarios

Start with this snippet (`MCPToolset`):

```python
class StdioMCPToolset(MCPToolset):
    def __init__(self):
        super().__init__()
        self._connection_params = StdioConnectionParams(
            server_params=McpStdioServerParameters(command="python3", args=["mcp_server.py"]),
            timeout=5,
        )
```

### 7. Skills

Recommended first:

- [examples/skills](./examples/skills/README.md) - `SkillToolSet` basics
- [examples/skills_with_container](./examples/skills_with_container/README.md) - Skills in containers
- [examples/skills_with_dynamic_tools](./examples/skills_with_dynamic_tools/README.md) - Dynamic tool skills

Related docs: [skill.md](./docs/mkdocs/en/skill.md)

This group helps you:

- Package reusable capabilities into Skills
- Support scenario-based dynamic tool composition
- Build reusable business skill modules

Start with this snippet (`SkillToolSet`):

```python
workspace_runtime = create_local_workspace_runtime()
repository = create_default_skill_repository(skill_paths, workspace_runtime=workspace_runtime)
skill_tool_set = SkillToolSet(repository=repository, run_tool_kwargs=tool_kwargs)
```

### 8. CodeExecutor

Recommended first:

- [examples/code_executors](./examples/code_executors/README.md) - `UnsafeLocalCodeExecutor` / `ContainerCodeExecutor`

Related docs: [code_executor.md](./docs/mkdocs/en/code_executor.md)

This group helps you:

- Choose local or containerized executors by runtime constraints
- Let agents execute code and ground tasks within controlled boundaries
- Combine with Skills/Tools for planning-and-execution loops

### 9. Session, Memory, and Knowledge

Recommended first:

- Session: [examples/session_service_with_in_memory](./examples/session_service_with_in_memory/README.md) / [examples/session_service_with_redis](./examples/session_service_with_redis/README.md) / [examples/session_service_with_sql](./examples/session_service_with_sql/README.md) / [examples/session_summarizer](./examples/session_summarizer/README.md) / [examples/session_state](./examples/session_state/README.md)
- Memory: [examples/memory_service_with_in_memory](./examples/memory_service_with_in_memory/README.md) / [examples/memory_service_with_redis](./examples/memory_service_with_redis/README.md) / [examples/memory_service_with_sql](./examples/memory_service_with_sql/README.md) / [examples/memory_service_with_mem0](./examples/memory_service_with_mem0/README.md) / [examples/mem_0](./examples/mem_0/README.md)
- Knowledge: [examples/knowledge_with_documentloader](./examples/knowledge_with_documentloader/README.md) / [examples/knowledge_with_vectorstore](./examples/knowledge_with_vectorstore/README.md) / [examples/knowledge_with_rag_agent](./examples/knowledge_with_rag_agent/README.md) / [examples/knowledge_with_searchtool_rag_agent](./examples/knowledge_with_searchtool_rag_agent/README.md) / [examples/knowledge_with_prompt_template](./examples/knowledge_with_prompt_template/README.md) / [examples/knowledge_with_custom_components](./examples/knowledge_with_custom_components/README.md)

Related docs:
- Session: [session.md](./docs/mkdocs/en/session.md) / [session_redis.md](./docs/mkdocs/en/session_redis.md) / [session_sql.md](./docs/mkdocs/en/session_sql.md) / [session_summary.md](./docs/mkdocs/en/session_summary.md)
- Memory: [memory.md](./docs/mkdocs/en/memory.md)
- Knowledge: [knowledge.md](./docs/mkdocs/en/knowledge.md) / [knowledge_document_loader.md](./docs/mkdocs/en/knowledge_document_loader.md) / [knowledge_retrievers.md](./docs/mkdocs/en/knowledge_retrievers.md) / [knowledge_vectorstore.md](./docs/mkdocs/en/knowledge_vectorstore.md) / [knowledge_prompt_template.md](./docs/mkdocs/en/knowledge_prompt_template.md) / [knowledge_custom_components.md](./docs/mkdocs/en/knowledge_custom_components.md)

This group helps you:

- Session: manage per-session messages, summaries, and state
- Memory: manage cross-session long-term memory (including Mem0)
- Knowledge: cover document loading, retrieval, RAG, and prompt templates

### 10. Serving and Protocols

Recommended first:

- [examples/fastapi_server](./examples/fastapi_server/README.md) - HTTP service (sync + SSE)
- [examples/a2a](./examples/a2a/README.md) / [examples/a2a_with_cancel](./examples/a2a_with_cancel/README.md) - A2A service and cancellation
- [examples/agui](./examples/agui/README.md) / [examples/agui_with_cancel](./examples/agui_with_cancel/README.md) - AG-UI service and cancellation

Related docs: [a2a.md](./docs/mkdocs/en/a2a.md) / [agui.md](./docs/mkdocs/en/agui.md) / [cancel.md](./docs/mkdocs/en/cancel.md)

This group helps you:

- Expose services through HTTP / A2A / AG-UI
- Integrate streaming responses and cancellation into real applications
- Use minimal templates for production service rollout

### 11. Filters and Execution Control

Recommended first:

- [examples/filter_with_model](./examples/filter_with_model/README.md) - Model-level filters
- [examples/filter_with_tool](./examples/filter_with_tool/README.md) - Tool-level filters
- [examples/filter_with_agent](./examples/filter_with_agent/README.md) - Agent-level filters
- [examples/llmagent_with_branch_filtering](./examples/llmagent_with_branch_filtering/README.md) - Branch filtering
- [examples/llmagent_with_timeline_filtering](./examples/llmagent_with_timeline_filtering/README.md) - Timeline filtering
- [examples/llmagent_with_cancel](./examples/llmagent_with_cancel/README.md) - Execution cancellation

Related docs: [filter.md](./docs/mkdocs/en/filter.md) / [cancel.md](./docs/mkdocs/en/cancel.md)

This group helps you:

- Apply control policies at model, tool, and agent layers
- Cover branch filtering, timeline filtering, and cancellation
- Build strong governance and risk-control constraints

### 12. Advanced LlmAgent Capabilities

Recommended first:

- [examples/llmagent_with_tool_prompt](./examples/llmagent_with_tool_prompt/README.md) - Tool-call prompt enhancement
- [examples/llmagent_with_thinking](./examples/llmagent_with_thinking/README.md) - Thinking mode
- [examples/llmagent_with_user_history](./examples/llmagent_with_user_history/README.md) - User history management
- [examples/llmagent_with_max_history_messages](./examples/llmagent_with_max_history_messages/README.md) - History window limits
- [examples/llmagent_with_model_create_fn](./examples/llmagent_with_model_create_fn/README.md) - Dynamic model factory
- [examples/llmagent_with_custom_agent](./examples/llmagent_with_custom_agent/README.md) - Custom agent extension

Related docs: [llm_agent.md](./docs/mkdocs/en/llm_agent.md) / [model.md](./docs/mkdocs/en/model.md) / [custom_agent.md](./docs/mkdocs/en/custom_agent.md)

This group helps you:

- Focus on `LlmAgent` extension points for context, prompting, and model routing
- Adapt a general-purpose agent to domain-specific business policies
- Build reusable behavior templates for repeated scenarios

### 13. LlmAgent Tool Calling and Interaction

Recommended first:

- [examples/llmagent_with_streaming_tool_simple](./examples/llmagent_with_streaming_tool_simple/README.md) - Simple streaming tool calls
- [examples/llmagent_with_streaming_tool_complex](./examples/llmagent_with_streaming_tool_complex/README.md) - Complex streaming tool calls
- [examples/llmagent_with_parallal_tools](./examples/llmagent_with_parallal_tools/README.md) - Parallel tool calling (directory name intentionally uses `parallal`)
- [examples/llmagent_with_human_in_the_loop](./examples/llmagent_with_human_in_the_loop/README.md) - Human-in-the-loop decisions

Related docs: [llm_agent.md](./docs/mkdocs/en/llm_agent.md) / [tool.md](./docs/mkdocs/en/tool.md) / [human_in_the_loop.md](./docs/mkdocs/en/human_in_the_loop.md)

This group helps you:

- Cover both simple and complex streaming tool interaction patterns
- Orchestrate parallel tool calls with human confirmation nodes
- Combine with filters and cancellation for more reliable execution chains

> For more examples, see each subdirectory README.md under [examples](./examples).

## Architecture Overview

![tRPC-Agent-Python Architecture](./docs/mkdocs/assets/imgs/architecture.png)

The framework is organized in an event-driven architecture where each layer can evolve independently:

- **Agent layer**: LlmAgent / ChainAgent / ParallelAgent / CycleAgent / TransferAgent
- **Agent ecosystem extension layer**: LangGraphAgent / ClaudeAgent / TeamAgent
- **Graph capability layer**: GraphAgent / trpc_agent_sdk.dsl.graph (DSL-based orchestration)
- **Runner layer**: Unified execution entry, coordinating Session / Memory / Artifact services
- **Tool layer**: FunctionTool / file tools / MCPToolset / Skill tools
- **Model layer**: OpenAIModel / AnthropicModel / LiteLLMModel
- **Memory layer**: SessionService / MemoryService / SessionSummarizer / Mem0MemoryService
- **Knowledge layer**: Production-grade LangChain-based knowledge and RAG capability
- **Execution and skill layer**: CodeExecutor (local / container) / Skills
- **Service layer**: FastAPI / A2A / AG-UI
- **Observability layer**: OpenTelemetry tracing/metrics, integrable with platforms like Langfuse
- **Ecosystem adapter layer**: claude-agent-sdk / mcp / LangChain / LiteLLM / Mem0 plugged into the main chain through model/tool/memory adapters

Key packages:

| Package | Responsibility |
| --- | --- |
| trpc_agent_sdk.agents | Agent abstractions, multi-agent orchestration, ecosystem extensions (LangGraphAgent / ClaudeAgent / TeamAgent) |
| trpc_agent_sdk.runners | Unified execution and event output |
| trpc_agent_sdk.models | Model adapter layer |
| trpc_agent_sdk.tools | Tooling system and MCP support |
| trpc_agent_sdk.sessions | Session management and summarization |
| trpc_agent_sdk.memory | Long-term memory services |
| trpc_agent_sdk.dsl.graph | DSL graph orchestration engine |
| trpc_agent_sdk.teams | Team collaboration mode |
| trpc_agent_sdk.code_executors | Code execution and workspace runtime |
| trpc_agent_sdk.skills | Skill repository and Skill tools |
| trpc_agent_sdk.server | FastAPI / A2A / AG-UI serving capabilities |

## Contributing

We love contributions! Join our growing developer community and help build the future of AI Agents.

### **Ways to Contribute**

- **Report bugs** or suggest new features through [Issues](https://github.com/trpc-group/trpc-agent-python/issues)
- **Improve documentation** to help others onboard faster
- **Submit PRs** for bug fixes, new features, or examples
- **Share your use cases** to inspire other builders

### **Quick Contribution Setup**

```bash
# Fork and clone the repository
git clone https://github.com/YOUR_USERNAME/trpc-agent-python.git
cd trpc-agent-python

# Install development dependencies and run tests
pip install -e ".[dev]"
pytest

# Make your changes and open a PR!
```

**Please read** [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines and coding standards.  
**Please follow** [CODE-OF-CONDUCT.md](CODE-OF-CONDUCT.md) to keep our community friendly, respectful, and inclusive.

## Acknowledgements

### **Enterprise Validation**

We sincerely thank Tencent Licaitong, Tencent Ads, and other business teams for continuous validation and feedback in real production scenarios, which helps us keep improving the framework.

### **Open-source Inspiration**

We are also inspired by outstanding open-source frameworks including **ADK**, **Agno**, **CrewAI**, and **AutoGen**. We keep moving forward on the shoulders of giants.

---

If this project helps you, a GitHub Star is always appreciated — it's the most direct encouragement and helps more developers discover this project.
