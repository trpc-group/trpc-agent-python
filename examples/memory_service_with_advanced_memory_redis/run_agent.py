#!/usr/bin/env python3
"""Run twice to verify Redis Advanced Memory survives process restarts."""

from __future__ import annotations

import asyncio
import argparse
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

from agent.agent import create_agent
from trpc_agent_sdk.advanced_memory import AdvancedCompactConfig
from trpc_agent_sdk.memory import AdvancedMemoryService
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, Part

load_dotenv(Path(__file__).with_name(".env"))

RUNNER_A_QUERIES = [
    "Do you remember my name?",
    "Do you remember my favorite color?",
    "what is the weather like in paris?",
    "Hello! My name is Alice. What's your name?",
    "Do you remember my name?",
    "Hello! My favorite color is blue. What's your favorite color?",
    "Do you remember my favorite color?",
]

RUNNER_B_QUERIES = [
    "Do you remember my name?",
    "Do you remember my favorite color?",
]


def build_redis_url_from_environment() -> str:
    """Use REDIS_URL directly, or construct it from standard Redis variables."""
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        return redis_url

    host = os.getenv("REDIS_HOST", "127.0.0.1")
    port = os.getenv("REDIS_PORT", "6379")
    database = os.getenv("REDIS_DB", "0")
    username = os.getenv("REDIS_USER", "")
    password = os.getenv("REDIS_PASSWORD", "")
    scheme = "rediss" if os.getenv("REDIS_TLS", "").lower() in {"1", "true", "yes"} else "redis"

    if username and password:
        auth = f"{quote(username, safe='')}:{quote(password, safe='')}@"
    elif password:
        auth = f":{quote(password, safe='')}@"
    else:
        auth = ""
    return f"{scheme}://{auth}{host}:{port}/{database}"


def create_advanced_memory_service(redis_url: str) -> AdvancedMemoryService:
    """Create the long-term Advanced Memory service backed by Redis."""
    memory_ttl = os.getenv("M_TTL")
    config = AdvancedCompactConfig(
        storage_backend="redis",
        redis_url=redis_url,
        redis_key_prefix="advanced-memory-redis-demo:v1",
        memory_ttl_seconds=int(memory_ttl) if memory_ttl else None,
    )
    return AdvancedMemoryService(config)


async def ask(runner: Runner, session_id: str, prompt: str) -> None:
    """Send one message through the shared app and user identity."""
    print(f"\n📝 user: {prompt}")
    async for event in runner.run_async(
            user_id="redis-demo-user",
            session_id=session_id,
            new_message=Content(parts=[Part.from_text(text=prompt)]),
    ):
        if not event.content or not event.content.parts:
            continue
        for part in event.content.parts:
            if part.function_call:
                print(f"🔧 tool call: {part.function_call.name}({part.function_call.args})")
            elif part.function_response:
                print(f"📊 Tool Result: {part.function_response.response}")
            elif not event.partial and part.text and not part.thought:
                print(f"🤖 Assistant: {part.text}")


async def run_phase(phase: str) -> None:
    """Run Runner A or Runner B against the same Redis user."""
    app_name = "advanced-memory-redis-demo"
    redis_url = build_redis_url_from_environment()
    runner = Runner(
        app_name=app_name,
        agent=create_agent(),
        session_service=InMemorySessionService(),
        memory_service=create_advanced_memory_service(redis_url),
    )
    try:
        queries = RUNNER_A_QUERIES if phase == "write" else RUNNER_B_QUERIES
        runner_name = "A" if phase == "write" else "B"
        for index, prompt in enumerate(queries):
            print(f"\n----- Runner {runner_name}, query {index + 1} -----")
            await ask(runner, f"redis-{phase}-session-{index}", prompt)
    finally:
        await runner.close()


def run_two_processes() -> None:
    """Start fresh writer and reader processes to prove Redis persistence."""
    for phase in ("write", "read"):
        print(f"\n{'=' * 20} {phase.upper()} PROCESS {'=' * 20}", flush=True)
        subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--phase", phase],
            check=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("write", "read"))
    arguments = parser.parse_args()
    if arguments.phase:
        asyncio.run(run_phase(arguments.phase))
    else:
        run_two_processes()
