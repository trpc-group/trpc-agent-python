# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Interactive skill execution tools.

Provides four tools that mirror the Go :mod:`~trpc_agent_sdk.skills.tools.SkillExecTool` implementation:

* :class:`~trpc_agent_sdk.skills.tools.SkillExecTool`          — start an interactive session
* :class:`~trpc_agent_sdk.skills.tools.WriteStdinTool`   — write stdin to a running session
* :class:`~trpc_agent_sdk.skills.tools.PollSessionTool`  — poll a session for new output
* :class:`~trpc_agent_sdk.skills.tools.KillSessionTool`  — terminate and remove a session

Sessions run real sub-processes inside the staged skill workspace.  When
:attr:`~trpc_agent_sdk.skills.tools.ExecInput.tty` is ``True`` a POSIX pseudo-terminal is allocated so TTY-aware programs work
correctly (e.g. interactive shells, ncurses UIs).

Usage example::

    # 1. Start a long-running interactive command
    result = await skill_exec_tool.run(ctx, {
        "skill": "my_skill",
        "command": "python interactive.py",
        "yield_ms": 500,
    })
    sid = result["session_id"]

    # 2. Respond to a prompt
    await write_stdin_tool.run(ctx, {"session_id": sid, "chars": "yes", "submit": True})

    # 3. Poll for more output
    await poll_tool.run(ctx, {"session_id": sid})

    # 4. Kill when done
    await kill_tool.run(ctx, {"session_id": sid})
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import pty
import time
import uuid
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing_extensions import override

from pydantic import BaseModel
from pydantic import Field
from trpc_agent_sdk.code_executors import DIR_OUT
from trpc_agent_sdk.code_executors import DIR_SKILLS
from trpc_agent_sdk.code_executors import DIR_WORK
from trpc_agent_sdk.code_executors import ENV_SKILL_NAME
from trpc_agent_sdk.code_executors import WorkspaceInfo
from trpc_agent_sdk.code_executors import WorkspaceInputSpec
from trpc_agent_sdk.code_executors import WorkspaceOutputSpec
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema

from ._copy_stager import SkillStageRequest
from ._skill_run import SkillRunInput
from ._skill_run import SkillRunOutput
from ._skill_run import SkillRunTool
from ._skill_run import _filter_failed_empty_outputs
from ._skill_run import _inline_json_schema_refs
from ._skill_run import _select_primary_output
from ._skill_run import _truncate_output

# ---------------------------------------------------------------------------
# Defaults (mirrors Go program's session defaults)
# ---------------------------------------------------------------------------

DEFAULT_EXEC_YIELD_MS: int = 300  # wait time on skill_exec
DEFAULT_IO_YIELD_MS: int = 100  # wait time on write/poll
DEFAULT_POLL_LINES: int = 50  # max lines returned per call
DEFAULT_SESSION_TTL: float = 300.0  # seconds after exit before GC

# Status strings
_STATUS_RUNNING = "running"
_STATUS_EXITED = "exited"

# Interaction kinds
_INTERACTION_PROMPT = "prompt"
_INTERACTION_SELECTION = "selection"

# ---------------------------------------------------------------------------
# Pydantic I/O models
# ---------------------------------------------------------------------------


class ExecInput(BaseModel):
    """Input for skill_exec."""

    skill: str = Field(..., description="Skill name")
    command: str = Field(..., description="Shell command to execute")
    cwd: str = Field(default="", description="Working directory (relative to skill root)")
    env: dict[str, str] = Field(default_factory=dict, description="Extra environment variables")
    stdin: str = Field(default="", description="Optional initial stdin written before yielding")
    tty: bool = Field(default=False, description="Allocate a pseudo-TTY")
    yield_ms: int = Field(default=0, description="Milliseconds to wait for initial output before returning")
    poll_lines: int = Field(default=0, description="Maximum output lines to return per call")
    output_files: list[str] = Field(default_factory=list, description="Glob patterns to collect on exit")
    timeout: int = Field(default=0, description="Timeout in seconds (0 = no timeout)")
    save_as_artifacts: bool = Field(default=False, description="Save collected files as artifacts")
    omit_inline_content: bool = Field(default=False, description="Omit inline file content")
    artifact_prefix: str = Field(default="", description="Artifact name prefix")
    inputs: list[WorkspaceInputSpec] = Field(default_factory=list, description="Input staging specs")
    outputs: Optional[WorkspaceOutputSpec] = Field(default=None, description="Declarative output spec")


class WriteStdinInput(BaseModel):
    """Input for skill_write_stdin."""

    session_id: str = Field(..., description="Session id returned by skill_exec")
    chars: str = Field(default="", description="Text to write to stdin")
    submit: bool = Field(default=False, description="Append a newline after chars")
    yield_ms: int = Field(default=0, description="Milliseconds to wait for new output")
    poll_lines: int = Field(default=0, description="Maximum output lines to return")


class PollSessionInput(BaseModel):
    """Input for skill_poll_session."""

    session_id: str = Field(..., description="Session id returned by skill_exec")
    yield_ms: int = Field(default=0, description="Milliseconds to wait for new output")
    poll_lines: int = Field(default=0, description="Maximum output lines to return")


class KillSessionInput(BaseModel):
    """Input for skill_kill_session."""

    session_id: str = Field(..., description="Session id returned by skill_exec")


class SessionInteraction(BaseModel):
    """Best-effort hint that the program is waiting for input."""

    needs_input: bool = Field(default=False, description="Whether input appears expected")
    kind: str = Field(default="", description="'prompt' or 'selection'")
    hint: str = Field(default="", description="Most relevant prompt line")


class ExecOutput(BaseModel):
    """Output for skill_exec, skill_write_stdin, and skill_poll_session."""

    status: str = Field(default=_STATUS_RUNNING, description="'running' or 'exited'")
    session_id: str = Field(default="", description="Interactive session id")
    output: str = Field(default="", description="New terminal output since last call")
    offset: int = Field(default=0, description="Start byte offset of returned output")
    next_offset: int = Field(default=0, description="End byte offset (use for next call)")
    exit_code: Optional[int] = Field(default=None, description="Process exit code (when exited)")
    interaction: Optional[SessionInteraction] = Field(default=None, description="Hint for stdin interaction")
    result: Optional[SkillRunOutput] = Field(default=None, description="Final run output (when exited)")


class SessionKillOutput(BaseModel):
    """Output for skill_kill_session."""

    ok: bool = Field(default=True, description="True when session was removed")
    session_id: str = Field(default="", description="Session id")
    status: str = Field(default="", description="Final status after kill")


# ---------------------------------------------------------------------------
# Internal session state
# ---------------------------------------------------------------------------


@dataclass
class _ExecSession:
    """Holds state for one running interactive skill session."""

    proc: asyncio.subprocess.Process
    ws: WorkspaceInfo
    in_data: ExecInput

    # Output buffer (all output since start as raw bytes → decoded text)
    _output_buf: list[str] = field(default_factory=list)
    _output_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _output_event: asyncio.Event = field(default_factory=asyncio.Event)

    # Current byte offset for incremental reads
    _read_offset: int = 0

    # Background reader task
    reader_task: Optional[asyncio.Task] = None

    # PTY master fd (None for non-TTY)
    master_fd: Optional[int] = None

    # Final state
    exit_code: Optional[int] = None
    exited_at: Optional[float] = None
    final_result: Optional[SkillRunOutput] = None
    finalized: bool = False

    async def append_output(self, chunk: str) -> None:
        async with self._output_lock:
            self._output_buf.append(chunk)
        self._output_event.set()

    async def total_output(self) -> str:
        async with self._output_lock:
            return "".join(self._output_buf)

    async def yield_output(self, yield_ms: int, poll_lines: int) -> tuple[str, str, int, int]:
        """Wait *yield_ms* ms for new output then return a chunk.

        Returns ``(status, output_chunk, offset, next_offset)``.
        """
        yield_sec = (yield_ms or DEFAULT_EXEC_YIELD_MS) / 1000.0
        deadline = asyncio.get_event_loop().time() + yield_sec

        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            self._output_event.clear()
            # Break early if process has already exited and we have all output
            if self.proc.returncode is not None:
                break
            try:
                await asyncio.wait_for(asyncio.shield(self._output_event.wait()), timeout=remaining)
            except asyncio.TimeoutError:
                break

        # Determine status
        rc = self.proc.returncode
        if rc is not None:
            self.exit_code = rc
            if self.exited_at is None:
                self.exited_at = time.time()
        status = _STATUS_RUNNING if rc is None else _STATUS_EXITED

        # Slice the output since last read
        async with self._output_lock:
            full = "".join(self._output_buf)

        chunk = full[self._read_offset:]

        # Apply poll_lines limit
        limit = poll_lines or DEFAULT_POLL_LINES
        if limit > 0 and chunk:
            lines = chunk.split("\n")
            if len(lines) > limit:
                chunk = "\n".join(lines[:limit])
                if not chunk.endswith("\n"):
                    chunk += "\n"

        offset = self._read_offset
        self._read_offset += len(chunk)
        next_offset = self._read_offset

        return status, chunk, offset, next_offset


# ---------------------------------------------------------------------------
# Background output readers
# ---------------------------------------------------------------------------


async def _read_pipe(session: _ExecSession, stream: asyncio.StreamReader) -> None:
    """Continuously read from *stream* and append to session output."""
    try:
        while True:
            data = await stream.read(4096)
            if not data:
                break
            await session.append_output(data.decode("utf-8", errors="replace"))
    except Exception:  # pylint: disable=broad-except
        pass
    finally:
        # Ensure exit is captured
        try:
            await session.proc.wait()
        except Exception:  # pylint: disable=broad-except
            pass
        session._output_event.set()  # unblock any waiting yield


async def _read_pty(session: _ExecSession, master_fd: int) -> None:
    """Continuously read from a PTY master fd and append to session output."""
    loop = asyncio.get_event_loop()
    try:
        # Set master_fd non-blocking
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        # Use loop.add_reader for non-blocking reads
        read_event = asyncio.Event()

        def _on_readable() -> None:
            read_event.set()

        loop.add_reader(master_fd, _on_readable)
        try:
            while True:
                read_event.clear()
                # Check if process has exited
                rc = session.proc.returncode
                if rc is not None:
                    # Drain remaining data
                    while True:
                        try:
                            data = os.read(master_fd, 4096)
                            if data:
                                await session.append_output(data.decode("utf-8", errors="replace"))
                            else:
                                break
                        except OSError:
                            break
                    break
                # Wait for readable or timeout
                try:
                    await asyncio.wait_for(read_event.wait(), timeout=0.05)
                except asyncio.TimeoutError:
                    pass
                try:
                    data = os.read(master_fd, 4096)
                    if data:
                        await session.append_output(data.decode("utf-8", errors="replace"))
                except BlockingIOError:
                    pass
                except OSError:
                    break
        finally:
            loop.remove_reader(master_fd)
    except Exception:  # pylint: disable=broad-except
        pass
    finally:
        try:
            await session.proc.wait()
        except Exception:  # pylint: disable=broad-except
            pass
        session._output_event.set()


# ---------------------------------------------------------------------------
# Interaction detection (port of Go detectInteraction)
# ---------------------------------------------------------------------------


def _last_non_empty_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _has_selection_items(text: str) -> bool:
    count = 0
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if len(s) >= 2 and s[0].isdigit() and s[1] in (".", ")"):
            count += 1
        if count >= 2:
            return True
    return False


def _detect_interaction(status: str, output: str) -> Optional[SessionInteraction]:
    if status != _STATUS_RUNNING:
        return None
    hint = _last_non_empty_line(output)
    if not hint:
        return None
    lower = hint.lower()
    if ("enter the number" in lower or "choose a number" in lower or "select a number" in lower
            or _has_selection_items(output)):
        return SessionInteraction(needs_input=True, kind=_INTERACTION_SELECTION, hint=hint)
    if (hint.endswith(":") or hint.endswith("?") or "press enter" in lower or "type your" in lower):
        return SessionInteraction(needs_input=True, kind=_INTERACTION_PROMPT, hint=hint)
    return None


# ---------------------------------------------------------------------------
# Workspace env helpers (mirrors LocalProgramRunner.run_program)
# ---------------------------------------------------------------------------


def _build_exec_env(ws: WorkspaceInfo, extra: dict[str, str]) -> dict[str, str]:
    """Build the merged environment for a subprocess in *ws*."""
    from trpc_agent_sdk.code_executors._constants import (  # lazy import to avoid circular
        WORKSPACE_ENV_DIR_KEY, ENV_SKILLS_DIR, ENV_WORK_DIR, ENV_OUTPUT_DIR, DIR_RUNS,
    )
    from datetime import datetime

    env = os.environ.copy()
    run_dir = str(Path(ws.path) / DIR_RUNS / f"run_{datetime.now().strftime('%Y%m%dT%H%M%S_%f')}")
    os.makedirs(run_dir, exist_ok=True)

    base = {
        WORKSPACE_ENV_DIR_KEY: ws.path,
        ENV_SKILLS_DIR: str(Path(ws.path) / DIR_SKILLS),
        ENV_WORK_DIR: str(Path(ws.path) / DIR_WORK),
        ENV_OUTPUT_DIR: str(Path(ws.path) / DIR_OUT),
        "RUN_DIR": run_dir,
    }
    env.update({k: v for k, v in base.items() if k not in extra})
    env.update(extra)
    return env


def _resolve_abs_cwd(ws_path: str, rel_cwd: str) -> str:
    """Return the absolute cwd by joining *ws_path* and *rel_cwd*."""
    if rel_cwd and os.path.isabs(rel_cwd):
        return rel_cwd
    resolved = os.path.normpath(os.path.join(ws_path, rel_cwd or "."))
    os.makedirs(resolved, exist_ok=True)
    return resolved


# ---------------------------------------------------------------------------
# SkillExecTool  (skill_exec)
# ---------------------------------------------------------------------------


class SkillExecTool(BaseTool):
    """Start an interactive shell command inside a staged skill workspace.

    Shares workspace, staging, and output-collection semantics with
    ``skill_run``, but keeps the process alive so stdin can be written
    and output polled incrementally.
    """

    def __init__(
        self,
        run_tool: SkillRunTool,
        filters: Optional[List[BaseFilter]] = None,
        session_ttl: float = DEFAULT_SESSION_TTL,
    ):
        super().__init__(name="skill_exec",
                         description=("Start an interactive command inside a skill workspace. "
                                      "Use it when a skill command may prompt for stdin, selection, "
                                      "or TTY interaction. Shares the same workspace, inputs, outputs, "
                                      "and artifact semantics as skill_run."),
                         filters=filters)
        self._run_tool = run_tool
        self._ttl = session_ttl
        self._sessions: dict[str, _ExecSession] = {}
        self._sessions_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Declaration
    # ------------------------------------------------------------------

    @override
    def _get_declaration(self) -> FunctionDeclaration:
        params_schema = _inline_json_schema_refs(ExecInput.model_json_schema())
        response_schema = _inline_json_schema_refs(ExecOutput.model_json_schema())
        return FunctionDeclaration(
            name="skill_exec",
            description=("Start an interactive command inside a skill workspace. "
                         "Use it when a skill command may prompt for stdin, selection, "
                         "or TTY interaction."),
            parameters=Schema.model_validate(params_schema),
            response=Schema.model_validate(response_schema),
        )

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    async def _put_session(self, sid: str, sess: _ExecSession) -> None:
        async with self._sessions_lock:
            await self._gc_expired_locked()
            self._sessions[sid] = sess

    async def get_session(self, sid: str) -> _ExecSession:
        async with self._sessions_lock:
            await self._gc_expired_locked()
            sess = self._sessions.get(sid)
        if sess is None:
            raise ValueError(f"unknown session_id: {sid}")
        return sess

    async def remove_session(self, sid: str) -> _ExecSession:
        async with self._sessions_lock:
            await self._gc_expired_locked()
            sess = self._sessions.pop(sid, None)
        if sess is None:
            raise ValueError(f"unknown session_id: {sid}")
        return sess

    async def _gc_expired_locked(self) -> None:
        if self._ttl <= 0:
            return
        now = time.time()
        expired = [
            sid for sid, s in self._sessions.items() if s.exited_at is not None and (now - s.exited_at) >= self._ttl
        ]
        for sid in expired:
            s = self._sessions.pop(sid)
            _close_session(s)

    # ------------------------------------------------------------------
    # Main execution
    # ------------------------------------------------------------------

    @override
    async def _run_async_impl(
        self,
        *,
        tool_context: InvocationContext,
        args: Dict[str, Any],
    ) -> Any:
        try:
            inputs = ExecInput.model_validate(args)
        except Exception as ex:  # pylint: disable=broad-except
            raise ValueError(f"Invalid skill_exec arguments: {ex}") from ex

        repository = self._run_tool._get_repository(tool_context)

        # Workspace creation
        session_id_ws = inputs.skill
        if tool_context.session and tool_context.session.id:
            session_id_ws = tool_context.session.id

        workspace_runtime = repository.workspace_runtime
        manager = workspace_runtime.manager(tool_context)
        ws = await manager.create_workspace(session_id_ws, tool_context)

        # Stage skill via the same pluggable stager used by SkillRunTool
        stage_result = await self._run_tool.skill_stager.stage_skill(
            SkillStageRequest(
                skill_name=inputs.skill,
                repository=repository,
                workspace=ws,
                ctx=tool_context,
                engine=workspace_runtime,
                timeout=self._run_tool._timeout,
            ))
        workspace_skill_dir = stage_result.workspace_skill_dir

        if inputs.inputs:
            fs = workspace_runtime.fs(tool_context)
            await fs.stage_inputs(ws, inputs.inputs, tool_context)

        # Resolve cwd and env
        rel_cwd = self._run_tool._resolve_cwd(inputs.cwd, workspace_skill_dir)
        abs_cwd = _resolve_abs_cwd(ws.path, rel_cwd)

        extra_env: dict[str, str] = dict(inputs.env)
        if ENV_SKILL_NAME not in extra_env:
            extra_env[ENV_SKILL_NAME] = inputs.skill
        merged_env = _build_exec_env(ws, extra_env)

        # Start subprocess
        sid = str(uuid.uuid4())
        sess = await _start_session(inputs, ws, abs_cwd, merged_env)
        await self._put_session(sid, sess)

        # Write initial stdin if provided
        if inputs.stdin:
            await _write_stdin(sess, inputs.stdin, submit=False)

        yield_ms = inputs.yield_ms or DEFAULT_EXEC_YIELD_MS
        status, chunk, offset, next_offset = await sess.yield_output(yield_ms, inputs.poll_lines)

        # Attempt to collect final result if already exited
        final_result = None
        if status == _STATUS_EXITED and not sess.finalized:
            final_result = await _collect_final_result(tool_context, sess, self._run_tool)

        out = ExecOutput(
            status=status,
            session_id=sid,
            output=chunk,
            offset=offset,
            next_offset=next_offset,
            exit_code=sess.exit_code,
            interaction=_detect_interaction(status, chunk),
            result=final_result,
        )
        return out.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# WriteStdinTool  (skill_write_stdin)
# ---------------------------------------------------------------------------


class WriteStdinTool(BaseTool):
    """Write to a running ``skill_exec`` session.

    When ``submit=True`` a newline is appended so the program receives a
    complete line.  When ``chars`` is empty and ``submit=False`` this behaves
    as a lightweight poll.
    """

    def __init__(self, exec_tool: SkillExecTool, filters: Optional[List[BaseFilter]] = None):
        super().__init__(name="skill_write_stdin",
                         description=("Write to a running skill_exec session. Set submit=true to append "
                                      "a newline. When chars is empty and submit is false, it acts like "
                                      "a lightweight poll."),
                         filters=filters)
        self._exec = exec_tool

    @override
    def _get_declaration(self) -> FunctionDeclaration:
        params_schema = _inline_json_schema_refs(WriteStdinInput.model_json_schema())
        response_schema = _inline_json_schema_refs(ExecOutput.model_json_schema())
        return FunctionDeclaration(
            name="skill_write_stdin",
            description=("Write to a running skill_exec session. Set submit=true to "
                         "append a newline. When chars is empty and submit is false, "
                         "it acts like a lightweight poll."),
            parameters=Schema.model_validate(params_schema),
            response=Schema.model_validate(response_schema),
        )

    @override
    async def _run_async_impl(
        self,
        *,
        tool_context: InvocationContext,
        args: Dict[str, Any],
    ) -> Any:
        try:
            inputs = WriteStdinInput.model_validate(args)
        except Exception as ex:  # pylint: disable=broad-except
            raise ValueError(f"Invalid skill_write_stdin arguments: {ex}") from ex

        sess = await self._exec.get_session(inputs.session_id)

        if inputs.chars or inputs.submit:
            await _write_stdin(sess, inputs.chars, submit=inputs.submit)

        yield_ms = inputs.yield_ms or DEFAULT_IO_YIELD_MS
        status, chunk, offset, next_offset = await sess.yield_output(yield_ms, inputs.poll_lines)

        final_result = None
        if status == _STATUS_EXITED and not sess.finalized:
            final_result = await _collect_final_result(tool_context, sess, self._exec._run_tool)

        out = ExecOutput(
            status=status,
            session_id=inputs.session_id,
            output=chunk,
            offset=offset,
            next_offset=next_offset,
            exit_code=sess.exit_code,
            interaction=_detect_interaction(status, chunk),
            result=final_result,
        )
        return out.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# PollSessionTool  (skill_poll_session)
# ---------------------------------------------------------------------------


class PollSessionTool(BaseTool):
    """Poll a running or recently exited ``skill_exec`` session for output."""

    def __init__(self, exec_tool: SkillExecTool, filters: Optional[List[BaseFilter]] = None):
        super().__init__(name="skill_poll_session",
                         description=("Poll a running or recently exited skill_exec session for "
                                      "additional output or final results."),
                         filters=filters)
        self._exec = exec_tool

    @override
    def _get_declaration(self) -> FunctionDeclaration:
        params_schema = _inline_json_schema_refs(PollSessionInput.model_json_schema())
        response_schema = _inline_json_schema_refs(ExecOutput.model_json_schema())
        return FunctionDeclaration(
            name="skill_poll_session",
            description=("Poll a running or recently exited skill_exec session for "
                         "additional output or final results."),
            parameters=Schema.model_validate(params_schema),
            response=Schema.model_validate(response_schema),
        )

    @override
    async def _run_async_impl(
        self,
        *,
        tool_context: InvocationContext,
        args: Dict[str, Any],
    ) -> Any:
        try:
            inputs = PollSessionInput.model_validate(args)
        except Exception as ex:  # pylint: disable=broad-except
            raise ValueError(f"Invalid skill_poll_session arguments: {ex}") from ex

        sess = await self._exec.get_session(inputs.session_id)

        yield_ms = inputs.yield_ms or DEFAULT_IO_YIELD_MS
        status, chunk, offset, next_offset = await sess.yield_output(yield_ms, inputs.poll_lines)

        final_result = None
        if status == _STATUS_EXITED and not sess.finalized:
            final_result = await _collect_final_result(tool_context, sess, self._exec._run_tool)

        out = ExecOutput(
            status=status,
            session_id=inputs.session_id,
            output=chunk,
            offset=offset,
            next_offset=next_offset,
            exit_code=sess.exit_code,
            interaction=_detect_interaction(status, chunk),
            result=final_result,
        )
        return out.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# KillSessionTool  (skill_kill_session)
# ---------------------------------------------------------------------------


class KillSessionTool(BaseTool):
    """Terminate and remove a ``skill_exec`` session."""

    def __init__(self, exec_tool: SkillExecTool, filters: Optional[List[BaseFilter]] = None):
        super().__init__(name="skill_kill_session",
                         description=("Terminate and remove a skill_exec session."),
                         filters=filters)
        self._exec = exec_tool

    @override
    def _get_declaration(self) -> FunctionDeclaration:
        params_schema = _inline_json_schema_refs(KillSessionInput.model_json_schema())
        response_schema = _inline_json_schema_refs(SessionKillOutput.model_json_schema())
        return FunctionDeclaration(
            name="skill_kill_session",
            description="Terminate and remove a skill_exec session.",
            parameters=Schema.model_validate(params_schema),
            response=Schema.model_validate(response_schema),
        )

    @override
    async def _run_async_impl(
        self,
        *,
        tool_context: InvocationContext,
        args: Dict[str, Any],
    ) -> Any:
        try:
            inputs = KillSessionInput.model_validate(args)
        except Exception as ex:  # pylint: disable=broad-except
            raise ValueError(f"Invalid skill_kill_session arguments: {ex}") from ex

        sess = await self._exec.remove_session(inputs.session_id)

        rc = sess.proc.returncode
        final_status = _STATUS_EXITED

        if rc is None:
            try:
                sess.proc.kill()
                await asyncio.wait_for(sess.proc.wait(), timeout=5.0)
            except Exception:  # pylint: disable=broad-except
                pass
            final_status = "killed"

        _close_session(sess)

        out = SessionKillOutput(
            ok=True,
            session_id=inputs.session_id,
            status=final_status,
        )
        return out.model_dump()


# ---------------------------------------------------------------------------
# Factory helper
# ---------------------------------------------------------------------------


def create_exec_tools(
    run_tool: SkillRunTool,
    filters: Optional[List[BaseFilter]] = None,
    session_ttl: float = DEFAULT_SESSION_TTL,
) -> tuple[SkillExecTool, WriteStdinTool, PollSessionTool, KillSessionTool]:
    """Create the full set of interactive exec tools sharing one session store.

    Args:
        run_tool: An existing :class:`SkillRunTool` whose staging and
                  workspace configuration will be reused.
        filters: Optional tool filters applied to all four tools.
        session_ttl: Seconds after process exit before a session is GC'd.

    Returns:
        ``(exec_tool, write_stdin_tool, poll_session_tool, kill_session_tool)``

    Example::

        exec_tool, write, poll, kill = create_exec_tools(run_tool)
        agent.add_tools([exec_tool, write, poll, kill])
    """
    exec_tool = SkillExecTool(run_tool, filters=filters, session_ttl=session_ttl)
    return (
        exec_tool,
        WriteStdinTool(exec_tool, filters=filters),
        PollSessionTool(exec_tool, filters=filters),
        KillSessionTool(exec_tool, filters=filters),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _start_session(
    inputs: ExecInput,
    ws: WorkspaceInfo,
    abs_cwd: str,
    env: dict[str, str],
) -> _ExecSession:
    """Spawn a subprocess and return an initialized :class:`_ExecSession`."""
    command = inputs.command
    master_fd: Optional[int] = None

    if inputs.tty:
        # Allocate a pseudo-TTY.
        master_fd, slave_fd = pty.openpty()
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash",
                "-c",
                command,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=abs_cwd,
                env=env,
                close_fds=True,
                preexec_fn=os.setsid,
            )
        finally:
            os.close(slave_fd)  # parent doesn't need the slave end

        sess = _ExecSession(proc=proc, ws=ws, in_data=inputs, master_fd=master_fd)
        sess.reader_task = asyncio.create_task(_read_pty(sess, master_fd))
    else:
        proc = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=abs_cwd,
            env=env,
        )
        sess = _ExecSession(proc=proc, ws=ws, in_data=inputs)
        if proc.stdout:
            sess.reader_task = asyncio.create_task(_read_pipe(sess, proc.stdout))

    return sess


async def _write_stdin(sess: _ExecSession, chars: str, submit: bool) -> None:
    """Write *chars* (and optionally a newline) to the session's stdin."""
    if sess.master_fd is not None:
        # PTY path: write to master fd
        data = (chars + ("\n" if submit else "")).encode("utf-8")
        if data:
            try:
                os.write(sess.master_fd, data)
            except OSError as ex:
                logger.debug("skill_exec: write to pty failed: %s", ex)
    elif sess.proc.stdin:
        # Pipe path: use asyncio StreamWriter
        data = (chars + ("\n" if submit else "")).encode("utf-8")
        if data:
            try:
                sess.proc.stdin.write(data)
                await sess.proc.stdin.drain()
            except Exception as ex:  # pylint: disable=broad-except
                logger.debug("skill_exec: write to stdin failed: %s", ex)


async def _collect_final_result(
    ctx: InvocationContext,
    sess: _ExecSession,
    run_tool: SkillRunTool,
) -> Optional[SkillRunOutput]:
    """Collect output files and build the final :class:`SkillRunOutput`."""
    if sess.finalized:
        return sess.final_result

    in_data = sess.in_data
    fake_run_input = SkillRunInput(
        skill=in_data.skill,
        command=in_data.command,
        cwd=in_data.cwd,
        env=in_data.env,
        output_files=in_data.output_files,
        timeout=in_data.timeout,
        save_as_artifacts=in_data.save_as_artifacts,
        omit_inline_content=in_data.omit_inline_content,
        artifact_prefix=in_data.artifact_prefix,
        inputs=in_data.inputs,
        outputs=in_data.outputs,
    )
    try:
        files, manifest = await run_tool._prepare_outputs(ctx, sess.ws, fake_run_input)
    except Exception as ex:  # pylint: disable=broad-except
        logger.warning("skill_exec: collect outputs failed: %s", ex)
        files, manifest = [], None

    total_out = await sess.total_output()
    exit_code = sess.exit_code or 0

    # Reuse the same output-quality helpers as skill_run
    warnings: list[str] = []
    stdout, trunc = _truncate_output(total_out)
    if trunc:
        warnings.append("stdout truncated")

    files, filter_warns = _filter_failed_empty_outputs(exit_code, False, files)
    warnings.extend(filter_warns)

    primary = _select_primary_output(files)

    result = SkillRunOutput(
        stdout=stdout,
        exit_code=exit_code,
        output_files=files,
        primary_output=primary,
        warnings=warnings,
    )

    try:
        await run_tool._attach_artifacts_if_requested(ctx, sess.ws, fake_run_input, result, files)
    except Exception as ex:  # pylint: disable=broad-except
        logger.warning("skill_exec: attach artifacts failed: %s", ex)

    if manifest:
        run_tool._merge_manifest_artifact_refs(manifest, result)

    sess.final_result = result
    sess.finalized = True
    return result


def _close_session(sess: _ExecSession) -> None:
    """Cancel background tasks and close any open fds."""
    if sess.reader_task and not sess.reader_task.done():
        sess.reader_task.cancel()
    if sess.master_fd is not None:
        try:
            os.close(sess.master_fd)
        except OSError:
            pass
        sess.master_fd = None
    if sess.proc.stdin and not sess.proc.stdin.is_closing():
        try:
            sess.proc.stdin.close()
        except Exception:  # pylint: disable=broad-except
            pass
