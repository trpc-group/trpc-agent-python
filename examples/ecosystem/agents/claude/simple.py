# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
#

import asyncio
import json
import os
import uuid

from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.server.agents.claude import ClaudeAgent
from trpc_agent_sdk.server.agents.claude import destroy_claude_env
from trpc_agent_sdk.server.agents.claude import setup_claude_env
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


def create_agent():
    """Create a ClaudeAgent with weather tool"""

    def get_weather(city: str) -> str:
        """Get the weather of a city"""
        return f"The weather of {city} is sunny."

    return ClaudeAgent(
        name="claude_weather_agent",
        description="A helpful Claude assistant for query weather",
        model=OpenAIModel(
            model_name="deepseek-v3-local-II",
            api_key=os.environ.get("API_KEY", ""),
            base_url="http://v2.open.venus.woa.com/llmproxy",
        ),
        instruction="You are a helpful assistant for query weather. You can also casually chat with user.",
        # generate_content_config=GenerateContentConfig(
        #     temperature=0.7,
        # ),
        tools=[FunctionTool(get_weather)],
    )


async def run_agent(runner: Runner, user_id: str, session_id: str, user_input: str):
    """Run Agent"""

    print(f"🆔 Session ID: {session_id[:8]}...")
    print(f"📝 用户: {user_input}")

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
                print(f"📊 [Tool Result: {part.function_response.name}({response_str})]", flush=True)
            # uncomment part.text to get the full text
            # elif part.text:
            #     print(f"\n[🤖 Agent:] {part.text}", flush=True)
    print("", flush=True)


async def main():
    """Main function to run Claude Agent"""

    # Initialize Claude environment (proxy server)
    # This must be called before using ClaudeAgent
    model = OpenAIModel(
        model_name="deepseek-v3-local-II",
        api_key=os.environ.get("API_KEY", ""),
        base_url="http://v2.open.venus.woa.com/llmproxy",
        # generate_content_config=GenerateContentConfig(
        #     temperature=0.7,
        # ),
    )
    # # Can use a function to create default model
    # async def create_default_model():
    #     print("create defaul model for claude-code")
    #     return OpenAIModel(
    #         model_name="deepseek-v3-local-II",
    #         api_key=os.environ.get("API_KEY", ""),
    #         base_url="http://v2.open.venus.woa.com/llmproxy",
    #     )
    # model = create_default_model

    agent = create_agent()

    setup_claude_env(proxy_host="0.0.0.0", proxy_port=8082, claude_models={"all": model})  # Maps to sonnet, opus, haiku
    agent.initialize()

    session_service = InMemorySessionService()
    runner = Runner(app_name="claude_weather_app", agent=agent, session_service=session_service)

    user_id = "Alice"
    session_id = str(uuid.uuid4())

    try:
        await run_agent(runner, user_id, session_id, "What is the weather in Beijing?")
    finally:
        await runner.close()
        agent.destroy()
        # Clean up: destroy Claude environment (stop proxy server)
        destroy_claude_env()
        print("🧹 Claude environment cleaned up")


if __name__ == "__main__":
    asyncio.run(main())
