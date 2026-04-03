# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock
from unittest.mock import Mock
from unittest.mock import patch

import pytest
from trpc_agent_sdk.code_executors._base_workspace_runtime import BaseWorkspaceFS
from trpc_agent_sdk.code_executors._base_workspace_runtime import BaseWorkspaceManager
from trpc_agent_sdk.code_executors._base_workspace_runtime import BaseProgramRunner
from trpc_agent_sdk.code_executors._constants import DEFAULT_FILE_MODE
from trpc_agent_sdk.code_executors._constants import DEFAULT_MAX_FILES
from trpc_agent_sdk.code_executors._constants import DEFAULT_MAX_TOTAL_BYTES
from trpc_agent_sdk.code_executors._constants import DIR_OUT
from trpc_agent_sdk.code_executors._constants import DIR_RUNS
from trpc_agent_sdk.code_executors._constants import DIR_SKILLS
from trpc_agent_sdk.code_executors._constants import DIR_WORK
from trpc_agent_sdk.code_executors._constants import MAX_READ_SIZE_BYTES
from trpc_agent_sdk.code_executors._constants import META_FILE_NAME
from trpc_agent_sdk.code_executors._types import CodeFile
from trpc_agent_sdk.code_executors._types import ManifestFileRef
from trpc_agent_sdk.code_executors._types import ManifestOutput
from trpc_agent_sdk.code_executors._types import WorkspaceCapabilities
from trpc_agent_sdk.code_executors._types import WorkspaceInfo
from trpc_agent_sdk.code_executors._types import WorkspaceInputSpec
from trpc_agent_sdk.code_executors._types import WorkspaceOutputSpec
from trpc_agent_sdk.code_executors._types import WorkspacePutFileInfo
from trpc_agent_sdk.code_executors._types import WorkspaceRunProgramSpec
from trpc_agent_sdk.code_executors._types import WorkspaceRunResult
from trpc_agent_sdk.code_executors._types import WorkspaceStageOptions
from trpc_agent_sdk.code_executors.local._local_ws_runtime import LocalProgramRunner
from trpc_agent_sdk.code_executors.local._local_ws_runtime import LocalWorkspaceFS
from trpc_agent_sdk.code_executors.local._local_ws_runtime import LocalWorkspaceManager
from trpc_agent_sdk.code_executors.local._local_ws_runtime import LocalWorkspaceRuntime
from trpc_agent_sdk.code_executors.local._local_ws_runtime import create_local_workspace_runtime
from trpc_agent_sdk.code_executors.utils import ensure_layout
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.utils import CommandExecResult


# ---------------------------------------------------------------------------
# LocalWorkspaceManager Tests
# ---------------------------------------------------------------------------
class TestLocalWorkspaceManager:
    """Tests for LocalWorkspaceManager."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.mock_fs = Mock(spec=BaseWorkspaceFS)
        self.manager = LocalWorkspaceManager(
            work_root=self.tmpdir,
            auto_inputs=False,
            inputs_host_base="",
            fs=self.mock_fs,
        )

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_create_workspace(self):
        ws = await self.manager.create_workspace("exec-1")
        assert isinstance(ws, WorkspaceInfo)
        assert ws.id == "exec-1"
        assert Path(ws.path).exists()

    @pytest.mark.asyncio
    async def test_create_workspace_idempotent(self):
        ws1 = await self.manager.create_workspace("exec-1")
        ws2 = await self.manager.create_workspace("exec-1")
        assert ws1.path == ws2.path

    @pytest.mark.asyncio
    async def test_create_workspace_different_ids(self):
        ws1 = await self.manager.create_workspace("exec-1")
        ws2 = await self.manager.create_workspace("exec-2")
        assert ws1.path != ws2.path

    @pytest.mark.asyncio
    async def test_create_workspace_sanitizes_exec_id(self):
        ws = await self.manager.create_workspace("exec/special:id@test")
        assert Path(ws.path).exists()
        dirname = Path(ws.path).name
        assert "/" not in dirname
        assert ":" not in dirname
        assert "@" not in dirname

    @pytest.mark.asyncio
    async def test_create_workspace_layout(self):
        ws = await self.manager.create_workspace("exec-1")
        ws_path = Path(ws.path)
        assert (ws_path / DIR_SKILLS).exists()
        assert (ws_path / DIR_WORK).exists()
        assert (ws_path / DIR_RUNS).exists()
        assert (ws_path / DIR_OUT).exists()
        assert (ws_path / META_FILE_NAME).exists()

    @pytest.mark.asyncio
    async def test_create_workspace_permissions(self):
        ws = await self.manager.create_workspace("exec-1")
        ws_path = Path(ws.path)
        mode = ws_path.stat().st_mode & 0o777
        assert mode == 0o777

    @pytest.mark.asyncio
    async def test_create_workspace_with_auto_inputs(self):
        self.mock_fs.stage_inputs = AsyncMock()
        mgr = LocalWorkspaceManager(
            work_root=self.tmpdir,
            auto_inputs=True,
            inputs_host_base="/host/path",
            fs=self.mock_fs,
        )
        ws = await mgr.create_workspace("exec-auto")
        self.mock_fs.stage_inputs.assert_called_once()
        call_args = self.mock_fs.stage_inputs.call_args
        assert call_args[0][0] == ws

    @pytest.mark.asyncio
    async def test_create_workspace_no_auto_inputs_without_host_base(self):
        self.mock_fs.stage_inputs = AsyncMock()
        mgr = LocalWorkspaceManager(
            work_root=self.tmpdir,
            auto_inputs=True,
            inputs_host_base="",
            fs=self.mock_fs,
        )
        await mgr.create_workspace("exec-no-host")
        self.mock_fs.stage_inputs.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_existing_workspace(self):
        ws = await self.manager.create_workspace("exec-cleanup")
        assert Path(ws.path).exists()
        await self.manager.cleanup("exec-cleanup")
        assert not Path(ws.path).exists()
        assert "exec-cleanup" not in self.manager.ws_paths

    @pytest.mark.asyncio
    async def test_cleanup_nonexistent_workspace(self):
        await self.manager.cleanup("nonexistent")

    @pytest.mark.asyncio
    async def test_cleanup_already_cleaned(self):
        ws = await self.manager.create_workspace("exec-double")
        shutil.rmtree(ws.path)
        await self.manager.cleanup("exec-double")
        assert "exec-double" not in self.manager.ws_paths

    def test_default_work_root_uses_tempdir(self):
        mgr = LocalWorkspaceManager(work_root="", fs=self.mock_fs)
        assert mgr.work_root == tempfile.gettempdir()


# ---------------------------------------------------------------------------
# LocalWorkspaceFS Tests
# ---------------------------------------------------------------------------
class TestLocalWorkspaceFS:
    """Tests for LocalWorkspaceFS."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ws = WorkspaceInfo(id="test-ws", path=self.tmpdir)
        self.fs = LocalWorkspaceFS()
        ensure_layout(self.tmpdir)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # --- put_files ---
    @pytest.mark.asyncio
    async def test_put_files_single(self):
        files = [WorkspacePutFileInfo(path="test.txt", content=b"hello", mode=0o644)]
        await self.fs.put_files(self.ws, files)
        written = Path(self.tmpdir) / "test.txt"
        assert written.exists()
        assert written.read_bytes() == b"hello"

    @pytest.mark.asyncio
    async def test_put_files_multiple(self):
        files = [
            WorkspacePutFileInfo(path="a.txt", content=b"a", mode=0o644),
            WorkspacePutFileInfo(path="b.txt", content=b"b", mode=0o644),
        ]
        await self.fs.put_files(self.ws, files)
        assert (Path(self.tmpdir) / "a.txt").read_bytes() == b"a"
        assert (Path(self.tmpdir) / "b.txt").read_bytes() == b"b"

    @pytest.mark.asyncio
    async def test_put_files_nested_path(self):
        files = [WorkspacePutFileInfo(path="sub/dir/file.txt", content=b"nested", mode=0o644)]
        await self.fs.put_files(self.ws, files)
        assert (Path(self.tmpdir) / "sub" / "dir" / "file.txt").read_bytes() == b"nested"

    @pytest.mark.asyncio
    async def test_put_files_empty_content(self):
        files = [WorkspacePutFileInfo(path="empty.txt", content=b"", mode=0o644)]
        await self.fs.put_files(self.ws, files)
        assert (Path(self.tmpdir) / "empty.txt").read_bytes() == b""

    @pytest.mark.asyncio
    async def test_put_files_empty_path_raises(self):
        files = [WorkspacePutFileInfo(path="", content=b"data")]
        with pytest.raises(ValueError, match="empty file path"):
            await self.fs.put_files(self.ws, files)

    @pytest.mark.asyncio
    async def test_put_files_absolute_path_outside_workspace(self):
        files = [WorkspacePutFileInfo(path="/absolute/outside.txt", content=b"data")]
        with pytest.raises(ValueError, match="path escapes workspace"):
            await self.fs.put_files(self.ws, files)

    @pytest.mark.asyncio
    async def test_put_files_default_mode(self):
        files = [WorkspacePutFileInfo(path="default_mode.txt", content=b"data", mode=0)]
        await self.fs.put_files(self.ws, files)
        written = Path(self.tmpdir) / "default_mode.txt"
        assert written.exists()
        mode = written.stat().st_mode & 0o777
        assert mode == DEFAULT_FILE_MODE

    # --- stage_directory ---
    @pytest.mark.asyncio
    async def test_stage_directory(self):
        src_dir = Path(self.tmpdir) / "source"
        src_dir.mkdir()
        (src_dir / "file.txt").write_text("content")
        (src_dir / "sub").mkdir()
        (src_dir / "sub" / "nested.txt").write_text("nested")

        await self.fs.stage_directory(self.ws, str(src_dir), "dest", WorkspaceStageOptions())
        dest = Path(self.tmpdir) / "dest"
        assert (dest / "file.txt").read_text() == "content"
        assert (dest / "sub" / "nested.txt").read_text() == "nested"

    @pytest.mark.asyncio
    async def test_stage_directory_read_only(self):
        src_dir = Path(self.tmpdir) / "src_ro"
        src_dir.mkdir()
        (src_dir / "file.txt").write_text("readonly")

        await self.fs.stage_directory(self.ws, str(src_dir), "dest_ro", WorkspaceStageOptions(read_only=True))
        dest_file = Path(self.tmpdir) / "dest_ro" / "file.txt"
        mode = dest_file.stat().st_mode
        assert not (mode & 0o222)  # no write bits

    @pytest.mark.asyncio
    async def test_stage_directory_read_only_via_fs_flag(self):
        src_dir = Path(self.tmpdir) / "src_fs_ro"
        src_dir.mkdir()
        (src_dir / "file.txt").write_text("fs_readonly")

        fs_ro = LocalWorkspaceFS(read_only_staged_skill=True)
        await fs_ro.stage_directory(self.ws, str(src_dir), "dest_fs_ro", WorkspaceStageOptions())
        dest_file = Path(self.tmpdir) / "dest_fs_ro" / "file.txt"
        mode = dest_file.stat().st_mode
        assert not (mode & 0o222)

    @pytest.mark.asyncio
    async def test_stage_directory_empty_dst(self):
        src_dir = Path(self.tmpdir) / "src_empty_dst"
        src_dir.mkdir()
        (src_dir / "file.txt").write_text("content")

        await self.fs.stage_directory(
            self.ws, str(src_dir), "",
            WorkspaceStageOptions(read_only=True),
        )
        # Files copied into ws root
        assert (Path(self.tmpdir) / "file.txt").read_text() == "content"

    # --- collect ---
    @pytest.mark.asyncio
    async def test_collect_files(self):
        out_dir = Path(self.tmpdir) / DIR_OUT
        (out_dir / "result.txt").write_text("result data")

        files = await self.fs.collect(self.ws, ["out/*.txt"])
        assert len(files) == 1
        assert files[0].name.endswith("result.txt")
        assert "result data" in files[0].content

    @pytest.mark.asyncio
    async def test_collect_no_matches(self):
        files = await self.fs.collect(self.ws, ["out/*.xyz"])
        assert len(files) == 0

    @pytest.mark.asyncio
    async def test_collect_multiple_patterns(self):
        out_dir = Path(self.tmpdir) / DIR_OUT
        (out_dir / "a.txt").write_text("a")
        (out_dir / "b.log").write_text("b")

        files = await self.fs.collect(self.ws, ["out/*.txt", "out/*.log"])
        names = {f.name for f in files}
        assert any("a.txt" in n for n in names)
        assert any("b.log" in n for n in names)

    @pytest.mark.asyncio
    async def test_collect_deduplicates(self):
        out_dir = Path(self.tmpdir) / DIR_OUT
        (out_dir / "dup.txt").write_text("dup")

        files = await self.fs.collect(self.ws, ["out/*.txt", "out/dup.txt"])
        txt_files = [f for f in files if "dup.txt" in f.name]
        assert len(txt_files) == 1

    @pytest.mark.asyncio
    async def test_collect_returns_code_file(self):
        out_dir = Path(self.tmpdir) / DIR_OUT
        (out_dir / "data.txt").write_text("data")
        files = await self.fs.collect(self.ws, ["out/data.txt"])
        assert len(files) == 1
        assert isinstance(files[0], CodeFile)
        assert files[0].mime_type

    # --- _read_limited ---
    def test_read_limited_small_file(self):
        f = Path(self.tmpdir) / "small.txt"
        f.write_text("hello")
        content, mime = self.fs._read_limited(f)
        assert content == "hello"
        assert mime

    def test_read_limited_nonexistent(self):
        f = Path(self.tmpdir) / "missing.txt"
        content, mime = self.fs._read_limited(f)
        assert content == ""
        assert mime == "application/octet-stream"

    # --- _read_limited_with_cap ---
    def test_read_limited_with_cap_zero(self):
        f = Path(self.tmpdir) / "cap_zero.txt"
        f.write_text("data")
        content, mime = self.fs._read_limited_with_cap(f, 0)
        assert content == ""

    def test_read_limited_with_cap_negative(self):
        f = Path(self.tmpdir) / "cap_neg.txt"
        f.write_text("data")
        content, mime = self.fs._read_limited_with_cap(f, -1)
        assert content == ""

    def test_read_limited_with_cap_small(self):
        f = Path(self.tmpdir) / "cap_small.txt"
        f.write_text("hello world")
        content, mime = self.fs._read_limited_with_cap(f, 5)
        assert len(content) <= 5

    def test_read_limited_with_cap_large(self):
        f = Path(self.tmpdir) / "cap_large.txt"
        f.write_text("hello world")
        content, mime = self.fs._read_limited_with_cap(f, MAX_READ_SIZE_BYTES + 1000)
        assert content == "hello world"

    def test_read_limited_with_cap_nonexistent(self):
        f = Path(self.tmpdir) / "missing_cap.txt"
        content, mime = self.fs._read_limited_with_cap(f, 100)
        assert content == ""
        assert mime == "application/octet-stream"

    # --- _copy_directory ---
    def test_copy_directory(self):
        src = Path(self.tmpdir) / "copy_src"
        src.mkdir()
        (src / "a.txt").write_text("a")
        (src / "sub").mkdir()
        (src / "sub" / "b.txt").write_text("b")

        dst = Path(self.tmpdir) / "copy_dst"
        self.fs._copy_directory(str(src), str(dst))

        assert (dst / "a.txt").read_text() == "a"
        assert (dst / "sub" / "b.txt").read_text() == "b"

    def test_copy_directory_empty(self):
        src = Path(self.tmpdir) / "empty_src"
        src.mkdir()
        dst = Path(self.tmpdir) / "empty_dst"
        self.fs._copy_directory(str(src), str(dst))
        assert dst.exists()

    # --- _make_tree_read_only ---
    def test_make_tree_read_only(self):
        target = Path(self.tmpdir) / "ro_tree"
        target.mkdir()
        (target / "file.txt").write_text("data")
        (target / "sub").mkdir()
        (target / "sub" / "nested.txt").write_text("nested")

        self.fs._make_tree_read_only(target)

        for f in target.rglob("*"):
            if f.is_file():
                mode = f.stat().st_mode
                assert not (mode & 0o222)

    # --- _input_default_name ---
    def test_input_default_name_with_path(self):
        assert self.fs._input_default_name("host:///path/to/file.txt") == "file.txt"

    def test_input_default_name_no_slash(self):
        assert self.fs._input_default_name("somefile") == "somefile"

    def test_input_default_name_trailing_slash(self):
        name = self.fs._input_default_name("host:///path/to/dir/")
        # When trailing slash, i+1 == len(src), condition `i + 1 < len(src)` is False, returns full src
        assert name == "host:///path/to/dir/"

    def test_input_default_name_scheme(self):
        assert self.fs._input_default_name("artifact://my_data") == "my_data"

    # --- _write_file_safe ---
    def test_write_file_safe(self):
        f = WorkspacePutFileInfo(path="safe.txt", content=b"safe_data", mode=0o644)
        self.fs._write_file_safe(self.tmpdir, f)
        assert (Path(self.tmpdir) / "safe.txt").read_bytes() == b"safe_data"

    def test_write_file_safe_empty_path_raises(self):
        f = WorkspacePutFileInfo(path="", content=b"data")
        with pytest.raises(ValueError, match="empty file path"):
            self.fs._write_file_safe(self.tmpdir, f)

    def test_write_file_safe_absolute_path_escape_raises(self):
        f = WorkspacePutFileInfo(path="/absolute/outside.txt", content=b"evil")
        with pytest.raises(ValueError, match="path escapes workspace"):
            self.fs._write_file_safe(self.tmpdir, f)

    def test_write_file_safe_creates_parents(self):
        f = WorkspacePutFileInfo(path="deep/nested/dir/file.txt", content=b"deep", mode=0o644)
        self.fs._write_file_safe(self.tmpdir, f)
        assert (Path(self.tmpdir) / "deep" / "nested" / "dir" / "file.txt").read_bytes() == b"deep"

    def test_write_file_safe_default_mode(self):
        f = WorkspacePutFileInfo(path="mode_test.txt", content=b"data", mode=0)
        self.fs._write_file_safe(self.tmpdir, f)
        written = Path(self.tmpdir) / "mode_test.txt"
        mode = written.stat().st_mode & 0o777
        assert mode == DEFAULT_FILE_MODE

    # --- stage_inputs ---
    @pytest.mark.asyncio
    async def test_stage_inputs_host_copy(self):
        host_dir = Path(self.tmpdir) / "host_src"
        host_dir.mkdir()
        (host_dir / "data.txt").write_text("host data")

        specs = [WorkspaceInputSpec(src=f"host://{host_dir}", dst="work/inputs/data", mode="copy")]
        await self.fs.stage_inputs(self.ws, specs)

        copied = Path(self.tmpdir) / "work" / "inputs"
        assert copied.exists()

    @pytest.mark.asyncio
    async def test_stage_inputs_host_link(self):
        host_dir = Path(self.tmpdir) / "host_link_src"
        host_dir.mkdir()
        (host_dir / "link.txt").write_text("link data")

        specs = [WorkspaceInputSpec(src=f"host://{host_dir}", dst="work/inputs/linked", mode="link")]
        await self.fs.stage_inputs(self.ws, specs)

        linked = Path(self.tmpdir) / "work" / "inputs" / "linked"
        assert linked.is_symlink() or linked.exists()

    @pytest.mark.asyncio
    async def test_stage_inputs_workspace(self):
        work_dir = Path(self.tmpdir) / DIR_WORK
        (work_dir / "existing.txt").write_text("existing")

        specs = [WorkspaceInputSpec(src="workspace://work/existing.txt", dst="work/copied.txt", mode="copy")]
        await self.fs.stage_inputs(self.ws, specs)

        assert (Path(self.tmpdir) / "work" / "copied.txt").exists()

    @pytest.mark.asyncio
    async def test_stage_inputs_workspace_link(self):
        work_dir = Path(self.tmpdir) / DIR_WORK
        (work_dir / "link_src.txt").write_text("link_src")

        specs = [WorkspaceInputSpec(src="workspace://work/link_src.txt", dst="work/link_dst.txt", mode="link")]
        await self.fs.stage_inputs(self.ws, specs)

        linked = Path(self.tmpdir) / "work" / "link_dst.txt"
        assert linked.is_symlink() or linked.exists()

    @pytest.mark.asyncio
    async def test_stage_inputs_skill(self):
        skills_dir = Path(self.tmpdir) / DIR_SKILLS
        (skills_dir / "my_skill").mkdir(parents=True)
        (skills_dir / "my_skill" / "code.py").write_text("skill code")

        specs = [WorkspaceInputSpec(src="skill://my_skill/code.py", dst="work/skill_code.py", mode="copy")]
        await self.fs.stage_inputs(self.ws, specs)

        assert (Path(self.tmpdir) / "work" / "skill_code.py").exists()

    @pytest.mark.asyncio
    async def test_stage_inputs_skill_link(self):
        skills_dir = Path(self.tmpdir) / DIR_SKILLS
        (skills_dir / "my_skill").mkdir(parents=True)
        (skills_dir / "my_skill" / "link.py").write_text("link skill")

        specs = [WorkspaceInputSpec(src="skill://my_skill/link.py", dst="work/link_skill.py", mode="link")]
        await self.fs.stage_inputs(self.ws, specs)

        assert (Path(self.tmpdir) / "work" / "link_skill.py").exists()

    @pytest.mark.asyncio
    async def test_stage_inputs_artifact(self):
        mock_ctx = AsyncMock(spec=InvocationContext)
        mock_ctx.load_artifact = AsyncMock(return_value=Mock(
            data=Mock(inline_data=Mock(data=b"artifact data")),
            version=Mock(version=1),
        ))

        specs = [WorkspaceInputSpec(src="artifact://my_artifact", dst="work/artifact.txt", mode="copy")]
        await self.fs.stage_inputs(self.ws, specs, ctx=mock_ctx)

        assert (Path(self.tmpdir) / "work" / "artifact.txt").exists()

    @pytest.mark.asyncio
    async def test_stage_inputs_artifact_with_version(self):
        mock_ctx = AsyncMock(spec=InvocationContext)
        mock_ctx.load_artifact = AsyncMock(return_value=Mock(
            data=Mock(inline_data=Mock(data=b"v2 data")),
            version=Mock(version=2),
        ))

        specs = [WorkspaceInputSpec(src="artifact://my_artifact@2", dst="work/v2.txt", mode="copy")]
        await self.fs.stage_inputs(self.ws, specs, ctx=mock_ctx)

        assert (Path(self.tmpdir) / "work" / "v2.txt").exists()

    @pytest.mark.asyncio
    async def test_stage_inputs_unsupported_scheme(self):
        specs = [WorkspaceInputSpec(src="ftp://server/file", dst="work/file.txt")]
        with pytest.raises(ValueError, match="unsupported input"):
            await self.fs.stage_inputs(self.ws, specs)

    @pytest.mark.asyncio
    async def test_stage_inputs_default_dst(self):
        host_dir = Path(self.tmpdir) / "host_default"
        host_dir.mkdir()
        (host_dir / "auto.txt").write_text("auto")

        specs = [WorkspaceInputSpec(src=f"host://{host_dir}/auto.txt", dst="", mode="copy")]
        await self.fs.stage_inputs(self.ws, specs)

    @pytest.mark.asyncio
    async def test_stage_inputs_records_metadata(self):
        host_dir = Path(self.tmpdir) / "host_meta"
        host_dir.mkdir()
        (host_dir / "meta.txt").write_text("meta")

        specs = [WorkspaceInputSpec(src=f"host://{host_dir}", dst="work/inputs/meta", mode="copy")]
        await self.fs.stage_inputs(self.ws, specs)

        meta_file = Path(self.tmpdir) / META_FILE_NAME
        assert meta_file.exists()

    # --- collect_outputs ---
    @pytest.mark.asyncio
    async def test_collect_outputs_basic(self):
        out_dir = Path(self.tmpdir) / DIR_OUT
        (out_dir / "output.txt").write_text("output data")

        spec = WorkspaceOutputSpec(globs=["out/*.txt"], inline=True)
        result = await self.fs.collect_outputs(self.ws, spec)

        assert isinstance(result, ManifestOutput)
        assert len(result.files) == 1
        assert result.files[0].content == "output data"

    @pytest.mark.asyncio
    async def test_collect_outputs_not_inline(self):
        out_dir = Path(self.tmpdir) / DIR_OUT
        (out_dir / "noinline.txt").write_text("no inline")

        spec = WorkspaceOutputSpec(globs=["out/*.txt"], inline=False)
        result = await self.fs.collect_outputs(self.ws, spec)

        assert len(result.files) == 1
        assert result.files[0].content == ""

    @pytest.mark.asyncio
    async def test_collect_outputs_max_files_limit(self):
        out_dir = Path(self.tmpdir) / DIR_OUT
        for i in range(5):
            (out_dir / f"file_{i}.txt").write_text(f"data_{i}")

        spec = WorkspaceOutputSpec(globs=["out/*.txt"], max_files=2, inline=True)
        result = await self.fs.collect_outputs(self.ws, spec)

        assert len(result.files) <= 2
        assert result.limits_hit is True

    @pytest.mark.asyncio
    async def test_collect_outputs_max_total_bytes_limit(self):
        out_dir = Path(self.tmpdir) / DIR_OUT
        (out_dir / "big.txt").write_text("x" * 100)
        (out_dir / "small.txt").write_text("y" * 10)

        spec = WorkspaceOutputSpec(globs=["out/*.txt"], max_total_bytes=50, inline=True)
        result = await self.fs.collect_outputs(self.ws, spec)

        assert result.limits_hit is True

    @pytest.mark.asyncio
    async def test_collect_outputs_no_matches(self):
        spec = WorkspaceOutputSpec(globs=["out/*.xyz"])
        result = await self.fs.collect_outputs(self.ws, spec)
        assert len(result.files) == 0

    @patch('trpc_agent_sdk.code_executors.local._local_ws_runtime.save_artifact_helper', new_callable=AsyncMock)
    @pytest.mark.asyncio
    async def test_collect_outputs_save_with_ctx(self, mock_save):
        mock_save.return_value = 1
        mock_ctx = AsyncMock(spec=InvocationContext)

        out_dir = Path(self.tmpdir) / DIR_OUT
        (out_dir / "saved.txt").write_text("save me")

        spec = WorkspaceOutputSpec(globs=["out/*.txt"], save=True)
        result = await self.fs.collect_outputs(self.ws, spec, ctx=mock_ctx)

        assert len(result.files) == 1
        assert result.files[0].saved_as
        assert result.files[0].version == 1
        mock_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_collect_outputs_save_without_ctx_raises(self):
        out_dir = Path(self.tmpdir) / DIR_OUT
        (out_dir / "no_ctx.txt").write_text("data")

        spec = WorkspaceOutputSpec(globs=["out/*.txt"], save=True)
        with pytest.raises(ValueError, match="Context is required"):
            await self.fs.collect_outputs(self.ws, spec)

    @patch('trpc_agent_sdk.code_executors.local._local_ws_runtime.save_artifact_helper', new_callable=AsyncMock)
    @pytest.mark.asyncio
    async def test_collect_outputs_save_with_name_template(self, mock_save):
        mock_save.return_value = 1
        mock_ctx = AsyncMock(spec=InvocationContext)

        out_dir = Path(self.tmpdir) / DIR_OUT
        (out_dir / "tmpl.txt").write_text("template data")

        spec = WorkspaceOutputSpec(globs=["out/*.txt"], save=True, name_template="prefix/")
        result = await self.fs.collect_outputs(self.ws, spec, ctx=mock_ctx)

        assert result.files[0].saved_as.startswith("prefix/")

    @pytest.mark.asyncio
    async def test_collect_outputs_records_metadata(self):
        out_dir = Path(self.tmpdir) / DIR_OUT
        (out_dir / "meta_out.txt").write_text("meta output")

        spec = WorkspaceOutputSpec(globs=["out/*.txt"])
        await self.fs.collect_outputs(self.ws, spec)

        meta_file = Path(self.tmpdir) / META_FILE_NAME
        assert meta_file.exists()

    @pytest.mark.asyncio
    async def test_collect_outputs_max_file_bytes_limit(self):
        out_dir = Path(self.tmpdir) / DIR_OUT
        (out_dir / "large.txt").write_text("x" * 1000)

        spec = WorkspaceOutputSpec(globs=["out/*.txt"], max_file_bytes=10, inline=True)
        result = await self.fs.collect_outputs(self.ws, spec)

        assert result.limits_hit is True


# ---------------------------------------------------------------------------
# LocalProgramRunner Tests
# ---------------------------------------------------------------------------
class TestLocalProgramRunner:
    """Tests for LocalProgramRunner."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ws = WorkspaceInfo(id="test-ws", path=self.tmpdir)
        self.runner = LocalProgramRunner()
        ensure_layout(self.tmpdir)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch('trpc_agent_sdk.code_executors.local._local_ws_runtime.async_execute_command')
    @pytest.mark.asyncio
    async def test_run_program_success(self, mock_exec):
        mock_exec.return_value = CommandExecResult(stdout="hello", stderr="", exit_code=0, is_timeout=False)

        spec = WorkspaceRunProgramSpec(cmd="echo", args=["hello"], timeout=10)
        result = await self.runner.run_program(self.ws, spec)

        assert isinstance(result, WorkspaceRunResult)
        assert result.stdout == "hello"
        assert result.exit_code == 0
        assert result.timed_out is False
        assert result.duration >= 0

    @patch('trpc_agent_sdk.code_executors.local._local_ws_runtime.async_execute_command')
    @pytest.mark.asyncio
    async def test_run_program_failure(self, mock_exec):
        mock_exec.return_value = CommandExecResult(stdout="", stderr="error", exit_code=1, is_timeout=False)

        spec = WorkspaceRunProgramSpec(cmd="false", timeout=10)
        result = await self.runner.run_program(self.ws, spec)

        assert result.exit_code == 1
        assert result.stderr == "error"

    @patch('trpc_agent_sdk.code_executors.local._local_ws_runtime.async_execute_command')
    @pytest.mark.asyncio
    async def test_run_program_timeout(self, mock_exec):
        mock_exec.return_value = CommandExecResult(stdout="", stderr="timed out", exit_code=-1, is_timeout=True)

        spec = WorkspaceRunProgramSpec(cmd="sleep", args=["100"], timeout=1)
        result = await self.runner.run_program(self.ws, spec)

        assert result.timed_out is True

    @patch('trpc_agent_sdk.code_executors.local._local_ws_runtime.async_execute_command')
    @pytest.mark.asyncio
    async def test_run_program_with_env(self, mock_exec):
        mock_exec.return_value = CommandExecResult(stdout="ok", stderr="", exit_code=0, is_timeout=False)

        spec = WorkspaceRunProgramSpec(cmd="env", env={"MY_VAR": "my_value"}, timeout=10)
        result = await self.runner.run_program(self.ws, spec)

        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs['env']['MY_VAR'] == "my_value"

    @patch('trpc_agent_sdk.code_executors.local._local_ws_runtime.async_execute_command')
    @pytest.mark.asyncio
    async def test_run_program_injects_workspace_env(self, mock_exec):
        mock_exec.return_value = CommandExecResult(stdout="", stderr="", exit_code=0, is_timeout=False)

        spec = WorkspaceRunProgramSpec(cmd="env", timeout=10)
        await self.runner.run_program(self.ws, spec)

        call_kwargs = mock_exec.call_args[1]
        env = call_kwargs['env']
        assert 'WORKSPACE_DIR' in env
        assert 'SKILLS_DIR' in env
        assert 'WORK_DIR' in env
        assert 'OUTPUT_DIR' in env
        assert 'RUN_DIR' in env

    @patch('trpc_agent_sdk.code_executors.local._local_ws_runtime.async_execute_command')
    @pytest.mark.asyncio
    async def test_run_program_cwd(self, mock_exec):
        mock_exec.return_value = CommandExecResult(stdout="", stderr="", exit_code=0, is_timeout=False)

        spec = WorkspaceRunProgramSpec(cmd="ls", cwd="work", timeout=10)
        await self.runner.run_program(self.ws, spec)

        call_kwargs = mock_exec.call_args[1]
        cwd = str(call_kwargs['work_dir'])
        assert "work" in cwd

    @patch('trpc_agent_sdk.code_executors.local._local_ws_runtime.async_execute_command')
    @pytest.mark.asyncio
    async def test_run_program_stdin(self, mock_exec):
        mock_exec.return_value = CommandExecResult(stdout="echoed", stderr="", exit_code=0, is_timeout=False)

        spec = WorkspaceRunProgramSpec(cmd="cat", stdin="input_data", timeout=10)
        await self.runner.run_program(self.ws, spec)

        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs['input'] == b"input_data"

    @patch('trpc_agent_sdk.code_executors.local._local_ws_runtime.async_execute_command')
    @pytest.mark.asyncio
    async def test_run_program_no_stdin(self, mock_exec):
        mock_exec.return_value = CommandExecResult(stdout="", stderr="", exit_code=0, is_timeout=False)

        spec = WorkspaceRunProgramSpec(cmd="echo", args=["test"], timeout=10)
        await self.runner.run_program(self.ws, spec)

        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs['input'] is None

    @patch('trpc_agent_sdk.code_executors.local._local_ws_runtime.async_execute_command')
    @pytest.mark.asyncio
    async def test_run_program_default_timeout(self, mock_exec):
        mock_exec.return_value = CommandExecResult(stdout="", stderr="", exit_code=0, is_timeout=False)

        spec = WorkspaceRunProgramSpec(cmd="echo")
        await self.runner.run_program(self.ws, spec)

        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs['timeout'] == 10.0  # DEFAULT_TIMEOUT_SEC

    @patch('trpc_agent_sdk.code_executors.local._local_ws_runtime.async_execute_command')
    @pytest.mark.asyncio
    async def test_run_program_creates_run_dir(self, mock_exec):
        mock_exec.return_value = CommandExecResult(stdout="", stderr="", exit_code=0, is_timeout=False)

        spec = WorkspaceRunProgramSpec(cmd="echo", timeout=10)
        await self.runner.run_program(self.ws, spec)

        runs_dir = Path(self.tmpdir) / DIR_RUNS
        run_dirs = list(runs_dir.iterdir())
        assert len(run_dirs) >= 1

    @patch('trpc_agent_sdk.code_executors.local._local_ws_runtime.async_execute_command')
    @pytest.mark.asyncio
    async def test_run_program_env_override(self, mock_exec):
        mock_exec.return_value = CommandExecResult(stdout="", stderr="", exit_code=0, is_timeout=False)

        spec = WorkspaceRunProgramSpec(cmd="echo", env={"WORKSPACE_DIR": "/custom"}, timeout=10)
        await self.runner.run_program(self.ws, spec)

        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs['env']['WORKSPACE_DIR'] == "/custom"

    @patch('trpc_agent_sdk.code_executors.local._local_ws_runtime.async_execute_command')
    @pytest.mark.asyncio
    async def test_run_program_creates_cwd_if_needed(self, mock_exec):
        mock_exec.return_value = CommandExecResult(stdout="", stderr="", exit_code=0, is_timeout=False)

        spec = WorkspaceRunProgramSpec(cmd="echo", cwd="new_dir/sub", timeout=10)
        await self.runner.run_program(self.ws, spec)

        cwd_path = Path(self.tmpdir) / "new_dir" / "sub"
        assert cwd_path.exists()


# ---------------------------------------------------------------------------
# LocalWorkspaceRuntime Tests
# ---------------------------------------------------------------------------
class TestLocalWorkspaceRuntime:
    """Tests for LocalWorkspaceRuntime."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_init_defaults(self):
        runtime = LocalWorkspaceRuntime()
        assert isinstance(runtime.manager(), BaseWorkspaceManager)
        assert isinstance(runtime.fs(), BaseWorkspaceFS)
        assert isinstance(runtime.runner(), BaseProgramRunner)

    def test_init_with_work_root(self):
        runtime = LocalWorkspaceRuntime(work_root=self.tmpdir)
        mgr = runtime.manager()
        assert isinstance(mgr, LocalWorkspaceManager)
        assert mgr.work_root == self.tmpdir

    def test_init_with_read_only_flag(self):
        runtime = LocalWorkspaceRuntime(read_only_staged_skill=True)
        fs = runtime.fs()
        assert isinstance(fs, LocalWorkspaceFS)
        assert fs.read_only_staged_skill is True

    def test_init_with_auto_inputs(self):
        runtime = LocalWorkspaceRuntime(auto_inputs=False)
        mgr = runtime.manager()
        assert mgr.auto_inputs is False

    def test_init_with_inputs_host_base(self):
        runtime = LocalWorkspaceRuntime(inputs_host_base="/host/base")
        mgr = runtime.manager()
        assert mgr.inputs_host_base == "/host/base"

    def test_manager_returns_local_manager(self):
        runtime = LocalWorkspaceRuntime(work_root=self.tmpdir)
        assert isinstance(runtime.manager(), LocalWorkspaceManager)

    def test_fs_returns_local_fs(self):
        runtime = LocalWorkspaceRuntime()
        assert isinstance(runtime.fs(), LocalWorkspaceFS)

    def test_runner_returns_local_runner(self):
        runtime = LocalWorkspaceRuntime()
        assert isinstance(runtime.runner(), LocalProgramRunner)

    def test_describe_capabilities(self):
        runtime = LocalWorkspaceRuntime()
        caps = runtime.describe()
        assert isinstance(caps, WorkspaceCapabilities)
        assert caps.isolation == "local"
        assert caps.network_allowed is True
        assert caps.read_only_mount is True
        assert caps.streaming is True

    def test_manager_with_ctx(self):
        runtime = LocalWorkspaceRuntime(work_root=self.tmpdir)
        ctx = Mock(spec=InvocationContext)
        mgr = runtime.manager(ctx=ctx)
        assert isinstance(mgr, LocalWorkspaceManager)

    def test_fs_with_ctx(self):
        runtime = LocalWorkspaceRuntime()
        ctx = Mock(spec=InvocationContext)
        fs = runtime.fs(ctx=ctx)
        assert isinstance(fs, LocalWorkspaceFS)

    def test_runner_with_ctx(self):
        runtime = LocalWorkspaceRuntime()
        ctx = Mock(spec=InvocationContext)
        runner = runtime.runner(ctx=ctx)
        assert isinstance(runner, LocalProgramRunner)

    def test_describe_with_ctx(self):
        runtime = LocalWorkspaceRuntime()
        ctx = Mock(spec=InvocationContext)
        caps = runtime.describe(ctx=ctx)
        assert isinstance(caps, WorkspaceCapabilities)


# ---------------------------------------------------------------------------
# create_local_workspace_runtime Tests
# ---------------------------------------------------------------------------
class TestCreateLocalWorkspaceRuntime:
    """Tests for create_local_workspace_runtime factory function."""

    def test_default(self):
        runtime = create_local_workspace_runtime()
        assert isinstance(runtime, LocalWorkspaceRuntime)

    def test_with_work_root(self):
        runtime = create_local_workspace_runtime(work_root="/tmp/test_root")
        mgr = runtime.manager()
        assert mgr.work_root == "/tmp/test_root"

    def test_with_read_only(self):
        runtime = create_local_workspace_runtime(read_only_staged_skill=True)
        fs = runtime.fs()
        assert fs.read_only_staged_skill is True

    def test_with_auto_inputs(self):
        runtime = create_local_workspace_runtime(auto_inputs=False)
        mgr = runtime.manager()
        assert mgr.auto_inputs is False

    def test_with_inputs_host_base(self):
        runtime = create_local_workspace_runtime(inputs_host_base="/host")
        mgr = runtime.manager()
        assert mgr.inputs_host_base == "/host"

    def test_all_params(self):
        runtime = create_local_workspace_runtime(
            work_root="/tmp/rt",
            read_only_staged_skill=True,
            auto_inputs=False,
            inputs_host_base="/base",
        )
        assert isinstance(runtime, LocalWorkspaceRuntime)
        mgr = runtime.manager()
        assert mgr.work_root == "/tmp/rt"
        assert mgr.auto_inputs is False
        assert mgr.inputs_host_base == "/base"
        assert runtime.fs().read_only_staged_skill is True
