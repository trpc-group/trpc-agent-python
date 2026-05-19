# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""End-to-end verification of StreamingProgressTool(skip_summarization=True).

What this script proves, in one run:

1. **Live streaming reaches the caller**
   The wrapped async generator's yields show up as partial events with
   ``custom_metadata.tool_progress=True`` *while* the tool is still running.

2. **The LLM is not asked to re-summarise the streamed output**
   Because the tool is constructed with ``skip_summarization=True``, the
   final ``function_response`` event has ``actions.skip_summarization=True``.
   :class:`LlmAgent` exits the conversation loop immediately after the
   tool returns – the caller will NOT see any "Assistant: ..." text after
   the tool result.

3. **The session keeps the final tool result, not the partials**
   Partial progress events have ``partial=True`` so session services
   skip them. The final ``function_response`` event is non-partial and
   IS persisted. We assert this against the in-memory session at the
   end of turn 1.

4. **Next turn can use the persisted data**
   In turn 2 we ask "Summarise what you fetched earlier". The LLM has
   access to the full tool response from turn 1 via session history and
   answers based on it. The accumulated ``titles`` list we put in the
   final yield is the source of truth.

Run::

    cd examples/llmagent_with_streaming_progress_tool
    # Ensure .env defines TRPC_AGENT_API_KEY / BASE_URL / MODEL_NAME
    python verify.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()

sys.path.append(str(Path(__file__).parent))

APP_NAME = "streaming_progress_verify"
USER_ID = "verify_user"


def _section(title: str) -> None:
    line = "=" * 70
    print(f"\n{line}\n  {title}\n{line}")


def _bullet(ok: bool, msg: str) -> None:
    mark = "[PASS]" if ok else "[FAIL]"
    print(f"  {mark} {msg}")


async def _run_turn(
    runner: Runner,
    session_id: str,
    query: str,
    label: str,
) -> tuple[list[dict], list, str, str]:
    """Run one user turn.

    Returns a 4-tuple ``(live_progress, all_events, post_tool_text, all_llm_text)``:

    - ``live_progress`` : every progress chunk that arrived live, proving
      the streaming pipe fired.
    - ``all_events``    : everything yielded by the runner, in order.
    - ``post_tool_text``: LLM-authored text that arrived *after* the
      function_response event. We use this to detect whether the LLM
      tried to "re-summarise" the tool output.
    - ``all_llm_text``  : every LLM-authored text chunk across the whole
      turn (used when the turn has no tool call at all).
    """
    _section(label)
    print(f"User: {query}")

    live_progress: list[dict] = []
    all_events: list = []
    tool_done = False
    post_tool_text_parts: list[str] = []
    all_llm_text_parts: list[str] = []

    user_content = Content(parts=[Part.from_text(text=query)])
    async for event in runner.run_async(user_id=USER_ID, session_id=session_id, new_message=user_content):
        all_events.append(event)
        meta = event.custom_metadata or {}

        # (1) Live progress
        if event.partial and meta.get("tool_progress"):
            payload = meta.get("payload")
            live_progress.append(payload if isinstance(payload, dict) else {"raw": payload})
            print(f"  [live] {meta.get('tool_name')} -> {payload}")
            continue

        if not event.content or not event.content.parts:
            continue

        # Streaming LLM text (partial assistant message)
        if event.partial:
            for part in event.content.parts:
                if part.text:
                    all_llm_text_parts.append(part.text)
                    if tool_done:
                        post_tool_text_parts.append(part.text)
                    print(part.text, end="", flush=True)
            continue

        # Final non-partial events
        for part in event.content.parts:
            if part.function_call:
                print(f"\n  [tool-call] {part.function_call.name}({part.function_call.args})")
            elif part.function_response:
                tool_done = True
                print(f"\n  [tool-result] {part.function_response.name} -> "
                      f"keys={list((part.function_response.response or {}).keys())}")
            elif part.text:
                all_llm_text_parts.append(part.text)
                if tool_done:
                    post_tool_text_parts.append(part.text)
                print(f"\n  Assistant: {part.text}")

    print()  # newline after streaming
    return (
        live_progress,
        all_events,
        "".join(post_tool_text_parts).strip(),
        "".join(all_llm_text_parts).strip(),
    )


def _verify_turn1(
    live_progress: list[dict],
    all_events: list,
    post_tool_text: str,
    persisted_events: list,
) -> bool:
    """Run all assertions for turn 1 and report PASS/FAIL per check.

    ``persisted_events`` is the list of events as stored in session – the
    caller MUST refetch the session via ``session_service.get_session``
    after the turn finishes (the InMemorySessionService returns a deep
    copy on creation, so any reference we held from ``create_session``
    is frozen at zero events).
    """
    _section("Turn-1 verification")

    ok = True

    has_progress = len(live_progress) > 0
    _bullet(has_progress, f"Streaming pipe fired: {len(live_progress)} live progress event(s) received.")
    ok &= has_progress

    no_followup = post_tool_text == ""
    _bullet(
        no_followup,
        "skip_summarization stopped the LLM from re-summarising "
        f"(post-tool LLM text length = {len(post_tool_text)} chars).",
    )
    if not no_followup:
        print(f"      Unexpected follow-up text: {post_tool_text!r}")
    ok &= no_followup

    persisted_partials = [e for e in persisted_events if e.partial]
    function_response_events = [
        e for e in persisted_events if e.content and any(p.function_response for p in e.content.parts)
    ]

    no_partials_persisted = len(persisted_partials) == 0
    _bullet(
        no_partials_persisted,
        f"No partial progress events leaked into session storage "
        f"({len(persisted_partials)} found, expected 0).",
    )
    ok &= no_partials_persisted

    has_function_response = len(function_response_events) == 1
    _bullet(
        has_function_response,
        f"Exactly one final function_response event persisted "
        f"({len(function_response_events)} found).",
    )
    ok &= has_function_response

    if function_response_events:
        fr_part = next(p for p in function_response_events[0].content.parts if p.function_response)
        response = fr_part.function_response.response or {}
        has_titles = isinstance(response.get("titles"), list) and len(response["titles"]) > 0
        _bullet(
            has_titles,
            f"Final tool response contains the accumulated titles list "
            f"(len={len(response.get('titles', []))}).",
        )
        ok &= has_titles
        print(f"      Persisted response payload: {response}")

        fe = function_response_events[0]
        has_skip = bool(fe.actions and fe.actions.skip_summarization)
        _bullet(
            has_skip,
            "Final tool event carries actions.skip_summarization=True.",
        )
        ok &= has_skip

    print()
    print(f"  Total events captured from runner: {len(all_events)}")
    print(f"  Total events persisted in session: {len(persisted_events)}")
    return ok


def _verify_turn2(all_llm_text: str, persisted_events: list) -> bool:
    """Turn 2 asks the LLM to summarise; verify it had context to do so."""
    _section("Turn-2 verification")
    ok = True

    has_assistant_reply = len(all_llm_text) > 0
    _bullet(
        has_assistant_reply,
        f"LLM produced an answer in turn 2 ({len(all_llm_text)} chars).",
    )
    ok &= has_assistant_reply

    references_data = "page" in all_llm_text.lower() or "example.com" in all_llm_text.lower()
    _bullet(
        references_data,
        "Turn-2 answer references turn-1 tool data "
        "(mentions 'page' or 'example.com').",
    )
    ok &= references_data

    # Bonus visibility: count how many events the session now holds (user,
    # tool_call, tool_response from turn 1 plus user + assistant from turn 2).
    print(f"  Total events now persisted in session: {len(persisted_events)}")
    return ok


async def _refetch_events(session_service: InMemorySessionService, session_id: str) -> list:
    """Pull the *live* event list from the session service.

    ``InMemorySessionService.create_session`` returns a ``copy.deepcopy``
    of the stored session, so the Session object the caller holds at
    creation time is frozen at zero events. Subsequent appends happen on
    a different object kept inside the service. We refetch through the
    public ``get_session`` API (which also returns a fresh deepcopy that
    DOES reflect the latest events).
    """
    s = await session_service.get_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id,
    )
    return list(s.events) if s else []


async def main() -> None:
    from agent.agent import root_agent

    session_service = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=root_agent, session_service=session_service)

    session_id = str(uuid.uuid4())
    await session_service.create_session(app_name=APP_NAME, user_id=USER_ID, session_id=session_id)

    # ----- Turn 1: streaming tool runs -----
    live, evs, post_tool_text, _all_llm_text1 = await _run_turn(
        runner,
        session_id,
        "Please crawl https://example.com and fetch the first 5 pages.",
        "Turn 1 -- streaming crawl",
    )
    persisted_after_turn1 = await _refetch_events(session_service, session_id)
    turn1_ok = _verify_turn1(live, evs, post_tool_text, persisted_after_turn1)

    # ----- Turn 2: ask the LLM to summarise; it must use turn-1 data -----
    _live2, _evs2, _post_tool_text2, all_llm_text2 = await _run_turn(
        runner,
        session_id,
        "Based ONLY on the previous crawl results, list every page title you fetched.",
        "Turn 2 -- LLM reads persisted tool data",
    )
    persisted_after_turn2 = await _refetch_events(session_service, session_id)
    turn2_ok = _verify_turn2(all_llm_text2, persisted_after_turn2)

    _section("Result")
    print(f"  Turn 1: {'PASS' if turn1_ok else 'FAIL'}")
    print(f"  Turn 2: {'PASS' if turn2_ok else 'FAIL'}")
    if not (turn1_ok and turn2_ok):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
