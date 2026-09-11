# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Demonstrate Advanced session compaction running before each model call."""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv

from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.sessions import SessionServiceConfig
from trpc_agent_sdk.sessions.compact import AdvancedAutoCompactSummarizer
from trpc_agent_sdk.sessions.compact import AdvancedAutoCompactSummarizerConfig
from trpc_agent_sdk.sessions.compact import AdvancedAutoCompactSummarizerManager
from trpc_agent_sdk.sessions.compact import AutoCompactSummarizerConfig
from trpc_agent_sdk.sessions.compact import SessionMemoryExtractorConfig
from trpc_agent_sdk.sessions.compact import TokenContextTrackerConfig
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv(Path(__file__).with_name(".env"))

SESSION_MEMORY_STATE_KEY = "_trpc_agent:summary"

PROJECT_BRIEF = """I am building Project Apollo and I want you to remember its constraints:
- Runtime: Python 3.12 with asyncio everywhere, no blocking calls in request paths.
- Web layer: FastAPI with Pydantic models for every request and response body.
- Storage: SQLite through SQLAlchemy, and every failed write must roll back.
- Testing: pytest with async tests covering each persistence path.
- Style: full type hints, small functions, no bare except.
- Deployment: Linux containers, released every Friday afternoon.
"""

# Multi-turn conversation. Real turns are what build alternating user/model
# history, which is the shape the compaction boundary is computed from.
CONVERSATIONS = (
    PROJECT_BRIEF + "\nAcknowledge the constraints and outline the module layout you would use.",
    "Show me the SQLAlchemy session helper for Project Apollo, "
    "including how rollback is handled on a failed write.",
    "Now show the pytest fixtures and one async test that proves the rollback path works.",
    "Explain how I should structure the FastAPI routers and dependency injection for this project.",
    "Recap Project Apollo: its stack, storage rules, testing rules, and release cadence.",
)


def create_compact_config() -> AdvancedAutoCompactSummarizerConfig:
    """Use small character budgets so the example compacts within a few turns."""
    return AdvancedAutoCompactSummarizerConfig(
        # Character thresholds keep the demo independent of any model's
        # context window. Production setups usually enable the token tracker.
        token_context_tracker=TokenContextTrackerConfig(enabled=False),
        session_memory=SessionMemoryExtractorConfig(
            initial_chars=1_000,
            update_chars=500,
        ),
        auto_compact=AutoCompactSummarizerConfig(
            trigger_chars=4_000,
            target_chars=2_000,
            blocking_chars=20_000,
            keep_recent_contents=2,
            summary_input_max_chars=12_000,
        ),
    )


def create_summarizer_manager(model: LLMModel) -> AdvancedAutoCompactSummarizerManager:
    """Create the Advanced summarizer that the model filter drives."""
    summarizer = AdvancedAutoCompactSummarizer(
        config=create_compact_config(),
        model=model,
    )
    return AdvancedAutoCompactSummarizerManager(summarizer=summarizer)


def print_session_state(label: str, session: Session) -> None:
    """Print the state needed to verify compaction behavior."""
    summary_anchor = bool(session.events and session.events[0].is_summary_event())
    print(f"\n{label}")
    print(f"  active events: {len(session.events)}")
    print(f"  historical events: {len(session.historical_events)}")
    print(f"  active window starts with summary: {summary_anchor}")
    print(f"  session memory persisted: {SESSION_MEMORY_STATE_KEY in session.state}")


async def run_turn(runner: Runner, user_id: str, session_id: str, prompt: str) -> None:
    """Send one user message and stream the answer."""
    print(f"\nUser: {prompt.splitlines()[0]}")
    print("Assistant: ", end="", flush=True)
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=Content(parts=[Part.from_text(text=prompt)]),
    ):
        if not event.content or not event.content.parts:
            continue
        for part in event.content.parts:
            if part.text and not part.thought:
                print(part.text, end="" if event.partial else "\n", flush=True)


async def main() -> None:
    """Run a multi-turn conversation and verify before-model Advanced AutoCompact."""
    app_name = "advanced-session-summarizer-demo"
    user_id = "demo-user"
    session_id = os.getenv("SESSION_ID", str(uuid.uuid4()))

    # Import after load_dotenv so the module-level Agent can read model settings.
    from agent.agent import root_agent

    manager = create_summarizer_manager(root_agent.model)
    session_service = InMemorySessionService(
        summarizer_manager=manager,
        session_config=SessionServiceConfig(store_historical_events=True),
    )
    runner = Runner(
        app_name=app_name,
        agent=root_agent,
        session_service=session_service,
    )

    try:
        await session_service.create_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        print(f"Session: {app_name}/{user_id}/{session_id}")

        compaction_turns: list[int] = []
        historical_count = 0
        for index, prompt in enumerate(CONVERSATIONS, start=1):
            await run_turn(runner, user_id, session_id, prompt)
            stored = await session_service.get_session(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
            )
            if stored is None:
                raise RuntimeError("Session disappeared during the conversation")
            print_session_state(f"After turn {index}", stored)
            if len(stored.historical_events) > historical_count:
                compaction_turns.append(index)
                historical_count = len(stored.historical_events)

        if not compaction_turns:
            raise RuntimeError(
                "Compaction never ran. Confirm that the model installs "
                "AdvancedAutoCompactSummarizerFilter, that the conversation "
                "exceeds auto_compact.trigger_chars, and that the model returns "
                "the requested <summary> block."
            )
        if not stored.events[0].is_summary_event():
            raise RuntimeError("Compaction ran but the active window lost its summary anchor")

        print(f"\nPASS: compaction ran on turn(s) {compaction_turns}.")
        print(f"  archived events: {len(stored.historical_events)}")
        print(f"  model-visible events: {len(stored.events)}")
    finally:
        await runner.close()


if __name__ == "__main__":
    asyncio.run(main())
