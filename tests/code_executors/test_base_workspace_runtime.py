# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from typing import Optional
from unittest.mock import Mock

import pytest
from trpc_agent_sdk.code_executors import BaseProgramRunner
from trpc_agent_sdk.code_executors import BaseWorkspaceFS
from trpc_agent_sdk.code_executors import BaseWorkspaceManager
from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
from trpc_agent_sdk.code_executors import DefaultWorkspace
from trpc_agent_sdk.code_executors import new_default_workspace_runtime
from trpc_agent_sdk.code_executors import CodeFile
from trpc_agent_sdk.code_executors import ManifestOutput
from trpc_agent_sdk.code_executors import WorkspaceCapabilities
from trpc_agent_sdk.code_executors import WorkspaceInfo
from trpc_agent_sdk.code_executors import WorkspaceInputSpec
from trpc_agent_sdk.code_executors import WorkspaceOutputSpec
from trpc_agent_sdk.code_executors import WorkspacePutFileInfo
from trpc_agent_sdk.code_executors import WorkspaceRunProgramSpec
from trpc_agent_sdk.code_executors import WorkspaceRunResult
from trpc_agent_sdk.code_executors import WorkspaceStageOptions
from trpc_agent_sdk.context import InvocationContext


class ConcreteWorkspaceManager(BaseWorkspaceManager):
    """Concrete implementation of BaseWorkspaceManager for testing."""

    async def create_workspace(self, exec_id: str, ctx: Optional[InvocationContext] = None) -> WorkspaceInfo:
        """Concrete implementation."""
        return WorkspaceInfo(id=exec_id, path=f"/tmp/{exec_id}")

    async def cleanup(self, exec_id: str, ctx: Optional[InvocationContext] = None) -> None:
        """Concrete implementation."""
        pass


class ConcreteWorkspaceFS(BaseWorkspaceFS):
    """Concrete implementation of BaseWorkspaceFS for testing."""

    async def put_files(self, ws: WorkspaceInfo, files: list[WorkspacePutFileInfo], ctx: Optional[InvocationContext] = None) -> None:
        """Concrete implementation."""
        pass

    async def stage_directory(self, ws: WorkspaceInfo, src: str, dst: str,
                              opt: WorkspaceStageOptions, ctx: Optional[InvocationContext] = None) -> None:
        """Concrete implementation."""
        pass

    async def collect(self, ws: WorkspaceInfo, patterns: list[str], ctx: Optional[InvocationContext] = None) -> list[CodeFile]:
        """Concrete implementation."""
        return []

    async def stage_inputs(self, ws: WorkspaceInfo, specs: list[WorkspaceInputSpec], ctx: Optional[InvocationContext] = None) -> None:
        """Concrete implementation."""
        pass

    async def collect_outputs(self, ws: WorkspaceInfo,
                              spec: WorkspaceOutputSpec,
                              ctx: Optional[InvocationContext] = None) -> ManifestOutput:
        """Concrete implementation."""
        return ManifestOutput()


class ConcreteProgramRunner(BaseProgramRunner):
    """Concrete implementation of BaseProgramRunner for testing."""

    async def run_program(self, ws: WorkspaceInfo,
                          spec: WorkspaceRunProgramSpec,
                          ctx: Optional[InvocationContext] = None) -> WorkspaceRunResult:
        """Concrete implementation."""
        return WorkspaceRunResult(stdout="output", stderr="", exit_code=0)


class ConcreteWorkspaceRuntime(BaseWorkspaceRuntime):
    """Concrete implementation of BaseWorkspaceRuntime for testing."""

    def __init__(self, manager: BaseWorkspaceManager, fs: BaseWorkspaceFS, runner: BaseProgramRunner):
        self._manager = manager
        self._fs = fs
        self._runner = runner

    def manager(self, ctx: Optional[InvocationContext] = None) -> BaseWorkspaceManager:
        return self._manager

    def fs(self, ctx: Optional[InvocationContext] = None) -> BaseWorkspaceFS:
        return self._fs

    def runner(self, ctx: Optional[InvocationContext] = None) -> BaseProgramRunner:
        return self._runner

    def describe(self, ctx: Optional[InvocationContext] = None) -> WorkspaceCapabilities:
        return WorkspaceCapabilities()


class TestBaseWorkspaceManager:
    """Test suite for BaseWorkspaceManager class."""

    def test_cannot_instantiate_abstract_class(self):
        """Test that BaseWorkspaceManager cannot be instantiated directly."""
        with pytest.raises(TypeError):
            BaseWorkspaceManager()

    def test_concrete_manager_instantiation(self):
        """Test that concrete manager can be instantiated."""
        manager = ConcreteWorkspaceManager()

        assert isinstance(manager, BaseWorkspaceManager)

    @pytest.mark.asyncio
    async def test_create_workspace_implementation(self):
        """Test create_workspace method implementation."""
        manager = ConcreteWorkspaceManager()
        mock_ctx = Mock(spec=InvocationContext)

        workspace = await manager.create_workspace("exec-123", mock_ctx)

        assert isinstance(workspace, WorkspaceInfo)
        assert workspace.id == "exec-123"

    @pytest.mark.asyncio
    async def test_cleanup_implementation(self):
        """Test cleanup method implementation."""
        manager = ConcreteWorkspaceManager()
        mock_ctx = Mock(spec=InvocationContext)

        # Should not raise error
        await manager.cleanup("exec-123", mock_ctx)


class TestBaseWorkspaceFS:
    """Test suite for BaseWorkspaceFS class."""

    def test_cannot_instantiate_abstract_class(self):
        """Test that BaseWorkspaceFS cannot be instantiated directly."""
        with pytest.raises(TypeError):
            BaseWorkspaceFS()

    def test_concrete_fs_instantiation(self):
        """Test that concrete filesystem can be instantiated."""
        fs = ConcreteWorkspaceFS()

        assert isinstance(fs, BaseWorkspaceFS)

    @pytest.mark.asyncio
    async def test_put_files_implementation(self):
        """Test put_files method implementation."""
        fs = ConcreteWorkspaceFS()
        mock_ctx = Mock(spec=InvocationContext)
        workspace = WorkspaceInfo(id="exec-123", path="/tmp/exec-123")
        files = [WorkspacePutFileInfo(path="/tmp/file.txt", content=b"content")]

        # Should not raise error
        await fs.put_files(workspace, files, mock_ctx)

    @pytest.mark.asyncio
    async def test_stage_directory_implementation(self):
        """Test stage_directory method implementation."""
        fs = ConcreteWorkspaceFS()
        mock_ctx = Mock(spec=InvocationContext)
        workspace = WorkspaceInfo(id="exec-123", path="/tmp/exec-123")
        options = WorkspaceStageOptions()

        # Should not raise error
        await fs.stage_directory(workspace, "/src", "/dst", options, mock_ctx)

    @pytest.mark.asyncio
    async def test_collect_implementation(self):
        """Test collect method implementation."""
        fs = ConcreteWorkspaceFS()
        mock_ctx = Mock(spec=InvocationContext)
        workspace = WorkspaceInfo(id="exec-123", path="/tmp/exec-123")

        files = await fs.collect(workspace, ["**/*.txt"], mock_ctx)

        assert isinstance(files, list)

    @pytest.mark.asyncio
    async def test_stage_inputs_implementation(self):
        """Test stage_inputs method implementation."""
        fs = ConcreteWorkspaceFS()
        mock_ctx = Mock(spec=InvocationContext)
        workspace = WorkspaceInfo(id="exec-123", path="/tmp/exec-123")
        specs = [WorkspaceInputSpec(src="artifact://name", dst="/tmp/input")]

        # Should not raise error
        await fs.stage_inputs(workspace, specs, mock_ctx)

    @pytest.mark.asyncio
    async def test_collect_outputs_implementation(self):
        """Test collect_outputs method implementation."""
        fs = ConcreteWorkspaceFS()
        mock_ctx = Mock(spec=InvocationContext)
        workspace = WorkspaceInfo(id="exec-123", path="/tmp/exec-123")
        spec = WorkspaceOutputSpec(globs=["**/*.txt"])

        output = await fs.collect_outputs(workspace, spec, mock_ctx)

        assert isinstance(output, ManifestOutput)


class TestBaseProgramRunner:
    """Test suite for BaseProgramRunner class."""

    def test_cannot_instantiate_abstract_class(self):
        """Test that BaseProgramRunner cannot be instantiated directly."""
        with pytest.raises(TypeError):
            BaseProgramRunner()

    def test_concrete_runner_instantiation(self):
        """Test that concrete runner can be instantiated."""
        runner = ConcreteProgramRunner()

        assert isinstance(runner, BaseProgramRunner)

    @pytest.mark.asyncio
    async def test_run_program_implementation(self):
        """Test run_program method implementation."""
        runner = ConcreteProgramRunner()
        mock_ctx = Mock(spec=InvocationContext)
        workspace = WorkspaceInfo(id="exec-123", path="/tmp/exec-123")
        spec = WorkspaceRunProgramSpec(cmd="python", args=["script.py"])

        result = await runner.run_program(workspace, spec, mock_ctx)

        assert isinstance(result, WorkspaceRunResult)
        assert result.stdout == "output"


class TestBaseWorkspaceRuntime:
    """Test suite for BaseWorkspaceRuntime class."""

    def test_cannot_instantiate_abstract_class(self):
        """Test that BaseWorkspaceRuntime cannot be instantiated directly."""
        with pytest.raises(TypeError):
            BaseWorkspaceRuntime()

    def test_concrete_runtime_instantiation(self):
        """Test that concrete runtime can be instantiated."""
        manager = ConcreteWorkspaceManager()
        fs = ConcreteWorkspaceFS()
        runner = ConcreteProgramRunner()
        runtime = ConcreteWorkspaceRuntime(manager, fs, runner)

        assert isinstance(runtime, BaseWorkspaceRuntime)

    def test_manager_method(self):
        """Test manager method."""
        manager = ConcreteWorkspaceManager()
        fs = ConcreteWorkspaceFS()
        runner = ConcreteProgramRunner()
        runtime = ConcreteWorkspaceRuntime(manager, fs, runner)
        mock_ctx = Mock(spec=InvocationContext)

        result = runtime.manager(mock_ctx)

        assert result == manager

    def test_fs_method(self):
        """Test fs method."""
        manager = ConcreteWorkspaceManager()
        fs = ConcreteWorkspaceFS()
        runner = ConcreteProgramRunner()
        runtime = ConcreteWorkspaceRuntime(manager, fs, runner)
        mock_ctx = Mock(spec=InvocationContext)

        result = runtime.fs(mock_ctx)

        assert result == fs

    def test_runner_method(self):
        """Test runner method."""
        manager = ConcreteWorkspaceManager()
        fs = ConcreteWorkspaceFS()
        runner = ConcreteProgramRunner()
        runtime = ConcreteWorkspaceRuntime(manager, fs, runner)
        mock_ctx = Mock(spec=InvocationContext)

        result = runtime.runner(mock_ctx)

        assert result == runner

    def test_describe_method(self):
        """Test describe method."""
        manager = ConcreteWorkspaceManager()
        fs = ConcreteWorkspaceFS()
        runner = ConcreteProgramRunner()
        runtime = ConcreteWorkspaceRuntime(manager, fs, runner)
        mock_ctx = Mock(spec=InvocationContext)

        capabilities = runtime.describe(mock_ctx)

        assert isinstance(capabilities, WorkspaceCapabilities)


class TestDefaultWorkspace:
    """Test suite for DefaultWorkspace class."""

    def test_instantiation(self):
        """Test DefaultWorkspace instantiation."""
        manager = ConcreteWorkspaceManager()
        fs = ConcreteWorkspaceFS()
        runner = ConcreteProgramRunner()
        workspace = DefaultWorkspace(manager=manager, fs=fs, runner=runner)

        assert isinstance(workspace, DefaultWorkspace)
        assert isinstance(workspace, BaseWorkspaceRuntime)

    def test_manager_method(self):
        """Test manager method."""
        manager = ConcreteWorkspaceManager()
        fs = ConcreteWorkspaceFS()
        runner = ConcreteProgramRunner()
        workspace = DefaultWorkspace(manager=manager, fs=fs, runner=runner)
        mock_ctx = Mock(spec=InvocationContext)

        result = workspace.manager(mock_ctx)

        assert result == manager

    def test_fs_method(self):
        """Test fs method."""
        manager = ConcreteWorkspaceManager()
        fs = ConcreteWorkspaceFS()
        runner = ConcreteProgramRunner()
        workspace = DefaultWorkspace(manager=manager, fs=fs, runner=runner)
        mock_ctx = Mock(spec=InvocationContext)

        result = workspace.fs(mock_ctx)

        assert result == fs

    def test_runner_method(self):
        """Test runner method."""
        manager = ConcreteWorkspaceManager()
        fs = ConcreteWorkspaceFS()
        runner = ConcreteProgramRunner()
        workspace = DefaultWorkspace(manager=manager, fs=fs, runner=runner)
        mock_ctx = Mock(spec=InvocationContext)

        result = workspace.runner(mock_ctx)

        assert result == runner

    def test_describe_method(self):
        """Test describe method."""
        manager = ConcreteWorkspaceManager()
        fs = ConcreteWorkspaceFS()
        runner = ConcreteProgramRunner()
        workspace = DefaultWorkspace(manager=manager, fs=fs, runner=runner)
        mock_ctx = Mock(spec=InvocationContext)

        capabilities = workspace.describe(mock_ctx)

        assert isinstance(capabilities, WorkspaceCapabilities)


class TestNewDefaultWorkspaceRuntime:
    """Test suite for new_default_workspace_runtime function."""

    def test_new_default_workspace_runtime(self):
        """Test creating default workspace runtime."""
        manager = ConcreteWorkspaceManager()
        fs = ConcreteWorkspaceFS()
        runner = ConcreteProgramRunner()

        workspace = new_default_workspace_runtime(manager, fs, runner)

        assert isinstance(workspace, DefaultWorkspace)
        assert isinstance(workspace, BaseWorkspaceRuntime)

    def test_new_default_workspace_runtime_methods(self):
        """Test that returned workspace has correct methods."""
        manager = ConcreteWorkspaceManager()
        fs = ConcreteWorkspaceFS()
        runner = ConcreteProgramRunner()
        mock_ctx = Mock(spec=InvocationContext)

        workspace = new_default_workspace_runtime(manager, fs, runner)

        assert workspace.manager(mock_ctx) == manager
        assert workspace.fs(mock_ctx) == fs
        assert workspace.runner(mock_ctx) == runner
