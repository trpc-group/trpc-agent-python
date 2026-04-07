# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Graph multi-turn example runner."""
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


def truncate_string(text: str, max_len: int = 120) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def normalize_author(author: str | None) -> str:
    return author if author else "unknown"


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


async def run_graph_multi_turns() -> None:
    app_name = "graph_multi_turns_demo"
    user_id = "demo_user"

    from agent.agent import root_agent

    session_service = InMemorySessionService()

    session_id = str(uuid.uuid4())
    await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        state={},
    )

    turns = [
        "llm: Define retrieval-augmented generation in one sentence.",
        "llm: Summarize your previous answer in six words.",
        "agent: What i ask? Reply as branch agent and then greet me.",
        "agent: Summarize what i have asked you to do Do.",
    ]

    print("=" * 44)
    print("Graph Multi-Turn Demo")
    print(f"Session: {session_id[:8]}...")

    for idx, turn_text in enumerate(turns, start=1):
        print("=" * 44)
        print(f"Turn {idx}/{len(turns)}")
        print(f"Input: {truncate_string(turn_text)}")
        print("-" * 44)

        user_content = Content(parts=[Part.from_text(text=turn_text)])

        streaming = False

        def end_stream_line() -> None:
            nonlocal streaming
            if streaming:
                print()
                streaming = False

        runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)
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
                    elif tool_meta.phase == ExecutionPhase.COMPLETE:
                        print(f"[Tool done ] {tool_meta.tool_name} (id={tool_meta.tool_id})")

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
    asyncio.run(run_graph_multi_turns())
