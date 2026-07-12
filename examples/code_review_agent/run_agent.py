"""Run the code review agent.

Usage::

    export TRPC_AGENT_API_KEY=your-hy3-key
    export TRPC_AGENT_BASE_URL=http://127.0.0.1:8000/v1
    python run_agent.py path/to/file.py
"""

import asyncio
import sys

from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService

from agent.agent import create_agent


async def main():
    if len(sys.argv) < 2:
        print("Usage: python run_agent.py <filepath>")
        sys.exit(1)

    filepath = sys.argv[1]
    with open(filepath, "r", encoding="utf-8") as f:
        code = f.read()

    agent = create_agent()
    session_service = InMemorySessionService()

    prompt = (
        f"Please review the following code file ({filepath}).\n"
        f"Call review_code first, then save_review with a score from 0-10, "
        f"and summarize your findings:\n\n```\n{code[:20000]}\n```"
    )

    runner = Runner(agent=agent, session_service=session_service)
    async for event in runner.run(prompt):
        if event.content:
            print(event.content, end="", flush=True)
    print()


if __name__ == "__main__":
    asyncio.run(main())
