# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
WorkspaceInfo types and helpers for code execution.

This module defines workspace types, policies, and interfaces for managing
isolated execution environments.
"""

from abc import ABC
from abc import abstractmethod
from typing import Callable
from typing import TypeAlias
from typing import List
from typing import Optional

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.log import logger

from ._types import CodeFile
from ._types import ManifestOutput
from ._types import WorkspaceCapabilities
from ._types import WorkspaceInfo
from ._types import WorkspaceInputSpec
from ._types import WorkspaceOutputSpec
from ._types import WorkspacePutFileInfo
from ._types import WorkspaceRunProgramSpec
from ._types import WorkspaceRunResult
from ._types import WorkspaceStageOptions

RunEnvProvider = Callable[[Optional[InvocationContext]], dict[str, str]]


class BaseWorkspaceManager(ABC):
    """
    Handles workspace lifecycle.
    """

    @abstractmethod
    async def create_workspace(
        self,
        exec_id: str,
        ctx: Optional[InvocationContext] = None,
    ) -> WorkspaceInfo:
        """
        Create a new workspace.
        """
        pass

    @abstractmethod
    async def cleanup(
        self,
        exec_id: str,
        ctx: Optional[InvocationContext] = None,
    ) -> None:
        """
        Clean up a workspace.
        """
        pass


class BaseWorkspaceFS(ABC):
    """
    Performs file operations within a workspace.
    """

    @abstractmethod
    async def put_files(
        self,
        ws: WorkspaceInfo,
        files: List[WorkspacePutFileInfo],
        ctx: Optional[InvocationContext] = None,
    ) -> None:
        """
        Put files into workspace.
        """
        pass

    @abstractmethod
    async def stage_directory(
        self,
        ws: WorkspaceInfo,
        src: str,
        dst: str,
        opt: WorkspaceStageOptions,
        ctx: Optional[InvocationContext] = None,
    ) -> None:
        """
        Stage a directory into workspace.
        """
        pass

    @abstractmethod
    async def collect(
        self,
        ws: WorkspaceInfo,
        patterns: List[str],
        ctx: Optional[InvocationContext] = None,
    ) -> List[CodeFile]:
        """
        Collect files matching patterns.
        """
        pass

    @abstractmethod
    async def stage_inputs(
        self,
        ws: WorkspaceInfo,
        specs: List[WorkspaceInputSpec],
        ctx: Optional[InvocationContext] = None,
    ) -> None:
        """
        Map external inputs into workspace according to specs.
        """
        pass

    @abstractmethod
    async def collect_outputs(
        self,
        ws: WorkspaceInfo,
        spec: WorkspaceOutputSpec,
        ctx: Optional[InvocationContext] = None,
    ) -> ManifestOutput:
        """
        Apply declarative output spec to collect files.
        """
        pass


class BaseProgramRunner(ABC):
    """
    Executes programs within a workspace.
    """

    def __init__(
        self,
        provider: Optional[RunEnvProvider] = None,
        enable_provider_env: bool = False,
    ) -> None:
        self._run_env_provider = provider
        self._enable_provider_env = bool(enable_provider_env and provider)

    def _apply_provider_env(
        self,
        spec: WorkspaceRunProgramSpec,
        ctx: Optional[InvocationContext] = None,
    ) -> WorkspaceRunProgramSpec:
        """Return spec with provider env merged when enabled.

        Provider values never override keys already present in ``spec.env``.
        The input ``spec`` is not mutated.
        """
        provider = getattr(self, "_run_env_provider", None)
        if not getattr(self, "_enable_provider_env", False) or provider is None:
            return spec
        try:
            extra = provider(ctx) or {}
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("run env provider failed: %s", ex)
            return spec
        if not extra:
            return spec
        merged = dict(spec.env or {})
        for key, value in extra.items():
            if key not in merged:
                merged[key] = value
        return spec.model_copy(update={"env": merged}, deep=True)

    @abstractmethod
    async def run_program(
        self,
        ws: WorkspaceInfo,
        spec: WorkspaceRunProgramSpec,
        ctx: Optional[InvocationContext] = None,
    ) -> WorkspaceRunResult:
        """
        Run a program in workspace.
        Args:
            ws: WorkspaceInfo
            spec: WorkspaceRunProgramSpec
            ctx: Optional[InvocationContext]
        Returns:
            WorkspaceRunResult
        """
        pass


class BaseWorkspaceRuntime(ABC):
    """
    Base class for workspace runtime implementations.
    """

    @abstractmethod
    def manager(self, ctx: Optional[InvocationContext] = None) -> BaseWorkspaceManager:
        """
        Get workspace manager.
        Args:
            ctx: Optional[InvocationContext]
        Returns:
            BaseWorkspaceManager
        """
        pass

    @abstractmethod
    def fs(self, ctx: Optional[InvocationContext] = None) -> BaseWorkspaceFS:
        """
        Get workspace filesystem.
        Args:
            ctx: Optional[InvocationContext]
        Returns:
            BaseWorkspaceFS
        """
        pass

    @abstractmethod
    def runner(self, ctx: Optional[InvocationContext] = None) -> BaseProgramRunner:
        """
        Get program runner.
        Args:
            ctx: Optional[InvocationContext]
        Returns:
            BaseProgramRunner
        """
        pass

    @abstractmethod
    def describe(self, ctx: Optional[InvocationContext] = None) -> WorkspaceCapabilities:
        """
        Get engine capabilities.
        Args:
            ctx: Optional[InvocationContext]
        Returns:
            WorkspaceCapabilities
        """
        pass


class DefaultWorkspace(BaseWorkspaceRuntime):
    """
    Standard workspace implementation.
    """

    def __init__(
        self,
        manager: BaseWorkspaceManager,
        fs: BaseWorkspaceFS,
        runner: BaseProgramRunner,
    ):
        self._manager = manager
        self._fs = fs
        self._runner = runner

    def manager(self, ctx: Optional[InvocationContext] = None) -> BaseWorkspaceManager:
        """
        Get workspace manager.
        Args:
            ctx: Optional[InvocationContext]
        Returns:
            BaseWorkspaceManager
        """
        return self._manager

    def fs(self, ctx: Optional[InvocationContext] = None) -> BaseWorkspaceFS:
        """
        Get workspace filesystem.
        Args:
            ctx: Optional[InvocationContext]
        Returns:
            BaseWorkspaceFS
        """
        return self._fs

    def runner(self, ctx: Optional[InvocationContext] = None) -> BaseProgramRunner:
        """
        Get program runner.
        Args:
            ctx: Optional[InvocationContext]
        Returns:
            BaseProgramRunner
        """
        return self._runner

    def describe(self, ctx: Optional[InvocationContext] = None) -> WorkspaceCapabilities:
        """
        Get engine capabilities.
        Args:
            ctx: Optional[InvocationContext]
        Returns:
            WorkspaceCapabilities
        """
        return WorkspaceCapabilities()


def new_default_workspace_runtime(
    manager: BaseWorkspaceManager,
    fs: BaseWorkspaceFS,
    runner: BaseProgramRunner,
) -> DefaultWorkspace:
    """
    Construct a simple workspace from its components.
    Args:
        manager: BaseWorkspaceManager
        fs: BaseWorkspaceFS
        runner: BaseProgramRunner
    Returns:
        DefaultWorkspace
    """
    return DefaultWorkspace(manager=manager, fs=fs, runner=runner)


WorkspaceRuntimeResolver: TypeAlias = Callable[[InvocationContext], BaseWorkspaceRuntime]
"""Callback to resolve a workspace runtime."""
