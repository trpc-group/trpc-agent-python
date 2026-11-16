# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Distributed cancellation support for TRPC Agent framework.

The in-process cancellation manager only sees runs registered in the current
process. Distributed deployments store a small flag in the shared session state
so that any node can request cancellation, while the node that owns the run
polls the flag and converts it into the local cancellation event.

Protocol:

* ``__trpc_agent_cancel`` absent: no active run.
* ``__trpc_agent_cancel is False``: a run is active and has not been cancelled.
* ``__trpc_agent_cancel is True``: cancellation has been requested.

The run side writes ``False`` when it starts and clears the key when it ends. The
cancel side only flips an existing key to ``True``.
"""

from __future__ import annotations

import asyncio
import random
from typing import Optional

from trpc_agent_sdk.abc import SessionABC
from trpc_agent_sdk.abc import SessionServiceABC
from trpc_agent_sdk.cancel._cancel import SessionKey
from trpc_agent_sdk.cancel._cancel import mark_cancelled
from trpc_agent_sdk.log import logger

_TRPC_AGENT_CANCEL_KEY = "__trpc_agent_cancel"

# Upper bound for the poll backoff when the shared session store is failing.
_MAX_POLL_INTERVAL = 10.0


class _DistributedCancelWatcher:
    """Poll the shared cancel flag and wake the local run when it turns True.

    The watcher runs in its own asyncio task so checkpoints continue to read
    only in-process state and never take a network round trip.
    """

    def __init__(
        self,
        *,
        session_service: SessionServiceABC,
        session: SessionABC,
        session_key: SessionKey,
        poll_interval: float,
    ) -> None:
        self._session_service = session_service
        self._session = session
        self._session_key = session_key
        self._poll_interval = poll_interval
        self._task: Optional[asyncio.Task] = None
        self._stopped = False

    async def start(self) -> None:
        """Write the running flag and start the watchdog task."""
        await self._session_service.update_session_state(
            self._session,
            {_TRPC_AGENT_CANCEL_KEY: False},
        )
        self._task = asyncio.create_task(
            self._run(),
            name=f"distributed-cancel-watcher:{self._session_key.session_id}",
        )

    async def _run(self) -> None:
        delay = self._poll_interval
        while not self._stopped:
            # Jitter the first tick too: many runs starting together should not
            # all hit the shared store in the same instant.
            await asyncio.sleep(delay * random.uniform(0.9, 1.1))

            try:
                session = await self._session_service.get_session(
                    app_name=self._session_key.app_name,
                    user_id=self._session_key.user_id,
                    session_id=self._session_key.session_id,
                )
                delay = self._poll_interval
            except asyncio.CancelledError:
                raise
            except Exception:  # pylint: disable=broad-except
                logger.warning(
                    "Distributed cancel poll failed (session=%s)",
                    self._session_key.session_id,
                    exc_info=True,
                )
                delay = min(delay * 2, _MAX_POLL_INTERVAL)
                continue

            flag = session.state.get(_TRPC_AGENT_CANCEL_KEY) if session is not None else None
            if flag:
                await mark_cancelled(self._session_key)
                return

    async def aclose(self) -> None:
        """Stop the watchdog and clear the shared flag.

        Clearing is best effort: a temporarily unavailable session store must
        not block the local ``cleanup_run()`` that wakes local waiters.
        """
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

        try:
            await self._session_service.update_session_state(
                self._session,
                {_TRPC_AGENT_CANCEL_KEY: None},
            )
        except Exception:  # pylint: disable=broad-except
            logger.warning(
                "Failed to clear distributed cancel flag (session=%s)",
                self._session_key.session_id,
                exc_info=True,
            )
        finally:
            # Keep the in-memory session used by tracing free of the internal
            # key. The shared store keeps ``None`` as a logical delete.
            self._session.state.pop(_TRPC_AGENT_CANCEL_KEY, None)


async def start_distributed_cancel(
    *,
    session_service: SessionServiceABC,
    session: SessionABC,
    session_key: SessionKey,
    poll_interval: float,
) -> Optional[_DistributedCancelWatcher]:
    """Start distributed cancellation tracking for one run.

    Returns None when the shared flag cannot be written or the watchdog cannot
    start. Distributed cancellation is an enhancement; the run should continue
    with local-only cancellation instead of failing.
    """
    watcher = _DistributedCancelWatcher(
        session_service=session_service,
        session=session,
        session_key=session_key,
        poll_interval=poll_interval,
    )
    try:
        await watcher.start()
    except Exception:  # pylint: disable=broad-except
        logger.warning(
            "Failed to start distributed cancel watcher (session=%s)",
            session_key.session_id,
            exc_info=True,
        )
        await watcher.aclose()
        return None
    return watcher


async def request_distributed_cancel(
    *,
    session_service: SessionServiceABC,
    app_name: str,
    user_id: str,
    session_id: str,
) -> bool:
    """Publish a cancel request for the active run in shared session state.

    The request is ignored when no run is active. The function returns as soon
    as the request is written; it does not wait for the owner's watchdog.
    """
    try:
        session = await session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
    except Exception:  # pylint: disable=broad-except
        logger.warning(
            "Failed to read distributed cancel flag for user_id %s, session %s",
            user_id,
            session_id,
            exc_info=True,
        )
        return False

    flag = session.state.get(_TRPC_AGENT_CANCEL_KEY) if session is not None else None
    if flag is None:
        logger.info(
            "No active run found in shared state for user_id %s, session %s",
            user_id,
            session_id,
        )
        return False

    if flag:
        return True

    try:
        await session_service.update_session_state(
            session,
            {_TRPC_AGENT_CANCEL_KEY: True},
        )
    except Exception:  # pylint: disable=broad-except
        logger.warning(
            "Failed to write distributed cancel flag for user_id %s, session %s",
            user_id,
            session_id,
            exc_info=True,
        )
        return False
    return True
