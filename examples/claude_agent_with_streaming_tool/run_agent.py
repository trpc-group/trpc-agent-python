# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""
ClaudeAgent Selective Streaming Tool Call Demo

This example demonstrates selective streaming tool support in ClaudeAgent,
which now aligns with LlmAgent behavior:

Key features demonstrated:
1. StreamingFunctionTool (write_file): Receives real-time streaming argument events
2. Regular FunctionTool (get_file_info): Does NOT receive streaming events
3. Both behaviors work correctly in the same agent
4. Consuming streaming events through Runner.run_async()

This aligns ClaudeAgent with LlmAgent - only tools with is_streaming=True
receive streaming argument updates.
"""

import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.models import _constants as const
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.server.agents.claude import destroy_claude_env
from trpc_agent_sdk.server.agents.claude import setup_claude_env
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


async def run_streaming_tool_demo():
    """Run the ClaudeAgent selective streaming tool demo.

    This demo shows the difference between streaming and non-streaming tools:

    1. write_file (StreamingFunctionTool):
       - Arguments stream in real-time as LLM generates them
       - You see ⏳ [Streaming] events during generation

    2. get_file_info (FunctionTool):
       - Arguments arrive only when complete
       - No streaming events, goes directly to ✅ [Tool Call Complete]
    """

    app_name = "claude_streaming_tool_demo"

    from agent.agent import root_agent, _create_model

    # Setup Claude environment with proxy server
    model = _create_model()
    setup_claude_env(proxy_host="0.0.0.0", proxy_port=8083, claude_models={"all": model})
    root_agent.initialize()

    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "demo_user"

    # Demo queries that trigger different tools:
    # - Query 1: Triggers write_file (streaming) - you'll see streaming events
    # - Query 2: Triggers get_file_info (non-streaming) - no streaming events
    demo_queries = [
        "请帮我创建一个简单的 HTML 页面，文件名为 index.html，内容是一个带有标题和段落的网页。",
        "请帮我查看 index.html 文件的信息。",
    ]

    try:
        for query in demo_queries:
            current_session_id = str(uuid.uuid4())

            await session_service.create_session(
                app_name=app_name,
                user_id=user_id,
                session_id=current_session_id,
            )

            print("=" * 60)
            print(f"🆔 Session ID: {current_session_id[:8]}...")
            print(f"📝 User: {query}")
            print("=" * 60)

            user_content = Content(parts=[Part.from_text(text=query)])

            print("\n🤖 Processing...\n")

            async for event in runner.run_async(user_id=user_id,
                                                session_id=current_session_id,
                                                new_message=user_content):
                if not event.content or not event.content.parts:
                    continue

                if event.is_streaming_tool_call():
                    # This is a streaming tool call event with partial arguments
                    # Only StreamingFunctionTool tools will reach here
                    for part in event.content.parts:
                        if part.function_call:
                            args = part.function_call.args or {}
                            delta = args.get(const.TOOL_STREAMING_ARGS, "")
                            if delta:
                                preview = delta[:60] + "..." if len(delta) > 60 else delta
                                print(f"⏳ [Streaming] {part.function_call.name}: {preview}")
                    continue

                # Handle partial text responses (streaming text)
                if event.partial:
                    for part in event.content.parts:
                        if part.text:
                            print(part.text, end="", flush=True)
                    continue

                # Handle complete events
                for part in event.content.parts:
                    if part.thought:
                        continue
                    if part.function_call:
                        # Complete tool call - both streaming and non-streaming tools reach here
                        print(f"\n✅ [Tool Call Complete] {part.function_call.name}")
                        print(f"   Arguments: {part.function_call.args}")
                    elif part.function_response:
                        print(f"\n📊 [Tool Result] {part.function_response.response}")

            print("\n" + "-" * 60 + "\n")

    finally:
        await runner.close()
        root_agent.destroy()
        destroy_claude_env()
        print("🧹 Claude environment cleaned up")


if __name__ == "__main__":
    print("""
╔══════════════════════════════════════════════════════════════╗
║   ClaudeAgent Selective Streaming Tool Call Demo             ║
╠══════════════════════════════════════════════════════════════╣
║  This demo shows selective streaming - only tools with       ║
║  is_streaming=True receive real-time argument updates.       ║
║                                                              ║
║  - write_file (StreamingFunctionTool): Shows ⏳ events       ║
║  - get_file_info (FunctionTool): No streaming events         ║
╚══════════════════════════════════════════════════════════════╝
    """)
    asyncio.run(run_streaming_tool_demo())
