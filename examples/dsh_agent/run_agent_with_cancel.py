# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Cancel a running DSHAgent turn and verify that the session recovers."""

import asyncio
import json
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.events import AgentCancelledEvent
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.server.agents.dsh import DSH_SESSION_STATE_KEY
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()

APP_NAME = "dsh_agent_cancel_demo"
USER_ID = "cancel_demo"

CANCEL_PROMPT = (
    "请写一篇关于人工智能发展历史的长文，至少 1200 字，分成 8 段，"
    "每段先写小标题再写正文。不要省略，直接输出正文。"
)
FOLLOWUP_PROMPT = "只回复 ok，不要调用任何工具。"


def _visible_text(event) -> str:
    if not event.content or not event.content.parts:
        return ""
    return "".join(
        part.text
        for part in event.content.parts
        if part.text and not part.thought
    )


def _state_summary(session) -> dict:
    """Return event count / last seq / version from the persisted DSH JSONL."""

    raw = session.state.get(DSH_SESSION_STATE_KEY) if session is not None else None
    if not isinstance(raw, dict) or not isinstance(raw.get("jsonl"), str):
        return {"events": 0, "last_seq": None, "version": None}

    lines = [line for line in raw["jsonl"].splitlines() if line.strip()]
    last_seq = None
    for line in lines[1:]:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event.get("seq"), int):
            last_seq = event["seq"]
    return {
        "events": max(0, len(lines) - 1),
        "last_seq": last_seq,
        "version": raw.get("version"),
    }


async def _run_turn(
    runner: Runner,
    session_id: str,
    prompt: str,
    *,
    first_text: asyncio.Event | None = None,
    result: dict | None = None,
) -> None:
    """Consume one turn; optionally signal when the first visible text arrives."""

    print(f"\n📝 User: {prompt}")
    print("🤖 Assistant: ", end="", flush=True)

    result = result if result is not None else {}
    result.setdefault("events", 0)
    result.setdefault("partial_chars", 0)
    result.setdefault("cancelled", False)
    result.setdefault("completed", False)
    result.setdefault("final_text", [])

    async for event in runner.run_async(
            user_id=USER_ID,
            session_id=session_id,
            new_message=Content(parts=[Part(text=prompt)]),
    ):
        result["events"] += 1

        if isinstance(event, AgentCancelledEvent):
            result["cancelled"] = True
            print(f"\n❌ Run was cancelled: {event.error_message}")
            # Keep consuming until the Runner finishes its cleanup; closing the
            # async generator at this yield would skip the cancel finalizer.
            continue

        if event.partial:
            chunk = _visible_text(event)
            if chunk:
                result["partial_chars"] += len(chunk)
                print(chunk, end="", flush=True)
                if first_text is not None:
                    first_text.set()
            continue

        if not event.content or not event.content.parts:
            continue

        for part in event.content.parts:
            if part.function_call:
                print(f"\n🔧 [Tool Call: {part.function_call.name}({part.function_call.args})]")
            elif part.function_response:
                print(f"📊 [Tool Result: {part.function_response.response}]")
            elif part.text and not part.thought:
                result["final_text"].append(part.text)

    result["completed"] = not result["cancelled"]
    if result["partial_chars"] == 0 and result["final_text"]:
        print("".join(result["final_text"]), end="", flush=True)
    print()


async def _cancel_current_turn(
    runner: Runner,
    session_id: str,
    task: asyncio.Task,
    first_text: asyncio.Event,
    result: dict,
) -> bool:
    """Wait for the first visible text, then request cancellation."""

    try:
        await asyncio.wait_for(first_text.wait(), timeout=20.0)
        print("\n⏸️  First partial text received; requesting cancellation...")
    except asyncio.TimeoutError:
        print("\n⚠️ No partial text within 20s; requesting cancellation anyway...")

    if task.done():
        print("⚠️ The turn finished before cancellation could be requested.")
        return False

    requested = await runner.cancel_run_async(
        user_id=USER_ID,
        session_id=session_id,
        timeout=30.0,
    )
    print(f"✓ Cancellation requested: {requested}")

    try:
        await asyncio.wait_for(task, timeout=60.0)
    except asyncio.TimeoutError:
        print("❌ Cancellation did not settle within 60s; cancelling the local task.")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    return requested


async def run_cancel_demo() -> None:
    from agent.agent import root_agent

    await root_agent.init()
    try:
        session_service = InMemorySessionService()
        runner = Runner(app_name=APP_NAME, agent=root_agent, session_service=session_service)
        session_id = str(uuid.uuid4())
        await session_service.create_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=session_id,
        )

        print("=" * 80)
        print("🎯 DSHAgent cancellation demo")
        print("=" * 80)
        print(f"🆔 Session ID: {session_id[:8]}...")

        result: dict = {}
        first_text = asyncio.Event()
        task = asyncio.create_task(
            _run_turn(runner, session_id, CANCEL_PROMPT, first_text=first_text, result=result))
        requested = await _cancel_current_turn(runner, session_id, task, first_text, result)

        session = await session_service.get_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=session_id,
        )
        after_cancel = _state_summary(session)
        print(
            f"\n📦 State after cancel: events={after_cancel['events']}, "
            f"last_seq={after_cancel['last_seq']}, version={after_cancel['version']}")

        print("\n" + "-" * 80)
        print("🔁 Follow-up turn on the same session")
        print("-" * 80)
        followup_result: dict = {}
        await _run_turn(runner, session_id, FOLLOWUP_PROMPT, result=followup_result)

        session = await session_service.get_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=session_id,
        )
        after_followup = _state_summary(session)
        print(
            f"\n📦 State after follow-up: events={after_followup['events']}, "
            f"last_seq={after_followup['last_seq']}, version={after_followup['version']}")

        resume_ok = (
            after_cancel["last_seq"] is not None
            and after_followup["last_seq"] is not None
            and after_followup["last_seq"] > after_cancel["last_seq"]
        )

        checks = [
            ("cancel_run_async returned True", requested),
            ("AgentCancelledEvent observed", result.get("cancelled", False)),
            ("cancel produced a non-empty JSONL state", after_cancel["events"] > 0),
            ("same session works after cancel", resume_ok),
        ]
        print("\n" + "=" * 80)
        for label, ok in checks:
            print(f"{'✅' if ok else '❌'} {label}")
        print("=" * 80)

        if not all(ok for _, ok in checks):
            raise SystemExit(1)
    finally:
        await root_agent.destroy()


if __name__ == "__main__":
    asyncio.run(run_cancel_demo())
