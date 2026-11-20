# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Fork-based multi-worker steering demo.

The master creates the shared-memory segment and forks two workers:

- worker A runs the agent, asks for Beijing, and reports when the weather
  tool call finishes;
- worker B waits for that moment and then sends a steering message correcting
  the request to Shanghai.

Run with:

    python3 run_agent_with_multi_worker.py
"""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import sys
import time
import traceback
import uuid

from dotenv import load_dotenv

from trpc_agent_sdk.cancel import SharedMemoryConfig
from trpc_agent_sdk.cancel import SteeringStatus
from trpc_agent_sdk.cancel import destroy_shared_mem
from trpc_agent_sdk.cancel import init_shared_mem
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()

APP_NAME = "weather_agent_steering_multi_worker_demo"
USER_ID = "demo_user"
QUERY = "What's the current weather in Beijing?"
STEERING_TEXT = "Sorry, I said it wrong. Please check Shanghai's weather instead."


def _create_runner(session_service: InMemorySessionService) -> Runner:
    from agent.agent import root_agent
    return Runner(app_name=APP_NAME, agent=root_agent, session_service=session_service)


def _elapsed(start: float) -> str:
    return f"{time.monotonic() - start:.2f}s"


def _run_worker(
    session_id: str,
    start: float,
    first_tool_done: multiprocessing.synchronize.Event,
    steering_sent: multiprocessing.synchronize.Event,
    steering_applied: multiprocessing.synchronize.Event,
    failed: multiprocessing.synchronize.Event,
) -> None:
    """Run the agent and report the first completed tool call."""

    async def main() -> None:
        session_service = InMemorySessionService()
        runner = _create_runner(session_service)
        cities = []
        seen_calls = set()
        seen_responses = set()
        first_response_seen = False
        try:
            print(f"[worker A pid={os.getpid()} {_elapsed(start)}] start run: session={session_id}", flush=True)
            async for event in runner.run_async(
                    user_id=USER_ID,
                    session_id=session_id,
                    new_message=Content(parts=[Part.from_text(text=QUERY)]),
            ):
                if event.partial and event.content:
                    for part in event.content.parts:
                        if part.text and not part.thought:
                            print(part.text, end="", flush=True)
                for call in event.get_function_calls():
                    call_key = (call.id, call.name, str(call.args))
                    if call_key in seen_calls:
                        continue
                    seen_calls.add(call_key)
                    city = (call.args or {}).get("city")
                    print(
                        f"\n[worker A pid={os.getpid()} {_elapsed(start)}] "
                        f"function call: {call.name}({call.args})",
                        flush=True)
                    if city:
                        cities.append(city)
                    if city == "Shanghai":
                        steering_applied.set()
                for response in event.get_function_responses():
                    response_key = (response.id, response.name)
                    if response_key in seen_responses:
                        continue
                    seen_responses.add(response_key)
                    print(
                        f"\n[worker A pid={os.getpid()} {_elapsed(start)}] "
                        f"function response: {response.name}: {response.response}",
                        flush=True)
                    if first_response_seen:
                        continue
                    first_response_seen = True
                    first_tool_done.set()
                    # Hold the event stream here until worker B has written the
                    # steering message; otherwise the agent may build its next
                    # LLM request before the message lands in shared memory.
                    if not steering_sent.wait(timeout=30):
                        print(f"[worker A pid={os.getpid()}] steering was not sent in time", flush=True)
                        failed.set()
                        return
            print()
            print(f"[worker A pid={os.getpid()} {_elapsed(start)}] function call cities: {cities}", flush=True)
            session = await session_service.get_session(
                app_name=APP_NAME,
                user_id=USER_ID,
                session_id=session_id,
            )
            print(f"[worker A pid={os.getpid()}] session events:", flush=True)
            for index, event in enumerate(session.events if session else []):
                calls = [f"{call.name}({call.args})" for call in event.get_function_calls()]
                responses = [f"{response.name}->{response.response}" for response in event.get_function_responses()]
                print(
                    f"  [{index}] author={event.author} visible={event.visible} "
                    f"text={event.get_text()!r} calls={calls} responses={responses}",
                    flush=True,
                )
            if "Shanghai" not in cities:
                failed.set()
            elif cities.index("Shanghai") == 0:
                failed.set()
        finally:
            await runner.close()

    try:
        asyncio.run(main())
    except Exception:  # pylint: disable=broad-except
        failed.set()
        traceback.print_exc()
        raise


def _steer_worker(
    session_id: str,
    start: float,
    first_tool_done: multiprocessing.synchronize.Event,
    steering_sent: multiprocessing.synchronize.Event,
    steering_accepted: multiprocessing.synchronize.Event,
    failed: multiprocessing.synchronize.Event,
) -> None:
    """Wait for the first tool call to finish, then send the steering message."""

    if not first_tool_done.wait(timeout=60):
        print(f"[worker B pid={os.getpid()}] first tool call did not finish in time", flush=True)
        failed.set()
        steering_sent.set()
        return

    async def main() -> None:
        runner = _create_runner(InMemorySessionService())
        try:
            print(f"[worker B pid={os.getpid()} {_elapsed(start)}] send steering: session={session_id}", flush=True)
            status = await runner.send_steering(
                user_id=USER_ID,
                session_id=session_id,
                content=Content(role="user", parts=[Part.from_text(text=STEERING_TEXT)]),
            )
            print(f"[worker B pid={os.getpid()} {_elapsed(start)}] steering status: {status}", flush=True)
            if status is SteeringStatus.ACCEPTED:
                steering_accepted.set()
            else:
                failed.set()
        finally:
            # Release worker A even on failure so it does not wait for the timeout.
            steering_sent.set()
            await runner.close()

    try:
        asyncio.run(main())
    except Exception:  # pylint: disable=broad-except
        failed.set()
        traceback.print_exc()
        raise


def _join(process: multiprocessing.Process, timeout: float) -> None:
    process.join(timeout)
    if process.is_alive():
        process.terminate()
        process.join()
        raise RuntimeError(f"worker {process.name} did not exit in {timeout}s")
    if process.exitcode != 0:
        raise RuntimeError(f"worker {process.name} exited with code {process.exitcode}")


def main() -> None:
    ctx = multiprocessing.get_context("fork")
    session_id = f"steering-fork-{uuid.uuid4().hex[:8]}"
    start = time.monotonic()
    first_tool_done = ctx.Event()
    steering_sent = ctx.Event()
    steering_accepted = ctx.Event()
    steering_applied = ctx.Event()
    failed = ctx.Event()

    init_shared_mem(SharedMemoryConfig())
    processes = [
        ctx.Process(
            target=_run_worker,
            name="run-worker",
            args=(session_id, start, first_tool_done, steering_sent, steering_applied, failed),
        ),
        ctx.Process(
            target=_steer_worker,
            name="steer-worker",
            args=(session_id, start, first_tool_done, steering_sent, steering_accepted, failed),
        ),
    ]
    try:
        for process in processes:
            process.start()
        print(f"[master pid={os.getpid()}] forked workers: {[p.pid for p in processes]}")

        _join(processes[0], timeout=180)
        _join(processes[1], timeout=60)

        if failed.is_set():
            raise RuntimeError("a worker reported a failure")
        if not steering_accepted.is_set():
            raise RuntimeError("steering was not accepted")
        if not steering_applied.is_set():
            raise RuntimeError("steering was not applied: no Shanghai tool call")
        print(f"[master pid={os.getpid()}] multi-worker steering verified")
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join()
        destroy_shared_mem()


if __name__ == "__main__":
    main()
    sys.exit(0)
