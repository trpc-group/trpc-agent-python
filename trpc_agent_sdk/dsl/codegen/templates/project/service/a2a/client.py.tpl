# -*- coding: utf-8 -*-
"""A2A client test for generated service."""

import asyncio
import os
import uuid

from dotenv import load_dotenv

from trpc.config import config
from trpc.plugin import setup

from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, Part
from trpc_agent_sdk.server.a2a.agent import TrpcRemoteA2aAgent


async def create_remote_agent() -> TrpcRemoteA2aAgent:
    """Create remote A2A agent."""
    agent = TrpcRemoteA2aAgent(
        name="generated_remote_agent",
        service_name="trpc.py_trpc_agent.helloworld.Greeter",
        description="Remote A2A agent for generated workflow",
    )
    await agent.initialize()
    return agent


async def test_remote_agent() -> None:
    """Run multi-turn test against A2A service."""
    app_name = "generated_a2a_client"
    user_id = "demo_user"
    session_id = str(uuid.uuid4())
    queries = [
        "Hello from generated A2A client.",
        "Please continue with the same context.",
    ]

    remote_agent = await create_remote_agent()
    runner = Runner(app_name=app_name, agent=remote_agent, session_service=InMemorySessionService())

    try:
        for query in queries:
            print("===============================")
            print(f"Q: {query}")
            print("A: ", end="", flush=True)

            user_message = Content(parts=[Part.from_text(text=query)])
            async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=user_message):
                if not event.content:
                    continue
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
            print("\n===============================")
    finally:
        await runner.close()


if __name__ == "__main__":
    load_dotenv()
    config_path = os.path.join(os.path.dirname(__file__), "trpc_python_client.yaml")
    config.load_global_config(config_path, "utf-8")
    setup()
    asyncio.run(test_remote_agent())

