# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Generated GraphAgent interactive runner."""

import asyncio
import uuid
from typing import Any

from dotenv import load_dotenv
from trpc_agent_sdk.dsl.graph import STATE_KEY_LAST_RESPONSE
from trpc_agent_sdk.dsl.graph._events import EventUtils
from trpc_agent_sdk.dsl.graph._events import ExecutionPhase
from trpc_agent_sdk.dsl.graph._events import ModelExecutionMetadata
from trpc_agent_sdk.dsl.graph._events import NodeExecutionMetadata
from trpc_agent_sdk.dsl.graph._events import ToolExecutionMetadata
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()

from agent.agent import root_agent

APP_NAME = 'knowledge-agent'


def normalize_author(author: str | None) -> str:
    return author if author else "unknown"


async def get_last_response_from_session(
    session_service: InMemorySessionService,
    app_name: str,
    user_id: str,
    session_id: str,
) -> Any:
    session = await session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    if session is None or session.state is None:
        return ""
    return session.state.get(STATE_KEY_LAST_RESPONSE, "")


async def stream_once(
    session_service: InMemorySessionService,
    app_name: str,
    user_id: str,
    session_id: str,
    content: Content,
) -> None:
    """Run one invocation."""
    streaming = False
    runner = Runner(
        app_name=app_name,
        agent=root_agent,
        session_service=session_service,
    )

    def end_stream_line() -> None:
        nonlocal streaming
        if streaming:
            print()
            streaming = False

    try:
        async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=content,
        ):

            if event is None:
                continue

            error_message = getattr(event, "error_message", "")
            if isinstance(error_message, str) and error_message:
                end_stream_line()
                print(f"  Error: {error_message}")
                continue

            node_meta = NodeExecutionMetadata.from_event(event)
            if node_meta:
                end_stream_line()
                node_description = node_meta.node_description or ""
                if node_meta.phase == ExecutionPhase.START:
                    print(f"  [Node start] node_type={node_meta.node_type}, "
                          f"node_id={node_meta.node_id}, node_description={node_description}")
                elif node_meta.phase == ExecutionPhase.COMPLETE:
                    print(f"  [Node done ] node_type={node_meta.node_type}, "
                          f"node_id={node_meta.node_id}, node_description={node_description}")
                elif node_meta.phase == ExecutionPhase.ERROR:
                    print(f"  [Node error] node_type={node_meta.node_type}, "
                          f"node_id={node_meta.node_id}, node_description={node_description}")
                    if node_meta.error:
                        print(f"    Error: {node_meta.error}")

            tool_meta = ToolExecutionMetadata.from_event(event)
            if tool_meta:
                end_stream_line()
                if tool_meta.phase == ExecutionPhase.START:
                    print(f"  [Tool start] {tool_meta.tool_name} (id={tool_meta.tool_id})")
                    if tool_meta.input_args:
                        print(f"    Args   : {tool_meta.input_args}")
                elif tool_meta.phase == ExecutionPhase.COMPLETE:
                    print(f"  [Tool done ] {tool_meta.tool_name} (id={tool_meta.tool_id})")
                    if tool_meta.output_result:
                        print(f"    Result : {tool_meta.output_result}")
                    if tool_meta.error:
                        print(f"    Error  : {tool_meta.error}")

            model_meta = ModelExecutionMetadata.from_event(event)
            if model_meta:
                end_stream_line()
                if model_meta.phase == ExecutionPhase.START:
                    print(f"  [Model start] {model_meta.model_name} ({model_meta.node_id})")
                elif model_meta.phase == ExecutionPhase.COMPLETE:
                    print(f"  [Model done ] {model_meta.model_name} ({model_meta.node_id})")

            if not EventUtils.is_graph_event(event) and event.content and event.content.parts:
                current_author = normalize_author(event.author)
                if event.partial:
                    for part in event.content.parts:
                        if part.text:
                            if not streaming:
                                end_stream_line()
                                print(f"  [{current_author}] ", end="", flush=True)
                                streaming = True
                            print(part.text, end="", flush=True)
                    continue

                end_stream_line()
                for part in event.content.parts:
                    if part.thought:
                        continue
                    if part.function_call:
                        print(
                            f"  [{current_author}] [Function call] {part.function_call.name}({part.function_call.args})"
                        )
                    elif part.function_response:
                        print(f"  [{current_author}] [Function result] {part.function_response.response}")

        final_output = await get_last_response_from_session(
            session_service=session_service,
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        if final_output:
            end_stream_line()
            print(f"  {final_output}")
    finally:
        await runner.close()

    return None


async def run_graph() -> None:
    user_id = "demo_user"
    session_service = InMemorySessionService()

    session_id = str(uuid.uuid4())
    await session_service.create_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
        state={},
    )

    print(f"Starting graph: {APP_NAME}")
    print("Interactive mode. Type 'exit' to quit, 'new' for new session.")

    while True:
        try:
            user_text = input("You: ").strip()
        except EOFError:
            print("\nGoodbye!")
            break
        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        if not user_text:
            continue

        lowered = user_text.lower()
        if lowered in {"exit", "quit"}:
            print("Goodbye!")
            break

        if lowered == "new":
            session_id = str(uuid.uuid4())
            await session_service.create_session(
                app_name=APP_NAME,
                user_id=user_id,
                session_id=session_id,
                state={},
            )
            print(f"New session: {session_id}")
            continue

        user_content = Content(parts=[Part.from_text(text=user_text)])
        print("Assistant:")

        await stream_once(
            session_service=session_service,
            app_name=APP_NAME,
            user_id=user_id,
            session_id=session_id,
            content=user_content,
        )


if __name__ == "__main__":
    asyncio.run(run_graph())
