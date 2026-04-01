# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
#
"""Claude runtime for TRPC Agent framework."""

import asyncio
import threading
from concurrent.futures import Future
from typing import Optional

from trpc_agent_sdk.log import logger


class AsyncRuntime:
    """Manages a dedicated event loop thread for async operations."""

    def __init__(self, thread_name: str = "AsyncRuntime"):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._thread_name = thread_name
        self._loop_ready = threading.Event()

    def start(self) -> None:

        def run_loop() -> None:
            logger.info("%s event loop thread started", self._thread_name)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            self._loop_ready.set()
            try:
                loop.run_forever()
            finally:
                _cancel_all_tasks(loop)
                loop.close()
                self._loop_ready.clear()
                self._loop = None
                logger.info("%s event loop thread stopped", self._thread_name)

        self._loop_ready.clear()
        thread = threading.Thread(target=run_loop, daemon=True, name=self._thread_name)
        self._loop_thread = thread
        thread.start()

        self._loop_ready.wait()

    def submit_coroutine(self, coro) -> Future:
        loop = self._ensure_loop()
        return asyncio.run_coroutine_threadsafe(coro, loop)

    def shutdown(self) -> None:
        loop = self._ensure_loop()
        loop.call_soon_threadsafe(loop.stop)

        thread = self._loop_thread
        if thread and thread.is_alive():
            thread.join(timeout=5.0)
            if thread.is_alive():
                logger.warning("%s thread did not terminate within timeout", self._thread_name)

        logger.info("%s thread terminated successfully", self._thread_name)

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            logger.error("%s event loop not initialized", self._thread_name)
            raise RuntimeError(f"{self._thread_name} event loop not initialized")
        return self._loop


def _cancel_all_tasks(loop: asyncio.AbstractEventLoop) -> None:
    pending = asyncio.all_tasks(loop)
    if not pending:
        return

    for task in pending:
        task.cancel()

    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
