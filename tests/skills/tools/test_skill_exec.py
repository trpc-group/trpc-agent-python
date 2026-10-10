# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import os
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest
from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
from trpc_agent_sdk.code_executors import DEFAULT_EXEC_YIELD_MS
from trpc_agent_sdk.code_executors import DEFAULT_IO_YIELD_MS
from trpc_agent_sdk.code_executors import DEFAULT_POLL_LINES
from trpc_agent_sdk.code_executors import DEFAULT_SESSION_TTL_SEC
from trpc_agent_sdk.code_executors import ENV_SKILL_NAME
from trpc_agent_sdk.code_executors import WorkspaceRunResult
from trpc_agent_sdk.skills.stager import SkillStageResult
from trpc_agent_sdk.skills.tools._skill_exec import ExecInput
from trpc_agent_sdk.skills.tools._skill_exec import PollSessionTool
from trpc_agent_sdk.skills.tools._skill_exec import SkillExecTool
from trpc_agent_sdk.skills.tools._skill_exec import WriteStdinTool
from trpc_agent_sdk.skills.tools._skill_exec import _ExecSession
from trpc_agent_sdk.skills.tools._skill_exec import _close_session
from trpc_agent_sdk.skills.tools._skill_exec import _collect_final_result
from trpc_agent_sdk.skills.tools._skill_exec import _detect_interaction
from trpc_agent_sdk.skills.tools._skill_exec import _has_selection_items
from trpc_agent_sdk.skills.tools._skill_exec import _last_non_empty_line
from trpc_agent_sdk.skills.tools._skill_exec import _start_session
from trpc_agent_sdk.skills.tools._skill_exec import create_exec_tools
from trpc_agent_sdk.skills.tools._skill_run import SkillRunFile
from trpc_agent_sdk.skills.tools._skill_run import SkillRunTool


def _make_exec_tool() -> SkillExecTool:
    run_tool = MagicMock()
    run_tool._repository = MagicMock()
    run_tool._timeout = 300.0
    run_tool._resolve_cwd = MagicMock(return_value="skills/test")
    run_tool._build_command = MagicMock(return_value=("bash", ["-lc", "echo hello"]))
    run_tool._prepare_outputs = AsyncMock(return_value=([], None))
    run_tool._attach_artifacts_if_requested = AsyncMock()
    run_tool._merge_manifest_artifact_refs = MagicMock()
    return SkillExecTool(run_tool)


class TestHelpers:

    def test_last_non_empty_line(self):
        assert _last_non_empty_line("a\n\nb\n") == "b"

    def test_has_selection_items(self):
        assert _has_selection_items("1. a\n2. b") is True
        assert _has_selection_items("1. a") is False

    def test_detect_interaction_prompt(self):
        ret = _detect_interaction("running", "Enter your name:")
        assert ret is not None
        assert ret.needs_input is True

    def test_detect_interaction_selection(self):
        ret = _detect_interaction("running", "Choose:\n1. A\n2. B\nEnter the number:")
        assert ret is not None
        assert ret.kind == "selection"


class TestModelsAndConstants:

    def test_exec_input_defaults(self):
        inp = ExecInput(skill="s", command="echo hi")
        assert inp.yield_time_ms == 0
        assert inp.poll_lines == 0
        assert inp.tty is False

    def test_default_constants(self):
        assert DEFAULT_EXEC_YIELD_MS > 0
        assert DEFAULT_IO_YIELD_MS > 0
        assert DEFAULT_POLL_LINES > 0
        assert DEFAULT_SESSION_TTL_SEC > 0


class TestSessionStore:

    @pytest.mark.asyncio
    async def test_put_get_remove(self):
        tool = _make_exec_tool()
        sess = MagicMock()
        sess.exited_at = None
        sess.proc.state = AsyncMock(return_value=MagicMock(status="running", exit_code=None))
        await tool._put_session("s1", sess)
        got = await tool._get_session("s1")
        assert got is sess
        removed = await tool._remove_session("s1")
        assert removed is sess


class TestFactoryAndDeclarations:

    def test_create_exec_tools(self):
        run_tool = MagicMock()
        run_tool._repository = MagicMock()
        run_tool._timeout = 300.0
        tools = create_exec_tools(run_tool)
        assert len(tools) == 4
        assert isinstance(tools[0], SkillExecTool)
        assert isinstance(tools[1], WriteStdinTool)
        assert isinstance(tools[2], PollSessionTool)

    def test_declaration_names(self):
        exec_tool = _make_exec_tool()
        assert exec_tool._get_declaration().name == "skill_exec"
        assert WriteStdinTool(exec_tool)._get_declaration().name == "skill_write_stdin"
        assert PollSessionTool(exec_tool)._get_declaration().name == "skill_poll_session"


class TestCloseSession:

    @pytest.mark.asyncio
    async def test_close_session(self):
        sess = MagicMock()
        sess.proc.close = AsyncMock()
        await _close_session(sess)
        sess.proc.close.assert_awaited_once()


class TestExecutionEnvironment:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool_name", ["skill_run", "skill_exec"])
    @pytest.mark.parametrize(
        ("call_env", "host_env", "expected"),
        [
            pytest.param({}, None, "injected", id="repository"),
            pytest.param({"TEST_SKILL_ENV": "explicit"}, None, "explicit", id="explicit"),
            pytest.param({}, "host", "host", id="host"),
            pytest.param({"TEST_SKILL_ENV": "explicit"}, "host", "explicit", id="explicit-over-host"),
            pytest.param({}, "  ", "injected", id="blank-host"),
            pytest.param({"TEST_SKILL_ENV": ""}, None, "", id="empty-explicit"),
        ],
    )
    async def test_skill_tools_apply_repository_env(self, tmp_path, monkeypatch, tool_name, call_env, host_env,
                                                    expected):
        if host_env is None:
            monkeypatch.delenv("TEST_SKILL_ENV", raising=False)
        else:
            monkeypatch.setenv("TEST_SKILL_ENV", host_env)

        ctx = MagicMock()
        ctx.agent_name = ""
        ctx.actions.state_delta = {}
        ctx.session_state = {}
        ws = MagicMock(path=str(tmp_path))
        workspace_runtime = MagicMock(spec=BaseWorkspaceRuntime)
        workspace_runtime.manager.return_value.create_workspace = AsyncMock(return_value=ws)
        workspace_runtime.fs.return_value.collect = AsyncMock(return_value=[])
        runner = workspace_runtime.runner.return_value
        runner.run_program = AsyncMock(return_value=WorkspaceRunResult(stdout="done", exit_code=0))
        runner.start_program = AsyncMock(return_value=MagicMock())

        repository = MagicMock()
        repository.get_workspace_runtime.return_value = workspace_runtime
        repository.skill_run_env.return_value = {"TEST_SKILL_ENV": "injected"}
        run_tool = SkillRunTool(repository=repository)
        run_tool.skill_stager.stage_skill = AsyncMock(return_value=SkillStageResult(workspace_skill_dir="skills/test"))
        monkeypatch.setattr(_ExecSession, "yield_output", AsyncMock(return_value=("running", "", 0, 0)))
        tool = run_tool if tool_name == "skill_run" else SkillExecTool(run_tool)

        await tool._run_async_impl(
            tool_context=ctx,
            args={
                "skill": " test ",
                "command": "echo done",
                "env": call_env
            },
        )

        if tool_name == "skill_run":
            env = runner.run_program.call_args.args[1].env
            effective_env = os.environ.copy()
            effective_env.update(env)
        else:
            effective_env = runner.start_program.call_args.args[2].env
        assert effective_env["TEST_SKILL_ENV"] == expected
        assert effective_env[ENV_SKILL_NAME] == "test"
        repository.skill_run_env.assert_called_once_with("test")


@pytest.mark.asyncio
async def test_collect_final_result_passes_workspace_runtime_and_returns_files():
    ctx = MagicMock()
    ws = MagicMock()
    workspace_runtime = MagicMock(spec=BaseWorkspaceRuntime)
    output_file = SkillRunFile(
        name="result.txt",
        content="done",
        mime_type="text/plain",
        size_bytes=4,
    )

    async def prepare_outputs(got_ctx, got_ws, got_runtime, input_data):
        assert got_ctx is ctx
        assert got_ws is ws
        assert got_runtime is workspace_runtime
        assert input_data.output_files == ["result.txt"]
        return [output_file], None

    run_tool = MagicMock()
    run_tool._prepare_outputs = prepare_outputs
    run_tool._attach_artifacts_if_requested = AsyncMock()
    run_tool._merge_manifest_artifact_refs = MagicMock()

    proc = MagicMock()
    proc.run_result = AsyncMock(return_value=MagicMock(
        stdout="command completed",
        stderr="",
        exit_code=0,
    ), )
    runner = MagicMock()
    runner.start_program = AsyncMock(return_value=proc)
    inputs = ExecInput(
        skill="test",
        command="echo done > result.txt",
        output_files=["result.txt"],
    )
    exec_session = await _start_session(
        runner=runner,
        tool_context=ctx,
        inputs=inputs,
        ws=ws,
        workspace_runtime=workspace_runtime,
        rel_cwd="skills/test",
        env={},
    )

    result = await _collect_final_result(ctx, exec_session, run_tool)

    assert result is not None
    assert result.output_files == [output_file]
    assert result.primary_output == output_file
    assert exec_session.finalized is True
