# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Interactive skill execution tools.

* ``skill_exec``          — start an interactive session (SkillExecTool)
* ``skill_write_stdin``   — write stdin to a running session (WriteStdinTool)
* ``skill_poll_session``  — poll a session for new output (PollSessionTool)
* ``skill_kill_session``  — terminate and remove a session (KillSessionTool)

Sessions run real sub-processes inside the staged skill workspace.  When
``tty=True`` a POSIX pseudo-terminal is allocated so TTY-aware programs work
correctly (e.g. interactive shells, ncurses UIs).

Usage example::

    # 1. Start a long-running interactive command
    result = await skill_exec_tool.run(ctx, {
        "skill": "my_skill",
        "command": "python interactive.py",
        "yield_time_ms": 500,
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

import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing_extensions import override

from pydantic import BaseModel
from pydantic import Field
from trpc_agent_sdk.code_executors import BaseProgramRunner
from trpc_agent_sdk.code_executors import BaseProgramSession
from trpc_agent_sdk.code_executors import DEFAULT_EXEC_YIELD_MS
from trpc_agent_sdk.code_executors import DEFAULT_IO_YIELD_MS
from trpc_agent_sdk.code_executors import DEFAULT_SESSION_KILL_SEC
from trpc_agent_sdk.code_executors import DEFAULT_SESSION_TTL_SEC
from trpc_agent_sdk.code_executors import DIR_OUT
from trpc_agent_sdk.code_executors import DIR_RUNS
from trpc_agent_sdk.code_executors import DIR_SKILLS
from trpc_agent_sdk.code_executors import DIR_WORK
from trpc_agent_sdk.code_executors import ENV_OUTPUT_DIR
from trpc_agent_sdk.code_executors import ENV_SKILLS_DIR
from trpc_agent_sdk.code_executors import ENV_SKILL_NAME
from trpc_agent_sdk.code_executors import ENV_WORK_DIR
from trpc_agent_sdk.code_executors import PROGRAM_STATUS_EXITED
from trpc_agent_sdk.code_executors import WORKSPACE_ENV_DIR_KEY
from trpc_agent_sdk.code_executors import WorkspaceInfo
from trpc_agent_sdk.code_executors import WorkspaceInputSpec
from trpc_agent_sdk.code_executors import WorkspaceOutputSpec
from trpc_agent_sdk.code_executors import WorkspaceRunProgramSpec
from trpc_agent_sdk.code_executors import poll_line_limit
from trpc_agent_sdk.code_executors import wait_for_program_output
from trpc_agent_sdk.code_executors import yield_duration_ms
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema

from .._constants import SKILL_ARTIFACTS_STATE_KEY
from ._common import CreateWorkspaceNameCallback
from ._common import cleanup_expired_sessions
from ._common import default_create_ws_name_callback
from ._common import inline_json_schema_refs
from ._common import require_non_empty
from ._copy_stager import SkillStageRequest
from ._skill_run import SkillRunInput
from ._skill_run import SkillRunOutput
from ._skill_run import SkillRunTool
from ._skill_run import _filter_failed_empty_outputs
from ._skill_run import _select_primary_output
from ._skill_run import _truncate_output

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
    yield_time_ms: int = Field(default=0, description="Milliseconds to wait for initial output before returning")
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
    yield_time_ms: int = Field(default=0, description="Milliseconds to wait for new output")
    poll_lines: int = Field(default=0, description="Maximum output lines to return")


class PollSessionInput(BaseModel):
    """Input for skill_poll_session."""

    session_id: str = Field(..., description="Session id returned by skill_exec")
    yield_time_ms: int = Field(default=0, description="Milliseconds to wait for new output")
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


def _apply_artifacts_state_delta(ctx: InvocationContext, output: ExecOutput) -> None:
    """Store replayable artifact refs in state delta (Go execArtifactsStateDelta parity)."""
    tool_call_id = (ctx.function_call_id or "").strip()
    if not tool_call_id or output.result is None or not output.result.artifact_files:
        return

    artifacts: list[dict[str, Any]] = []
    for item in output.result.artifact_files:
        name = (item.name or "").strip()
        version = int(item.version)
        if not name or version < 0:
            continue
        artifacts.append({
            "name": name,
            "version": version,
            "ref": f"artifact://{name}@{version}",
        })
    if not artifacts:
        return
    ctx.actions.state_delta[SKILL_ARTIFACTS_STATE_KEY] = {
        "tool_call_id": tool_call_id,
        "artifacts": artifacts,
    }


# ---------------------------------------------------------------------------
# Internal session state
# ---------------------------------------------------------------------------


@dataclass
class _ExecSession:
    """Holds state for one running interactive skill session."""

    proc: BaseProgramSession
    ws: WorkspaceInfo
    in_data: ExecInput

    # Final state
    exit_code: Optional[int] = None
    exited_at: Optional[float] = None
    final_result: Optional[SkillRunOutput] = None
    finalized: bool = False

    async def yield_output(self, yield_time_ms: int, poll_lines: int) -> tuple[str, str, int, int]:
        """Wait *yield_time_ms* ms for new output then return a chunk.

        Returns ``(status, output_chunk, offset, next_offset)``.
        """
        poll = await wait_for_program_output(
            self.proc,
            yield_duration_ms(yield_time_ms, DEFAULT_EXEC_YIELD_MS),
            poll_line_limit(poll_lines),
        )
        if poll.exit_code is not None:
            self.exit_code = poll.exit_code
        if poll.status == _STATUS_EXITED and self.exited_at is None:
            self.exited_at = time.time()
        return poll.status, poll.output, poll.offset, poll.next_offset


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
    has_selection_prompt = any(phrase in lower for phrase in ("enter the number", "choose a number", "select a number"))
    if has_selection_prompt or _has_selection_items(output):
        return SessionInteraction(needs_input=True, kind=_INTERACTION_SELECTION, hint=hint)
    if (hint.endswith(":") or hint.endswith("?") or "press enter" in lower or "type your" in lower):
        return SessionInteraction(needs_input=True, kind=_INTERACTION_PROMPT, hint=hint)
    return None


# ---------------------------------------------------------------------------
# Workspace env helpers (mirrors LocalProgramRunner.run_program)
# ---------------------------------------------------------------------------


def _build_exec_env(ws: WorkspaceInfo, extra: dict[str, str]) -> dict[str, str]:
    """Build the merged environment for a subprocess in *ws*."""

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
        session_ttl: float = DEFAULT_SESSION_TTL_SEC,
        create_ws_name_cb: Optional[CreateWorkspaceNameCallback] = None,
    ):
        super().__init__(name="skill_exec",
                         description=("Start an interactive command inside a skill workspace. "
                                      "Use it when a skill command may prompt for stdin, selection, "
                                      "or TTY interaction. Shares the same workspace, inputs, outputs, "
                                      "and artifact semantics as skill_run."),
                         filters=filters)
        self._run_tool = run_tool
        self._ttl = session_ttl
        self._create_ws_name_cb = create_ws_name_cb or default_create_ws_name_callback
        self._sessions: dict[str, _ExecSession] = {}

    # ------------------------------------------------------------------
    # Declaration
    # ------------------------------------------------------------------

    @override
    def _get_declaration(self) -> FunctionDeclaration:
        params_schema = inline_json_schema_refs(ExecInput.model_json_schema())
        response_schema = inline_json_schema_refs(ExecOutput.model_json_schema())
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

    async def _put_session(self, sid: str, exec_session: _ExecSession) -> None:
        await self._gc_expired_sessions()
        self._sessions[sid] = exec_session

    async def _get_session(self, sid: str) -> _ExecSession:
        await self._gc_expired_sessions()
        session = self._sessions.get(sid)
        if session is None:
            raise ValueError(f"unknown session_id: {sid}")
        return session

    async def _remove_session(self, sid: str) -> _ExecSession:
        await self._gc_expired_sessions()
        session = self._sessions.pop(sid, None)
        if session is None:
            raise ValueError(f"unknown session_id: {sid}")
        return session

    async def _gc_expired_sessions(self) -> None:

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
            close_session=_close_session,
        )

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
        normalized_skill = inputs.skill.strip()
        normalized_command = inputs.command.strip()
        if not normalized_skill or not normalized_command:
            raise ValueError("skill and command are required")
        inputs = inputs.model_copy(update={"skill": normalized_skill, "command": normalized_command})

        if self._run_tool.require_skill_loaded and not self._run_tool._is_skill_loaded(tool_context, normalized_skill):
            raise ValueError(f"skill_exec requires skill_load first for {normalized_skill!r}")

        repository = self._run_tool._get_repository(tool_context)

        # Workspace creation
        workspace_runtime = repository.workspace_runtime
        manager = workspace_runtime.manager(tool_context)
        workspace_id = self._create_ws_name_cb(tool_context)
        ws = await manager.create_workspace(workspace_id, tool_context)

        # Stage skill via the same pluggable stager used by SkillRunTool
        stage_result = await self._run_tool.skill_stager.stage_skill(
            SkillStageRequest(
                skill_name=normalized_skill,
                repository=repository,
                workspace=ws,
                ctx=tool_context,
                timeout=self._run_tool._timeout,
            ))
        workspace_skill_dir = stage_result.workspace_skill_dir

        if inputs.inputs:
            fs = workspace_runtime.fs(tool_context)
            await fs.stage_inputs(ws, inputs.inputs, tool_context)

        # Resolve cwd and env
        rel_cwd = self._run_tool._resolve_cwd(inputs.cwd, workspace_skill_dir)

        extra_env: dict[str, str] = dict(inputs.env)
        if ENV_SKILL_NAME not in extra_env:
            extra_env[ENV_SKILL_NAME] = normalized_skill
        merged_env = _build_exec_env(ws, extra_env)

        # Start interactive program session via runtime runner.
        runner = workspace_runtime.runner(tool_context)
        start_program = getattr(runner, "start_program", None)
        if start_program is None:
            raise ValueError("skill_exec is not supported by the current executor")
        sid = str(uuid.uuid4())
        exec_session = await _start_session(
            runner=runner,
            tool_context=tool_context,
            inputs=inputs,
            ws=ws,
            rel_cwd=rel_cwd,
            env=merged_env,
        )
        await self._put_session(sid, exec_session)

        yield_time_ms = inputs.yield_time_ms or DEFAULT_EXEC_YIELD_MS
        status, chunk, offset, next_offset = await exec_session.yield_output(yield_time_ms, inputs.poll_lines)

        # Attempt to collect final result if already exited
        final_result = None
        if status == _STATUS_EXITED and not exec_session.finalized:
            final_result = await _collect_final_result(tool_context, exec_session, self._run_tool)

        out = ExecOutput(
            status=status,
            session_id=sid,
            output=chunk,
            offset=offset,
            next_offset=next_offset,
            exit_code=exec_session.exit_code,
            interaction=_detect_interaction(status, chunk),
            result=final_result,
        )
        _apply_artifacts_state_delta(tool_context, out)
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
        params_schema = inline_json_schema_refs(WriteStdinInput.model_json_schema())
        response_schema = inline_json_schema_refs(ExecOutput.model_json_schema())
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
        normalized_session_id = require_non_empty(inputs.session_id, field_name="session_id")
        inputs = inputs.model_copy(update={"session_id": normalized_session_id})

        exec_session = await self._exec._get_session(inputs.session_id)

        if inputs.chars or inputs.submit:
            await _write_stdin(exec_session, inputs.chars, submit=inputs.submit)

        yield_time_ms = inputs.yield_time_ms or DEFAULT_IO_YIELD_MS
        status, chunk, offset, next_offset = await exec_session.yield_output(yield_time_ms, inputs.poll_lines)

        final_result = None
        if status == _STATUS_EXITED and not exec_session.finalized:
            final_result = await _collect_final_result(tool_context, exec_session, self._exec._run_tool)

        out = ExecOutput(
            status=status,
            session_id=normalized_session_id,
            output=chunk,
            offset=offset,
            next_offset=next_offset,
            exit_code=exec_session.exit_code,
            interaction=_detect_interaction(status, chunk),
            result=final_result,
        )
        _apply_artifacts_state_delta(tool_context, out)
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
        params_schema = inline_json_schema_refs(PollSessionInput.model_json_schema())
        response_schema = inline_json_schema_refs(ExecOutput.model_json_schema())
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
        normalized_session_id = require_non_empty(inputs.session_id, field_name="session_id")
        inputs = inputs.model_copy(update={"session_id": normalized_session_id})

        exec_session = await self._exec._get_session(inputs.session_id)

        yield_time_ms = inputs.yield_time_ms or DEFAULT_IO_YIELD_MS
        status, chunk, offset, next_offset = await exec_session.yield_output(yield_time_ms, inputs.poll_lines)

        final_result = None
        if status == _STATUS_EXITED and not exec_session.finalized:
            final_result = await _collect_final_result(tool_context, exec_session, self._exec._run_tool)

        out = ExecOutput(
            status=status,
            session_id=normalized_session_id,
            output=chunk,
            offset=offset,
            next_offset=next_offset,
            exit_code=exec_session.exit_code,
            interaction=_detect_interaction(status, chunk),
            result=final_result,
        )
        _apply_artifacts_state_delta(tool_context, out)
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
        params_schema = inline_json_schema_refs(KillSessionInput.model_json_schema())
        response_schema = inline_json_schema_refs(SessionKillOutput.model_json_schema())
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
        normalized_session_id = require_non_empty(inputs.session_id, field_name="session_id")
        inputs = inputs.model_copy(update={"session_id": normalized_session_id})
        exec_session = await self._exec._get_session(normalized_session_id)

        final_status = _STATUS_EXITED

        poll = await exec_session.proc.poll(None)
        if poll.status == _STATUS_RUNNING:
            try:
                await exec_session.proc.kill(DEFAULT_SESSION_KILL_SEC)
            except Exception:  # pylint: disable=broad-except
                pass
            final_status = "killed"

        await self._exec._remove_session(normalized_session_id)
        await _close_session(exec_session)

        out = SessionKillOutput(
            ok=True,
            session_id=normalized_session_id,
            status=final_status,
        )
        return out.model_dump()


# ---------------------------------------------------------------------------
# Factory helper
# ---------------------------------------------------------------------------


def create_exec_tools(
    run_tool: SkillRunTool,
    filters: Optional[List[BaseFilter]] = None,
    session_ttl: float = DEFAULT_SESSION_TTL_SEC,
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
    *,
    runner: BaseProgramRunner,
    tool_context: InvocationContext,
    inputs: ExecInput,
    ws: WorkspaceInfo,
    rel_cwd: str,
    env: dict[str, str],
) -> _ExecSession:
    """Start a ProgramSession via runtime runner."""
    spec = WorkspaceRunProgramSpec(
        cmd="bash",
        args=["-c", inputs.command],
        env=env,
        cwd=rel_cwd,
        stdin=inputs.stdin,
        timeout=float(inputs.timeout or 0),
        tty=inputs.tty,
    )
    proc = await runner.start_program(tool_context, ws, spec)
    return _ExecSession(proc=proc, ws=ws, in_data=inputs)


async def _write_stdin(exec_session: _ExecSession, chars: str, submit: bool) -> None:
    """Write *chars* (and optionally a newline) to the session's stdin."""
    try:
        await exec_session.proc.write(chars, submit)
    except Exception as ex:  # pylint: disable=broad-except
        logger.debug("skill_exec: write to stdin failed: %s", ex)


async def _collect_final_result(
    ctx: InvocationContext,
    exec_session: _ExecSession,
    run_tool: SkillRunTool,
) -> Optional[SkillRunOutput]:
    """Collect output files and build the final :class:`SkillRunOutput`."""
    if exec_session.finalized:
        return exec_session.final_result

    in_data = exec_session.in_data
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
        files, manifest = await run_tool._prepare_outputs(ctx, exec_session.ws, fake_run_input)
    except Exception as ex:  # pylint: disable=broad-except
        logger.warning("skill_exec: collect outputs failed: %s", ex)
        files, manifest = [], None

    try:
        run_result = await exec_session.proc.run_result()
        total_out = (run_result.stdout or "") + (run_result.stderr or "")
        exit_code = run_result.exit_code
    except Exception:  # pylint: disable=broad-except
        run_log = await exec_session.proc.log(None, None)
        total_out = run_log.output or ""
        exit_code = exec_session.exit_code or 0

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
        await run_tool._attach_artifacts_if_requested(ctx, exec_session.ws, fake_run_input, result, files)
    except Exception as ex:  # pylint: disable=broad-except
        logger.warning("skill_exec: attach artifacts failed: %s", ex)

    if manifest:
        run_tool._merge_manifest_artifact_refs(manifest, result)

    exec_session.final_result = result
    exec_session.finalized = True
    return result


async def _close_session(exec_session: _ExecSession) -> None:
    """Release program session resources."""
    try:
        await exec_session.proc.close()
    except Exception:  # pylint: disable=broad-except
        pass
