"""Evaluation + Optimization auto-regression pipeline.

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
        "List eval cases, then score this response against the 'greet' case "
        "(expected keywords: hello, hi): 'Hey there!'. "
        "Finally run optimize_prompt with the current prompt: "
        "'You are a helpful assistant' and eval scores [0.6, 0.8, 0.5]."
    )

    runner = Runner(agent=agent, session_service=session_service)
    async for event in runner.run(prompt):
        if event.content:
            print(event.content, end="", flush=True)
    print()


if __name__ == "__main__":
    asyncio.run(main())
