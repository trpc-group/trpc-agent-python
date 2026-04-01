# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.agents import BranchFilterMode
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part
# Load environment variables from the .env file
load_dotenv()


async def run_branch_filtering_demo():
    """Run branch filtering mode comparison demo.

    Use ALL / PREFIX / EXACT three BranchFilterMode to run the same customer support conversation,
    compare the differences in the visibility of historical messages of each Agent under different modes.
    """

    app_name = "branch_filter_demo"
    user_id = "customer_12345"

    from agent.agent import create_agent

    # Define three test scenarios for the three filtering modes
    # - ALL: All agents see messages from all branches
    # - PREFIX: Agents can only see messages from ancestors, self, and descendants on their own branch, and sibling branches are isolated
    # - EXACT: Agents can only see messages from their own branch, completely isolated
    test_scenarios = [
        {
            "title": "Scenario 1: BranchFilterMode.ALL",
            "filter_mode": BranchFilterMode.ALL,
            "description": "All agents see messages from ALL branches",
        },
        {
            "title": "Scenario 2: BranchFilterMode.PREFIX",
            "filter_mode": BranchFilterMode.PREFIX,
            "description": "Agents see ancestors, self, and descendants only",
        },
        {
            "title": "Scenario 3: BranchFilterMode.EXACT",
            "filter_mode": BranchFilterMode.EXACT,
            "description": "Agents only see their own messages",
        },
    ]

    # Simulate customer support conversation: first report technical issues, then deep database diagnosis, finally query bill
    # This query will trigger the complete chain of TechnicalSupport -> DatabaseExpert -> BillingSupport
    demo_queries = [
        "Hello, our application is running very slow. Can you check what's wrong?",
        "The database seems to be the problem. Can you diagnose it in detail?",
        "Thanks! Also, can you look up my invoice for customer ID 12345?",
    ]

    for scenario in test_scenarios:
        print("\n" + "=" * 80)
        print(f"{scenario['title']}")
        print(f"Description: {scenario['description']}")
        print(f"CustomerService: EXACT (always)")
        print(f"TechnicalSupport / DatabaseExpert / BillingSupport: {scenario['filter_mode'].value}")
        print("=" * 80)

        # Create an independent Agent layer for each scenario, using the corresponding filter_mode
        agent = create_agent(filter_mode=scenario["filter_mode"])

        # Use an independent session_service and runner for each scenario, to avoid cross-scenario state pollution
        session_service = InMemorySessionService()
        runner = Runner(app_name=app_name, agent=agent, session_service=session_service)

        # Use the same session_id for multiple rounds of conversation within the same scenario, to observe the accumulation and filtering effect of historical messages
        current_session_id = str(uuid.uuid4())

        for i, message in enumerate(demo_queries, 1):
            print(f"\n{'─' * 80}")
            print(f"Customer Request {i}: {message}")
            print(f"{'─' * 80}")

            user_content = Content(parts=[Part.from_text(text=message)])

            async for event in runner.run_async(user_id=user_id,
                                                session_id=current_session_id,
                                                new_message=user_content):
                # Skip empty content events
                if not event.content or not event.content.parts:
                    continue

                # partial=True means the intermediate fragments of the streaming output, print the text in real time
                if event.partial:
                    for part in event.content.parts:
                        if part.text:
                            print(part.text, end="", flush=True)
                    continue

                # Complete event: print tool calls and tool return results, skip the thinking process
                for part in event.content.parts:
                    if part.thought:
                        continue
                    if part.function_call:
                        print(f"[{event.author}] 🔧 Calling: {part.function_call.name}({part.function_call.args})")
                    elif part.function_response:
                        print(f"[{event.author}] 📥 Result: {part.function_response.response}")

            print("\n" + "-" * 40)

        print()

    # Summary of the core differences between the three modes
    print("=" * 80)
    print("Key Takeaways:")
    print("1. BranchFilterMode.ALL:")
    print("   - All agents see messages from all branches")
    print("   - Best for scenarios requiring full conversation context")
    print("2. BranchFilterMode.PREFIX:")
    print("   - Agents see ancestors, self, and descendants only")
    print("   - Enables hierarchical workflows with proper context flow")
    print("   - Sibling branches are isolated")
    print("3. BranchFilterMode.EXACT:")
    print("   - Agents only see their own messages")
    print("   - Complete isolation for maximum privacy")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_branch_filtering_demo())
