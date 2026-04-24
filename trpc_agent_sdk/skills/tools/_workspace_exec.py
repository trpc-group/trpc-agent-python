# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared executor workspace tools."""

from __future__ import annotations

import posixpath
import time
from dataclasses import dataclass
from typing import Any
from typing import Optional
from typing_extensions import override

from pydantic import BaseModel
from pydantic import Field
from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import BaseProgramSession
from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
from trpc_agent_sdk.code_executors import WorkspaceRuntimeResolver
from trpc_agent_sdk.code_executors import DEFAULT_EXEC_YIELD_MS
from trpc_agent_sdk.code_executors import DEFAULT_SESSION_KILL_SEC
from trpc_agent_sdk.code_executors import DEFAULT_SESSION_TTL_SEC
from trpc_agent_sdk.code_executors import DIR_OUT
from trpc_agent_sdk.code_executors import DIR_RUNS
from trpc_agent_sdk.code_executors import DIR_SKILLS
from trpc_agent_sdk.code_executors import DIR_WORK
from trpc_agent_sdk.code_executors import PROGRAM_STATUS_EXITED
from trpc_agent_sdk.code_executors import PROGRAM_STATUS_RUNNING
from trpc_agent_sdk.code_executors import ProgramPoll
from trpc_agent_sdk.code_executors import WorkspaceRunProgramSpec
from trpc_agent_sdk.code_executors import poll_line_limit
from trpc_agent_sdk.code_executors import WorkspaceInfo
from trpc_agent_sdk.code_executors import wait_for_program_output
from trpc_agent_sdk.code_executors import yield_duration_ms
from trpc_agent_sdk.code_executors.utils import normalize_globs
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

from ._common import CreateWorkspaceNameCallback
from ._common import cleanup_expired_sessions
from ._common import default_create_ws_name_callback
from ._common import require_non_empty

_DEFAULT_WORKSPACE_EXEC_TIMEOUT_SEC = 5 * 60
_DEFAULT_WORKSPACE_WRITE_YIELD_MS = 200


def _combine_output(stdout: str, stderr: str) -> str:
    if not stdout:
        return stderr
    if not stderr:
        return stdout
    return stdout + stderr


def _has_glob_meta(s: str) -> bool:
    return any(ch in s for ch in ("*", "?", "["))


def _has_env_prefix(s: str, name: str) -> bool:
    if s.startswith(f"${name}"):
        tail = s[len(name) + 1:]
        return tail == "" or tail.startswith("/") or tail.startswith("\\")
    brace = f"${{{name}}}"
    if s.startswith(brace):
        tail = s[len(brace):]
        return tail == "" or tail.startswith("/") or tail.startswith("\\")
    return False


def _is_workspace_env_path(s: str) -> bool:
    return any((
        _has_env_prefix(s, "WORKSPACE_DIR"),
        _has_env_prefix(s, "SKILLS_DIR"),
        _has_env_prefix(s, "WORK_DIR"),
        _has_env_prefix(s, "OUTPUT_DIR"),
        _has_env_prefix(s, "RUN_DIR"),
    ))


def _is_allowed_workspace_path(rel: str) -> bool:
    return any(rel == root or rel.startswith(f"{root}/") for root in (DIR_SKILLS, DIR_WORK, DIR_OUT, DIR_RUNS))


def _normalize_cwd(raw: str) -> str:
    s = (raw or "").strip().replace("\\", "/")
    if not s:
        return "."
    if _has_glob_meta(s):
        raise ValueError("cwd must not contain glob patterns")
    if _is_workspace_env_path(s):
        out = normalize_globs([s])
        if not out:
            raise ValueError("invalid cwd")
        s = out[0]
    if s.startswith("/"):
        rel = posixpath.normpath(s).lstrip("/")
        if rel in ("", "."):
            return "."
        if not _is_allowed_workspace_path(rel):
            raise ValueError(f"cwd must stay under workspace roots: {raw!r}")
        return rel
    rel = posixpath.normpath(s)
    if rel == ".":
        return "."
    if rel == ".." or rel.startswith("../"):
        raise ValueError("cwd must stay within the workspace")
    if not _is_allowed_workspace_path(rel):
        raise ValueError(
            f"cwd must stay under supported workspace roots such as skills/, work/, out/, or runs/: {raw!r}")
    return rel


def _exec_timeout_seconds(raw: int) -> float:
    if raw <= 0:
        return float(_DEFAULT_WORKSPACE_EXEC_TIMEOUT_SEC)
    return float(raw)


def _exec_yield_seconds(background: bool, raw_ms: Optional[int]) -> float:
    if background:
        if raw_ms is not None and raw_ms > 0:
            return raw_ms / 1000.0
        return 0.0
    return yield_duration_ms(raw_ms or 0, DEFAULT_EXEC_YIELD_MS)


def _write_yield_seconds(raw_ms: Optional[int]) -> float:
    if raw_ms is None:
        return _DEFAULT_WORKSPACE_WRITE_YIELD_MS / 1000.0
    if raw_ms < 0:
        return 0.0
    return raw_ms / 1000.0


class _ExecInput(BaseModel):
    command: str = Field(default="")
    cwd: str = Field(default="")
    env: dict[str, str] = Field(default_factory=dict)
    stdin: str = Field(default="")
    yield_time_ms: int = Field(default=0)
    background: bool = Field(default=False)
    timeout_sec: int = Field(default=0)
    tty: bool = Field(default=False)


class _WriteInput(BaseModel):
    session_id: str = Field(default="")
    chars: str = Field(default="")
    yield_time_ms: Optional[int] = Field(default=None)
    append_newline: bool = Field(default=False)


@dataclass
class _ExecSession:
    proc: BaseProgramSession
    exited_at: Optional[float] = None
    finalized: bool = False
    finalized_at: Optional[float] = None


class WorkspaceExecTool(BaseTool):
    """Execute shell commands in shared executor workspace."""

    def __init__(
        self,
        workspace_runtime: BaseWorkspaceRuntime,
        workspace_runtime_resolver: Optional[WorkspaceRuntimeResolver] = None,
        create_ws_name_cb: Optional[CreateWorkspaceNameCallback] = None,
        session_ttl: float = DEFAULT_SESSION_TTL_SEC,
        filters_name: Optional[list[str]] = None,
        filters: Optional[list[BaseFilter]] = None,
    ):
        super().__init__(
            name="workspace_exec",
            description=("Execute a shell command inside the current executor workspace. "
                         "This is the default shell runner for executor-side work not bound to a specific skill."),
            filters_name=filters_name,
            filters=filters,
        )
        self._workspace_runtime = workspace_runtime
        self._workspace_runtime_resolver = workspace_runtime_resolver
        self._create_ws_name_cb = create_ws_name_cb or default_create_ws_name_callback
        self._ttl = session_ttl
        self._sessions: dict[str, _ExecSession] = {}

    def _runtime(self, ctx: InvocationContext) -> BaseWorkspaceRuntime:
        if self._workspace_runtime_resolver is not None:
            return self._workspace_runtime_resolver(ctx)
        return self._workspace_runtime

    async def _workspace(self, ctx: InvocationContext) -> tuple[BaseWorkspaceRuntime, WorkspaceInfo]:
        runtime = self._runtime(ctx)
        manager = runtime.manager(ctx)
        workspace_id = self._create_ws_name_cb(ctx)
        ws = await manager.create_workspace(workspace_id, ctx)
        return runtime, ws

    def _supports_interactive(self, ctx: InvocationContext) -> bool:
        runner = self._runtime(ctx).runner(ctx)
        start_program = getattr(runner, "start_program", None)
        return start_program is not None

    async def _put_session(self, sid: str, session: _ExecSession) -> None:
        await self._cleanup_expired_locked()
        self._sessions[sid] = session

    async def _get_session(self, sid: str) -> _ExecSession:
        await self._cleanup_expired_locked()
        session = self._sessions.get(sid)
        if session is None:
            raise ValueError(f"unknown session_id: {sid}")
        return session

    async def _remove_session(self, sid: str) -> _ExecSession:
        await self._cleanup_expired_locked()
        session = self._sessions.pop(sid, None)
        if session is None:
            raise ValueError(f"unknown session_id: {sid}")
        return session

    async def _finalize_and_remove_session(self, sid: str) -> None:
        session = await self._get_session(sid)
        now = time.time()
        session.finalized = True
        session.finalized_at = now
        session.exited_at = now
        await session.proc.close()
        await self._remove_session(sid)

    async def _cleanup_expired_locked(self) -> None:

        async def _refresh_exit_state(session: _ExecSession, now: float) -> None:
            if session.exited_at is not None:
                return
            session_state = await session.proc.state()
            if session_state.status == PROGRAM_STATUS_EXITED:
                session.exited_at = now

        await cleanup_expired_sessions(
            self._sessions,
            ttl=self._ttl,
            refresh_exit_state=_refresh_exit_state,
            close_session=lambda s: s.proc.close(),
        )

    @override
    def _get_declaration(self) -> FunctionDeclaration:
        return FunctionDeclaration(
            name="workspace_exec",
            description=("Execute a shell command in the shared executor workspace. "
                         "Use for workspace-level file operations and validation commands."),
            parameters=Schema(
                type=Type.OBJECT,
                required=["command"],
                properties={
                    "command": Schema(type=Type.STRING, description="Shell command to execute."),
                    "cwd": Schema(type=Type.STRING, description="Optional workspace-relative cwd."),
                    "env": Schema(type=Type.OBJECT, description="Optional environment overrides."),
                    "stdin": Schema(type=Type.STRING, description="Optional initial stdin text."),
                    "timeout_sec": Schema(type=Type.INTEGER, description="Maximum command runtime in seconds."),
                    "yield_time_ms": Schema(type=Type.INTEGER, description="How long to wait before returning."),
                    "background": Schema(type=Type.BOOLEAN, description="Start command in background session."),
                    "tty": Schema(type=Type.BOOLEAN, description="Allocate TTY for interactive commands."),
                },
            ),
            response=_exec_output_schema(
                "Result of workspace_exec. output is aggregated terminal text and may combine stdout/stderr."),
        )

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        inputs = _ExecInput.model_validate(args)
        command = (inputs.command or "").strip()
        if not command:
            raise ValueError("command is required")
        cwd = _normalize_cwd(inputs.cwd)
        timeout_raw = inputs.timeout_sec
        tty = inputs.tty
        yield_ms = inputs.yield_time_ms

        runtime, ws = await self._workspace(tool_context)
        spec = WorkspaceRunProgramSpec(
            cmd="sh",
            args=["-lc", command],
            env=inputs.env,
            cwd=cwd,
            stdin=inputs.stdin,
            timeout=_exec_timeout_seconds(timeout_raw),
        )

        if not self._supports_interactive(tool_context):
            if inputs.background or tty:
                raise ValueError("workspace_exec interactive sessions are not supported by the current executor")
            return await _run_one_shot(runtime, ws, spec, tool_context)

        if (not inputs.background) and (not tty) and yield_ms <= 0:
            return await _run_one_shot(runtime, ws, spec, tool_context)

        runner = self._runtime(tool_context).runner(tool_context)
        interactive_spec = WorkspaceRunProgramSpec(
            cmd=spec.cmd,
            args=spec.args,
            env=spec.env,
            cwd=spec.cwd,
            stdin=spec.stdin,
            timeout=spec.timeout,
            limits=spec.limits,
            tty=tty,
        )
        proc = await runner.start_program(tool_context, ws, interactive_spec)  # type: ignore[attr-defined]
        sid = proc.id()
        await self._put_session(sid, _ExecSession(proc=proc))

        if inputs.background and yield_ms <= 0:
            poll = await proc.poll(poll_line_limit(0))
        else:
            poll = await wait_for_program_output(
                proc,
                _exec_yield_seconds(inputs.background, yield_ms if yield_ms > 0 else None),
                poll_line_limit(0),
            )

        out = _poll_output(sid, poll)
        if poll.status == PROGRAM_STATUS_EXITED:
            try:
                await self._finalize_and_remove_session(sid)
            except Exception:  # pylint: disable=broad-except
                out["session_id"] = sid
        return out


class WorkspaceWriteStdinTool(BaseTool):
    """Write stdin to a running workspace_exec session or poll it."""

    def __init__(
        self,
        exec_tool: WorkspaceExecTool,
        *,
        filters_name: Optional[list[str]] = None,
        filters: Optional[list[BaseFilter]] = None,
    ):
        super().__init__(
            name="workspace_write_stdin",
            description="Write to a running workspace_exec session. Empty chars acts like poll.",
            filters_name=filters_name,
            filters=filters,
        )
        self._exec = exec_tool

    @override
    def _get_declaration(self) -> FunctionDeclaration:
        return FunctionDeclaration(
            name="workspace_write_stdin",
            description="Write to a running workspace_exec session. Empty chars acts like poll.",
            parameters=Schema(
                type=Type.OBJECT,
                required=["session_id"],
                properties={
                    "session_id": Schema(type=Type.STRING, description="Session id returned by workspace_exec."),
                    "chars": Schema(type=Type.STRING, description="Characters to write."),
                    "yield_time_ms": Schema(type=Type.INTEGER, description="Optional wait before polling output."),
                    "append_newline": Schema(type=Type.BOOLEAN, description="Append newline after chars."),
                },
            ),
            response=_exec_output_schema("Result of stdin write or follow-up poll."),
        )

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        inputs = _WriteInput.model_validate(args)
        session_id = require_non_empty(inputs.session_id, field_name="session_id")
        session = await self._exec._get_session(session_id)

        append_newline = inputs.append_newline
        if inputs.chars or append_newline:
            await session.proc.write(inputs.chars or "", append_newline)

        yield_ms = inputs.yield_time_ms or 0
        user_set_yield = inputs.yield_time_ms is not None
        poll = await wait_for_program_output(
            session.proc,
            _write_yield_seconds(yield_ms if user_set_yield else None),
            poll_line_limit(0),
        )
        out = _poll_output(session_id, poll)
        if poll.status == PROGRAM_STATUS_EXITED:
            try:
                await self._exec._finalize_and_remove_session(session_id)
            except Exception:  # pylint: disable=broad-except
                out["session_id"] = session_id
        return out


class WorkspaceKillSessionTool(BaseTool):
    """Terminate a running workspace_exec session."""

    def __init__(
        self,
        exec_tool: WorkspaceExecTool,
        *,
        filters_name: Optional[list[str]] = None,
        filters: Optional[list[BaseFilter]] = None,
    ):
        super().__init__(
            name="workspace_kill_session",
            description="Terminate a running workspace_exec session.",
            filters_name=filters_name,
            filters=filters,
        )
        self._exec = exec_tool

    @override
    def _get_declaration(self) -> FunctionDeclaration:
        return FunctionDeclaration(
            name="workspace_kill_session",
            description="Terminate a running workspace_exec session.",
            parameters=Schema(
                type=Type.OBJECT,
                required=["session_id"],
                properties={
                    "session_id": Schema(type=Type.STRING, description="Session id returned by workspace_exec."),
                },
            ),
            response=Schema(
                type=Type.OBJECT,
                required=["ok", "session_id", "status"],
                properties={
                    "ok": Schema(type=Type.BOOLEAN, description="True when session was removed."),
                    "session_id": Schema(type=Type.STRING, description="Session id."),
                    "status": Schema(type=Type.STRING, description="Final status."),
                },
            ),
        )

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        if "session_id" not in args:
            raise ValueError("session_id is required")
        session_id = args["session_id"]
        session = await self._exec._get_session(session_id)
        status = PROGRAM_STATUS_EXITED
        poll = await session.proc.poll(None)
        if poll.status == PROGRAM_STATUS_RUNNING:
            await session.proc.kill(DEFAULT_SESSION_KILL_SEC)
            status = "killed"
        await self._exec._finalize_and_remove_session(session_id)
        return {"ok": True, "session_id": session_id, "status": status}


def _exec_output_schema(description: str) -> Schema:
    return Schema(
        type=Type.OBJECT,
        description=description,
        required=["status", "offset", "next_offset"],
        properties={
            "status": Schema(type=Type.STRING, description="running or exited"),
            "output": Schema(type=Type.STRING, description="Aggregated terminal text observed for this call."),
            "exit_code": Schema(type=Type.INTEGER, description="Exit code when session has exited."),
            "session_id": Schema(type=Type.STRING, description="Interactive session id when still running."),
            "offset": Schema(type=Type.INTEGER, description="Start offset of returned output."),
            "next_offset": Schema(type=Type.INTEGER, description="Next output offset."),
        },
    )


def _poll_output(session_id: str, poll: ProgramPoll) -> dict[str, Any]:
    out: dict[str, Any] = {
        "status": poll.status,
        "output": poll.output,
        "offset": poll.offset,
        "next_offset": poll.next_offset,
    }
    if poll.exit_code is not None:
        out["exit_code"] = poll.exit_code
    if poll.status == PROGRAM_STATUS_RUNNING:
        out["session_id"] = session_id
    return out


async def _run_one_shot(
    runtime: BaseWorkspaceRuntime,
    workspace: Any,
    spec: WorkspaceRunProgramSpec,
    ctx: InvocationContext,
) -> dict[str, Any]:
    rr = await runtime.runner(ctx).run_program(workspace, spec, ctx)
    return {
        "status": PROGRAM_STATUS_EXITED,
        "output": _combine_output(rr.stdout, rr.stderr),
        "exit_code": rr.exit_code,
        "offset": 0,
        "next_offset": 0,
    }


def create_workspace_exec_tools(
    code_executor: BaseCodeExecutor,
    *,
    workspace_runtime: Optional[BaseWorkspaceRuntime] = None,
    workspace_runtime_resolver: Optional[WorkspaceRuntimeResolver] = None,
    session_ttl: float = DEFAULT_SESSION_TTL_SEC,
    filters_name: Optional[list[str]] = None,
    filters: Optional[list[BaseFilter]] = None,
) -> tuple[WorkspaceExecTool, WorkspaceWriteStdinTool, WorkspaceKillSessionTool]:
    """Create workspace_exec tool trio."""
    exec_tool = WorkspaceExecTool(
        code_executor=code_executor,
        workspace_runtime=workspace_runtime,
        workspace_runtime_resolver=workspace_runtime_resolver,
        session_ttl=session_ttl,
        filters_name=filters_name,
        filters=filters,
    )
    write_tool = WorkspaceWriteStdinTool(exec_tool, filters_name=filters_name, filters=filters)
    kill_tool = WorkspaceKillSessionTool(exec_tool, filters_name=filters_name, filters=filters)
    return exec_tool, write_tool, kill_tool
