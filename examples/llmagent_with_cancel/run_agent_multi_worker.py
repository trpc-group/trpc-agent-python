# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Fork-based multi-worker cancellation demo.

The master starts the Unix socket server, forks two worker processes,
and waits for them:

- worker A owns the run and consumes the event stream;
- worker B cancels the same session through the Unix socket control plane.

Run with:

    python3 run_agent_multi_worker.py
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

from trpc_agent_sdk.cancel import UnixSocketConfig
from trpc_agent_sdk.cancel import init_unix_socket
from trpc_agent_sdk.cancel import release_unix_socket
from trpc_agent_sdk.events import AgentCancelledEvent
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()

APP_NAME = "weather_agent_cancel_multi_worker_demo"
USER_ID = "demo_user"
QUERY = "Introduce yourself in detail, what can you do."
CANCEL_DELAY_SECONDS = 0.2


def _create_runner(session_service: InMemorySessionService) -> Runner:
    from agent.agent import root_agent
    return Runner(app_name=APP_NAME, agent=root_agent, session_service=session_service)


def _run_worker(
    session_id: str,
    run_started: multiprocessing.synchronize.Event,
    cancelled: multiprocessing.synchronize.Event,
    failed: multiprocessing.synchronize.Event,
) -> None:
    """Run the agent and wait for the cancel signal from worker B."""

    async def main() -> None:
        session_service = InMemorySessionService()
        runner = _create_runner(session_service)
        try:
            print(f"[worker A pid={os.getpid()}] start run: session={session_id}")
            async for event in runner.run_async(
                    user_id=USER_ID,
                    session_id=session_id,
                    new_message=Content(parts=[Part.from_text(text=QUERY)]),
            ):
                run_started.set()
                if isinstance(event, AgentCancelledEvent):
                    cancelled.set()
                    print(f"\n[worker A pid={os.getpid()}] AgentCancelledEvent: {event.error_message}")
                    # Keep consuming until the SDK generator ends so its spans close
                    # in this task's context instead of another context.
                    continue
                if event.partial and event.content:
                    for part in event.content.parts:
                        if part.text:
                            print(part.text, end="", flush=True)

            session = await session_service.get_session(
                app_name=APP_NAME,
                user_id=USER_ID,
                session_id=session_id,
            )
            last_text = session.events[-1].get_text() if session and session.events else ""
            print(f"\n[worker A pid={os.getpid()}] last session event: {last_text!r}")
        finally:
            await runner.close()

    try:
        asyncio.run(main())
    except Exception:  # pylint: disable=broad-except
        failed.set()
        traceback.print_exc()
        raise


def _cancel_worker(
    session_id: str,
    run_started: multiprocessing.synchronize.Event,
    cancel_accepted: multiprocessing.synchronize.Event,
    failed: multiprocessing.synchronize.Event,
) -> None:
    """Wait for the run to start, then cancel it from this process."""

    if not run_started.wait(timeout=60):
        print(f"[worker B pid={os.getpid()}] run did not start in time")
        failed.set()
        return

    time.sleep(CANCEL_DELAY_SECONDS)

    async def main() -> None:
        runner = _create_runner(InMemorySessionService())
        try:
            print(f"[worker B pid={os.getpid()}] request cancel: session={session_id}")
            accepted = await runner.cancel_run_async(user_id=USER_ID, session_id=session_id)
            print(f"[worker B pid={os.getpid()}] cancel accepted: {accepted}")
            if accepted:
                cancel_accepted.set()
            else:
                failed.set()
        finally:
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
    session_id = f"cancel-fork-{uuid.uuid4().hex[:8]}"
    run_started = ctx.Event()
    cancelled = ctx.Event()
    cancel_accepted = ctx.Event()
    failed = ctx.Event()

    init_unix_socket(UnixSocketConfig())
    processes = [
        ctx.Process(
            target=_run_worker,
            name="run-worker",
            args=(session_id, run_started, cancelled, failed),
        ),
        ctx.Process(
            target=_cancel_worker,
            name="cancel-worker",
            args=(session_id, run_started, cancel_accepted, failed),
        ),
    ]
    try:
        for process in processes:
            process.start()
        print(f"[master pid={os.getpid()}] forked workers: {[p.pid for p in processes]}")

        _join(processes[0], timeout=120)
        _join(processes[1], timeout=120)

        if failed.is_set():
            raise RuntimeError("a worker reported a failure")
        if not cancel_accepted.is_set():
            raise RuntimeError("cancel was not accepted by the run owner")
        if not cancelled.is_set():
            raise RuntimeError("run worker did not receive AgentCancelledEvent")
        print(f"[master pid={os.getpid()}] multi-worker cancellation verified")
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join()
        release_unix_socket()


if __name__ == "__main__":
    main()
    sys.exit(0)
