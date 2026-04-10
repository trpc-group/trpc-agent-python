# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Program-session helpers.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from typing import Optional

from ._types import WorkspaceRunResult

# Default wait windows (milliseconds).
DEFAULT_EXEC_YIELD_MS = 1_000
DEFAULT_IO_YIELD_MS = 400
DEFAULT_POLL_LINES = 40

# Poll pacing / settle windows (seconds).
DEFAULT_POLL_WAIT_SEC = 0.05
DEFAULT_POLL_SETTLE_SEC = 0.075

# Session lifecycle defaults (seconds).
DEFAULT_SESSION_TTL_SEC = 30 * 60
DEFAULT_SESSION_KILL_SEC = 2.0

PROGRAM_STATUS_RUNNING = "running"
PROGRAM_STATUS_EXITED = "exited"


@dataclass
class ProgramPoll:
    """Incremental output chunk for a running or exited session."""

    status: str = PROGRAM_STATUS_RUNNING
    output: str = ""
    offset: int = 0
    next_offset: int = 0
    exit_code: Optional[int] = None


@dataclass
class ProgramLog:
    """Non-destructive output window from a specific offset."""

    output: str = ""
    offset: int = 0
    next_offset: int = 0


@dataclass
class ProgramState:
    """Non-streaming session status without cursor mutation."""

    status: str = PROGRAM_STATUS_RUNNING
    exit_code: Optional[int] = None


class BaseProgramSession(ABC):
    """Base class for program sessions."""

    @abstractmethod
    def id(self) -> str:
        """Return stable session id."""

    @abstractmethod
    async def poll(self, limit: Optional[int] = None) -> ProgramPoll:
        """Advance cursor and return incremental output."""

    @abstractmethod
    async def log(self, offset: Optional[int] = None, limit: Optional[int] = None) -> ProgramLog:
        """Read output from offset without advancing cursor."""

    @abstractmethod
    async def write(self, data: str, newline: bool) -> None:
        """Write input to session."""

    @abstractmethod
    async def kill(self, grace_seconds: float) -> None:
        """Terminate session, escalating after grace period."""

    @abstractmethod
    async def close(self) -> None:
        """Release resources and stop background routines."""

    @abstractmethod
    async def state(self) -> ProgramState:
        """Return current state snapshot."""

    @abstractmethod
    async def run_result(self) -> WorkspaceRunResult:
        """Return final run result after session exits."""


def yield_duration_ms(ms: int, fallback_ms: int) -> float:
    """Normalize milliseconds into seconds with fallback and clamping."""
    if ms < 0:
        ms = 0
    if ms == 0:
        ms = fallback_ms
    return ms / 1000.0


def poll_line_limit(lines: int) -> int:
    """Return a positive poll-line limit with default fallback."""
    if lines <= 0:
        lines = DEFAULT_POLL_LINES
    return lines


async def wait_for_program_output(
    proc: BaseProgramSession,
    yield_seconds: float,
    limit: Optional[int],
) -> ProgramPoll:
    """Poll until session exits or output settles within the yield window."""
    deadline = time.monotonic()
    if yield_seconds > 0:
        deadline += yield_seconds

    out_parts: list[str] = []
    offset = 0
    next_offset = 0
    have_chunk = False
    settle_deadline = 0.0

    while True:
        poll = await proc.poll(limit)
        if poll.output:
            if not have_chunk:
                offset = poll.offset
                have_chunk = True
            out_parts.append(poll.output)
            next_offset = poll.next_offset
            settle_deadline = time.monotonic() + DEFAULT_POLL_SETTLE_SEC
            if yield_seconds <= 0:
                deadline = settle_deadline
        elif not have_chunk:
            offset = poll.offset
            next_offset = poll.next_offset
        else:
            next_offset = poll.next_offset

        if poll.status == PROGRAM_STATUS_EXITED:
            poll.output = "".join(out_parts)
            poll.offset = offset
            poll.next_offset = next_offset
            return poll

        now = time.monotonic()
        if settle_deadline and now > settle_deadline:
            poll.output = "".join(out_parts)
            poll.offset = offset
            poll.next_offset = next_offset
            return poll

        if yield_seconds > 0 and now > deadline:
            poll.output = "".join(out_parts)
            poll.offset = offset
            poll.next_offset = next_offset
            return poll

        await asyncio.sleep(DEFAULT_POLL_WAIT_SEC)
