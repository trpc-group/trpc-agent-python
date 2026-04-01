# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
#
"""Claude session manager for TRPC Agent framework."""

from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from typing import AsyncGenerator
from typing import Dict
from typing import Optional

from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk import ClaudeSDKClient

from trpc_agent_sdk.log import logger

from ._runtime import AsyncRuntime
from ._session_config import SessionConfig

# Sentinel object used to signal the end of a response stream
_RESPONSE_SENTINEL = object()


@dataclass
class _SessionRequest:
    """Represents a single query request to be processed by a session worker.

    Attributes:
        prompt: The user's input prompt/message
        session_id: Unique identifier for the Claude session
        response_queue: Thread-safe queue for receiving response messages from the worker
        cancelled: Thread-safe flag to signal cancellation to the worker
    """

    prompt: str
    session_id: str
    response_queue: "queue.Queue[object]"
    cancelled: threading.Event = None

    def __post_init__(self):
        """Initialize the cancellation event if not provided."""
        if self.cancelled is None:
            self.cancelled = threading.Event()

    def is_cancelled(self) -> bool:
        """Check if this request has been cancelled."""
        return self.cancelled.is_set()

    def cancel(self) -> None:
        """Signal cancellation for this request."""
        self.cancelled.set()


@dataclass
class _SessionState:
    """Tracks the state of a managed Claude session.

    Attributes:
        worker: The session worker managing the ClaudeSDKClient lifecycle
        options_signature: Fingerprint of the ClaudeAgentOptions for cache validation
        last_access: Timestamp of last access, used for TTL-based cleanup
    """

    worker: "_SessionWorker"
    options_signature: str
    last_access: float


def _fingerprint_options(options: ClaudeAgentOptions) -> str:
    """Generate a unique fingerprint string from ClaudeAgentOptions.

    This is used to detect when options have changed, requiring a new client.
    Tries multiple serialization methods for compatibility with different Pydantic versions.

    Args:
        options: The ClaudeAgentOptions to fingerprint

    Returns:
        A stable JSON string representation of the options
    """
    try:
        return options.model_dump_json()
    except AttributeError:
        try:
            dump = options.model_dump()
        except AttributeError:
            try:
                return options.json()
            except AttributeError:
                return repr(options)
        else:
            return json.dumps(dump, sort_keys=True, default=str)


class _SessionWorker:
    """Long-lived async task that owns a ClaudeSDKClient lifecycle.

    Each session worker runs in the AsyncRuntime's event loop and maintains a persistent
    ClaudeSDKClient connection. It processes requests sequentially from a queue, allowing
    multiple queries to reuse the same client and maintain conversation context.
    """

    def __init__(self, session_id: str, options: ClaudeAgentOptions):
        self._session_id = session_id
        self._options = options
        self._request_queue: asyncio.Queue[Optional[_SessionRequest]] = asyncio.Queue()
        self._closed = False
        self._run_future: Optional[Future[None]] = None

    def attach_future(self, future: Future[None]) -> None:
        """Attach the Future representing the worker's run() task.

        Args:
            future: Future returned from submitting run() to the event loop
        """
        self._run_future = future

    @property
    def run_future(self) -> Optional[Future[None]]:
        return self._run_future

    async def run(self) -> None:
        """Main worker loop that processes requests using a persistent ClaudeSDKClient.

        Creates and manages the ClaudeSDKClient lifecycle, processes queued requests,
        and handles cleanup on shutdown or error.
        """
        logger.debug("Session worker starting for '%s'", self._session_id)
        try:
            async with ClaudeSDKClient(options=self._options) as client:
                while True:
                    request = await self._request_queue.get()
                    if request is None:  # Shutdown signal
                        break
                    await self._handle_request(client, request)
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Session worker for '%s' encountered an error: %s", self._session_id, exc, exc_info=True)
            await self._drain_pending_requests(exc)
        finally:
            self._closed = True
            await self._drain_pending_requests()
            logger.debug("Session worker stopped for '%s'", self._session_id)

    async def _handle_request(self, client: ClaudeSDKClient, request: _SessionRequest) -> None:
        """Process a single request and stream responses back through the queue.

        Args:
            client: The persistent ClaudeSDKClient instance
            request: The request to process
        """
        try:
            await client.query(request.prompt, session_id=request.session_id)
            async for message in client.receive_response():
                # Check if request was cancelled before sending message
                if request.is_cancelled():
                    logger.debug("Request cancelled for session '%s', stopping message stream", self._session_id)
                    break
                request.response_queue.put(message)
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Error handling request for session '%s': %s", self._session_id, exc, exc_info=True)
            if not request.is_cancelled():
                request.response_queue.put(exc)
        finally:
            request.response_queue.put(_RESPONSE_SENTINEL)

    async def _drain_pending_requests(self, error: Optional[Exception] = None) -> None:
        """Drain any pending requests from the queue, optionally sending an error.

        Called during worker shutdown to ensure all pending requests are completed.

        Args:
            error: Optional exception to send to pending requests
        """
        while True:
            try:
                pending = self._request_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if pending is None:
                continue
            if error is not None:
                pending.response_queue.put(error)
            pending.response_queue.put(_RESPONSE_SENTINEL)

    async def enqueue(self, request: _SessionRequest) -> None:
        """Enqueue a request for processing by this worker.

        Args:
            request: The request to enqueue

        Raises:
            RuntimeError: If the worker has been closed
        """
        if self._closed:
            logger.error("Attempted to enqueue request to closed session worker")
            raise RuntimeError("Session worker is closed")
        await self._request_queue.put(request)

    async def close(self) -> None:
        """Signal the worker to shut down gracefully.

        Sends a None sentinel to the queue, which will cause the worker's run() loop to exit.
        """
        if self._closed:
            return
        await self._request_queue.put(None)


class SessionManager:
    """Manages Claude SDK sessions with dedicated async workers per session.

    The SessionManager provides:
    - Session lifecycle management: Creates, caches, and cleans up session workers
    - Client reuse: Each session maintains a persistent ClaudeSDKClient for context continuity
    - Options validation: Recreates clients when configuration changes
    - TTL-based cleanup: Automatically removes idle sessions after a configurable timeout

    This enables efficient multi-turn conversations where Claude maintains context across
    multiple queries within the same session.
    """

    def __init__(self, runtime: AsyncRuntime, config: Optional[SessionConfig] = None):
        """Initialize the session manager.

        Args:
            runtime: The AsyncRuntime providing the event loop for workers
            config: Configuration for session behavior (default: SessionConfig())
        """
        self._runtime = runtime
        self._config = config or SessionConfig()
        self._session_ttl = self._config.ttl
        self._sessions: Dict[str, _SessionState] = {}  # session_id -> _SessionState
        self._thread_lock = threading.Lock()  # Protects _sessions dict access
        self._is_closed = False

    async def stream_query(
        self,
        session_id: str,
        options: ClaudeAgentOptions,
        prompt: str,
    ) -> AsyncGenerator[object, None]:
        """Submit a query to a session and stream responses back.

        This is the main entry point for querying Claude. It:
        1. Ensures a worker exists for the session (creating if needed)
        2. Enqueues the request to the worker
        3. Streams response messages back as they arrive

        Args:
            session_id: Unique identifier for the session
            options: Configuration for the Claude client
            prompt: The user's message/prompt

        Yields:
            Response messages from Claude (AssistantMessage, StreamEvent, etc.)

        Raises:
            RuntimeError: If the SessionManager is closed
            Exception: Any exception raised by the Claude SDK
        """
        if self._is_closed:
            logger.error("Attempted to stream_query on closed SessionManager")
            raise RuntimeError("SessionManager is closed")

        await self._cleanup_expired_sessions()
        state = await self._get_or_create_state(session_id, options)

        now = time.time()
        with self._thread_lock:
            state.last_access = now

        response_queue: queue.Queue[object] = queue.Queue()
        request = _SessionRequest(prompt=prompt, session_id=session_id, response_queue=response_queue)

        enqueue_future = self._runtime.submit_coroutine(state.worker.enqueue(request))
        await asyncio.wrap_future(enqueue_future)

        try:
            while True:
                message = await asyncio.to_thread(response_queue.get)
                if message is _RESPONSE_SENTINEL:
                    break
                if isinstance(message, Exception):
                    raise message
                yield message
        finally:
            # Signal cancellation to the worker so it stops sending messages
            request.cancel()

            # Drain any remaining messages from the queue to prevent memory leak
            # The worker will stop sending after seeing the cancellation flag
            while True:
                try:
                    remaining = response_queue.get_nowait()
                    if remaining is _RESPONSE_SENTINEL:
                        break
                except queue.Empty:
                    # Wait briefly for the sentinel, then give up
                    try:
                        remaining = response_queue.get(timeout=0.1)
                        if remaining is _RESPONSE_SENTINEL:
                            break
                    except queue.Empty:
                        break

            with self._thread_lock:
                state.last_access = time.time()
            logger.debug("Cleaned up stream_query for session '%s'", session_id)

    async def _get_or_create_state(
        self,
        session_id: str,
        options: ClaudeAgentOptions,
    ) -> _SessionState:
        """Get existing session state or create a new one.

        If the session exists but options have changed, the old session is shut down
        and a new one is created with the updated options.

        Args:
            session_id: Unique identifier for the session
            options: Configuration for the Claude client

        Returns:
            The session state (existing or newly created)
        """
        options_signature = _fingerprint_options(options)
        to_close: Optional[_SessionState] = None

        with self._thread_lock:
            existing = self._sessions.get(session_id)
            if existing:
                if existing.options_signature == options_signature:
                    return existing
                # Options changed; replace the session.
                to_close = existing
                del self._sessions[session_id]

        if to_close is not None:
            await self._shutdown_state(session_id, to_close)

        new_state = self._create_state(session_id, options, options_signature)
        return new_state

    def _create_state(
        self,
        session_id: str,
        options: ClaudeAgentOptions,
        options_signature: str,
    ) -> _SessionState:
        """Create a new session state with a worker running in the AsyncRuntime.

        Args:
            session_id: Unique identifier for the session
            options: Configuration for the Claude client
            options_signature: Fingerprint of options for validation

        Returns:
            The newly created session state
        """
        worker = _SessionWorker(session_id=session_id, options=options)
        run_future = self._runtime.submit_coroutine(worker.run())
        worker.attach_future(run_future)

        state = _SessionState(worker=worker, options_signature=options_signature, last_access=time.time())
        with self._thread_lock:
            self._sessions[session_id] = state
        return state

    async def _shutdown_state(self, session_id: str, state: _SessionState) -> None:
        """Shut down a session worker and wait for cleanup.

        Args:
            session_id: Unique identifier for the session
            state: The session state to shut down
        """
        logger.info("Shutting down session '%s'", session_id)
        try:
            close_future = self._runtime.submit_coroutine(state.worker.close())
            await asyncio.wrap_future(close_future)
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Error closing session '%s': %s", session_id, exc, exc_info=True)

        run_future = state.worker.run_future
        if run_future is not None:
            try:
                await asyncio.wrap_future(run_future)
            except Exception as exc:  # pylint: disable=broad-except
                logger.error("Session loop for '%s' raised during shutdown: %s", session_id, exc, exc_info=True)

    async def _cleanup_expired_sessions(self) -> None:
        """Clean up sessions that have exceeded their TTL.

        Called before each query to remove idle sessions based on last_access time.
        """
        if self._session_ttl <= 0:
            return

        current_time = time.time()
        expired: list[tuple[str, _SessionState]] = []

        with self._thread_lock:
            for session_id, state in list(self._sessions.items()):
                if current_time - state.last_access > self._session_ttl:
                    expired.append((session_id, state))
                    del self._sessions[session_id]

        for session_id, state in expired:
            logger.info("Session '%s' expired after %ss of inactivity", session_id, self._session_ttl)
            await self._shutdown_state(session_id, state)

    def close(self) -> None:
        """Close the session manager and shut down all active sessions.

        This is a synchronous method that blocks until all workers are terminated.
        Should be called during application shutdown.
        """
        if self._is_closed:
            return

        with self._thread_lock:
            self._is_closed = True
            sessions = list(self._sessions.items())
            self._sessions.clear()

        for session_id, state in sessions:
            try:
                close_future = self._runtime.submit_coroutine(state.worker.close())
                close_future.result()
            except Exception as exc:  # pylint: disable=broad-except
                logger.error("Error closing session '%s': %s", session_id, exc, exc_info=True)

            run_future = state.worker.run_future
            if run_future is not None:
                try:
                    run_future.result()
                except Exception as exc:  # pylint: disable=broad-except
                    logger.error("Session loop for '%s' raised during manager close: %s",
                                 session_id,
                                 exc,
                                 exc_info=True)
