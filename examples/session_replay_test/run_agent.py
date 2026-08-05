"""Session/Memory replay consistency testing framework.

Usage::

    export TRPC_AGENT_API_KEY=your-key
    export TRPC_AGENT_BASE_URL=https://tokenhub.tencentmaas.com/v1
    python run_agent.py
"""

import asyncio
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from agent.agent import create_agent


async def main():
    agent = create_agent()
    session_service = InMemorySessionService()

    prompt = (
        "List the available backends. Then replay this test conversation: "
        "[{'role': 'user', 'content': 'What is 2+2?'}, "
        "{'role': 'assistant', 'content': '4'}] "
        "across the first available session and memory backends."
    )

    runner = Runner(agent=agent, session_service=session_service)
    async for event in runner.run(prompt):
        if event.content:
            print(event.content, end="", flush=True)
    print()


if __name__ == "__main__":
    asyncio.run(main())
