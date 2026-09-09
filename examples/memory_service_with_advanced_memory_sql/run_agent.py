#!/usr/bin/env python3
"""Run the Advanced Memory SQL persistence example."""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

from agent.agent import create_agent
from trpc_agent_sdk.advanced_memory import AdvancedMemoryConfig
from trpc_agent_sdk.memory import AdvancedMemoryService
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import SessionServiceConfig, SqlSessionService
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


def build_sql_url_from_environment() -> str:
    """Use SQL_URL or build a MySQL URL from standard environment variables."""
    sql_url = os.getenv("SQL_URL")
    if sql_url:
        return sql_url

    user = quote(os.getenv("MYSQL_USER", "root"), safe="")
    password = quote(os.getenv("MYSQL_PASSWORD", ""), safe="")
    host = os.getenv("MYSQL_HOST", "127.0.0.1")
    port = os.getenv("MYSQL_PORT", "3306")
    database = os.getenv("MYSQL_DB", "trpc_agent_advanced_memory")
    return f"mysql+aiomysql://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4"


def sql_is_async() -> bool:
    """Return whether the configured SQL driver is asynchronous."""
    return os.getenv("SQL_IS_ASYNC", "true").lower() in {"1", "true", "yes"}


def create_advanced_memory_service(sql_url: str) -> AdvancedMemoryService:
    """Create Advanced Memory backed by SQL."""
    memory_ttl = os.getenv("M_TTL")
    session_ttl = os.getenv("SESSION_TTL")
    config = AdvancedMemoryConfig(
        storage_backend="sql",
        sql_url=sql_url,
        sql_is_async=sql_is_async(),
        memory_ttl_seconds=int(memory_ttl) if memory_ttl else None,
        session_ttl_seconds=int(session_ttl) if session_ttl else None,
    )
    return AdvancedMemoryService(config)


def create_sql_session_service(sql_url: str) -> SqlSessionService:
    """Create the SQL-backed framework session service."""
    session_ttl = os.getenv("SESSION_TTL")
    ttl_seconds = int(session_ttl) if session_ttl else 0
    return SqlSessionService(
        db_url=sql_url,
        is_async=sql_is_async(),
        session_config=SessionServiceConfig(ttl=SessionServiceConfig.create_ttl_config(
            enable=bool(session_ttl),
            ttl_seconds=ttl_seconds,
            cleanup_interval_seconds=ttl_seconds,
        ), ),
    )


async def run_phase(phase: str) -> None:
    """Run Runner A or Runner B against the same SQL database."""
    sql_url = build_sql_url_from_environment()
    runner = Runner(
        app_name="advanced-memory-sql-demo",
        agent=create_agent(),
        session_service=create_sql_session_service(sql_url),
        memory_service=create_advanced_memory_service(sql_url),
    )
    try:
        queries = RUNNER_A_QUERIES if phase == "write" else RUNNER_B_QUERIES
        runner_name = "A" if phase == "write" else "B"
        for index, prompt in enumerate(queries):
            print(f"\n----- Runner {runner_name}, query {index + 1} -----")
            print(f"📝 user: {prompt}")
            async for event in runner.run_async(
                    user_id="sql-demo-user",
                    session_id=f"sql-{phase}-session-{index}",
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
    finally:
        await runner.close()


def run_two_processes() -> None:
    """Start independent writer and reader processes."""
    for phase in ("write", "read"):
        print(f"\n{'=' * 20} {phase.upper()} PROCESS {'=' * 20}", flush=True)
        subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--phase", phase],
            check=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("write", "read"))
    args = parser.parse_args()
    if args.phase:
        asyncio.run(run_phase(args.phase))
    else:
        run_two_processes()
