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

load_dotenv()


async def run_cycle_agent():
    """Run the Cycle Agent Demo"""

    APP_NAME = "cycle_agent_demo"
    USER_ID = "demo_user"

    print("=" * 60)
    print("Cycle Agent Demo - Iterative Content Improvement Cycle")
    print("=" * 60)

    from agent.agent import root_agent
    session_service = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=root_agent, session_service=session_service)

    user_request = ("Write a professional product description for an AI-powered smart home security system. "
                    "Include key features, benefits, and target audience.")

    print(f"Creation Requirements：{user_request}")
    print("\nIterative Improvement Process：")

    user_message = Content(parts=[Part.from_text(text=user_request)])

    iteration_count = 0
    current_agent = None

    async for event in runner.run_async(user_id=USER_ID, session_id=str(uuid.uuid4()), new_message=user_message):
        if event.content and event.content.parts and event.author != "user":
            if not event.partial:
                for part in event.content.parts:
                    if part.function_call and part.function_call.name == "exit_refinement_loop":
                        print(f"\n🔧 Invoke Tool：{part.function_call.name}")
                    elif part.function_response:
                        print(f"📋 Tool Result：{part.function_response.response}")
                        print("\n🎉 Content Improvement Completed！")
                    elif part.text:
                        # Detect new iteration rounds
                        if event.author == "content_writer" and current_agent != "content_writer":
                            iteration_count += 1
                            print(f"\n{'='*20} Round {iteration_count}  {'='*20}")
                            print(f"[{event.author}] Content Creation：")
                        elif event.author == "content_evaluator":
                            print(f"\n[{event.author}] Quality Assessment：")
                        else:
                            print(f"[{event.author}]:")

                        print(part.text)
                        print("-" * 40)
                        current_agent = event.author


if __name__ == "__main__":
    asyncio.run(run_cycle_agent())
