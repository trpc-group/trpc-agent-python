# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.code_executors.cube._runtime."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from trpc_agent_sdk.code_executors._constants import (
    DEFAULT_MAX_FILES,
    DEFAULT_TIMEOUT_SEC,
    DIR_OUT,
    DIR_RUNS,
    DIR_SKILLS,
    DIR_WORK,
    ENV_OUTPUT_DIR,
    ENV_RUN_DIR,
    ENV_SKILLS_DIR,
    ENV_WORK_DIR,
    WORKSPACE_ENV_DIR_KEY,
)
from trpc_agent_sdk.code_executors._types import (
    WorkspaceCapabilities,
    WorkspaceInfo,
    WorkspaceInputSpec,
    WorkspaceOutputSpec,
    WorkspacePutFileInfo,
    WorkspaceRunProgramSpec,
    WorkspaceStageOptions,
)
from trpc_agent_sdk.code_executors.cube import _runtime as rt_mod
from trpc_agent_sdk.code_executors.cube._runtime import (
    CubeProgramRunner,
    CubeWorkspaceFS,
    CubeWorkspaceManager,
    CubeWorkspaceRuntime,
    _input_default_name,
    create_cube_workspace_runtime,
)
from trpc_agent_sdk.code_executors.cube._sandbox import (
    CubeCommandResult,
    CubeSandboxClient,
)
from trpc_agent_sdk.code_executors.cube._types import (
    CubeCodeExecutorConfig,
    CubeWorkspaceRuntimeConfig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(stdout: str = "", stderr: str = "", exit_code: int = 0) -> CubeCommandResult:
    return CubeCommandResult(stdout=stdout, stderr=stderr, exit_code=exit_code, duration=0.0)


def _err(stderr: str = "boom", exit_code: int = 1) -> CubeCommandResult:
    return CubeCommandResult(stdout="", stderr=stderr, exit_code=exit_code, duration=0.0)


@pytest.fixture
def mock_client():
    c = MagicMock(spec=CubeSandboxClient)
    c.sandbox_id = "sbx"
    c.commands_run = AsyncMock(return_value=_ok())
    c.read_file_bytes = AsyncMock(return_value=b"")
    c.write_file_bytes = AsyncMock(return_value=None)
    c.upload_path = AsyncMock(return_value=None)
    c.download_path = AsyncMock(return_value=None)
    return c


def _ws(path: str = "/workspace/cube_agent/ws_test_1") -> WorkspaceInfo:
    return WorkspaceInfo(id="test", path=path)


# ---------------------------------------------------------------------------
# _input_default_name
# ---------------------------------------------------------------------------


class TestInputDefaultName:

    @pytest.mark.parametrize("src,expected", [
        ("a/b/c.txt", "c.txt"),
        ("file.txt", "file.txt"),
        ("/abs/path/file", "file"),
        ("a/", "a/"),  # trailing slash: last segment empty, falls through
        ("", ""),
    ])
    def test_basename(self, src, expected):
        assert _input_default_name(src) == expected


# ---------------------------------------------------------------------------
# CubeWorkspaceManager
# ---------------------------------------------------------------------------


class TestCubeWorkspaceManager:

    @pytest.mark.asyncio
    async def test_create_workspace_builds_mkdir_command(self, mock_client, monkeypatch):
        monkeypatch.setattr(rt_mod.time, "time_ns", lambda: 123456789)
        mgr = CubeWorkspaceManager(mock_client, "/workspace/cube_agent", 30.0)
        info = await mgr.create_workspace("my-id")

        assert info.id == "my-id"
        assert info.path == "/workspace/cube_agent/ws_my-id_123456789"

        mock_client.commands_run.assert_awaited_once()
        cmd = mock_client.commands_run.await_args.args[0]
        assert "set -e" in cmd
        assert "mkdir -p" in cmd
        assert "'/workspace/cube_agent/ws_my-id_123456789'" in cmd
        for sub in (DIR_WORK, DIR_OUT, DIR_SKILLS, DIR_RUNS):
            assert sub in cmd

    @pytest.mark.asyncio
    async def test_create_workspace_sanitizes_exec_id(self, mock_client):
        """BUG PROBE: `/`, `@`, spaces, `.` must be replaced by `_`."""
        mgr = CubeWorkspaceManager(mock_client, "/ws", 30.0)
        info = await mgr.create_workspace("user@example.com/rundir space")
        # All non-[a-zA-Z0-9_-] replaced with "_".
        assert "ws_user_example_com_rundir_space_" in info.path

    @pytest.mark.asyncio
    async def test_create_workspace_empty_id_becomes_anon(self, mock_client):
        mgr = CubeWorkspaceManager(mock_client, "/ws", 30.0)
        info = await mgr.create_workspace("")
        assert "ws_anon_" in info.path

    @pytest.mark.asyncio
    async def test_create_workspace_failure_raises(self, mock_client):
        mock_client.commands_run.return_value = _err("mkdir fail")
        mgr = CubeWorkspaceManager(mock_client, "/ws", 30.0)
        with pytest.raises(RuntimeError, match="Failed to create cube workspace"):
            await mgr.create_workspace("id")

    @pytest.mark.asyncio
    async def test_create_workspace_idempotent(self, mock_client):
        """Second call with same id returns cached info on a stable path,
        but re-issues an idempotent mkdir -p so the cache is reconciled
        with the remote (heals if the dir was deleted externally — see
        BUG 8 in test_bug_hunt.py).
        """
        mgr = CubeWorkspaceManager(mock_client, "/ws", 30.0)
        info1 = await mgr.create_workspace("id")
        info2 = await mgr.create_workspace("id")
        assert info1 is info2
        assert info1.path == info2.path
        assert mock_client.commands_run.await_count == 2
        for call in mock_client.commands_run.await_args_list:
            cmd = call.args[0]
            assert "mkdir -p" in cmd
            assert f"'{info1.path}'" in cmd

    @pytest.mark.asyncio
    async def test_cleanup_runs_rm(self, mock_client):
        mgr = CubeWorkspaceManager(mock_client, "/ws", 30.0)
        await mgr.create_workspace("id")
        mock_client.commands_run.reset_mock()
        await mgr.cleanup("id")
        cmd = mock_client.commands_run.await_args.args[0]
        assert cmd.startswith("rm -rf ")

    @pytest.mark.asyncio
    async def test_cleanup_unknown_is_noop(self, mock_client):
        mgr = CubeWorkspaceManager(mock_client, "/ws", 30.0)
        await mgr.cleanup("unknown-id")
        mock_client.commands_run.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cleanup_failure_raises(self, mock_client):
        mgr = CubeWorkspaceManager(mock_client, "/ws", 30.0)
        await mgr.create_workspace("id")
        mock_client.commands_run.return_value = _err("rm fail")
        with pytest.raises(RuntimeError, match="Failed to clean cube workspace"):
            await mgr.cleanup("id")


# ---------------------------------------------------------------------------
# CubeWorkspaceFS.put_files
# ---------------------------------------------------------------------------


class TestPutFiles:

    @pytest.mark.asyncio
    async def test_basic_write(self, mock_client):
        fs = CubeWorkspaceFS(mock_client, 30.0)
        ws = _ws()
        await fs.put_files(ws, [WorkspacePutFileInfo(path="sub/a.txt", content=b"hi")])
        mock_client.write_file_bytes.assert_awaited_once_with(
            f"{ws.path}/sub/a.txt", b"hi"
        )
        # Parent mkdir happened.
        assert any(
            "mkdir" in call.args[0] for call in mock_client.commands_run.await_args_list
        )

    @pytest.mark.asyncio
    async def test_empty_path_raises(self, mock_client):
        fs = CubeWorkspaceFS(mock_client, 30.0)
        with pytest.raises(ValueError, match="empty file path"):
            await fs.put_files(_ws(), [WorkspacePutFileInfo(path="", content=b"x")])

    @pytest.mark.asyncio
    async def test_dotdot_rejected(self, mock_client):
        fs = CubeWorkspaceFS(mock_client, 30.0)
        with pytest.raises(ValueError, match="escapes"):
            await fs.put_files(_ws(), [WorkspacePutFileInfo(path="../escape", content=b"")])

    @pytest.mark.asyncio
    async def test_parent_is_ws_root_no_mkdir(self, mock_client):
        fs = CubeWorkspaceFS(mock_client, 30.0)
        ws = _ws()
        await fs.put_files(ws, [WorkspacePutFileInfo(path="toplevel.txt", content=b"")])
        # No mkdir issued because parent == ws.path.
        mock_client.commands_run.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_none_content_writes_empty_bytes(self, mock_client):
        fs = CubeWorkspaceFS(mock_client, 30.0)
        # Pydantic default is b"" but simulate explicit none-y content.
        await fs.put_files(_ws(), [WorkspacePutFileInfo(path="x.txt", content=b"")])
        mock_client.write_file_bytes.assert_awaited_once()
        assert mock_client.write_file_bytes.await_args.args[1] == b""


# ---------------------------------------------------------------------------
# CubeWorkspaceFS.stage_directory
# ---------------------------------------------------------------------------


class TestStageDirectory:

    @pytest.mark.asyncio
    async def test_missing_src_raises(self, mock_client):
        fs = CubeWorkspaceFS(mock_client, 30.0)
        with pytest.raises(ValueError, match="src is empty"):
            await fs.stage_directory(_ws(), "", "dst", WorkspaceStageOptions())

    @pytest.mark.asyncio
    async def test_nonexistent_src_raises(self, mock_client, tmp_path):
        fs = CubeWorkspaceFS(mock_client, 30.0)
        missing = str(tmp_path / "missing")
        with pytest.raises(FileNotFoundError):
            await fs.stage_directory(_ws(), missing, "", WorkspaceStageOptions())

    @pytest.mark.asyncio
    async def test_file_not_dir_raises(self, mock_client, tmp_path):
        fs = CubeWorkspaceFS(mock_client, 30.0)
        f = tmp_path / "file"
        f.write_text("x")
        with pytest.raises(FileNotFoundError):
            await fs.stage_directory(_ws(), str(f), "", WorkspaceStageOptions())

    @pytest.mark.asyncio
    async def test_empty_dst_stages_to_ws_root(self, mock_client, tmp_path):
        fs = CubeWorkspaceFS(mock_client, 30.0)
        src = tmp_path / "s"
        src.mkdir()
        ws = _ws()
        await fs.stage_directory(ws, str(src), "", WorkspaceStageOptions())
        mock_client.upload_path.assert_awaited_once()
        args = mock_client.upload_path.await_args.args
        assert str(args[0]) == str(src.resolve()) or args[0] == Path(str(src.resolve()))
        assert args[1] == ws.path  # direct ws root, no subdir joined

    @pytest.mark.asyncio
    async def test_dst_dot_stages_to_ws_root(self, mock_client, tmp_path):
        fs = CubeWorkspaceFS(mock_client, 30.0)
        src = tmp_path / "s"
        src.mkdir()
        ws = _ws()
        await fs.stage_directory(ws, str(src), ".", WorkspaceStageOptions())
        args = mock_client.upload_path.await_args.args
        assert args[1] == ws.path

    @pytest.mark.asyncio
    async def test_read_only_issues_chmod(self, mock_client, tmp_path):
        fs = CubeWorkspaceFS(mock_client, 30.0)
        src = tmp_path / "s"
        src.mkdir()
        await fs.stage_directory(_ws(), str(src), "sub", WorkspaceStageOptions(read_only=True))
        assert any(
            call.args[0].startswith("chmod -R a-w")
            for call in mock_client.commands_run.await_args_list
        )

    @pytest.mark.asyncio
    async def test_no_chmod_when_read_only_false(self, mock_client, tmp_path):
        fs = CubeWorkspaceFS(mock_client, 30.0)
        src = tmp_path / "s"
        src.mkdir()
        await fs.stage_directory(_ws(), str(src), "sub", WorkspaceStageOptions(read_only=False))
        for call in mock_client.commands_run.await_args_list:
            assert "chmod" not in call.args[0]


# ---------------------------------------------------------------------------
# CubeWorkspaceFS.stage_inputs
# ---------------------------------------------------------------------------


class TestStageInputs:

    @pytest.mark.asyncio
    async def test_empty_src_is_skipped(self, mock_client):
        fs = CubeWorkspaceFS(mock_client, 30.0)
        await fs.stage_inputs(_ws(), [WorkspaceInputSpec(src="", dst="dst")])
        mock_client.upload_path.assert_not_awaited()
        mock_client.write_file_bytes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_artifact_without_ctx_raises(self, mock_client):
        fs = CubeWorkspaceFS(mock_client, 30.0)
        with pytest.raises(ValueError, match="Context is required"):
            await fs.stage_inputs(
                _ws(),
                [WorkspaceInputSpec(src="artifact://name", dst="dst.txt")],
                ctx=None,
            )

    @pytest.mark.asyncio
    async def test_artifact_success_writes_bytes(self, mock_client, monkeypatch):
        fs = CubeWorkspaceFS(mock_client, 30.0)
        ctx = MagicMock()

        async def fake_load(ctx, name, version):
            return b"artifact-bytes", 1

        monkeypatch.setattr(rt_mod, "load_artifact_helper", fake_load)
        monkeypatch.setattr(rt_mod, "parse_artifact_ref", lambda r: ("name", None))

        await fs.stage_inputs(
            _ws(),
            [WorkspaceInputSpec(src="artifact://name", dst="a.txt")],
            ctx=ctx,
        )
        mock_client.write_file_bytes.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_host_scheme_uploads(self, mock_client, tmp_path):
        fs = CubeWorkspaceFS(mock_client, 30.0)
        host_file = tmp_path / "f.txt"
        host_file.write_text("payload")
        await fs.stage_inputs(
            _ws(),
            [WorkspaceInputSpec(src=f"host://{host_file}", dst="f.txt")],
        )
        mock_client.upload_path.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_host_missing_raises(self, mock_client, tmp_path):
        fs = CubeWorkspaceFS(mock_client, 30.0)
        with pytest.raises(FileNotFoundError):
            await fs.stage_inputs(
                _ws(),
                [WorkspaceInputSpec(src=f"host://{tmp_path}/nope", dst="x.txt")],
            )

    @pytest.mark.asyncio
    async def test_workspace_scheme_remote_copy(self, mock_client):
        fs = CubeWorkspaceFS(mock_client, 30.0)
        await fs.stage_inputs(
            _ws(),
            [WorkspaceInputSpec(src="workspace://src.txt", dst="dst.txt")],
        )
        # At least one command is `cp -a`.
        assert any(
            "cp -a" in call.args[0] for call in mock_client.commands_run.await_args_list
        )

    @pytest.mark.asyncio
    async def test_skill_scheme_remote_copy(self, mock_client):
        fs = CubeWorkspaceFS(mock_client, 30.0)
        await fs.stage_inputs(
            _ws(),
            [WorkspaceInputSpec(src="skill://lint/main.py", dst="x.py")],
        )
        # cp from under {ws}/skills/lint/main.py
        cp_calls = [c for c in mock_client.commands_run.await_args_list if "cp -a" in c.args[0]]
        assert cp_calls
        cmd = cp_calls[0].args[0]
        assert f"/{DIR_SKILLS}/lint/main.py" in cmd

    @pytest.mark.asyncio
    async def test_unknown_scheme_raises(self, mock_client):
        fs = CubeWorkspaceFS(mock_client, 30.0)
        with pytest.raises(ValueError, match="unsupported input scheme"):
            await fs.stage_inputs(_ws(), [WorkspaceInputSpec(src="ftp://x", dst="y")])

    @pytest.mark.asyncio
    async def test_default_dst_when_empty(self, mock_client, tmp_path):
        """Empty dst falls back to work/inputs/<basename>."""
        fs = CubeWorkspaceFS(mock_client, 30.0)
        host_file = tmp_path / "myfile.txt"
        host_file.write_text("x")
        await fs.stage_inputs(_ws(), [WorkspaceInputSpec(src=f"host://{host_file}", dst="")])
        dst_arg = mock_client.upload_path.await_args.args[1]
        assert f"{DIR_WORK}/inputs/myfile.txt" in dst_arg


# ---------------------------------------------------------------------------
# CubeWorkspaceFS.collect / collect_outputs / _glob / _mkdir / _copy_remote
# ---------------------------------------------------------------------------


class TestGlob:

    @pytest.mark.asyncio
    async def test_empty_patterns_no_call(self, mock_client):
        fs = CubeWorkspaceFS(mock_client, 30.0)
        ws = _ws()
        out = await fs._glob(ws.path, [])
        assert out == []
        mock_client.commands_run.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_patterns_issue_shell_command(self, mock_client):
        mock_client.commands_run.return_value = _ok(
            stdout=f"{_ws().path}/a.txt\n{_ws().path}/b.txt\n"
        )
        fs = CubeWorkspaceFS(mock_client, 30.0)
        ws = _ws()
        out = await fs._glob(ws.path, ["*.txt"])
        assert out == [f"{ws.path}/a.txt", f"{ws.path}/b.txt"]
        cmd = mock_client.commands_run.await_args.args[0]
        assert "globstar" in cmd
        assert "'*.txt'" in cmd

    @pytest.mark.asyncio
    async def test_glob_failure_raises(self, mock_client):
        mock_client.commands_run.return_value = _err("glob died")
        fs = CubeWorkspaceFS(mock_client, 30.0)
        with pytest.raises(RuntimeError, match="glob failed"):
            await fs._glob(_ws().path, ["*"])


class TestCollect:

    @pytest.mark.asyncio
    async def test_empty_match(self, mock_client):
        mock_client.commands_run.return_value = _ok(stdout="")
        fs = CubeWorkspaceFS(mock_client, 30.0)
        out = await fs.collect(_ws(), ["*.nope"])
        assert out == []

    @pytest.mark.asyncio
    async def test_dedup_by_rel(self, mock_client):
        ws = _ws()
        # Glob returns same path twice.
        mock_client.commands_run.return_value = _ok(stdout=f"{ws.path}/a.txt\n{ws.path}/a.txt\n")
        mock_client.read_file_bytes.return_value = b"content"
        fs = CubeWorkspaceFS(mock_client, 30.0)
        out = await fs.collect(ws, ["*.txt"])
        assert len(out) == 1
        assert out[0].name == "a.txt"

    @pytest.mark.asyncio
    async def test_truncation_marker(self, mock_client, monkeypatch):
        # Force a tiny per-file cap so truncation happens. ``max_read_size``
        # is a keyword-only argument on the protected base-class helper
        # with a module-level default; patching the function's
        # ``__kwdefaults__`` lets us simulate a tiny cap without altering
        # the public ``CubeWorkspaceFS.collect`` signature.
        from trpc_agent_sdk.code_executors._base_workspace_runtime import BaseWorkspaceFS
        monkeypatch.setitem(
            BaseWorkspaceFS._build_code_files.__kwdefaults__,
            "max_read_size",
            4,
        )
        ws = _ws()
        mock_client.commands_run.return_value = _ok(stdout=f"{ws.path}/f.txt\n")
        mock_client.read_file_bytes.return_value = b"0123456789"
        fs = CubeWorkspaceFS(mock_client, 30.0)
        out = await fs.collect(ws, ["*.txt"])
        assert len(out) == 1
        assert out[0].truncated is True
        assert out[0].size_bytes == 10


class TestCollectOutputs:

    @pytest.mark.asyncio
    async def test_max_files_limit(self, mock_client):
        ws = _ws()
        mock_client.commands_run.return_value = _ok(
            stdout="\n".join(f"{ws.path}/f{i}.txt" for i in range(5))
        )
        mock_client.read_file_bytes.return_value = b"x"
        fs = CubeWorkspaceFS(mock_client, 30.0)
        manifest = await fs.collect_outputs(ws, WorkspaceOutputSpec(globs=["*"], max_files=2))
        assert len(manifest.files) == 2
        assert manifest.limits_hit is True

    @pytest.mark.asyncio
    async def test_file_bytes_limit_sets_truncated(self, mock_client):
        ws = _ws()
        mock_client.commands_run.return_value = _ok(stdout=f"{ws.path}/f.txt\n")
        mock_client.read_file_bytes.return_value = b"A" * 100
        fs = CubeWorkspaceFS(mock_client, 30.0)
        manifest = await fs.collect_outputs(
            ws, WorkspaceOutputSpec(globs=["*"], max_file_bytes=4)
        )
        assert manifest.limits_hit is True

    @pytest.mark.asyncio
    async def test_save_requires_ctx(self, mock_client):
        ws = _ws()
        mock_client.commands_run.return_value = _ok(stdout=f"{ws.path}/f.txt\n")
        mock_client.read_file_bytes.return_value = b"x"
        fs = CubeWorkspaceFS(mock_client, 30.0)
        with pytest.raises(ValueError, match="Context is required"):
            await fs.collect_outputs(ws, WorkspaceOutputSpec(globs=["*"], save=True))

    @pytest.mark.asyncio
    async def test_save_happy_path(self, mock_client, monkeypatch):
        ws = _ws()
        mock_client.commands_run.return_value = _ok(stdout=f"{ws.path}/f.txt\n")
        mock_client.read_file_bytes.return_value = b"x"
        fs = CubeWorkspaceFS(mock_client, 30.0)

        saved = []

        async def fake_save(ctx, name, data, mime):
            saved.append((name, data, mime))
            return 7

        from trpc_agent_sdk.code_executors import _base_workspace_runtime as base_mod
        monkeypatch.setattr(base_mod, "save_artifact_helper", fake_save)
        ctx = MagicMock()
        manifest = await fs.collect_outputs(
            ws,
            WorkspaceOutputSpec(globs=["*"], save=True, inline=True, name_template="prefix/"),
            ctx=ctx,
        )
        assert len(manifest.files) == 1
        ref = manifest.files[0]
        assert ref.saved_as == "prefix/f.txt"
        assert ref.version == 7
        assert ref.content == "x"

    @pytest.mark.asyncio
    async def test_inline_only_no_save(self, mock_client):
        ws = _ws()
        mock_client.commands_run.return_value = _ok(stdout=f"{ws.path}/f.txt\n")
        mock_client.read_file_bytes.return_value = b"hello"
        fs = CubeWorkspaceFS(mock_client, 30.0)
        manifest = await fs.collect_outputs(ws, WorkspaceOutputSpec(globs=["*"], inline=True))
        assert manifest.files[0].content == "hello"
        assert manifest.files[0].saved_as == ""


# ---------------------------------------------------------------------------
# CubeWorkspaceFS._mkdir / _copy_remote
# ---------------------------------------------------------------------------


class TestMkdirAndCopy:

    @pytest.mark.asyncio
    async def test_mkdir_empty_is_noop(self, mock_client):
        fs = CubeWorkspaceFS(mock_client, 30.0)
        await fs._mkdir("")
        mock_client.commands_run.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mkdir_failure_raises(self, mock_client):
        mock_client.commands_run.return_value = _err("perm denied")
        fs = CubeWorkspaceFS(mock_client, 30.0)
        with pytest.raises(RuntimeError, match="mkdir -p failed"):
            await fs._mkdir("/some/path")

    @pytest.mark.asyncio
    async def test_copy_remote_failure_raises(self, mock_client):
        """mkdir → rm → cp; failing cp must surface as ``remote cp failed``.

        The rm step defends against the ``cp -a`` directory-footgun (BUG 11),
        so we get an extra command between mkdir and cp. Stub all three.
        """
        mock_client.commands_run.side_effect = [_ok(), _ok(), _err("cp fail")]
        fs = CubeWorkspaceFS(mock_client, 30.0)
        with pytest.raises(RuntimeError, match="remote cp failed"):
            await fs._copy_remote("/src", "/dst")

    @pytest.mark.asyncio
    async def test_copy_remote_rm_failure_raises(self, mock_client):
        """The defensive rm step must surface its own failure, not be swallowed."""
        mock_client.commands_run.side_effect = [_ok(), _err("perm denied")]
        fs = CubeWorkspaceFS(mock_client, 30.0)
        with pytest.raises(RuntimeError, match="remote rm failed"):
            await fs._copy_remote("/src", "/dst")


# ---------------------------------------------------------------------------
# CubeProgramRunner
# ---------------------------------------------------------------------------


class TestCubeProgramRunner:

    @pytest.mark.asyncio
    async def test_shell_pipeline_structure(self, mock_client, monkeypatch):
        monkeypatch.setattr(rt_mod.time, "strftime", lambda fmt: "20260506T120000")
        mock_client.commands_run.return_value = _ok(stdout="ok")
        runner = CubeProgramRunner(mock_client, 30.0)
        ws = _ws()
        spec = WorkspaceRunProgramSpec(cmd="python", args=["-c", "print(1)"])

        result = await runner.run_program(ws, spec)

        cmd = mock_client.commands_run.await_args.args[0]
        assert "set -e" in cmd
        assert "mkdir -p" in cmd
        assert f"cd '{ws.path}'" in cmd
        # Args are shell-quoted:
        assert "'python'" in cmd and "'-c'" in cmd and "'print(1)'" in cmd

        env = mock_client.commands_run.await_args.kwargs["env"]
        assert env[WORKSPACE_ENV_DIR_KEY] == ws.path
        assert env[ENV_SKILLS_DIR] == f"{ws.path}/{DIR_SKILLS}"
        assert env[ENV_WORK_DIR] == f"{ws.path}/{DIR_WORK}"
        assert env[ENV_OUTPUT_DIR] == f"{ws.path}/{DIR_OUT}"
        assert env[ENV_RUN_DIR] == f"{ws.path}/{DIR_RUNS}/run_20260506T120000"
        assert result.stdout == "ok"

    @pytest.mark.asyncio
    async def test_cwd_override(self, mock_client):
        mock_client.commands_run.return_value = _ok()
        runner = CubeProgramRunner(mock_client, 30.0)
        ws = _ws()
        await runner.run_program(ws, WorkspaceRunProgramSpec(cmd="ls", cwd="sub/dir"))
        cmd = mock_client.commands_run.await_args.args[0]
        assert f"cd '{ws.path}/sub/dir'" in cmd

    @pytest.mark.asyncio
    async def test_spec_env_overrides_default(self, mock_client):
        mock_client.commands_run.return_value = _ok()
        runner = CubeProgramRunner(mock_client, 30.0)
        spec = WorkspaceRunProgramSpec(cmd="x", env={"WORKSPACE_DIR": "override"})
        await runner.run_program(_ws(), spec)
        env = mock_client.commands_run.await_args.kwargs["env"]
        # spec.env wins.
        assert env["WORKSPACE_DIR"] == "override"

    @pytest.mark.asyncio
    async def test_stdin_encoded(self, mock_client):
        mock_client.commands_run.return_value = _ok()
        runner = CubeProgramRunner(mock_client, 30.0)
        spec = WorkspaceRunProgramSpec(cmd="cat", stdin="héllo")
        await runner.run_program(_ws(), spec)
        assert mock_client.commands_run.await_args.kwargs["stdin"] == "héllo".encode("utf-8")

    @pytest.mark.asyncio
    async def test_stdin_empty_is_none(self, mock_client):
        mock_client.commands_run.return_value = _ok()
        runner = CubeProgramRunner(mock_client, 30.0)
        spec = WorkspaceRunProgramSpec(cmd="cat", stdin="")
        await runner.run_program(_ws(), spec)
        assert mock_client.commands_run.await_args.kwargs["stdin"] is None

    @pytest.mark.asyncio
    async def test_timeout_positive_forwarded(self, mock_client):
        mock_client.commands_run.return_value = _ok()
        runner = CubeProgramRunner(mock_client, 30.0)
        spec = WorkspaceRunProgramSpec(cmd="x", timeout=17)
        await runner.run_program(_ws(), spec)
        assert mock_client.commands_run.await_args.kwargs["timeout"] == 17.0

    @pytest.mark.asyncio
    async def test_timeout_zero_falls_back_to_default(self, mock_client):
        mock_client.commands_run.return_value = _ok()
        runner = CubeProgramRunner(mock_client, 30.0)
        spec = WorkspaceRunProgramSpec(cmd="x", timeout=0)
        await runner.run_program(_ws(), spec)
        assert mock_client.commands_run.await_args.kwargs["timeout"] == float(DEFAULT_TIMEOUT_SEC)

    @pytest.mark.asyncio
    async def test_provider_env_merged_when_enabled(self, mock_client):
        mock_client.commands_run.return_value = _ok()
        provider = lambda ctx: {"EXTRA": "V"}
        runner = CubeProgramRunner(mock_client, 30.0, provider=provider, enable_provider_env=True)
        spec = WorkspaceRunProgramSpec(cmd="x")
        await runner.run_program(_ws(), spec)
        env = mock_client.commands_run.await_args.kwargs["env"]
        assert env["EXTRA"] == "V"

    @pytest.mark.asyncio
    async def test_provider_env_ignored_when_disabled(self, mock_client):
        mock_client.commands_run.return_value = _ok()
        provider = lambda ctx: {"EXTRA": "V"}
        # enable_provider_env=False → extras not merged.
        runner = CubeProgramRunner(mock_client, 30.0, provider=provider, enable_provider_env=False)
        spec = WorkspaceRunProgramSpec(cmd="x")
        await runner.run_program(_ws(), spec)
        env = mock_client.commands_run.await_args.kwargs["env"]
        assert "EXTRA" not in env


# ---------------------------------------------------------------------------
# CubeWorkspaceRuntime + create_cube_workspace_runtime
# ---------------------------------------------------------------------------


class TestCubeWorkspaceRuntime:

    def test_components_exposed(self, mock_client):
        rt = CubeWorkspaceRuntime(mock_client, remote_workspace="/ws", execute_timeout=30.0)
        assert isinstance(rt.manager(), CubeWorkspaceManager)
        assert isinstance(rt.fs(), CubeWorkspaceFS)
        assert isinstance(rt.runner(), CubeProgramRunner)

    def test_describe(self, mock_client):
        rt = CubeWorkspaceRuntime(mock_client, remote_workspace="/ws", execute_timeout=30.0)
        caps = rt.describe()
        assert caps.isolation == "cube"
        assert caps.network_allowed is True
        assert caps.read_only_mount is False
        assert caps.streaming is False


class TestCreateCubeWorkspaceRuntime:

    def test_reuses_executor_client(self, mock_client):
        cfg = CubeCodeExecutorConfig(
            template="t", api_url="u", api_key="k", execute_timeout=42.0
        )
        ex = MagicMock()
        ex.sandbox_client = mock_client
        ex.config = cfg
        rt = create_cube_workspace_runtime(ex)
        assert rt._client is mock_client
        # Inherits execute_timeout from exec cfg.
        assert rt._fs._timeout == 42.0
        assert rt._runner._timeout == 42.0

    def test_uses_default_workspace_when_none(self, mock_client):
        cfg = CubeCodeExecutorConfig(template="t", api_url="u", api_key="k")
        ex = MagicMock()
        ex.sandbox_client = mock_client
        ex.config = cfg
        rt = create_cube_workspace_runtime(ex)
        assert rt._manager._root == "/workspace/cube_agent"

    def test_custom_workspace_cfg(self, mock_client):
        cfg = CubeCodeExecutorConfig(template="t", api_url="u", api_key="k")
        ex = MagicMock()
        ex.sandbox_client = mock_client
        ex.config = cfg
        rt = create_cube_workspace_runtime(
            ex, workspace_cfg=CubeWorkspaceRuntimeConfig(remote_workspace="/custom")
        )
        assert rt._manager._root == "/custom"

    def test_provider_and_flag_forwarded(self, mock_client):
        cfg = CubeCodeExecutorConfig(template="t", api_url="u", api_key="k")
        ex = MagicMock()
        ex.sandbox_client = mock_client
        ex.config = cfg
        provider = lambda ctx: {}
        rt = create_cube_workspace_runtime(ex, provider=provider, enable_provider_env=True)
        assert rt._runner._run_env_provider is provider
        assert rt._runner._enable_provider_env is True


class TestCubeWorkspaceRuntimeAutoRecover:

    @pytest.mark.asyncio
    async def test_recreates_and_retries_when_sandbox_is_missing(self, fake_e2b, monkeypatch):
        cfg = CubeCodeExecutorConfig(template="tpl", api_url="url", api_key="key", auto_recover=True)
        sandbox1 = MagicMock()
        sandbox1.sandbox_id = "old"
        sandbox1.kill = AsyncMock(return_value=None)
        sandbox1.set_timeout = AsyncMock(return_value=None)
        sandbox1.commands = MagicMock()
        sandbox1.commands.run = AsyncMock(side_effect=fake_e2b.SandboxNotFoundException("gone"))
        client1 = CubeSandboxClient(sandbox1, cfg)

        sandbox2 = MagicMock()
        sandbox2.sandbox_id = "new"
        sandbox2.set_timeout = AsyncMock(return_value=None)
        sandbox2.commands = MagicMock()
        sandbox2.commands.run = AsyncMock(return_value=SimpleNamespace(stdout="", stderr="", exit_code=0))
        client2 = CubeSandboxClient(sandbox2, cfg)

        executor1 = MagicMock()
        executor1.config = cfg
        executor1.sandbox_id = "old"
        executor1.sandbox_client = client1

        open_new = AsyncMock(return_value=client2)
        monkeypatch.setattr(rt_mod.CubeSandboxClient, "open_new", open_new)
        monkeypatch.setattr(rt_mod.time, "time_ns", lambda: 123)

        runtime = create_cube_workspace_runtime(
            executor1,
            workspace_cfg=CubeWorkspaceRuntimeConfig(),
        )
        info = await runtime.manager().create_workspace("id")

        assert info.path == "/workspace/cube_agent/ws_id_123"
        assert runtime.sandbox_id == "new"
        open_new.assert_awaited_once_with(cfg)
        sandbox1.kill.assert_awaited_once()
        sandbox2.commands.run.assert_awaited_once()
