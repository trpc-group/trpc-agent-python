# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unix socket server for the multi-worker control plane."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
from collections import deque
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Deque
from typing import Dict
from typing import Optional
from typing import Set

from trpc_agent_sdk.log import logger

from .._types import SessionKey
from .._types import Status
from ._config import UnixSocketConfig
from ._protocol import _key_from_data
from ._protocol import _read_frame
from ._protocol import _status_to_data
from ._protocol import _write_frame


@dataclass
class _ServerRun:
    """Server-side state for one active run."""

    cancelled: bool
    owner: asyncio.StreamWriter
    steering: Deque[str] = field(default_factory=deque)


class _UnixSocketServer:
    """Run table exposed over a Unix domain socket.

    The server runs its own asyncio event loop in a background thread so the
    master process can stay synchronous. Each run owns one connection; requests
    on a connection are handled sequentially.
    """

    def __init__(self, config: UnixSocketConfig) -> None:
        self._config = config
        self._owner_pid = os.getpid()
        self._socket_path = config.socket_path
        self._runs: Dict[SessionKey, _ServerRun] = {}
        self._connections: Set[asyncio.StreamWriter] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server: Optional[asyncio.AbstractServer] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._error: Optional[BaseException] = None

    @property
    def owner_pid(self) -> int:
        """Return the PID that created the server."""
        return self._owner_pid

    def start(self) -> None:
        """Start the server thread and wait until it is listening."""
        if self._thread is not None:
            return

        self._prepare_socket_path()
        self._ready.clear()
        self._error = None
        self._thread = threading.Thread(
            target=self._run,
            name="trpc-agent-unix-socket-server",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=self._config.timeout):
            self._thread = None
            raise RuntimeError("Timed out starting Unix socket server")
        if self._error is not None:
            error = self._error
            self._thread.join(timeout=self._config.timeout)
            self._thread = None
            raise RuntimeError(f"Failed to start Unix socket server: {error}") from error

    def stop(self) -> None:
        """Stop the server, close connections, and unlink the socket path."""
        thread = self._thread
        if thread is None:
            self._unlink_socket_path()
            return

        loop = self._loop
        if loop is not None and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self._shutdown(), loop)
            try:
                future.result(timeout=self._config.timeout)
            except Exception:  # pylint: disable=broad-except
                logger.warning("Failed to shut down Unix socket server cleanly", exc_info=True)
            loop.call_soon_threadsafe(loop.stop)

        thread.join(timeout=self._config.timeout)
        self._thread = None
        self._loop = None
        self._server = None
        self._unlink_socket_path()

    def _prepare_socket_path(self) -> None:
        parent = os.path.dirname(self._socket_path)
        if parent:
            os.makedirs(parent, mode=0o700, exist_ok=True)

        if not os.path.exists(self._socket_path):
            return

        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(1.0)
        try:
            probe.connect(self._socket_path)
        except OSError:
            try:
                os.unlink(self._socket_path)
            except FileNotFoundError:
                pass
        else:
            raise RuntimeError(f"Unix socket {self._socket_path} is already in use")
        finally:
            probe.close()

    def _unlink_socket_path(self) -> None:
        try:
            os.unlink(self._socket_path)
        except FileNotFoundError:
            pass
        except OSError:  # pylint: disable=broad-except
            logger.warning("Failed to unlink Unix socket %s", self._socket_path, exc_info=True)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            self._server = loop.run_until_complete(
                asyncio.start_unix_server(self._handle_connection, path=self._socket_path))
        except Exception as ex:  # pylint: disable=broad-except
            self._error = ex
            self._ready.set()
            loop.close()
            return

        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()

    async def _shutdown(self) -> None:
        server = self._server
        if server is not None:
            server.close()
        for writer in list(self._connections):
            writer.close()
        self._connections.clear()
        self._runs.clear()
        if server is not None:
            # ``Server.wait_closed()`` waits for active connections to finish on
            # Python 3.12+, so close them before waiting.
            await server.wait_closed()

    async def _handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._connections.add(writer)
        try:
            while True:
                raw = await _read_frame(reader, timeout=None)
                if raw is None:
                    break
                try:
                    request = json.loads(raw.decode("utf-8"))
                    response = self._dispatch(writer, request)
                except Exception as ex:  # pylint: disable=broad-except
                    response = {"ok": False, "error": str(ex)}
                await _write_frame(
                    writer,
                    json.dumps(response, separators=(",", ":")).encode("utf-8"),
                    self._config.timeout,
                )
        except (asyncio.TimeoutError, ConnectionError, OSError):
            pass
        finally:
            self._connections.discard(writer)
            self._drop_connection(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    def _drop_connection(self, writer: asyncio.StreamWriter) -> None:
        for key, run in list(self._runs.items()):
            if run.owner is writer:
                self._runs.pop(key, None)

    def _dispatch(self, writer: asyncio.StreamWriter, request: Dict[str, Any]) -> Dict[str, Any]:
        op = request.get("op")
        if op == "register":
            return self._register(writer, request)
        if op == "unregister":
            return self._unregister(request)
        if op == "sync":
            return self._sync(writer, request)
        if op == "send_cancel":
            return self._send_cancel(request)
        if op == "send_steering":
            return self._send_steering(request)
        if op == "is_running":
            return self._is_running(request)
        return {"ok": False, "error": f"unknown op: {op!r}"}

    def _register(self, writer: asyncio.StreamWriter, request: Dict[str, Any]) -> Dict[str, Any]:
        key = _key_from_data(request["key"])
        if key in self._runs:
            return {"ok": False, "error": "duplicate"}
        self._runs[key] = _ServerRun(cancelled=False, owner=writer)
        return {"ok": True}

    def _is_running(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Report whether ``key`` still has an active run."""
        key = _key_from_data(request["key"])
        return {"ok": True, "running": key in self._runs}

    def _unregister(self, request: Dict[str, Any]) -> Dict[str, Any]:
        key = _key_from_data(request["key"])
        self._runs.pop(key, None)
        return {"ok": True}

    def _sync(self, writer: asyncio.StreamWriter, request: Dict[str, Any]) -> Dict[str, Any]:
        key = _key_from_data(request["key"])
        run = self._runs.get(key)
        if run is None or run.owner is not writer or run.cancelled:
            return {"ok": True, "cancelled": True, "steering": []}
        steering = list(run.steering)
        run.steering.clear()
        return {"ok": True, "cancelled": False, "steering": steering}

    def _send_cancel(self, request: Dict[str, Any]) -> Dict[str, Any]:
        key = _key_from_data(request["key"])
        run = self._runs.get(key)
        if run is None:
            status = Status(ok=False, error_msg="no active run")
            return {"ok": True, "result": _status_to_data(status)}
        run.cancelled = True
        return {"ok": True, "result": _status_to_data(Status(ok=True))}

    def _send_steering(self, request: Dict[str, Any]) -> Dict[str, Any]:
        key = _key_from_data(request["key"])
        run = self._runs.get(key)
        if run is None or run.cancelled:
            status = Status(ok=False, error_msg="no active run")
            return {"ok": True, "result": _status_to_data(status)}
        run.steering.append(str(request["payload"]))
        return {"ok": True, "result": _status_to_data(Status(ok=True))}


__all__ = [
    "_UnixSocketServer",
]
