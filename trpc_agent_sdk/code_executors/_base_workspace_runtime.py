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
from typing import Awaitable
from typing import Callable
from typing import TypeAlias
from typing import List
from typing import Optional
from typing import Tuple

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.log import logger

from ._artifacts import save_artifact_helper
from ._constants import DEFAULT_MAX_FILES
from ._constants import DEFAULT_MAX_TOTAL_BYTES
from ._constants import MAX_READ_SIZE_BYTES
from ._types import CodeFile
from ._types import ManifestFileRef
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

ManifestFetcher: TypeAlias = Callable[[str, int], Awaitable[Tuple[bytes, int]]]
"""Async callable ``(absolute_path, max_bytes) -> (data, raw_size)``.

Contract:
- ``data`` is the file's content truncated to at most ``max_bytes``. If the
  underlying medium cannot cheaply report the full size (e.g. a streaming
  read), the fetcher may return ``raw_size = len(data)``; callers that care
  about truncation must then treat ``len(data) == max_bytes`` as "possibly
  truncated".
- ``raw_size`` is the size of the file on the underlying medium before any
  truncation, used only to decide ``truncated`` / ``limits_hit`` flags.
- The fetcher must *not* raise for merely-empty files; it should raise only
  for genuine I/O errors so the backend can surface a meaningful message.
"""


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

    Subclasses are expected to implement the abstract operations using
    whatever I/O mechanism their backend exposes (direct filesystem,
    docker ``get_archive``, Cube RPC, ...). The shared *post-fetch*
    pipeline — turning raw matched paths plus a fetcher into
    :class:`CodeFile` / :class:`ManifestOutput` models — is provided
    here as protected helpers (:meth:`_build_code_files`,
    :meth:`_build_manifest_output`) so subclasses can call them
    directly without re-implementing the limit / inline / save / MIME
    sniffing plumbing, and can override them when they need a tweak.
    """

    @staticmethod
    def _relativize(ws_path: str, full_path: str) -> str:
        """Return ``full_path`` stripped of the ``ws.path + "/"`` prefix.

        Kept as a single helper so every backend produces identical
        relative paths in :class:`CodeFile` / :class:`ManifestFileRef`.
        Falls back to ``full_path`` when the match somehow escapes the
        workspace root (e.g. a symlink resolution surfaced an absolute
        path on a different mount).
        """
        prefix = ws_path.rstrip("/") + "/"
        if full_path.startswith(prefix):
            return full_path[len(prefix):]
        return full_path

    @staticmethod
    async def _build_code_files(
        ws_path: str,
        matches: List[str],
        fetcher: ManifestFetcher,
        *,
        max_read_size: int = MAX_READ_SIZE_BYTES,
    ) -> List[CodeFile]:
        """Materialise a :meth:`collect` call.

        Reads each matched path with a single per-file byte cap
        (``max_read_size``, defaulting to :data:`MAX_READ_SIZE_BYTES`),
        sniffs the MIME type, and wraps the result in a :class:`CodeFile`.
        Duplicate ``rel`` paths are skipped so callers can pass the raw
        glob output without pre-deduping.

        Subclasses normally call this from their :meth:`collect`
        override, supplying a ``fetcher`` that knows how to read bytes
        from the underlying medium (see :data:`ManifestFetcher`).
        Override this method to change the post-fetch shape (e.g. to
        emit a richer ``CodeFile`` subclass) without re-implementing
        the dedupe / sniff / cap loop.
        """
        # Local import keeps the base-class file free of optional /
        # heavy dependencies (libmagic, mimetypes lookup tables) at
        # module-load time.
        from .utils._files import detect_content_type

        seen: set[str] = set()
        out: List[CodeFile] = []
        for full_path in matches:
            rel = BaseWorkspaceFS._relativize(ws_path, full_path)
            if rel in seen:
                continue
            seen.add(rel)
            try:
                data, raw_size = await fetcher(full_path, max_read_size)
            except Exception:  # pylint: disable=broad-except
                # Keep collect() best-effort: a single unreadable file
                # must not abort the whole batch. Backends that prefer
                # strict semantics can short-circuit themselves before
                # calling us.
                out.append(CodeFile(name=rel, content="", mime_type="application/octet-stream"))
                continue
            mime = detect_content_type(full_path, data)
            out.append(
                CodeFile(
                    name=rel,
                    content=data.decode("utf-8", errors="replace"),
                    mime_type=mime,
                    size_bytes=raw_size,
                    truncated=raw_size > len(data),
                ))
        return out

    @staticmethod
    async def _build_manifest_output(
        ws_path: str,
        spec: WorkspaceOutputSpec,
        matches: List[str],
        fetcher: ManifestFetcher,
        ctx: Optional[InvocationContext],
        *,
        strict_truncated_save: bool = False,
    ) -> Tuple[ManifestOutput, List[str], List[int]]:
        """Materialise a :meth:`collect_outputs` call.

        Applies ``spec``'s limits (``max_files`` / ``max_file_bytes`` /
        ``max_total_bytes``), fills ``inline`` / ``save`` branches, and
        produces a :class:`ManifestOutput`. Also returns the list of
        saved artifact names and versions so backends that record
        metadata (e.g. local's ``OutputRecordMeta``) don't need to
        re-scan the manifest.

        Args:
            ws_path: Absolute workspace path, used to produce relative
                ``name`` fields.
            spec: The output spec declared by the caller.
            matches: Absolute paths already filtered by the backend's
                glob.
            fetcher: Async callable that returns ``(data, raw_size)``
                for a path, capped by a requested byte budget. See
                :data:`ManifestFetcher`.
            ctx: Invocation context. Required when ``spec.save`` is set,
                because artifact persistence goes through it.
            strict_truncated_save: When ``True``, raise ``RuntimeError``
                if ``spec.save`` is requested for a file that was
                truncated by the per-file cap. Container preserves this
                "refuse to save half a binary" behaviour; local/cube
                historically allow it.

        Returns:
            Tuple of ``(manifest, saved_names, saved_versions)``.
        """
        from .utils._files import detect_content_type

        max_files = spec.max_files or DEFAULT_MAX_FILES
        max_file_bytes = spec.max_file_bytes or MAX_READ_SIZE_BYTES
        max_total = spec.max_total_bytes or DEFAULT_MAX_TOTAL_BYTES

        manifest = ManifestOutput()
        saved_names: List[str] = []
        saved_versions: List[int] = []

        seen: set[str] = set()
        total_bytes = 0
        count = 0

        for full_path in matches:
            # Check limits *before* fetching so a blown budget doesn't
            # cause a useless read of the next big file.
            if count >= max_files or total_bytes >= max_total:
                manifest.limits_hit = True
                break

            rel = BaseWorkspaceFS._relativize(ws_path, full_path)
            if rel in seen:
                continue
            seen.add(rel)

            # Per-file cap is ``max_file_bytes``, but also clamp to the
            # remaining total budget so a single huge file cannot exceed
            # ``max_total`` all on its own.
            remaining_total = max_total - total_bytes
            read_budget = min(max_file_bytes, remaining_total)
            if read_budget <= 0:
                manifest.limits_hit = True
                break

            try:
                data, raw_size = await fetcher(full_path, read_budget)
            except Exception:  # pylint: disable=broad-except
                # Mirror ``_build_code_files``: a single unreadable file
                # must not abort the whole collection. Emit a sentinel
                # entry with empty content and the canonical
                # "unknown / unreadable" MIME type. This preserves the
                # pre-refactor local behaviour
                # (``_read_limited_with_cap`` caught and returned
                # ``("", "application/octet-stream")``) and is a small
                # tolerance upgrade for the container backend, which
                # used to abort on the first transient tar error.
                manifest.files.append(ManifestFileRef(name=rel, mime_type="application/octet-stream"))
                count += 1
                continue

            # Mark limits_hit if either cap actually bit.
            if raw_size > len(data):
                manifest.limits_hit = True

            truncated = raw_size > len(data)
            if truncated and spec.save and strict_truncated_save:
                raise RuntimeError(f"cannot save truncated output file: {rel}")

            total_bytes += len(data)
            count += 1

            mime = detect_content_type(full_path, data)
            file_ref = ManifestFileRef(name=rel, mime_type=mime)

            if spec.inline:
                file_ref.content = data.decode("utf-8", errors="replace")

            if spec.save:
                if ctx is None:
                    raise ValueError("Context is required to save artifacts")
                save_name = (spec.name_template + rel) if spec.name_template else rel
                version = await save_artifact_helper(ctx, save_name, data, mime)
                file_ref.saved_as = save_name
                file_ref.version = version
                saved_names.append(save_name)
                saved_versions.append(version)

            manifest.files.append(file_ref)

        return manifest, saved_names, saved_versions

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


def get_workspace_runtime_with_resolver(
        ctx: InvocationContext,
        resolver: Optional[WorkspaceRuntimeResolver] = None,
        workspace_runtime: Optional[BaseWorkspaceRuntime] = None) -> BaseWorkspaceRuntime:
    """
    Get workspace runtime.
    Args:
        ctx: InvocationContext
        resolver: WorkspaceRuntimeResolver
        workspace_runtime: Optional[BaseWorkspaceRuntime]
    Returns:
        BaseWorkspaceRuntime
    """
    if resolver is not None:
        workspace_runtime = resolver(ctx)
    if workspace_runtime is None:
        raise ValueError("Workspace runtime not found")
    return workspace_runtime
