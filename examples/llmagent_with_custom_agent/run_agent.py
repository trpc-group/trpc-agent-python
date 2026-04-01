# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Custom Agent example - Smart Document Processor """

import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


async def run_custom_agent():
    """Run the custom agent demo - Smart Document Processor"""

    app_name = "custom_agent_demo"

    from agent.agent import root_agent
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "demo_user"

    # Test different types of documents
    test_documents = [
        {
            "title": "Simple document example",
            "content": "Explain what artificial intelligence is and its applications in daily life.",
            "expected_type": "simple",
        },
        {
            "title": "Complex document example",
            "content": """Annual financial report summary:

Revenue growth analysis:
The total revenue for this year reached 500 million yuan, an increase of 25% compared to last year. The main sources of growth include:
1. Core product sales increased by 30%
2. New product lines contributed 15% of revenue
3. Overseas market expansion brings a 20% increase

Cost structure optimization:
Through supply chain restructuring and automation improvements, the operating cost has decreased by 8%.

Market prospect:
Based on current trend analysis, the expected annual growth rate is expected to remain within the range of 20-30%.

Need to deeply analyze the correlation and trend of various data.""",
            "expected_type": "complex",
        },
        {
            "title": "Technical document example",
            "content": """Python asynchronous programming best practices:

1. Use async/await syntax
async def fetch_data(url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return await response.json()

2. Avoid using blocking calls in asynchronous functions
# Error example
async def bad_example():
    time.sleep(1)  # Blocking call

# Correct example
async def good_example():
    await asyncio.sleep(1)  # Non-blocking

3. Use asyncio.gather for concurrent processing
results = await asyncio.gather(
    fetch_data(url1),
    fetch_data(url2),
    fetch_data(url3)
)

Need to provide technical accurate explanations and code examples.""",
            "expected_type": "technical",
        },
    ]

    for i, doc in enumerate(test_documents, 1):
        print(f"\n{'='*20} Test case {i}: {doc['title']} {'='*20}")
        print(f"Expected type: {doc['expected_type']}")
        print(f"Document content: {doc['content'][:100]}...")
        print("\nProcessing:")

        current_session_id = str(uuid.uuid4())

        await session_service.create_session(
            app_name=app_name,
            user_id=user_id,
            session_id=current_session_id,
            state={"user_input": doc["content"]},
        )

        user_content = Content(parts=[Part.from_text(text=doc["content"])])

        async for event in runner.run_async(user_id=user_id, session_id=current_session_id, new_message=user_content):
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
                    print(f"\n🔧 [Invoke Tool: {part.function_call.name}({part.function_call.args})]")
                elif part.function_response:
                    print(f"📊 [Tool Result: {part.function_response.response}]")

        print("\n" + "-" * 80)


if __name__ == "__main__":
    asyncio.run(run_custom_agent())
