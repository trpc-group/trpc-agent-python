# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""WorkspaceInfo runtime for local code execution.

This module provides the WorkspaceRuntime class which allows local code execution.
It provides methods for staging directories and inputs into the workspace.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from typing import Optional
from typing_extensions import override

from .._program_session import BaseProgramSession
from .._program_session import PROGRAM_STATUS_EXITED
from .._program_session import PROGRAM_STATUS_RUNNING
from .._program_session import ProgramLog
from .._program_session import ProgramPoll
from .._program_session import ProgramState
from .._types import WorkspaceRunResult

_DEFAULT_INTERACTIVE_MAX_LINES = 20_000
if sys.platform != "win32":
    import fcntl


def _split_lines_with_partial(text: str) -> tuple[list[str], str]:
    normalized = text.replace("\r\n", "\n")
    parts = normalized.split("\n")
    if len(parts) == 1:
        return [], parts[0]
    return parts[:-1], parts[-1]


class LocalProgramSession(BaseProgramSession):
    """Local interactive subprocess session."""

    def __init__(
        self,
        process: asyncio.subprocess.Process,
        *,
        max_lines: int = _DEFAULT_INTERACTIVE_MAX_LINES,
        master_fd: Optional[int] = None,
    ) -> None:
        self._id = uuid.uuid4().hex
        self._process = process
        self._max_lines = max_lines
        self._master_fd = master_fd
        self._lock = asyncio.Lock()
        self._closed = False

        self._started_at = time.time()
        self._finished_at: Optional[float] = None
        self._exit_code: Optional[int] = None
        self._timed_out = False

        self._line_base = 0
        self._lines: list[str] = []
        self._partial = ""
        self._poll_cursor = 0

        self._stdout = ""
        self._stderr = ""
        if self._master_fd is not None:
            self._stdout_task = asyncio.create_task(self._read_pty(self._master_fd))
            self._stderr_task = asyncio.create_task(asyncio.sleep(0))
        else:
            self._stdout_task = asyncio.create_task(self._read_stream(self._process.stdout, stream_name="stdout"))
            self._stderr_task = asyncio.create_task(self._read_stream(self._process.stderr, stream_name="stderr"))
        self._wait_task = asyncio.create_task(self._watch_process_exit())

    @override
    def id(self) -> str:
        return self._id

    async def _read_stream(self, reader: Optional[asyncio.StreamReader], *, stream_name: str) -> None:
        if reader is None:
            return
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                return
            await self._append_output(chunk.decode("utf-8", errors="replace"), stream=stream_name)

    async def _read_pty(self, master_fd: int) -> None:
        loop = asyncio.get_running_loop()
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        read_event = asyncio.Event()

        def _on_readable() -> None:
            read_event.set()

        loop.add_reader(master_fd, _on_readable)
        try:
            while True:
                read_event.clear()
                if self._process.returncode is not None:
                    while True:
                        try:
                            data = os.read(master_fd, 4096)
                            if not data:
                                break
                            await self._append_output(data.decode("utf-8", errors="replace"), stream="stdout")
                        except BlockingIOError:
                            break
                        except OSError:
                            break
                    return

                try:
                    await asyncio.wait_for(read_event.wait(), timeout=0.05)
                except asyncio.TimeoutError:
                    pass
                try:
                    data = os.read(master_fd, 4096)
                    if data:
                        await self._append_output(data.decode("utf-8", errors="replace"), stream="stdout")
                except BlockingIOError:
                    pass
                except OSError:
                    return
        finally:
            loop.remove_reader(master_fd)

    async def _append_output(self, chunk: str, *, stream: str) -> None:
        normalized = chunk.replace("\r\n", "\n")
        async with self._lock:
            if stream == "stderr":
                self._stderr += normalized
            else:
                self._stdout += normalized

            merged = self._partial + normalized
            lines, self._partial = _split_lines_with_partial(merged)
            self._lines.extend(lines)
            self._trim_lines_locked()

    async def _watch_process_exit(self) -> None:
        code = await self._process.wait()
        await asyncio.gather(self._stdout_task, self._stderr_task, return_exceptions=True)
        async with self._lock:
            if self._finished_at is not None:
                return
            if self._partial:
                self._lines.append(self._partial)
                self._partial = ""
                self._trim_lines_locked()
            self._exit_code = code
            self._finished_at = time.time()

    def _trim_lines_locked(self) -> None:
        if self._max_lines <= 0:
            return
        if len(self._lines) <= self._max_lines:
            return
        drop = len(self._lines) - self._max_lines
        self._lines = self._lines[drop:]
        self._line_base += drop
        if self._poll_cursor < self._line_base:
            self._poll_cursor = self._line_base

    @override
    async def poll(self, limit: Optional[int] = None) -> ProgramPoll:
        async with self._lock:
            start = self._poll_cursor
            if start < self._line_base:
                start = self._line_base
                self._poll_cursor = start
            end = self._line_base + len(self._lines)
            if limit is not None and limit > 0:
                end = min(end, start + limit)

            out = ""
            if end > start:
                out = "\n".join(self._lines[start - self._line_base:end - self._line_base])
            if end == self._line_base + len(self._lines) and self._partial:
                out = f"{out}\n{self._partial}" if out else self._partial

            self._poll_cursor = end
            status = PROGRAM_STATUS_RUNNING if self._finished_at is None else PROGRAM_STATUS_EXITED
            return ProgramPoll(
                status=status,
                output=out,
                offset=start,
                next_offset=end,
                exit_code=self._exit_code,
            )

    @override
    async def log(self, offset: Optional[int] = None, limit: Optional[int] = None) -> ProgramLog:
        async with self._lock:
            start = self._line_base if offset is None else offset
            end = self._line_base + len(self._lines)

            if start < self._line_base:
                start = self._line_base
            if start > end:
                start = end
            if limit is not None and limit > 0:
                end = min(end, start + limit)

            out = ""
            if end > start:
                out = "\n".join(self._lines[start - self._line_base:end - self._line_base])
            if end == self._line_base + len(self._lines) and self._partial:
                out = f"{out}\n{self._partial}" if out else self._partial
            return ProgramLog(output=out, offset=start, next_offset=end)

    @override
    async def write(self, data: str, newline: bool) -> None:
        if not data and not newline:
            return
        if self._process.returncode is not None:
            raise ValueError("session is not running")
        text = data
        if newline:
            text += "\n"
        if self._master_fd is not None:
            try:
                os.write(self._master_fd, text.encode("utf-8"))
                return
            except OSError as ex:
                raise ValueError("stdin is not available") from ex
        stdin = self._process.stdin
        if stdin is None:
            raise ValueError("stdin is not available")
        stdin.write(text.encode("utf-8"))
        await stdin.drain()

    @override
    async def kill(self, grace_seconds: float) -> None:
        if self._process.returncode is not None:
            return
        self._process.terminate()
        try:
            await asyncio.wait_for(self._process.wait(), timeout=max(0.0, grace_seconds))
            return
        except asyncio.TimeoutError:
            pass
        if self._process.returncode is None:
            self._process.kill()
            await self._process.wait()

    @override
    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
        if self._process.returncode is None:
            await self.kill(0.5)
        if self._process.stdin is not None:
            try:
                self._process.stdin.close()
            except Exception:  # pylint: disable=broad-except
                pass
        await asyncio.gather(self._stdout_task, self._stderr_task, self._wait_task, return_exceptions=True)
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass

    async def enforce_timeout(self, timeout_sec: float) -> None:
        if timeout_sec <= 0:
            return
        await asyncio.sleep(timeout_sec)
        if self._process.returncode is None:
            self._timed_out = True
            await self.kill(0.5)

    @override
    async def state(self) -> ProgramState:
        if self._finished_at is None:
            return ProgramState(status=PROGRAM_STATUS_RUNNING)
        return ProgramState(status=PROGRAM_STATUS_EXITED, exit_code=self._exit_code)

    @override
    async def run_result(self) -> WorkspaceRunResult:
        duration = 0.0
        if self._finished_at is not None:
            duration = self._finished_at - self._started_at
        return WorkspaceRunResult(
            stdout=self._stdout,
            stderr=self._stderr,
            exit_code=self._exit_code or 0,
            duration=duration,
            timed_out=self._timed_out,
        )
