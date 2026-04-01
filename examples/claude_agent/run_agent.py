# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""
ClaudeAgent Basic Demo

This example demonstrates the basic usage of ClaudeAgent with:
1. Custom FunctionTool for weather queries
2. Streaming response handling
3. Tool call and response display
"""

import asyncio
import json
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.server.agents.claude import destroy_claude_env
from trpc_agent_sdk.server.agents.claude import setup_claude_env
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


async def run_agent(runner: Runner, user_id: str, session_id: str, user_input: str):
    """Run Agent with user input.

    Args:
        runner: The runner instance
        user_id: User identifier
        session_id: Session identifier
        user_input: User query text
    """
    print(f"🆔 Session ID: {session_id[:8]}...")
    print(f"📝 User: {user_input}")

    user_content = Content(parts=[Part.from_text(text=user_input)])

    print("\n🤖 Agent: ", end="", flush=True)
    async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=user_content):
        # Check if event.content exists
        if not event.content or not event.content.parts:
            continue

        if event.partial:
            for part in event.content.parts:
                if part.text:
                    print(part.text, end="", flush=True)
            continue

        for part in event.content.parts:
            if part.function_call:
                args_str = json.dumps(part.function_call.args, ensure_ascii=False)[:200]
                print(f"\n🔧 [Tool Call: {part.function_call.name}({args_str})]", flush=True)
            elif part.function_response:
                response_str = json.dumps(part.function_response.response, ensure_ascii=False)[:200]
                print(f"📊 [Tool Result: {response_str}]", flush=True)

    print("", flush=True)


async def main():
    """Main function to run Claude Agent demo."""
    print("=" * 80)
    print("ClaudeAgent Basic Demo - Weather Query")
    print("=" * 80)
    print()

    # Import agent and initialize Claude environment
    from agent.agent import root_agent, _create_model

    # Setup Claude environment with proxy server
    model = _create_model()
    setup_claude_env(proxy_host="0.0.0.0", proxy_port=8082, claude_models={"all": model})
    root_agent.initialize()

    # Create session service and runner
    session_service = InMemorySessionService()
    runner = Runner(app_name="claude_weather_app", agent=root_agent, session_service=session_service)

    user_id = "demo_user"
    session_id = str(uuid.uuid4())

    try:
        await run_agent(runner, user_id, session_id, "What is the weather in Beijing?")

        print()
        print("=" * 80)
        print("Demo completed!")
        print("=" * 80)

    finally:
        await runner.close()
        root_agent.destroy()
        destroy_claude_env()
        print("🧹 Claude environment cleaned up")


if __name__ == "__main__":
    asyncio.run(main())
