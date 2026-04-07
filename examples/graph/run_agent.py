# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Minimal GraphAgent example runner."""
import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.dsl.graph import STATE_KEY_LAST_RESPONSE
from trpc_agent_sdk.dsl.graph import EventUtils
from trpc_agent_sdk.dsl.graph import ExecutionPhase
from trpc_agent_sdk.dsl.graph import ModelExecutionMetadata
from trpc_agent_sdk.dsl.graph import NodeExecutionMetadata
from trpc_agent_sdk.dsl.graph import ToolExecutionMetadata
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()

# Set to True to enable the knowledge search branch.
# Requires TRAG_NAMESPACE (and related env vars) to be configured in .env.
ENABLE_KNOWLEDGE = False


def truncate_string(s: str, max_len: int = 120) -> str:
    if len(s) <= max_len:
        return s
    return s[:max_len - 3] + "..."


async def get_last_response_from_session(
    session_service: InMemorySessionService,
    app_name: str,
    user_id: str,
    session_id: str,
) -> str:
    session = await session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    if session is None or session.state is None:
        return ""
    response = session.state.get(STATE_KEY_LAST_RESPONSE, "")
    return response if isinstance(response, str) else ""


async def run_graph() -> None:
    app_name = "graph_demo"

    from agent.agent import create_agent

    agent = create_agent(enable_knowledge=ENABLE_KNOWLEDGE)

    session_service = InMemorySessionService()

    user_id = "demo_user"

    demo_inputs = [
        # Preview branch (EventWriter)
        "A short note about exercise and health.",
        # Subgraph branch (agent_node -> delegate GraphAgent)
        "subgraph: Please reply as a friendly assistant.",
        # LLM Agent branch with weather tool
        "llm_agent: What's the weather in Seattle today?",
        # LLM Agent branch with transfer to domain_explainer sub-agent
        "llm_agent: child: Explain retrieval-augmented generation in one sentence.",
        # Tool branch (llm_node with built-in tool execution)
        "tool: Count words for this text and show the stats.",
        # Code execution branch (code_node with UnsafeLocalCodeExecutor)
        'code: run python analysis',
        # MCP branch (stdio mcp_node -> calculate tool)
        'mcp: {"operation": "add", "a": 3, "b": 5}',
        # Summarize branch (LLM node, 40+ words)
        "This is a longer paragraph meant to trigger summarization. "
        "It should contain enough words to cross the summary threshold. "
        "Add a few sentences so we exceed eighty words and demonstrate the LLM branch. "
        "We keep adding sentences to ensure the word count is high enough. "
        "This way the graph routes to the summarizer node and shows the LLM path.",
    ]

    if ENABLE_KNOWLEDGE:
        demo_inputs.insert(-1, "knowledge: What is retrieval augmented generation?")

    for idx, user_text in enumerate(demo_inputs, start=1):
        session_id = str(uuid.uuid4())
        await session_service.create_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            state={},
        )

        print("=" * 44)
        print(f"Run {idx}/{len(demo_inputs)}")
        print(f"Session: {session_id[:8]}...")
        print(f"Input: {truncate_string(user_text)}")
        print("-" * 44)

        user_content = Content(parts=[Part.from_text(text=user_text)])

        streaming = False

        def end_stream_line() -> None:
            nonlocal streaming
            if streaming:
                print()
                streaming = False

        def normalize_author(author: str | None) -> str:
            return author if author else "unknown"

        runner = Runner(app_name=app_name, agent=agent, session_service=session_service)
        try:
            async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=user_content):
                node_meta = NodeExecutionMetadata.from_event(event)
                if node_meta:
                    end_stream_line()
                    if node_meta.phase == ExecutionPhase.START:
                        print(f"[Node start] node_type={node_meta.node_type}, node_name={node_meta.node_id}")
                    elif node_meta.phase == ExecutionPhase.COMPLETE:
                        print(f"[Node done ] node_type={node_meta.node_type}, node_name={node_meta.node_id}")
                    elif node_meta.phase == ExecutionPhase.ERROR:
                        print(f"[Node error] node_type={node_meta.node_type}, node_name={node_meta.node_id}")
                        if node_meta.error:
                            print(f"  Error: {node_meta.error}")

                tool_meta = ToolExecutionMetadata.from_event(event)
                if tool_meta:
                    end_stream_line()
                    if tool_meta.phase == ExecutionPhase.START:
                        print(f"[Tool start] {tool_meta.tool_name} (id={tool_meta.tool_id})")
                        if tool_meta.input_args:
                            print(f"  Args   : {tool_meta.input_args}")
                    elif tool_meta.phase == ExecutionPhase.COMPLETE:
                        print(f"[Tool done ] {tool_meta.tool_name} (id={tool_meta.tool_id})")
                        if tool_meta.output_result:
                            print(f"  Result : {tool_meta.output_result}")
                        if tool_meta.error:
                            print(f"  Error  : {tool_meta.error}")

                model_meta = ModelExecutionMetadata.from_event(event)
                if model_meta:
                    end_stream_line()
                    if model_meta.phase == ExecutionPhase.START:
                        print(f"[Model start] {model_meta.model_name} ({model_meta.node_id})")
                    elif model_meta.phase == ExecutionPhase.COMPLETE:
                        print(f"[Model done ] {model_meta.model_name} ({model_meta.node_id})")

                if not EventUtils.is_graph_event(event) and event.content and event.content.parts:
                    current_author = normalize_author(event.author)
                    if event.partial:
                        for part in event.content.parts:
                            if part.text:
                                if not streaming:
                                    end_stream_line()
                                    print(f"[{current_author}] ", end="", flush=True)
                                    streaming = True
                                print(part.text, end="", flush=True)
                        continue

                    end_stream_line()
                    for part in event.content.parts:
                        if part.thought:
                            continue
                        if part.function_call:
                            print(
                                f"[{current_author}] [Function call] {part.function_call.name}({part.function_call.args})"
                            )
                        elif part.function_response:
                            print(f"[{current_author}] [Function result] {part.function_response.response}")

            final_output = await get_last_response_from_session(
                session_service=session_service,
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
            )
            if final_output:
                end_stream_line()
                print(final_output)
        finally:
            await runner.close()

        print("-" * 40)


if __name__ == "__main__":
    asyncio.run(run_graph())
