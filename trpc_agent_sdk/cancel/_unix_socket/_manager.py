# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Worker-side manager for the Unix socket control plane."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Deque
from typing import Dict
from typing import List
from typing import Optional

from trpc_agent_sdk.log import logger
from trpc_agent_sdk.types import Content

from .._base import CancelManager
from .._types import SessionKey
from .._types import Status
from ._config import UnixSocketConfig
from ._protocol import _key_to_data
from ._protocol import _read_frame
from ._protocol import _status_from_data
from ._protocol import _write_frame


@dataclass
class _ClientRun:
    """Worker-local state for one active run."""

    key: SessionKey
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    event: asyncio.Event = field(default_factory=asyncio.Event)
    steering: Deque[Content] = field(default_factory=deque)
    poller: Optional[asyncio.Task] = None


class _UnixSocketManager(CancelManager):
    """Manages one Unix socket connection per active run.

    The manager lives in the worker process. Each run owns its own connection,
    so control calls for different runs never share a stream.
    """

    def __init__(self, config: UnixSocketConfig) -> None:
        self._config = config
        self._runs: Dict[SessionKey, _ClientRun] = {}

    async def _connect(self):
        try:
            return await asyncio.wait_for(
                asyncio.open_unix_connection(self._config.socket_path),
                timeout=self._config.timeout,
            )
        except Exception as ex:  # pylint: disable=broad-except
            raise RuntimeError(f"Failed to connect to Unix socket {self._config.socket_path}: {ex}") from ex

    async def _read_response(self, reader: asyncio.StreamReader) -> Dict[str, Any]:
        raw = await _read_frame(reader, timeout=self._config.timeout)
        if raw is None:
            raise ConnectionError("Unix socket server closed the connection")
        return json.loads(raw.decode("utf-8"))

    async def _rpc(self, run: _ClientRun, request: Dict[str, Any]) -> Dict[str, Any]:
        await _write_frame(
            run.writer,
            json.dumps(request, separators=(",", ":")).encode("utf-8"),
            self._config.timeout,
        )
        return await self._read_response(run.reader)

    async def _send_once(self, request: Dict[str, Any]) -> Dict[str, Any]:
        reader, writer = await self._connect()
        try:
            await _write_frame(
                writer,
                json.dumps(request, separators=(",", ":")).encode("utf-8"),
                self._config.timeout,
            )
            return await self._read_response(reader)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    def _cancel_poller(self, run: _ClientRun) -> None:
        poller = run.poller
        run.poller = None
        if poller is None or poller.done():
            return
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        if poller is not current:
            poller.cancel()

    def _close_run(self, run: _ClientRun) -> None:
        self._cancel_poller(run)
        run.writer.close()

    def _fail_run(self, run: _ClientRun) -> None:
        run.event.set()
        self._close_run(run)

    async def register_run(self, key: SessionKey) -> None:
        """Register one active run and start its sync poller."""
        if key in self._runs:
            raise RuntimeError("run is already registered")

        reader, writer = await self._connect()
        run = _ClientRun(key=key, reader=reader, writer=writer)
        try:
            response = await self._rpc(run, {"op": "register", "key": _key_to_data(key)})
            if not response.get("ok"):
                raise RuntimeError(str(response.get("error", "register failed")))
        except Exception:
            writer.close()
            raise
        self._runs[key] = run
        loop = asyncio.get_running_loop()
        run.poller = loop.create_task(
            self._poll(run),
            name="trpc-agent-unix-socket-sync",
        )

    async def unregister_run(self, key: SessionKey) -> None:
        """Release one run. Safe to call more than once."""
        run = self._runs.pop(key, None)
        if run is None:
            return
        try:
            await self._send_once({"op": "unregister", "key": _key_to_data(key)})
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("Failed to send unregister for session %s: %s", key.session_id, ex)
        finally:
            self._close_run(run)

    async def cancel_run(self, key: SessionKey, timeout: float = 1.0) -> Status:
        """Request cancellation for ``key`` and wait for the run to stop.

        ``timeout`` bounds how long we poll the master for the run to be
        released. On timeout, ``Status(ok=False)`` tells the caller the run is
        still active, so ``Runner.cancel_run_async`` can log the failure.
        """
        run = self._runs.get(key)
        if run is not None:
            run.event.set()
        try:
            response = await self._send_once({"op": "send_cancel", "key": _key_to_data(key)})
            if not response.get("ok"):
                return Status(ok=False, error_msg=str(response.get("error", "cancel request failed")))
            status = _status_from_data(response["result"])
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("Failed to send cancel for session %s: %s", key.session_id, ex)
            return Status(ok=False, error_msg=f"failed to send cancel: {ex}")
        if not status.ok:
            return status
        return await self._wait_until_stopped(key, timeout)

    async def _wait_until_stopped(self, key: SessionKey, timeout: float) -> Status:
        """Poll the master until ``key`` has no active run or ``timeout`` expires.

        The caller guarantees no new run is registered on ``key`` while
        ``cancel_run`` is still waiting, so checking the SessionKey is enough.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            try:
                response = await self._send_once({
                    "op": "is_running",
                    "key": _key_to_data(key),
                })
            except Exception as ex:  # pylint: disable=broad-except
                return Status(ok=False, error_msg=f"failed to query run state: {ex}")
            if not response.get("ok"):
                return Status(ok=False, error_msg=str(response.get("error", "is_running failed")))
            if not response.get("running", False):
                return Status(ok=True)
            remaining = deadline - loop.time()
            if remaining <= 0:
                return Status(
                    ok=False,
                    error_msg=(f"Cancel timed out after {timeout}s: "
                               f"run still active for session {key.session_id}"),
                )
            await asyncio.sleep(min(self._config.poll_interval, remaining))

    async def send_steering(self, key: SessionKey, content: Content) -> Status:
        """Enqueue one steering message for ``key``."""
        if content is None or not content.parts:
            return Status(ok=False, error_msg="steering content is empty")

        run = self._runs.get(key)
        if run is not None:
            if run.event.is_set():
                return Status(ok=False, error_msg="no active run")
            run.steering.append(content)
            return Status(ok=True)

        payload = content.model_dump_json(exclude_none=True)
        try:
            response = await self._send_once({
                "op": "send_steering",
                "key": _key_to_data(key),
                "payload": payload,
            })
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("Failed to send steering for session %s: %s", key.session_id, ex)
            return Status(ok=False, error_msg=f"failed to send steering: {ex}")
        if not response.get("ok"):
            return Status(ok=False, error_msg=str(response.get("error", "steering request failed")))
        try:
            return _status_from_data(response["result"])
        except (KeyError, TypeError, ValueError):
            return Status(ok=False, error_msg="invalid steering response")

    async def drain_steering(self, key: SessionKey) -> List[Content]:
        """Take all pending steering messages for ``key`` from the local queue."""
        run = self._runs.get(key)
        if run is None or run.event.is_set():
            return []

        contents = list(run.steering)
        run.steering.clear()
        return contents

    async def check_cancel(self, key: SessionKey) -> bool:
        """Return whether ``key`` has been cancelled."""
        run = self._runs.get(key)
        return run is not None and run.event.is_set()

    async def get_cancel_event(self, key: SessionKey) -> Optional[asyncio.Event]:
        """Return the local cancellation event for ``key``."""
        run = self._runs.get(key)
        if run is None:
            return None
        return run.event

    async def _poll(self, run: _ClientRun) -> None:
        """Sync cancel state and steering from the master at ``poll_interval``."""
        try:
            while not run.event.is_set():
                await asyncio.sleep(self._config.poll_interval)
                if run.event.is_set():
                    return
                try:
                    response = await self._rpc(run, {"op": "sync", "key": _key_to_data(run.key)})
                except Exception as ex:  # pylint: disable=broad-except
                    logger.warning("Cancel sync failed for session %s: %s", run.key.session_id, ex)
                    self._fail_run(run)
                    return
                if not response.get("ok"):
                    self._fail_run(run)
                    return
                if response.get("cancelled"):
                    run.event.set()
                    return
                for payload in response.get("steering", []):
                    try:
                        run.steering.append(Content.model_validate_json(payload))
                    except Exception as ex:  # pylint: disable=broad-except
                        logger.warning("Failed to decode steering payload: %s", ex)
        except asyncio.CancelledError:
            raise
        finally:
            if run.poller is asyncio.current_task():
                run.poller = None


__all__ = [
    "_UnixSocketManager",
]
