# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.skills.tools._skill_exec.

Covers:
- Pydantic I/O models: ExecInput, WriteStdinInput, PollSessionInput,
  KillSessionInput, SessionInteraction, ExecOutput, SessionKillOutput
- Helper functions: _last_non_empty_line, _has_selection_items,
  _detect_interaction, _build_exec_env, _resolve_abs_cwd
- SkillExecTool: session management, declarations
- create_exec_tools factory
- _close_session
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trpc_agent_sdk.skills.tools._skill_exec import (
    DEFAULT_EXEC_YIELD_MS,
    DEFAULT_IO_YIELD_MS,
    DEFAULT_POLL_LINES,
    DEFAULT_SESSION_TTL,
    ExecInput,
    ExecOutput,
    KillSessionInput,
    KillSessionTool,
    PollSessionInput,
    PollSessionTool,
    SessionInteraction,
    SessionKillOutput,
    SkillExecTool,
    WriteStdinInput,
    WriteStdinTool,
    _close_session,
    _detect_interaction,
    _has_selection_items,
    _last_non_empty_line,
    _resolve_abs_cwd,
    create_exec_tools,
)


# ---------------------------------------------------------------------------
# _last_non_empty_line
# ---------------------------------------------------------------------------

class TestLastNonEmptyLine:
    def test_normal(self):
        assert _last_non_empty_line("line1\nline2\nline3") == "line3"

    def test_trailing_empty_lines(self):
        assert _last_non_empty_line("line1\nline2\n\n\n") == "line2"

    def test_all_empty(self):
        assert _last_non_empty_line("\n\n") == ""

    def test_empty_string(self):
        assert _last_non_empty_line("") == ""

    def test_single_line(self):
        assert _last_non_empty_line("hello") == "hello"

    def test_whitespace_lines(self):
        assert _last_non_empty_line("  \n  \n  content  \n  ") == "content"


# ---------------------------------------------------------------------------
# _has_selection_items
# ---------------------------------------------------------------------------

class TestHasSelectionItems:
    def test_numbered_list(self):
        text = "Choose an option:\n1. Option A\n2. Option B\n3. Option C"
        assert _has_selection_items(text) is True

    def test_numbered_paren(self):
        text = "1) Option A\n2) Option B"
        assert _has_selection_items(text) is True

    def test_no_numbers(self):
        text = "No numbered items here"
        assert _has_selection_items(text) is False

    def test_single_number(self):
        text = "1. Only one item"
        assert _has_selection_items(text) is False

    def test_empty(self):
        assert _has_selection_items("") is False


# ---------------------------------------------------------------------------
# _detect_interaction
# ---------------------------------------------------------------------------

class TestDetectInteraction:
    def test_exited_returns_none(self):
        assert _detect_interaction("exited", "some output") is None

    def test_empty_output_returns_none(self):
        assert _detect_interaction("running", "") is None

    def test_colon_prompt(self):
        result = _detect_interaction("running", "Enter your name:")
        assert result is not None
        assert result.needs_input is True
        assert result.kind == "prompt"

    def test_question_mark_prompt(self):
        result = _detect_interaction("running", "Continue?")
        assert result is not None
        assert result.needs_input is True

    def test_press_enter(self):
        result = _detect_interaction("running", "Press Enter to continue")
        assert result is not None
        assert result.needs_input is True

    def test_selection_detection(self):
        text = "Choose a number:\n1. Option A\n2. Option B\nEnter the number:"
        result = _detect_interaction("running", text)
        assert result is not None
        assert result.kind == "selection"

    def test_normal_output(self):
        result = _detect_interaction("running", "Processing data...\nDone.")
        assert result is None

    def test_type_your_prompt(self):
        result = _detect_interaction("running", "Type your answer here")
        assert result is not None
        assert result.needs_input is True


# ---------------------------------------------------------------------------
# _resolve_abs_cwd
# ---------------------------------------------------------------------------

class TestResolveAbsCwd:
    def test_relative_cwd(self, tmp_path):
        result = _resolve_abs_cwd(str(tmp_path), "sub")
        assert os.path.isabs(result)
        assert os.path.isdir(result)

    def test_absolute_cwd(self, tmp_path):
        result = _resolve_abs_cwd(str(tmp_path), str(tmp_path / "abs"))
        assert result == str(tmp_path / "abs")

    def test_empty_cwd(self, tmp_path):
        result = _resolve_abs_cwd(str(tmp_path), "")
        assert os.path.isabs(result)


# ---------------------------------------------------------------------------
# Pydantic I/O models
# ---------------------------------------------------------------------------

class TestExecModels:
    def test_exec_input_required(self):
        inp = ExecInput(skill="test", command="ls")
        assert inp.skill == "test"
        assert inp.command == "ls"
        assert inp.tty is False
        assert inp.yield_ms == 0

    def test_write_stdin_input(self):
        inp = WriteStdinInput(session_id="abc")
        assert inp.session_id == "abc"
        assert inp.chars == ""
        assert inp.submit is False

    def test_poll_session_input(self):
        inp = PollSessionInput(session_id="abc")
        assert inp.session_id == "abc"

    def test_kill_session_input(self):
        inp = KillSessionInput(session_id="abc")
        assert inp.session_id == "abc"

    def test_session_interaction(self):
        si = SessionInteraction(needs_input=True, kind="prompt", hint="Enter:")
        assert si.needs_input is True
        assert si.kind == "prompt"

    def test_exec_output_defaults(self):
        out = ExecOutput()
        assert out.status == "running"
        assert out.session_id == ""
        assert out.output == ""
        assert out.exit_code is None

    def test_session_kill_output(self):
        out = SessionKillOutput(ok=True, session_id="abc", status="killed")
        assert out.ok is True


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestExecConstants:
    def test_defaults(self):
        assert DEFAULT_EXEC_YIELD_MS == 300
        assert DEFAULT_IO_YIELD_MS == 100
        assert DEFAULT_POLL_LINES == 50
        assert DEFAULT_SESSION_TTL == 300.0


# ---------------------------------------------------------------------------
# SkillExecTool — session management
# ---------------------------------------------------------------------------

class TestSkillExecToolSessions:
    def _make_exec_tool(self):
        run_tool = MagicMock()
        run_tool._repository = MagicMock()
        run_tool._timeout = 300.0
        return SkillExecTool(run_tool)

    async def test_put_and_get_session(self):
        tool = self._make_exec_tool()
        mock_session = MagicMock()
        mock_session.exited_at = None
        await tool._put_session("s1", mock_session)
        result = await tool.get_session("s1")
        assert result is mock_session

    async def test_get_unknown_session_raises(self):
        tool = self._make_exec_tool()
        with pytest.raises(ValueError, match="unknown session_id"):
            await tool.get_session("nonexistent")

    async def test_remove_session(self):
        tool = self._make_exec_tool()
        mock_session = MagicMock()
        mock_session.exited_at = None
        await tool._put_session("s1", mock_session)
        result = await tool.remove_session("s1")
        assert result is mock_session

    async def test_remove_unknown_session_raises(self):
        tool = self._make_exec_tool()
        with pytest.raises(ValueError, match="unknown session_id"):
            await tool.remove_session("nonexistent")

    def test_declaration(self):
        tool = self._make_exec_tool()
        decl = tool._get_declaration()
        assert decl.name == "skill_exec"


# ---------------------------------------------------------------------------
# WriteStdinTool, PollSessionTool, KillSessionTool — declarations
# ---------------------------------------------------------------------------

class TestSubToolDeclarations:
    def _make_exec_tool(self):
        run_tool = MagicMock()
        run_tool._repository = MagicMock()
        run_tool._timeout = 300.0
        return SkillExecTool(run_tool)

    def test_write_stdin_declaration(self):
        exec_tool = self._make_exec_tool()
        tool = WriteStdinTool(exec_tool)
        decl = tool._get_declaration()
        assert decl.name == "skill_write_stdin"

    def test_poll_session_declaration(self):
        exec_tool = self._make_exec_tool()
        tool = PollSessionTool(exec_tool)
        decl = tool._get_declaration()
        assert decl.name == "skill_poll_session"

    def test_kill_session_declaration(self):
        exec_tool = self._make_exec_tool()
        tool = KillSessionTool(exec_tool)
        decl = tool._get_declaration()
        assert decl.name == "skill_kill_session"


# ---------------------------------------------------------------------------
# create_exec_tools
# ---------------------------------------------------------------------------

class TestCreateExecTools:
    def test_creates_four_tools(self):
        run_tool = MagicMock()
        run_tool._repository = MagicMock()
        run_tool._timeout = 300.0
        result = create_exec_tools(run_tool)
        assert len(result) == 4
        exec_tool, write_tool, poll_tool, kill_tool = result
        assert isinstance(exec_tool, SkillExecTool)
        assert isinstance(write_tool, WriteStdinTool)
        assert isinstance(poll_tool, PollSessionTool)
        assert isinstance(kill_tool, KillSessionTool)

    def test_custom_ttl(self):
        run_tool = MagicMock()
        run_tool._repository = MagicMock()
        run_tool._timeout = 300.0
        exec_tool, *_ = create_exec_tools(run_tool, session_ttl=60.0)
        assert exec_tool._ttl == 60.0


# ---------------------------------------------------------------------------
# _close_session
# ---------------------------------------------------------------------------

class TestCloseSession:
    def test_close_with_reader_task(self):
        sess = MagicMock()
        sess.reader_task = MagicMock()
        sess.reader_task.done = MagicMock(return_value=False)
        sess.master_fd = None
        sess.proc.stdin = None
        _close_session(sess)
        sess.reader_task.cancel.assert_called_once()

    def test_close_with_master_fd(self):
        sess = MagicMock()
        sess.reader_task = None
        sess.master_fd = 42
        sess.proc.stdin = None
        with patch("os.close") as mock_close:
            _close_session(sess)
            mock_close.assert_called_once_with(42)
        assert sess.master_fd is None

    def test_close_with_stdin(self):
        sess = MagicMock()
        sess.reader_task = None
        sess.master_fd = None
        sess.proc.stdin = MagicMock()
        sess.proc.stdin.is_closing = MagicMock(return_value=False)
        _close_session(sess)
        sess.proc.stdin.close.assert_called_once()

    def test_close_already_done(self):
        sess = MagicMock()
        sess.reader_task = MagicMock()
        sess.reader_task.done = MagicMock(return_value=True)
        sess.master_fd = None
        sess.proc.stdin = None
        _close_session(sess)
        sess.reader_task.cancel.assert_not_called()

    def test_close_no_reader_task(self):
        sess = MagicMock()
        sess.reader_task = None
        sess.master_fd = None
        sess.proc.stdin = None
        _close_session(sess)

    def test_close_master_fd_oserror(self):
        sess = MagicMock()
        sess.reader_task = None
        sess.master_fd = 42
        sess.proc.stdin = None
        with patch("os.close", side_effect=OSError("test")):
            _close_session(sess)
        assert sess.master_fd is None

    def test_close_stdin_closing(self):
        sess = MagicMock()
        sess.reader_task = None
        sess.master_fd = None
        sess.proc.stdin = MagicMock()
        sess.proc.stdin.is_closing = MagicMock(return_value=True)
        _close_session(sess)
        sess.proc.stdin.close.assert_not_called()


# ---------------------------------------------------------------------------
# _ExecSession
# ---------------------------------------------------------------------------

class TestExecSession:
    async def test_append_and_total_output(self):
        from trpc_agent_sdk.skills.tools._skill_exec import _ExecSession
        proc = MagicMock()
        proc.returncode = None
        ws = MagicMock()
        in_data = ExecInput(skill="test", command="echo hello")
        sess = _ExecSession(proc=proc, ws=ws, in_data=in_data)
        await sess.append_output("hello ")
        await sess.append_output("world")
        total = await sess.total_output()
        assert total == "hello world"

    async def test_yield_output_exited(self):
        from trpc_agent_sdk.skills.tools._skill_exec import _ExecSession
        proc = MagicMock()
        proc.returncode = 0
        ws = MagicMock()
        in_data = ExecInput(skill="test", command="echo hello")
        sess = _ExecSession(proc=proc, ws=ws, in_data=in_data)
        await sess.append_output("output text")
        status, chunk, offset, next_offset = await sess.yield_output(50, 0)
        assert status == "exited"
        assert "output text" in chunk
        assert sess.exit_code == 0


# ---------------------------------------------------------------------------
# _build_exec_env
# ---------------------------------------------------------------------------

class TestBuildExecEnv:
    def test_builds_env(self, tmp_path):
        from trpc_agent_sdk.skills.tools._skill_exec import _build_exec_env
        ws = MagicMock()
        ws.path = str(tmp_path)
        env = _build_exec_env(ws, {"MY_VAR": "test"})
        assert "MY_VAR" in env
        assert env["MY_VAR"] == "test"

    def test_sets_workspace_dirs(self, tmp_path):
        from trpc_agent_sdk.skills.tools._skill_exec import _build_exec_env
        ws = MagicMock()
        ws.path = str(tmp_path)
        env = _build_exec_env(ws, {})
        assert any("skills" in v.lower() or "SKILLS" in k for k, v in env.items())


# ---------------------------------------------------------------------------
# GC expired sessions
# ---------------------------------------------------------------------------

class TestGcExpired:
    async def test_gc_expired_sessions(self):
        import time
        run_tool = MagicMock()
        run_tool._repository = MagicMock()
        run_tool._timeout = 300.0
        tool = SkillExecTool(run_tool, session_ttl=1.0)

        mock_session = MagicMock()
        mock_session.exited_at = time.time() - 100
        mock_session.reader_task = None
        mock_session.master_fd = None
        mock_session.proc.stdin = None
        tool._sessions["expired_session"] = mock_session

        # gc is called within put_session
        new_session = MagicMock()
        new_session.exited_at = None
        await tool._put_session("new", new_session)

        assert "expired_session" not in tool._sessions
        assert "new" in tool._sessions

    async def test_gc_ttl_zero_skips(self):
        run_tool = MagicMock()
        run_tool._repository = MagicMock()
        run_tool._timeout = 300.0
        tool = SkillExecTool(run_tool, session_ttl=0)

        mock_session = MagicMock()
        mock_session.exited_at = 1.0
        tool._sessions["s1"] = mock_session

        new_session = MagicMock()
        new_session.exited_at = None
        await tool._put_session("s2", new_session)

        assert "s1" in tool._sessions


# ---------------------------------------------------------------------------
# _write_stdin helper
# ---------------------------------------------------------------------------

class TestWriteStdin:
    async def test_write_pipe(self):
        from trpc_agent_sdk.skills.tools._skill_exec import _write_stdin, _ExecSession
        proc = MagicMock()
        proc.returncode = None
        proc.stdin = MagicMock()
        proc.stdin.write = MagicMock()
        proc.stdin.drain = AsyncMock()
        ws = MagicMock()
        in_data = ExecInput(skill="test", command="cat")
        sess = _ExecSession(proc=proc, ws=ws, in_data=in_data)

        await _write_stdin(sess, "hello", submit=True)
        proc.stdin.write.assert_called_once()
        written = proc.stdin.write.call_args[0][0]
        assert b"hello\n" == written

    async def test_write_pipe_no_submit(self):
        from trpc_agent_sdk.skills.tools._skill_exec import _write_stdin, _ExecSession
        proc = MagicMock()
        proc.returncode = None
        proc.stdin = MagicMock()
        proc.stdin.write = MagicMock()
        proc.stdin.drain = AsyncMock()
        ws = MagicMock()
        in_data = ExecInput(skill="test", command="cat")
        sess = _ExecSession(proc=proc, ws=ws, in_data=in_data)

        await _write_stdin(sess, "hello", submit=False)
        written = proc.stdin.write.call_args[0][0]
        assert b"hello" == written

    async def test_write_pipe_error_handled(self):
        from trpc_agent_sdk.skills.tools._skill_exec import _write_stdin, _ExecSession
        proc = MagicMock()
        proc.returncode = None
        proc.stdin = MagicMock()
        proc.stdin.write = MagicMock(side_effect=RuntimeError("broken"))
        proc.stdin.drain = AsyncMock()
        ws = MagicMock()
        in_data = ExecInput(skill="test", command="cat")
        sess = _ExecSession(proc=proc, ws=ws, in_data=in_data)

        await _write_stdin(sess, "test", submit=False)

    async def test_write_pty(self):
        from trpc_agent_sdk.skills.tools._skill_exec import _write_stdin, _ExecSession
        proc = MagicMock()
        proc.returncode = None
        proc.stdin = None
        ws = MagicMock()
        in_data = ExecInput(skill="test", command="cat")
        sess = _ExecSession(proc=proc, ws=ws, in_data=in_data)
        sess.master_fd = 42

        with patch("os.write") as mock_write:
            await _write_stdin(sess, "hello", submit=True)
            mock_write.assert_called_once_with(42, b"hello\n")

    async def test_write_pty_error(self):
        from trpc_agent_sdk.skills.tools._skill_exec import _write_stdin, _ExecSession
        proc = MagicMock()
        proc.returncode = None
        proc.stdin = None
        ws = MagicMock()
        in_data = ExecInput(skill="test", command="cat")
        sess = _ExecSession(proc=proc, ws=ws, in_data=in_data)
        sess.master_fd = 42

        with patch("os.write", side_effect=OSError("broken")):
            await _write_stdin(sess, "hello", submit=False)

    async def test_write_no_stdin_no_pty(self):
        from trpc_agent_sdk.skills.tools._skill_exec import _write_stdin, _ExecSession
        proc = MagicMock()
        proc.returncode = None
        proc.stdin = None
        ws = MagicMock()
        in_data = ExecInput(skill="test", command="cat")
        sess = _ExecSession(proc=proc, ws=ws, in_data=in_data)
        await _write_stdin(sess, "hello", submit=True)


# ---------------------------------------------------------------------------
# _collect_final_result
# ---------------------------------------------------------------------------

class TestCollectFinalResult:
    async def test_already_finalized(self):
        from trpc_agent_sdk.skills.tools._skill_exec import _collect_final_result, _ExecSession
        from trpc_agent_sdk.skills.tools._skill_run import SkillRunOutput
        proc = MagicMock()
        proc.returncode = 0
        ws = MagicMock()
        in_data = ExecInput(skill="test", command="echo")
        sess = _ExecSession(proc=proc, ws=ws, in_data=in_data)
        sess.finalized = True
        expected = SkillRunOutput(stdout="cached")
        sess.final_result = expected

        ctx = MagicMock()
        run_tool = MagicMock()
        result = await _collect_final_result(ctx, sess, run_tool)
        assert result is expected

    async def test_collect_with_outputs(self):
        from trpc_agent_sdk.skills.tools._skill_exec import _collect_final_result, _ExecSession
        from trpc_agent_sdk.skills.tools._skill_run import SkillRunFile
        proc = MagicMock()
        proc.returncode = 0
        ws = MagicMock()
        in_data = ExecInput(skill="test", command="echo", output_files=["out/*.txt"])
        sess = _ExecSession(proc=proc, ws=ws, in_data=in_data)
        sess.exit_code = 0
        await sess.append_output("test output")

        ctx = MagicMock()
        ctx.artifact_service = None
        run_tool = MagicMock()
        run_tool._prepare_outputs = AsyncMock(return_value=([], None))
        run_tool._attach_artifacts_if_requested = AsyncMock()
        run_tool._merge_manifest_artifact_refs = MagicMock()

        result = await _collect_final_result(ctx, sess, run_tool)
        assert result is not None
        assert result.stdout == "test output"
        assert sess.finalized is True

    async def test_collect_prepare_outputs_error(self):
        from trpc_agent_sdk.skills.tools._skill_exec import _collect_final_result, _ExecSession
        proc = MagicMock()
        proc.returncode = 0
        ws = MagicMock()
        in_data = ExecInput(skill="test", command="echo")
        sess = _ExecSession(proc=proc, ws=ws, in_data=in_data)
        sess.exit_code = 0
        await sess.append_output("output")

        ctx = MagicMock()
        ctx.artifact_service = None
        run_tool = MagicMock()
        run_tool._prepare_outputs = AsyncMock(side_effect=RuntimeError("fail"))
        run_tool._attach_artifacts_if_requested = AsyncMock()
        run_tool._merge_manifest_artifact_refs = MagicMock()

        result = await _collect_final_result(ctx, sess, run_tool)
        assert result is not None
        assert sess.finalized is True


# ---------------------------------------------------------------------------
# _ExecSession — yield_output more tests
# ---------------------------------------------------------------------------

class TestExecSessionYieldOutput:
    async def test_yield_with_poll_lines_limit(self):
        from trpc_agent_sdk.skills.tools._skill_exec import _ExecSession
        proc = MagicMock()
        proc.returncode = 0
        ws = MagicMock()
        in_data = ExecInput(skill="test", command="echo")
        sess = _ExecSession(proc=proc, ws=ws, in_data=in_data)

        long_output = "\n".join([f"line {i}" for i in range(100)])
        await sess.append_output(long_output)

        status, chunk, offset, next_offset = await sess.yield_output(10, 5)
        lines = chunk.strip().split("\n")
        assert len(lines) <= 5

    async def test_yield_incremental(self):
        from trpc_agent_sdk.skills.tools._skill_exec import _ExecSession
        proc = MagicMock()
        proc.returncode = 0
        ws = MagicMock()
        in_data = ExecInput(skill="test", command="echo")
        sess = _ExecSession(proc=proc, ws=ws, in_data=in_data)

        await sess.append_output("first")
        _, chunk1, _, _ = await sess.yield_output(10, 0)
        assert "first" in chunk1

        await sess.append_output("second")
        _, chunk2, _, _ = await sess.yield_output(10, 0)
        assert "second" in chunk2
        assert "first" not in chunk2


# ---------------------------------------------------------------------------
# _read_pipe
# ---------------------------------------------------------------------------

class TestReadPipe:
    async def test_read_pipe_empty(self):
        from trpc_agent_sdk.skills.tools._skill_exec import _read_pipe, _ExecSession
        proc = MagicMock()
        proc.returncode = 0
        proc.wait = AsyncMock()
        ws = MagicMock()
        in_data = ExecInput(skill="test", command="echo")
        sess = _ExecSession(proc=proc, ws=ws, in_data=in_data)

        stream = AsyncMock()
        stream.read = AsyncMock(return_value=b"")

        await _read_pipe(sess, stream)

    async def test_read_pipe_with_data(self):
        from trpc_agent_sdk.skills.tools._skill_exec import _read_pipe, _ExecSession
        proc = MagicMock()
        proc.returncode = 0
        proc.wait = AsyncMock()
        ws = MagicMock()
        in_data = ExecInput(skill="test", command="echo")
        sess = _ExecSession(proc=proc, ws=ws, in_data=in_data)

        stream = AsyncMock()
        stream.read = AsyncMock(side_effect=[b"hello", b""])

        await _read_pipe(sess, stream)
        total = await sess.total_output()
        assert "hello" in total


# ---------------------------------------------------------------------------
# KillSessionTool._run_async_impl
# ---------------------------------------------------------------------------

class TestKillSessionToolRun:
    def _make_exec_tool(self):
        run_tool = MagicMock()
        run_tool._repository = MagicMock()
        run_tool._timeout = 300.0
        return SkillExecTool(run_tool)

    async def test_kill_exited_session(self):
        exec_tool = self._make_exec_tool()
        kill_tool = KillSessionTool(exec_tool)

        mock_session = MagicMock()
        mock_session.proc.returncode = 0
        mock_session.exited_at = None
        mock_session.reader_task = None
        mock_session.master_fd = None
        mock_session.proc.stdin = None

        await exec_tool._put_session("s1", mock_session)

        ctx = MagicMock()
        result = await kill_tool._run_async_impl(
            tool_context=ctx,
            args={"session_id": "s1"},
        )
        assert result["ok"] is True
        assert result["status"] == "exited"

    async def test_kill_running_session(self):
        exec_tool = self._make_exec_tool()
        kill_tool = KillSessionTool(exec_tool)

        mock_session = MagicMock()
        mock_session.proc.returncode = None
        mock_session.proc.kill = MagicMock()
        mock_session.proc.wait = AsyncMock()
        mock_session.exited_at = None
        mock_session.reader_task = None
        mock_session.master_fd = None
        mock_session.proc.stdin = None

        await exec_tool._put_session("s1", mock_session)

        ctx = MagicMock()
        result = await kill_tool._run_async_impl(
            tool_context=ctx,
            args={"session_id": "s1"},
        )
        assert result["ok"] is True
        assert result["status"] == "killed"
        mock_session.proc.kill.assert_called_once()

    async def test_kill_invalid_args_raises(self):
        exec_tool = self._make_exec_tool()
        kill_tool = KillSessionTool(exec_tool)
        ctx = MagicMock()
        with pytest.raises(ValueError, match="Invalid"):
            await kill_tool._run_async_impl(tool_context=ctx, args={})

    async def test_kill_unknown_session_raises(self):
        exec_tool = self._make_exec_tool()
        kill_tool = KillSessionTool(exec_tool)
        ctx = MagicMock()
        with pytest.raises(ValueError, match="unknown session_id"):
            await kill_tool._run_async_impl(
                tool_context=ctx,
                args={"session_id": "nonexistent"},
            )


# ---------------------------------------------------------------------------
# PollSessionTool._run_async_impl
# ---------------------------------------------------------------------------

class TestPollSessionToolRun:
    async def test_poll_running_session(self):
        run_tool = MagicMock()
        run_tool._repository = MagicMock()
        run_tool._timeout = 300.0
        exec_tool = SkillExecTool(run_tool)
        poll_tool = PollSessionTool(exec_tool)

        from trpc_agent_sdk.skills.tools._skill_exec import _ExecSession
        proc = MagicMock()
        proc.returncode = 0
        ws = MagicMock()
        in_data = ExecInput(skill="test", command="echo")
        sess = _ExecSession(proc=proc, ws=ws, in_data=in_data)
        await sess.append_output("poll output")

        await exec_tool._put_session("s1", sess)

        ctx = MagicMock()
        ctx.artifact_service = None
        result = await poll_tool._run_async_impl(
            tool_context=ctx,
            args={"session_id": "s1", "yield_ms": 10},
        )
        assert result["status"] == "exited"
        assert "poll output" in result["output"]

    async def test_poll_invalid_args_raises(self):
        run_tool = MagicMock()
        run_tool._repository = MagicMock()
        run_tool._timeout = 300.0
        exec_tool = SkillExecTool(run_tool)
        poll_tool = PollSessionTool(exec_tool)
        ctx = MagicMock()
        with pytest.raises(ValueError, match="Invalid"):
            await poll_tool._run_async_impl(tool_context=ctx, args={})


# ---------------------------------------------------------------------------
# WriteStdinTool._run_async_impl
# ---------------------------------------------------------------------------

class TestWriteStdinToolRun:
    async def test_write_and_poll(self):
        run_tool = MagicMock()
        run_tool._repository = MagicMock()
        run_tool._timeout = 300.0
        exec_tool = SkillExecTool(run_tool)
        write_tool = WriteStdinTool(exec_tool)

        from trpc_agent_sdk.skills.tools._skill_exec import _ExecSession
        proc = MagicMock()
        proc.returncode = 0
        proc.stdin = MagicMock()
        proc.stdin.write = MagicMock()
        proc.stdin.drain = AsyncMock()
        ws = MagicMock()
        in_data = ExecInput(skill="test", command="cat")
        sess = _ExecSession(proc=proc, ws=ws, in_data=in_data)
        await sess.append_output("response")

        await exec_tool._put_session("s1", sess)

        ctx = MagicMock()
        ctx.artifact_service = None
        result = await write_tool._run_async_impl(
            tool_context=ctx,
            args={"session_id": "s1", "chars": "input", "submit": True, "yield_ms": 10},
        )
        assert result["status"] == "exited"

    async def test_write_empty_chars(self):
        run_tool = MagicMock()
        run_tool._repository = MagicMock()
        run_tool._timeout = 300.0
        exec_tool = SkillExecTool(run_tool)
        write_tool = WriteStdinTool(exec_tool)

        from trpc_agent_sdk.skills.tools._skill_exec import _ExecSession
        proc = MagicMock()
        proc.returncode = 0
        ws = MagicMock()
        in_data = ExecInput(skill="test", command="cat")
        sess = _ExecSession(proc=proc, ws=ws, in_data=in_data)

        await exec_tool._put_session("s1", sess)

        ctx = MagicMock()
        ctx.artifact_service = None
        result = await write_tool._run_async_impl(
            tool_context=ctx,
            args={"session_id": "s1", "yield_ms": 10},
        )
        assert "status" in result

    async def test_write_invalid_args_raises(self):
        run_tool = MagicMock()
        run_tool._repository = MagicMock()
        run_tool._timeout = 300.0
        exec_tool = SkillExecTool(run_tool)
        write_tool = WriteStdinTool(exec_tool)
        ctx = MagicMock()
        with pytest.raises(ValueError, match="Invalid"):
            await write_tool._run_async_impl(tool_context=ctx, args={})
