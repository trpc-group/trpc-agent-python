#!/usr/bin/env python3
"""Exercise Advanced Memory compression and session summaries on Redis or SQL."""

import argparse
import asyncio
import os
import uuid
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

from trpc_agent_sdk.advanced_memory import AdvancedMemoryConfig
from trpc_agent_sdk.memory import AdvancedMemoryService
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import RedisSessionService
from trpc_agent_sdk.sessions import SessionServiceConfig
from trpc_agent_sdk.sessions import SqlSessionService
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import Content, Part

load_dotenv(Path(__file__).with_name(".env"))


def build_redis_url() -> str:
    """Build a Redis URL from REDIS_URL or standard Redis variables."""
    direct = os.getenv("REDIS_URL")
    if direct:
        return direct
    host = os.getenv("REDIS_HOST", "127.0.0.1")
    port = os.getenv("REDIS_PORT", "6379")
    database = os.getenv("REDIS_DB", "0")
    password = os.getenv("REDIS_PASSWORD", "")
    auth = f":{quote(password, safe='')}@" if password else ""
    scheme = "rediss" if os.getenv("REDIS_TLS", "").lower() in {"1", "true", "yes"} else "redis"
    return f"{scheme}://{auth}{host}:{port}/{database}"


def build_sql_url() -> str:
    """Build a SQL URL from SQL_URL or MySQL variables."""
    direct = os.getenv("SQL_URL")
    if direct:
        return direct
    user = quote(os.getenv("MYSQL_USER", "root"), safe="")
    password = quote(os.getenv("MYSQL_PASSWORD", ""), safe="")
    host = os.getenv("MYSQL_HOST", "127.0.0.1")
    port = os.getenv("MYSQL_PORT", "3306")
    database = os.getenv("MYSQL_DB", "trpc_agent_compression_check")
    return f"mysql+aiomysql://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4"


def sql_is_async() -> bool:
    """Return whether the selected SQL driver is asynchronous."""
    return os.getenv("SQL_IS_ASYNC", "true").lower() in {"1", "true", "yes"}


def generate_large_report(size: int = 6_000) -> dict:
    """Return deterministic tool output large enough to trigger compression."""
    # Keep the request bounded even when the model asks for an unexpectedly
    # large value; otherwise one backend run can trip the circuit breaker
    # before session-memory extraction gets a chance to run.
    size = max(1, min(size, 6_000))
    return {
        "status": "success",
        "report": ("compression-test-record " * ((size // 25) + 1))[:size],
    }


def create_agent() -> LlmAgent:
    """Create an agent that reliably has a large tool result to compact."""
    model_name = os.environ["TRPC_AGENT_MODEL_NAME"]
    model = OpenAIModel(
        model_name=model_name,
        api_key=os.environ["TRPC_AGENT_API_KEY"],
        base_url=os.environ["TRPC_AGENT_BASE_URL"],
    )
    return LlmAgent(
        name="advanced_memory_compression_checker",
        description="Checks Advanced Memory compression and session summaries.",
        model=model,
        instruction=(
            "This is a storage integration test. For every user request, call "
            "generate_large_report once before answering. Keep the answer concise. "
            "Do not call any other tool."
        ),
        tools=[FunctionTool(generate_large_report)],
    )


def create_config(backend: str, url: str) -> AdvancedMemoryConfig:
    """Create a deliberately low-threshold configuration for this check."""
    common = dict(
        storage_backend=backend,
        session_memory_initial_chars=1,
        session_memory_update_chars=1,
        session_memory_tool_calls_between_updates=1,
        session_memory_prompt_max_chars=20_000,
        history_snip_trigger_chars=1_500,
        history_snip_target_chars=900,
        history_snip_keep_recent=1,
        autocompact_trigger_chars=2_500,
        autocompact_target_chars=1_200,
        autocompact_blocking_chars=4_000,
        autocompact_keep_recent_contents=2,
        autocompact_summary_input_max_chars=10_000,
        microcompact_trigger_count=2,
        microcompact_keep_recent=1,
        tool_result_max_chars=8_000,
        tool_results_per_message_max_chars=12_000,
    )
    if backend == "redis":
        return AdvancedMemoryConfig(redis_url=url, redis_key_prefix="advanced-memory-compression-check:v1", **common)
    return AdvancedMemoryConfig(
        sql_url=url,
        sql_is_async=sql_is_async(),
        **common,
    )


def create_session_service(backend: str, url: str):
    """Create the framework session service using the same backend."""
    ttl = int(os.getenv("SESSION_TTL", "0"))
    session_config = SessionServiceConfig(
        ttl=SessionServiceConfig.create_ttl_config(
            enable=ttl > 0,
            ttl_seconds=ttl,
            cleanup_interval_seconds=max(ttl, 1),
        ),
    )
    if backend == "redis":
        return RedisSessionService(db_url=url, is_async=True, session_config=session_config)
    return SqlSessionService(db_url=url, is_async=sql_is_async(), session_config=session_config)


async def run(backend: str) -> None:
    """Run enough turns to exercise all low-threshold paths."""
    url = build_redis_url() if backend == "redis" else build_sql_url()
    memory_service = AdvancedMemoryService(create_config(backend, url))
    runner = Runner(
        app_name=f"advanced-memory-compression-{backend}",
        agent=create_agent(),
        session_service=create_session_service(backend, url),
        memory_service=memory_service,
    )
    session_id = os.getenv(
        "CHECK_SESSION_ID",
        f"compression-check-{backend}-{uuid.uuid4().hex[:8]}",
    )
    print(f"Session ID: {session_id}")
    try:
        for index in range(4):
            prompt = (
                f"Compression test turn {index + 1}. "
                "Call the large report tool, then briefly confirm the turn."
            )
            print(f"\n📝 user: {prompt}")
            async for event in runner.run_async(
                user_id="compression-check-user",
                session_id=session_id,
                new_message=Content(parts=[Part.from_text(text=prompt)]),
            ):
                if not event.content or not event.content.parts:
                    continue
                for part in event.content.parts:
                    if part.function_call:
                        print(f"🔧 tool call: {part.function_call.name}({part.function_call.args})")
                    elif part.function_response:
                        print(f"📊 Tool Result: {str(part.function_response.response)[:160]}...")
                    elif not event.partial and part.text and not part.thought:
                        print(f"🤖 Assistant: {part.text}")

        runtime = memory_service.runtime.for_scope(
            f"advanced-memory-compression-{backend}",
            "compression-check-user",
        )
        records = await runtime.transcripts.read_all(session_id)
        kinds = {}
        for record in records:
            kind = record.get("kind", "unknown")
            kinds[kind] = kinds.get(kind, 0) + 1
        session_memory = await runtime.session_memory.read(session_id)
        print("\n" + "=" * 60)
        print(f"Backend: {backend}")
        print(f"Transcript record counts: {kinds}")
        print(f"Session memory written: {'yes' if session_memory else 'no'}")
        print("Expected records include: session-memory-checkpoint, autocompact-success,")
        print("history-snip, microcompact-clear, or tool-result-* depending on model output.")
    finally:
        await runner.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=("redis", "sql"), required=True)
    args = parser.parse_args()
    asyncio.run(run(args.backend))
