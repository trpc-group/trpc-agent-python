# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for container workspace runtime (_container_ws_runtime.py).

Covers:
- RuntimeConfig defaults
- ContainerWorkspaceManager: create_workspace, cleanup, _sanitize
- ContainerWorkspaceFS: put_files, stage_directory, collect, stage_inputs,
    collect_outputs, and all static helpers (_normalize_globs, _input_base,
    _detect_mime_type, _create_tar_from_files)
- ContainerProgramRunner: run_program, _shell_quote
- ContainerWorkspaceRuntime: init, manager/fs/runner properties, _find_bind_source, describe
- create_container_workspace_runtime factory
"""

from __future__ import annotations

import io
import tarfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from trpc_agent_sdk.code_executors._constants import (
    DEFAULT_INPUTS_CONTAINER,
    DEFAULT_RUN_CONTAINER_BASE,
    DEFAULT_SKILLS_CONTAINER,
    DIR_OUT,
    DIR_RUNS,
    DIR_SKILLS,
    DIR_WORK,
    MAX_READ_SIZE_BYTES,
)
from trpc_agent_sdk.code_executors._types import (
    ManifestFileRef,
    ManifestOutput,
    WorkspaceCapabilities,
    WorkspaceInfo,
    WorkspaceInputSpec,
    WorkspaceOutputSpec,
    WorkspacePutFileInfo,
    WorkspaceRunProgramSpec,
    WorkspaceRunResult,
    WorkspaceStageOptions,
)
from trpc_agent_sdk.code_executors.container._container_cli import (
    CommandArgs,
    ContainerClient,
    ContainerConfig,
)
from trpc_agent_sdk.code_executors.container._container_ws_runtime import (
    ContainerProgramRunner,
    ContainerWorkspaceFS,
    ContainerWorkspaceManager,
    ContainerWorkspaceRuntime,
    RuntimeConfig,
    create_container_workspace_runtime,
)
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.utils import CommandExecResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(stdout="", stderr=""):
    return CommandExecResult(stdout=stdout, stderr=stderr, exit_code=0, is_timeout=False)


def _err(stderr="fail"):
    return CommandExecResult(stdout="", stderr=stderr, exit_code=1, is_timeout=False)


def _mock_container_client():
    cc = MagicMock(spec=ContainerClient)
    cc.exec_run = AsyncMock(return_value=_ok())
    cc.client = MagicMock()
    cc.container = MagicMock()
    cc.container.id = "ctr-1"
    return cc


def _make_ws(exec_id="test", path="/tmp/run/ws_test_123"):
    return WorkspaceInfo(id=exec_id, path=path)


# ---------------------------------------------------------------------------
# RuntimeConfig
# ---------------------------------------------------------------------------


class TestRuntimeConfig:

    def test_defaults(self):
        cfg = RuntimeConfig()
        assert cfg.skills_host_base == ""
        assert cfg.skills_container_base == DEFAULT_SKILLS_CONTAINER
        assert cfg.run_container_base == DEFAULT_RUN_CONTAINER_BASE
        assert cfg.inputs_host_base == ""
        assert cfg.inputs_container_base == DEFAULT_INPUTS_CONTAINER
        assert cfg.auto_map_inputs is False
        assert isinstance(cfg.command_args, CommandArgs)

    def test_custom_values(self):
        args = CommandArgs(timeout=30.0)
        cfg = RuntimeConfig(
            skills_host_base="/host/skills",
            run_container_base="/custom/run",
            auto_map_inputs=True,
            command_args=args,
        )
        assert cfg.skills_host_base == "/host/skills"
        assert cfg.run_container_base == "/custom/run"
        assert cfg.auto_map_inputs is True
        assert cfg.command_args.timeout == 30.0


# ---------------------------------------------------------------------------
# ContainerWorkspaceManager._sanitize
# ---------------------------------------------------------------------------


class TestSanitize:

    def test_alphanumeric_unchanged(self):
        assert ContainerWorkspaceManager._sanitize("hello123") == "hello123"

    def test_hyphens_underscores_kept(self):
        assert ContainerWorkspaceManager._sanitize("my-exec_id") == "my-exec_id"

    def test_special_chars_replaced(self):
        assert ContainerWorkspaceManager._sanitize("a/b.c:d") == "a_b_c_d"

    def test_spaces_replaced(self):
        assert ContainerWorkspaceManager._sanitize("hello world") == "hello_world"

    def test_empty_string(self):
        assert ContainerWorkspaceManager._sanitize("") == ""


# ---------------------------------------------------------------------------
# ContainerWorkspaceManager.create_workspace
# ---------------------------------------------------------------------------


class TestCreateWorkspace:

    async def test_create_new_workspace(self):
        cc = _mock_container_client()
        cfg = RuntimeConfig()
        fs = MagicMock()
        mgr = ContainerWorkspaceManager(cc, cfg, fs)

        ws = await mgr.create_workspace("exec-1")

        assert ws.id == "exec-1"
        assert ws.path.startswith(DEFAULT_RUN_CONTAINER_BASE)
        assert "exec-1" in mgr.ws_paths
        cc.exec_run.assert_called_once()

    async def test_create_workspace_idempotent(self):
        cc = _mock_container_client()
        cfg = RuntimeConfig()
        fs = MagicMock()
        mgr = ContainerWorkspaceManager(cc, cfg, fs)

        ws1 = await mgr.create_workspace("exec-1")
        ws2 = await mgr.create_workspace("exec-1")

        assert ws1 is ws2
        cc.exec_run.assert_called_once()

    async def test_create_workspace_exec_fails(self):
        cc = _mock_container_client()
        cc.exec_run = AsyncMock(return_value=_err("mkdir failed"))
        cfg = RuntimeConfig()
        fs = MagicMock()
        mgr = ContainerWorkspaceManager(cc, cfg, fs)

        with pytest.raises(RuntimeError, match="Failed to create workspace"):
            await mgr.create_workspace("fail-id")

    async def test_create_workspace_auto_map_inputs(self):
        cc = _mock_container_client()
        cfg = RuntimeConfig(auto_map_inputs=True, inputs_host_base="/host/inputs")
        fs = MagicMock()
        fs.stage_inputs = AsyncMock()
        mgr = ContainerWorkspaceManager(cc, cfg, fs)

        ws = await mgr.create_workspace("auto-exec")

        fs.stage_inputs.assert_called_once()
        args = fs.stage_inputs.call_args
        specs = args.args[1] if len(args.args) > 1 else args[0][1]
        assert any("host://" in s.src for s in specs)

    async def test_create_workspace_auto_map_inputs_no_host_base(self):
        cc = _mock_container_client()
        cfg = RuntimeConfig(auto_map_inputs=True, inputs_host_base="")
        fs = MagicMock()
        fs.stage_inputs = AsyncMock()
        mgr = ContainerWorkspaceManager(cc, cfg, fs)

        await mgr.create_workspace("no-host")
        fs.stage_inputs.assert_not_called()


# ---------------------------------------------------------------------------
# ContainerWorkspaceManager.cleanup
# ---------------------------------------------------------------------------


class TestCleanup:

    async def test_cleanup_existing_workspace(self):
        cc = _mock_container_client()
        cfg = RuntimeConfig()
        fs = MagicMock()
        mgr = ContainerWorkspaceManager(cc, cfg, fs)

        ws = await mgr.create_workspace("exec-1")
        assert "exec-1" in mgr.ws_paths

        await mgr.cleanup("exec-1")
        assert "exec-1" not in mgr.ws_paths

    async def test_cleanup_nonexistent_workspace(self):
        cc = _mock_container_client()
        cfg = RuntimeConfig()
        fs = MagicMock()
        mgr = ContainerWorkspaceManager(cc, cfg, fs)

        await mgr.cleanup("nope")

    async def test_cleanup_exec_fails(self):
        cc = _mock_container_client()
        cfg = RuntimeConfig()
        fs = MagicMock()
        mgr = ContainerWorkspaceManager(cc, cfg, fs)

        await mgr.create_workspace("exec-1")
        cc.exec_run = AsyncMock(return_value=_err("rm failed"))

        with pytest.raises(RuntimeError, match="Failed to clean up workspace"):
            await mgr.cleanup("exec-1")

    async def test_cleanup_ws_with_empty_path(self):
        cc = _mock_container_client()
        cfg = RuntimeConfig()
        fs = MagicMock()
        mgr = ContainerWorkspaceManager(cc, cfg, fs)
        mgr.ws_paths["empty"] = WorkspaceInfo(id="empty", path="")

        await mgr.cleanup("empty")


# ---------------------------------------------------------------------------
# ContainerWorkspaceFS static helpers
# ---------------------------------------------------------------------------


class TestNormalizeGlobs:

    def test_basic_patterns(self):
        result = ContainerWorkspaceFS._normalize_globs(["*.py", "*.txt"])
        assert result == ["*.py", "*.txt"]

    def test_env_var_replacement(self):
        result = ContainerWorkspaceFS._normalize_globs([
            "$OUTPUT_DIR/**", "${OUTPUT_DIR}/file.txt",
            "$WORK_DIR/data", "${WORK_DIR}/data",
            "$WORKSPACE_DIR/all", "${WORKSPACE_DIR}/all",
        ])
        assert result == [
            f"{DIR_OUT}/**", f"{DIR_OUT}/file.txt",
            f"{DIR_WORK}/data", f"{DIR_WORK}/data",
            "./all", "./all",
        ]

    def test_empty_patterns_stripped(self):
        result = ContainerWorkspaceFS._normalize_globs(["", "  ", "*.py"])
        assert result == ["*.py"]

    def test_whitespace_trimmed(self):
        result = ContainerWorkspaceFS._normalize_globs(["  *.py  "])
        assert result == ["*.py"]


class TestInputBase:

    def test_simple_path(self):
        assert ContainerWorkspaceFS._input_base("host:///data/files") == "files"

    def test_artifact_ref(self):
        assert ContainerWorkspaceFS._input_base("artifact://model@3") == "model@3"

    def test_just_filename(self):
        assert ContainerWorkspaceFS._input_base("data.csv") == "data.csv"


class TestDetectMimeType:

    def test_png(self):
        assert ContainerWorkspaceFS._detect_mime_type(b'\x89PNG\r\n\x1a\n') == "image/png"

    def test_jpeg(self):
        assert ContainerWorkspaceFS._detect_mime_type(b'\xff\xd8\xff\xe0') == "image/jpeg"

    def test_pdf(self):
        assert ContainerWorkspaceFS._detect_mime_type(b'%PDF-1.4') == "application/pdf"

    def test_json_object(self):
        assert ContainerWorkspaceFS._detect_mime_type(b'{"key": "value"}') == "application/json"

    def test_json_array(self):
        assert ContainerWorkspaceFS._detect_mime_type(b'[1,2,3]') == "application/json"

    def test_plain_text(self):
        assert ContainerWorkspaceFS._detect_mime_type(b'hello world') == "text/plain"

    def test_empty(self):
        assert ContainerWorkspaceFS._detect_mime_type(b'') == "text/plain"


class TestCreateTarFromFiles:

    def test_creates_tar_with_files(self):
        files = [
            WorkspacePutFileInfo(path="a.txt", content=b"hello", mode=0o644),
            WorkspacePutFileInfo(path="b.txt", content=b"world", mode=0o755),
        ]
        tar_stream = ContainerWorkspaceFS._create_tar_from_files(files)

        assert isinstance(tar_stream, io.BytesIO)
        tar_stream.seek(0)
        with tarfile.open(fileobj=tar_stream, mode='r') as tar:
            names = tar.getnames()
            assert "a.txt" in names
            assert "b.txt" in names
            member_a = tar.getmember("a.txt")
            assert member_a.size == 5
            assert member_a.mode == 0o644

    def test_empty_files_list(self):
        tar_stream = ContainerWorkspaceFS._create_tar_from_files([])
        tar_stream.seek(0)
        with tarfile.open(fileobj=tar_stream, mode='r') as tar:
            assert tar.getnames() == []


# ---------------------------------------------------------------------------
# ContainerWorkspaceFS.put_files
# ---------------------------------------------------------------------------


class TestPutFiles:

    async def test_put_files_success(self):
        cc = _mock_container_client()
        cc.client.api.put_archive.return_value = True
        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)
        ws = _make_ws()

        files = [WorkspacePutFileInfo(path="test.txt", content=b"data", mode=0o644)]
        await fs.put_files(ws, files)

        cc.client.api.put_archive.assert_called_once()

    async def test_put_files_empty_list(self):
        cc = _mock_container_client()
        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)
        ws = _make_ws()

        await fs.put_files(ws, [])
        cc.client.api.put_archive.assert_not_called()

    async def test_put_files_failure(self):
        cc = _mock_container_client()
        cc.client.api.put_archive.return_value = False
        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)
        ws = _make_ws()

        files = [WorkspacePutFileInfo(path="f.txt", content=b"x", mode=0o644)]
        with pytest.raises(RuntimeError, match="Failed to put files"):
            await fs.put_files(ws, files)


# ---------------------------------------------------------------------------
# ContainerWorkspaceFS.stage_directory
# ---------------------------------------------------------------------------


class TestStageDirectory:

    async def test_stage_without_mount(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "f.txt").write_text("data")

        cc = _mock_container_client()
        cc.client.api.put_archive.return_value = True
        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)
        ws = _make_ws()

        opt = WorkspaceStageOptions(allow_mount=False, read_only=False)
        await fs.stage_directory(ws, str(src), "dest", opt)

        cc.client.api.put_archive.assert_called()

    async def test_stage_with_read_only(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "f.txt").write_text("data")

        cc = _mock_container_client()
        cc.client.api.put_archive.return_value = True
        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)
        ws = _make_ws()

        opt = WorkspaceStageOptions(allow_mount=False, read_only=True)
        await fs.stage_directory(ws, str(src), "dest", opt)

        # chmod call should be made
        assert cc.exec_run.await_count >= 2

    async def test_stage_with_mount_and_skills_base(self, tmp_path):
        cc = _mock_container_client()
        cc.client.api.put_archive.return_value = True

        src_dir = tmp_path / "skill1"
        src_dir.mkdir()

        cfg = RuntimeConfig(skills_host_base=str(tmp_path))
        fs = ContainerWorkspaceFS(cc, cfg)
        ws = _make_ws()

        opt = WorkspaceStageOptions(allow_mount=True, read_only=False)
        await fs.stage_directory(ws, str(src_dir), "skills/s1", opt)

        assert cc.exec_run.await_count >= 1

    async def test_stage_read_only_chmod_fails(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "f.txt").write_text("data")

        cc = _mock_container_client()
        cc.client.api.put_archive.return_value = True
        cc.exec_run = AsyncMock(side_effect=[_ok(), _err("chmod failed")])
        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)
        ws = _make_ws()

        opt = WorkspaceStageOptions(allow_mount=False, read_only=True)
        with pytest.raises(RuntimeError, match="Failed to chmod"):
            await fs.stage_directory(ws, str(src), "dest", opt)


# ---------------------------------------------------------------------------
# ContainerWorkspaceFS.collect
# ---------------------------------------------------------------------------


class TestCollect:

    async def test_collect_files(self):
        ws = _make_ws()
        cc = _mock_container_client()
        cc.exec_run = AsyncMock(return_value=_ok(stdout=f"{ws.path}/out/result.txt\n"))

        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode='w') as tar:
            data = b"file content"
            info = tarfile.TarInfo(name="result.txt")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        tar_buf.seek(0)
        cc.client.api.get_archive.return_value = (iter([tar_buf.getvalue()]), {})

        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        files = await fs.collect(ws, ["out/*.txt"])
        assert len(files) == 1
        assert files[0].name == "out/result.txt"
        assert files[0].content == "file content"

    async def test_collect_empty_result(self):
        ws = _make_ws()
        cc = _mock_container_client()
        cc.exec_run = AsyncMock(return_value=_ok(stdout=""))

        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        files = await fs.collect(ws, ["*.nothing"])
        assert files == []

    async def test_collect_exec_fails(self):
        ws = _make_ws()
        cc = _mock_container_client()
        cc.exec_run = AsyncMock(return_value=_err("command failed"))

        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        with pytest.raises(RuntimeError, match="Failed to collect files"):
            await fs.collect(ws, ["*.py"])

    async def test_collect_deduplicates(self):
        ws = _make_ws()
        cc = _mock_container_client()
        cc.exec_run = AsyncMock(return_value=_ok(
            stdout=f"{ws.path}/a.txt\n{ws.path}/a.txt\n"))

        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode='w') as tar:
            data = b"content"
            info = tarfile.TarInfo(name="a.txt")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        tar_buf.seek(0)
        cc.client.api.get_archive.return_value = (iter([tar_buf.getvalue()]), {})

        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        files = await fs.collect(ws, ["*.txt"])
        assert len(files) == 1


# ---------------------------------------------------------------------------
# ContainerWorkspaceFS.stage_inputs
# ---------------------------------------------------------------------------


class TestStageInputs:

    async def test_stage_host_input(self):
        ws = _make_ws()
        cc = _mock_container_client()
        cc.client.api.put_archive.return_value = True
        cfg = RuntimeConfig(inputs_host_base="/host/inputs")
        fs = ContainerWorkspaceFS(cc, cfg)

        specs = [WorkspaceInputSpec(src="host:///host/inputs/data", dst="work/data", mode="copy")]
        await fs.stage_inputs(ws, specs)

        assert cc.exec_run.await_count >= 1

    async def test_stage_workspace_input_copy(self):
        ws = _make_ws()
        cc = _mock_container_client()
        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        specs = [WorkspaceInputSpec(src="workspace://work/file.txt", dst="work/copy.txt", mode="copy")]
        await fs.stage_inputs(ws, specs)

        cc.exec_run.assert_called()

    async def test_stage_workspace_input_link(self):
        ws = _make_ws()
        cc = _mock_container_client()
        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        specs = [WorkspaceInputSpec(src="workspace://work/src", dst="work/link", mode="link")]
        await fs.stage_inputs(ws, specs)

        cmd_str = str(cc.exec_run.await_args)
        assert "ln" in cmd_str

    async def test_stage_skill_input(self):
        ws = _make_ws()
        cc = _mock_container_client()
        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        specs = [WorkspaceInputSpec(src="skill://my_skill/data.csv", dst="work/s", mode="copy")]
        await fs.stage_inputs(ws, specs)
        cc.exec_run.assert_called()

    async def test_stage_artifact_input(self):
        ws = _make_ws()
        cc = _mock_container_client()
        cc.client.api.put_archive.return_value = True
        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        mock_ctx = MagicMock(spec=InvocationContext)
        mock_artifact_entry = MagicMock()
        mock_artifact_entry.version.version = 1
        mock_artifact_entry.data.inline_data.data = b"artifact data"
        mock_ctx.load_artifact = AsyncMock(return_value=mock_artifact_entry)

        specs = [WorkspaceInputSpec(src="artifact://model@1", dst="work/model", mode="copy")]
        await fs.stage_inputs(ws, specs, ctx=mock_ctx)

        cc.client.api.put_archive.assert_called()

    async def test_stage_artifact_without_ctx_raises(self):
        ws = _make_ws()
        cc = _mock_container_client()
        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        specs = [WorkspaceInputSpec(src="artifact://model", dst="work/model", mode="copy")]
        with pytest.raises(ValueError, match="Context is required"):
            await fs.stage_inputs(ws, specs, ctx=None)

    async def test_stage_unsupported_scheme(self):
        ws = _make_ws()
        cc = _mock_container_client()
        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        specs = [WorkspaceInputSpec(src="ftp://server/file", dst="work/f", mode="copy")]
        with pytest.raises(RuntimeError, match="Unsupported input"):
            await fs.stage_inputs(ws, specs)

    async def test_stage_input_default_dst(self):
        ws = _make_ws()
        cc = _mock_container_client()
        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        specs = [WorkspaceInputSpec(src="workspace://work/file.txt", dst="", mode="copy")]
        await fs.stage_inputs(ws, specs)
        cc.exec_run.assert_called()

    async def test_stage_workspace_input_fails(self):
        ws = _make_ws()
        cc = _mock_container_client()
        cc.exec_run = AsyncMock(return_value=_err("cp failed"))
        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        specs = [WorkspaceInputSpec(src="workspace://work/src", dst="work/dst", mode="copy")]
        with pytest.raises(RuntimeError, match="Failed to stage input"):
            await fs.stage_inputs(ws, specs)

    async def test_stage_host_input_link_mode(self):
        ws = _make_ws()
        cc = _mock_container_client()
        cfg = RuntimeConfig(inputs_host_base="/host/inputs")
        fs = ContainerWorkspaceFS(cc, cfg)

        specs = [WorkspaceInputSpec(src="host:///host/inputs/data", dst="work/link", mode="link")]
        await fs.stage_inputs(ws, specs)
        cmd_str = str(cc.exec_run.await_args)
        assert "ln" in cmd_str

    async def test_stage_host_input_no_inputs_host_base(self, tmp_path):
        src = tmp_path / "data"
        src.mkdir()
        (src / "f.txt").write_text("x")

        ws = _make_ws()
        cc = _mock_container_client()
        cc.client.api.put_archive.return_value = True
        cfg = RuntimeConfig(inputs_host_base="")
        fs = ContainerWorkspaceFS(cc, cfg)

        specs = [WorkspaceInputSpec(src=f"host://{src}", dst="work/d", mode="copy")]
        await fs.stage_inputs(ws, specs)
        cc.client.api.put_archive.assert_called()


# ---------------------------------------------------------------------------
# ContainerWorkspaceFS.collect_outputs
# ---------------------------------------------------------------------------


class TestCollectOutputs:

    def _make_tar_archive(self, filename="result.txt", data=b"output data"):
        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode='w') as tar:
            info = tarfile.TarInfo(name=filename)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        tar_buf.seek(0)
        return tar_buf

    async def test_collect_outputs_inline(self):
        ws = _make_ws()
        cc = _mock_container_client()
        cc.exec_run = AsyncMock(return_value=_ok(stdout=f"{ws.path}/out/result.txt\n"))

        tar_buf = self._make_tar_archive()
        cc.client.api.get_archive.return_value = (iter([tar_buf.getvalue()]), {})

        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        spec = WorkspaceOutputSpec(globs=["out/**"], inline=True)
        manifest = await fs.collect_outputs(ws, spec)

        assert len(manifest.files) == 1
        assert manifest.files[0].content == "output data"
        assert manifest.files[0].name == "out/result.txt"
        assert manifest.limits_hit is False

    async def test_collect_outputs_max_files_limit(self):
        ws = _make_ws()
        cc = _mock_container_client()
        lines = "\n".join(f"{ws.path}/f{i}.txt" for i in range(10))
        cc.exec_run = AsyncMock(return_value=_ok(stdout=lines))

        def _make_tar_side_effect(*args, **kwargs):
            tar_buf = self._make_tar_archive("file.txt", b"content")
            return (iter([tar_buf.getvalue()]), {})

        cc.client.api.get_archive.side_effect = _make_tar_side_effect

        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        spec = WorkspaceOutputSpec(globs=["*"], max_files=3, inline=True)
        manifest = await fs.collect_outputs(ws, spec)

        assert len(manifest.files) == 3
        assert manifest.limits_hit is True

    async def test_collect_outputs_max_file_bytes_truncation(self):
        ws = _make_ws()
        cc = _mock_container_client()
        cc.exec_run = AsyncMock(return_value=_ok(stdout=f"{ws.path}/big.txt\n"))

        big_data = b"x" * 1000
        tar_buf = self._make_tar_archive("big.txt", big_data)
        cc.client.api.get_archive.return_value = (iter([tar_buf.getvalue()]), {})

        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        spec = WorkspaceOutputSpec(globs=["*"], max_file_bytes=100, inline=True)
        manifest = await fs.collect_outputs(ws, spec)

        assert manifest.limits_hit is True

    async def test_collect_outputs_exec_fails(self):
        ws = _make_ws()
        cc = _mock_container_client()
        cc.exec_run = AsyncMock(return_value=_err("find failed"))

        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        spec = WorkspaceOutputSpec(globs=["*"])
        with pytest.raises(RuntimeError, match="Failed to collect outputs"):
            await fs.collect_outputs(ws, spec)

    async def test_collect_outputs_save_artifact(self):
        ws = _make_ws()
        cc = _mock_container_client()
        cc.exec_run = AsyncMock(return_value=_ok(stdout=f"{ws.path}/out/model.bin\n"))

        tar_buf = self._make_tar_archive("model.bin", b"model data")
        cc.client.api.get_archive.return_value = (iter([tar_buf.getvalue()]), {})

        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        mock_ctx = MagicMock(spec=InvocationContext)
        mock_ctx.save_artifact = AsyncMock(return_value=1)

        spec = WorkspaceOutputSpec(globs=["out/*"], save=True, inline=False)
        manifest = await fs.collect_outputs(ws, spec, ctx=mock_ctx)

        assert len(manifest.files) == 1
        assert manifest.files[0].saved_as == "out/model.bin"
        assert manifest.files[0].version == 1

    async def test_collect_outputs_save_with_name_template(self):
        ws = _make_ws()
        cc = _mock_container_client()
        cc.exec_run = AsyncMock(return_value=_ok(stdout=f"{ws.path}/out/file.txt\n"))

        tar_buf = self._make_tar_archive("file.txt", b"data")
        cc.client.api.get_archive.return_value = (iter([tar_buf.getvalue()]), {})

        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        mock_ctx = MagicMock(spec=InvocationContext)
        mock_ctx.save_artifact = AsyncMock(return_value=2)

        spec = WorkspaceOutputSpec(globs=["out/*"], save=True, name_template="prefix/")
        manifest = await fs.collect_outputs(ws, spec, ctx=mock_ctx)

        assert manifest.files[0].saved_as == "prefix/out/file.txt"

    async def test_collect_outputs_save_without_ctx_raises(self):
        ws = _make_ws()
        cc = _mock_container_client()
        cc.exec_run = AsyncMock(return_value=_ok(stdout=f"{ws.path}/out/x.txt\n"))

        tar_buf = self._make_tar_archive("x.txt", b"data")
        cc.client.api.get_archive.return_value = (iter([tar_buf.getvalue()]), {})

        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        spec = WorkspaceOutputSpec(globs=["out/*"], save=True)
        with pytest.raises(ValueError, match="Context is required"):
            await fs.collect_outputs(ws, spec, ctx=None)

    async def test_collect_outputs_empty(self):
        ws = _make_ws()
        cc = _mock_container_client()
        cc.exec_run = AsyncMock(return_value=_ok(stdout="\n"))

        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        spec = WorkspaceOutputSpec(globs=["out/*"])
        manifest = await fs.collect_outputs(ws, spec)

        assert len(manifest.files) == 0

    async def test_collect_outputs_max_total_bytes(self):
        ws = _make_ws()
        cc = _mock_container_client()
        lines = "\n".join(f"{ws.path}/f{i}.txt" for i in range(5))
        cc.exec_run = AsyncMock(return_value=_ok(stdout=lines))

        big_data = b"x" * 100

        def _make_tar_side_effect(*args, **kwargs):
            tar_buf = self._make_tar_archive("f.txt", big_data)
            return (iter([tar_buf.getvalue()]), {})

        cc.client.api.get_archive.side_effect = _make_tar_side_effect

        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        spec = WorkspaceOutputSpec(globs=["*"], max_total_bytes=150, inline=True)
        manifest = await fs.collect_outputs(ws, spec)

        assert manifest.limits_hit is True


# ---------------------------------------------------------------------------
# ContainerWorkspaceFS._copy_file_out
# ---------------------------------------------------------------------------


class TestCopyFileOut:

    def test_copy_file_out_success(self):
        cc = _mock_container_client()
        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        data = b"file content"
        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode='w') as tar:
            info = tarfile.TarInfo(name="test.txt")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        tar_buf.seek(0)

        cc.client.api.get_archive.return_value = (iter([tar_buf.getvalue()]), {})

        result_data, mime = fs._copy_file_out("/container/path/test.txt")
        assert result_data == b"file content"
        assert mime == "text/plain"

    def test_copy_file_out_no_file_in_archive(self):
        cc = _mock_container_client()
        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode='w') as tar:
            info = tarfile.TarInfo(name="dir/")
            info.type = tarfile.DIRTYPE
            tar.addfile(info)
        tar_buf.seek(0)

        cc.client.api.get_archive.return_value = (iter([tar_buf.getvalue()]), {})

        with pytest.raises(RuntimeError, match="Failed to copy file"):
            fs._copy_file_out("/container/path/dir")

    def test_copy_file_out_api_exception(self):
        cc = _mock_container_client()
        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        cc.client.api.get_archive.side_effect = Exception("API error")

        with pytest.raises(RuntimeError, match="Failed to copy file"):
            fs._copy_file_out("/container/path/missing.txt")


# ---------------------------------------------------------------------------
# ContainerWorkspaceFS._put_bytes_tar
# ---------------------------------------------------------------------------


class TestPutBytesTar:

    async def test_put_bytes_tar_success(self):
        cc = _mock_container_client()
        cc.client.api.put_archive.return_value = True
        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        await fs._put_bytes_tar(b"hello", "/container/work/inputs/data.txt")
        cc.client.api.put_archive.assert_called_once()

    async def test_put_bytes_tar_mkdir_fails(self):
        cc = _mock_container_client()
        cc.exec_run = AsyncMock(return_value=_err("mkdir failed"))
        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        with pytest.raises(RuntimeError, match="Failed to stage directory"):
            await fs._put_bytes_tar(b"data", "/container/work/inputs/file.bin")

    async def test_put_bytes_tar_put_archive_fails(self):
        cc = _mock_container_client()
        cc.client.api.put_archive.return_value = False
        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        with pytest.raises(RuntimeError, match="Failed to copy bytes"):
            await fs._put_bytes_tar(b"data", "/container/work/inputs/file.bin")


# ---------------------------------------------------------------------------
# ContainerWorkspaceFS._put_directory
# ---------------------------------------------------------------------------


class TestPutDirectory:

    async def test_put_directory_tar_fallback(self, tmp_path):
        src = tmp_path / "source"
        src.mkdir()
        (src / "test.txt").write_text("hello")

        cc = _mock_container_client()
        cc.client.api.put_archive.return_value = True
        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)
        ws = _make_ws()

        await fs._put_directory(ws, str(src), "dest")
        cc.client.api.put_archive.assert_called()

    async def test_put_directory_with_skills_base(self, tmp_path):
        src = tmp_path / "skill1"
        src.mkdir()
        (src / "f.txt").write_text("data")

        cc = _mock_container_client()
        cc.client.api.put_archive.return_value = True
        cfg = RuntimeConfig(skills_host_base=str(tmp_path))
        fs = ContainerWorkspaceFS(cc, cfg)
        ws = _make_ws()

        await fs._put_directory(ws, str(src), "skills/s1")
        assert cc.exec_run.await_count >= 1

    async def test_put_directory_mkdir_fails(self, tmp_path):
        src = tmp_path / "source"
        src.mkdir()

        cc = _mock_container_client()
        cc.exec_run = AsyncMock(return_value=_err("mkdir failed"))
        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)
        ws = _make_ws()

        with pytest.raises(RuntimeError, match="Failed to stage directory"):
            await fs._put_directory(ws, str(src), "dest")

    async def test_put_directory_archive_fails(self, tmp_path):
        src = tmp_path / "source"
        src.mkdir()
        (src / "f.txt").write_text("x")

        cc = _mock_container_client()
        cc.client.api.put_archive.return_value = False
        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)
        ws = _make_ws()

        with pytest.raises(RuntimeError, match="Failed to copy directory"):
            await fs._put_directory(ws, str(src), "dest")

    async def test_put_directory_no_dst(self, tmp_path):
        src = tmp_path / "source"
        src.mkdir()
        (src / "f.txt").write_text("data")

        cc = _mock_container_client()
        cc.client.api.put_archive.return_value = True
        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)
        ws = _make_ws()

        await fs._put_directory(ws, str(src), "")
        cc.client.api.put_archive.assert_called()


# ---------------------------------------------------------------------------
# ContainerWorkspaceFS._stage_host_input
# ---------------------------------------------------------------------------


class TestStageHostInput:

    async def test_with_inputs_host_base_copy(self):
        ws = _make_ws()
        cc = _mock_container_client()
        cfg = RuntimeConfig(inputs_host_base="/host/inputs")
        fs = ContainerWorkspaceFS(cc, cfg)

        await fs._stage_host_input(ws, "/host/inputs/data.csv", "/ws/work/data.csv", "copy")
        cmd_str = str(cc.exec_run.await_args)
        assert "cp" in cmd_str

    async def test_with_inputs_host_base_link(self):
        ws = _make_ws()
        cc = _mock_container_client()
        cfg = RuntimeConfig(inputs_host_base="/host/inputs")
        fs = ContainerWorkspaceFS(cc, cfg)

        await fs._stage_host_input(ws, "/host/inputs/data", "/ws/work/data", "link")
        cmd_str = str(cc.exec_run.await_args)
        assert "ln" in cmd_str

    async def test_fallback_to_tar_copy(self, tmp_path):
        src = tmp_path / "data"
        src.mkdir()
        (src / "f.txt").write_text("x")

        ws = _make_ws()
        cc = _mock_container_client()
        cc.client.api.put_archive.return_value = True
        cfg = RuntimeConfig(inputs_host_base="")
        fs = ContainerWorkspaceFS(cc, cfg)

        await fs._stage_host_input(ws, str(src), "/ws/work/data", "copy")
        cc.client.api.put_archive.assert_called()


# ---------------------------------------------------------------------------
# ContainerWorkspaceFS._stage_workspace_input
# ---------------------------------------------------------------------------


class TestStageWorkspaceInput:

    async def test_copy_mode(self):
        cc = _mock_container_client()
        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        await fs._stage_workspace_input("/ws/work/src", "/ws/work/dst", "copy")
        cmd_str = str(cc.exec_run.await_args)
        assert "cp" in cmd_str

    async def test_link_mode(self):
        cc = _mock_container_client()
        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        await fs._stage_workspace_input("/ws/work/src", "/ws/work/link", "link")
        cmd_str = str(cc.exec_run.await_args)
        assert "ln" in cmd_str

    async def test_exec_fails_raises(self):
        cc = _mock_container_client()
        cc.exec_run = AsyncMock(return_value=_err("cp error"))
        cfg = RuntimeConfig()
        fs = ContainerWorkspaceFS(cc, cfg)

        with pytest.raises(RuntimeError, match="Failed to stage input"):
            await fs._stage_workspace_input("/ws/src", "/ws/dst", "copy")


# ---------------------------------------------------------------------------
# ContainerProgramRunner._shell_quote
# ---------------------------------------------------------------------------


class TestShellQuote:

    def test_empty_string(self):
        assert ContainerProgramRunner._shell_quote("") == "''"

    def test_simple_string(self):
        assert ContainerProgramRunner._shell_quote("hello") == "'hello'"

    def test_string_with_single_quote(self):
        result = ContainerProgramRunner._shell_quote("it's")
        assert result == "'it'\\''s'"

    def test_string_with_spaces(self):
        assert ContainerProgramRunner._shell_quote("hello world") == "'hello world'"

    def test_string_with_special_chars(self):
        assert ContainerProgramRunner._shell_quote("a;b|c") == "'a;b|c'"


# ---------------------------------------------------------------------------
# ContainerProgramRunner.run_program
# ---------------------------------------------------------------------------


class TestRunProgram:

    async def test_basic_run(self):
        cc = _mock_container_client()
        cc.exec_run = AsyncMock(return_value=CommandExecResult(
            stdout="output", stderr="", exit_code=0, is_timeout=False))
        cfg = RuntimeConfig()
        runner = ContainerProgramRunner(cc, cfg)
        ws = _make_ws()

        spec = WorkspaceRunProgramSpec(cmd="python3", args=["script.py"])
        result = await runner.run_program(ws, spec)

        assert isinstance(result, WorkspaceRunResult)
        assert result.stdout == "output"
        assert result.exit_code == 0
        assert result.timed_out is False

    async def test_run_with_cwd(self):
        cc = _mock_container_client()
        cc.exec_run = AsyncMock(return_value=CommandExecResult(
            stdout="", stderr="", exit_code=0, is_timeout=False))
        cfg = RuntimeConfig()
        runner = ContainerProgramRunner(cc, cfg)
        ws = _make_ws()

        spec = WorkspaceRunProgramSpec(cmd="ls", cwd="work")
        result = await runner.run_program(ws, spec)
        assert result.exit_code == 0

    async def test_run_with_custom_env(self):
        cc = _mock_container_client()
        cc.exec_run = AsyncMock(return_value=CommandExecResult(
            stdout="", stderr="", exit_code=0, is_timeout=False))
        cfg = RuntimeConfig()
        runner = ContainerProgramRunner(cc, cfg)
        ws = _make_ws()

        spec = WorkspaceRunProgramSpec(cmd="echo", env={"MY_VAR": "123"})
        result = await runner.run_program(ws, spec)
        assert result.exit_code == 0

    async def test_run_with_timeout(self):
        cc = _mock_container_client()
        cc.exec_run = AsyncMock(return_value=CommandExecResult(
            stdout="", stderr="timed out", exit_code=-1, is_timeout=True))
        cfg = RuntimeConfig(command_args=CommandArgs(timeout=30.0))
        runner = ContainerProgramRunner(cc, cfg)
        ws = _make_ws()

        spec = WorkspaceRunProgramSpec(cmd="sleep", args=["100"], timeout=5.0)
        result = await runner.run_program(ws, spec)
        assert result.timed_out is True

        call_args = cc.exec_run.await_args.kwargs.get("command_args")
        assert call_args.timeout == 5.0

    async def test_run_timeout_from_config_when_spec_zero(self):
        """When spec.timeout is 0 (falsy), config timeout is selected initially,
        but then min(config_timeout, spec.timeout=0) yields 0."""
        cc = _mock_container_client()
        cc.exec_run = AsyncMock(return_value=CommandExecResult(
            stdout="ok", stderr="", exit_code=0, is_timeout=False))
        cfg = RuntimeConfig(command_args=CommandArgs(timeout=10.0))
        runner = ContainerProgramRunner(cc, cfg)
        ws = _make_ws()

        spec = WorkspaceRunProgramSpec(cmd="echo", timeout=0)
        await runner.run_program(ws, spec)

        call_args = cc.exec_run.await_args.kwargs.get("command_args")
        assert call_args.timeout == 0

    async def test_run_timeout_spec_takes_precedence_when_truthy(self):
        """When spec.timeout is truthy, it is used as the initial timeout,
        then min(spec.timeout, spec.timeout) keeps it."""
        cc = _mock_container_client()
        cc.exec_run = AsyncMock(return_value=CommandExecResult(
            stdout="ok", stderr="", exit_code=0, is_timeout=False))
        cfg = RuntimeConfig(command_args=CommandArgs(timeout=10.0))
        runner = ContainerProgramRunner(cc, cfg)
        ws = _make_ws()

        spec = WorkspaceRunProgramSpec(cmd="echo", timeout=15.0)
        await runner.run_program(ws, spec)

        call_args = cc.exec_run.await_args.kwargs.get("command_args")
        assert call_args.timeout == 15.0

    async def test_run_timeout_no_config_no_spec(self):
        """When config timeout is None and spec.timeout is 0 (falsy), the
        initial `or` yields None, then the `is None` branch sets timeout = spec.timeout = 0."""
        cc = _mock_container_client()
        cc.exec_run = AsyncMock(return_value=CommandExecResult(
            stdout="ok", stderr="", exit_code=0, is_timeout=False))
        cfg = RuntimeConfig(command_args=CommandArgs(timeout=None))
        runner = ContainerProgramRunner(cc, cfg)
        ws = _make_ws()

        spec = WorkspaceRunProgramSpec(cmd="echo", timeout=0)
        await runner.run_program(ws, spec)

        call_args = cc.exec_run.await_args.kwargs.get("command_args")
        assert call_args.timeout == 0

    async def test_run_measures_duration(self):
        cc = _mock_container_client()
        cc.exec_run = AsyncMock(return_value=CommandExecResult(
            stdout="ok", stderr="", exit_code=0, is_timeout=False))
        cfg = RuntimeConfig()
        runner = ContainerProgramRunner(cc, cfg)
        ws = _make_ws()

        spec = WorkspaceRunProgramSpec(cmd="echo")
        result = await runner.run_program(ws, spec)
        assert result.duration >= 0

    async def test_run_with_env_override_base(self):
        cc = _mock_container_client()
        cc.exec_run = AsyncMock(return_value=CommandExecResult(
            stdout="", stderr="", exit_code=0, is_timeout=False))
        cfg = RuntimeConfig()
        runner = ContainerProgramRunner(cc, cfg)
        ws = _make_ws()

        spec = WorkspaceRunProgramSpec(
            cmd="echo", env={"WORKSPACE_DIR": "/custom/ws"})
        await runner.run_program(ws, spec)

        cmd_str = str(cc.exec_run.await_args)
        assert "WORKSPACE_DIR=" in cmd_str


# ---------------------------------------------------------------------------
# ContainerWorkspaceRuntime
# ---------------------------------------------------------------------------


class TestContainerWorkspaceRuntime:

    def test_init_without_host_config(self):
        cc = _mock_container_client()
        runtime = ContainerWorkspaceRuntime(container=cc)

        assert isinstance(runtime.manager(), ContainerWorkspaceManager)
        assert isinstance(runtime.fs(), ContainerWorkspaceFS)
        assert isinstance(runtime.runner(), ContainerProgramRunner)

    def test_init_with_host_config_binds(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        inputs_dir = tmp_path / "inputs"
        inputs_dir.mkdir()

        cc = _mock_container_client()
        host_config = {
            "Binds": [
                f"{skills_dir}:{DEFAULT_SKILLS_CONTAINER}:ro",
                f"{inputs_dir}:{DEFAULT_INPUTS_CONTAINER}:ro",
            ]
        }
        runtime = ContainerWorkspaceRuntime(
            container=cc, host_config=host_config, auto_inputs=True)

        assert isinstance(runtime.manager(), ContainerWorkspaceManager)

    def test_init_with_host_config_no_binds(self):
        cc = _mock_container_client()
        runtime = ContainerWorkspaceRuntime(
            container=cc, host_config={"Other": "value"})
        assert isinstance(runtime.manager(), ContainerWorkspaceManager)

    def test_describe(self):
        cc = _mock_container_client()
        runtime = ContainerWorkspaceRuntime(container=cc)
        caps = runtime.describe()

        assert isinstance(caps, WorkspaceCapabilities)
        assert caps.isolation == "container"
        assert caps.network_allowed is True
        assert caps.read_only_mount is True
        assert caps.streaming is True

    def test_manager_fs_runner_return_correct_types(self):
        cc = _mock_container_client()
        runtime = ContainerWorkspaceRuntime(container=cc)

        assert isinstance(runtime.manager(), ContainerWorkspaceManager)
        assert isinstance(runtime.fs(), ContainerWorkspaceFS)
        assert isinstance(runtime.runner(), ContainerProgramRunner)


# ---------------------------------------------------------------------------
# ContainerWorkspaceRuntime._find_bind_source
# ---------------------------------------------------------------------------


class TestFindBindSource:

    def test_two_part_bind_does_not_match(self, tmp_path):
        """Two-part bind: parts[-2] yields source (not dest), so it won't match
        unless source happens to equal the dest string."""
        bind_dir = tmp_path / "skills"
        bind_dir.mkdir()
        binds = [f"{bind_dir}:/opt/skills"]
        result = ContainerWorkspaceRuntime._find_bind_source(binds, "/opt/skills")
        assert result == ""

    def test_three_part_bind(self, tmp_path):
        bind_dir = tmp_path / "skills"
        bind_dir.mkdir()
        binds = [f"{bind_dir}:/opt/skills:ro"]
        result = ContainerWorkspaceRuntime._find_bind_source(binds, "/opt/skills")
        assert result == str(bind_dir)

    def test_no_matching_bind(self):
        binds = ["/host/data:/container/data:rw"]
        result = ContainerWorkspaceRuntime._find_bind_source(binds, "/opt/skills")
        assert result == ""

    def test_single_part_bind_skipped(self):
        binds = ["/just/a/path"]
        result = ContainerWorkspaceRuntime._find_bind_source(binds, "/just/a/path")
        assert result == ""

    def test_source_dir_not_exists(self):
        binds = ["/nonexistent/path:/opt/skills:ro"]
        result = ContainerWorkspaceRuntime._find_bind_source(binds, "/opt/skills")
        assert result == ""

    def test_empty_binds(self):
        result = ContainerWorkspaceRuntime._find_bind_source([], "/opt/skills")
        assert result == ""

    def test_multiple_binds_finds_correct_one(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        inputs_dir = tmp_path / "inputs"
        inputs_dir.mkdir()
        binds = [
            f"{skills_dir}:{DEFAULT_SKILLS_CONTAINER}:ro",
            f"{inputs_dir}:{DEFAULT_INPUTS_CONTAINER}:ro",
        ]
        result = ContainerWorkspaceRuntime._find_bind_source(binds, DEFAULT_SKILLS_CONTAINER)
        assert result == str(skills_dir)


# ---------------------------------------------------------------------------
# create_container_workspace_runtime
# ---------------------------------------------------------------------------


class TestCreateContainerWorkspaceRuntime:

    @patch("trpc_agent_sdk.code_executors.container._container_ws_runtime.ContainerClient")
    def test_with_container_config(self, mock_cc_cls):
        mock_cc_cls.return_value = _mock_container_client()
        cfg = ContainerConfig(image="custom:latest")
        runtime = create_container_workspace_runtime(
            container_config=cfg, host_config=None, auto_inputs=False)

        assert isinstance(runtime, ContainerWorkspaceRuntime)
        mock_cc_cls.assert_called_once()

    @patch("trpc_agent_sdk.code_executors.container._container_ws_runtime.ContainerClient")
    def test_without_container_config(self, mock_cc_cls):
        mock_cc_cls.return_value = _mock_container_client()
        runtime = create_container_workspace_runtime()

        assert isinstance(runtime, ContainerWorkspaceRuntime)
        mock_cc_cls.assert_called_once()

    @patch("trpc_agent_sdk.code_executors.container._container_ws_runtime.ContainerClient")
    def test_with_host_config(self, mock_cc_cls):
        mock_cc_cls.return_value = _mock_container_client()
        hcfg = {"Binds": ["/host:/container:ro"]}
        runtime = create_container_workspace_runtime(
            container_config=ContainerConfig(), host_config=hcfg, auto_inputs=True)

        assert isinstance(runtime, ContainerWorkspaceRuntime)
        call_kwargs = mock_cc_cls.call_args
        passed_config = call_kwargs.kwargs.get("config", call_kwargs.args[0] if call_kwargs.args else None)
        assert passed_config.host_config == hcfg
