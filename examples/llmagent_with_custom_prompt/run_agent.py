# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

# Load environment variables from the .env file
load_dotenv()


async def run_custom_prompt_demo():
    """Run custom prompt injection demonstration.

    Run three configuration scenarios to demonstrate the effects of add_name_to_instruction and default_transfer_message:
   - Default behavior: framework automatically injects Agent name and forwarding instruction
   - Disable name injection: add_name_to_instruction=False
   - Custom forwarding instruction: default_transfer_message uses custom content
   """

    app_name = "custom_prompt_demo"
    user_id = "demo_user"

    from agent.agent import create_agent
    from agent.prompts import CUSTOM_TRANSFER_MESSAGE

    # Three configuration scenarios comparison
    scenarios = [
        {
            "title": "Scenario 1: Default (framework auto-injection enabled)",
            "add_name": True,
            "transfer_message": None,
        },
        {
            "title": "Scenario 2: add_name_to_instruction=False",
            "add_name": False,
            "transfer_message": None,
        },
        {
            "title": "Scenario 3: Custom default_transfer_message",
            "add_name": True,
            "transfer_message": CUSTOM_TRANSFER_MESSAGE,
        },
    ]

    # Test query coverage of two sub-Agents: weather and translation
    demo_queries = [
        "What's the weather in Beijing?",
        "Translate 'hello' to Chinese",
    ]

    for scenario in scenarios:
        print("\n" + "=" * 80)
        print(scenario["title"])
        print(f"  add_name_to_instruction = {scenario['add_name']}")
        print(
            f"  default_transfer_message = {repr(scenario['transfer_message'][:30] + '...') if scenario['transfer_message'] else repr(scenario['transfer_message'])}"
        )
        print("=" * 80)

        agent = create_agent(
            add_name=scenario["add_name"],
            transfer_message=scenario["transfer_message"],
        )

        session_service = InMemorySessionService()
        runner = Runner(app_name=app_name, agent=agent, session_service=session_service)

        current_session_id = str(uuid.uuid4())

        for query in demo_queries:
            print(f"\n📝 User query: {query}")
            print("🤖 Assistant: ", end="", flush=True)

            user_content = Content(parts=[Part.from_text(text=query)])

            async for event in runner.run_async(
                    user_id=user_id,
                    session_id=current_session_id,
                    new_message=user_content,
            ):
                if not event.content or not event.content.parts:
                    continue

                if event.partial:
                    for part in event.content.parts:
                        if part.text:
                            print(part.text, end="", flush=True)
                    continue

                for part in event.content.parts:
                    if part.thought:
                        continue
                    if part.function_call:
                        print(f"\n🔧 [{event.author}] Invoke Tool: {part.function_call.name}({part.function_call.args})")
                    elif part.function_response:
                        print(f"📊 [{event.author}] Tool Result: {part.function_response.response}")

            print("\n" + "-" * 40)

        print()


if __name__ == "__main__":
    asyncio.run(run_custom_prompt_demo())
