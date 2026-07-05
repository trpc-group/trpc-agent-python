#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run a code review through the LlmAgent (Skills + tool) — the framework-exercising path.

Dry-run by default: with no API key, FakeReviewModel drives one call to the review_code tool and
summarizes the result — no LLM, no secrets. Set TRPC_AGENT_API_KEY to use a real model instead.

    python run_agent.py --fixture 0001_insecure.diff
"""
from __future__ import annotations

import argparse
import asyncio
import uuid
from pathlib import Path

from dotenv import load_dotenv

from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, Part

from agent.agent import create_agent

HERE = Path(__file__).parent


async def review(diff_text: str, dry_run: bool = False) -> None:
    app_name = "code_review_agent"
    agent = create_agent(dry_run=dry_run)
    runner = Runner(app_name=app_name, agent=agent, session_service=InMemorySessionService())

    user_id, session_id = "reviewer", str(uuid.uuid4())
    await runner.session_service.create_session(app_name=app_name, user_id=user_id, session_id=session_id)

    message = Content(role="user", parts=[Part(text=diff_text)])
    async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=message):
        if not event.content or not event.content.parts:
            continue
        for part in event.content.parts:
            if part.text:
                print(part.text, end="", flush=True)
            elif part.function_call:
                print(f"\n[tool call] {part.function_call.name}")
            elif part.function_response:
                print(f"[tool result] {part.function_response.response}")
    print()


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser(description="Review a diff via the code-review agent.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--diff-file")
    src.add_argument("--fixture")
    ap.add_argument("--dry-run",
                    action="store_true",
                    help="force the fake model even if an API key is set (no real LLM call)")
    args = ap.parse_args()
    path = Path(args.diff_file) if args.diff_file else HERE / "fixtures" / "diffs" / args.fixture
    asyncio.run(review(path.read_text(encoding="utf-8"), dry_run=args.dry_run))


if __name__ == "__main__":
    main()
