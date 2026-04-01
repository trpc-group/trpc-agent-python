# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""
Base skill stager implementation.

This module provides the BaseStager class which is responsible for staging skills
to the workspace.
"""

from __future__ import annotations

import json
import posixpath
from datetime import datetime

from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
from trpc_agent_sdk.code_executors import DIR_OUT
from trpc_agent_sdk.code_executors import DIR_SKILLS
from trpc_agent_sdk.code_executors import DIR_WORK
from trpc_agent_sdk.code_executors import META_FILE_NAME
from trpc_agent_sdk.code_executors import TMP_FILE_NAME
from trpc_agent_sdk.code_executors import WorkspaceInfo
from trpc_agent_sdk.code_executors import WorkspacePutFileInfo
from trpc_agent_sdk.code_executors import WorkspaceRunProgramSpec
from trpc_agent_sdk.code_executors import WorkspaceStageOptions
from trpc_agent_sdk.context import InvocationContext

from .._types import SkillMetadata
from .._types import SkillWorkspaceMetadata
from .._utils import compute_dir_digest
from .._utils import shell_quote
from ._types import SkillStageRequest
from ._types import SkillStageResult
from ._utils import default_workspace_skill_dir

_SKILL_DIR_INPUTS = "inputs"
_SKILL_DIR_VENV = ".venv"

# Timeout for lightweight in-workspace bash helpers (chmod, ln, mv, test …).
# Heavy staging operations (stage_directory) are controlled by the caller.
_DEFAULT_HELPER_TIMEOUT = 5.0


class Stager:
    """Materializes skill package contents into a workspace.

    Mirrors Go's ``internal/skillstage.Stager``.  Create an instance with
    ``Stager()`` (or the module-level helper :func:`new`) and call
    :meth:`stage_skill` from async tool code.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def stage_skill(self, request: SkillStageRequest) -> SkillStageResult:
        """Copy *root* into ``skills/<name>`` and link shared workspace dirs.

        Re-staging is skipped when the directory digest is unchanged *and* the
        expected symlinks are already present (idempotent).  When either check
        fails the old staged directory is removed first so read-only files do
        not block the copy.

        Args:
            ctx:     Invocation context forwarded to workspace APIs.
            runtime: Workspace runtime used for FS and runner access.
            ws:      Target workspace.
            root:    Absolute host-side path of the skill source directory.
            name:    Skill name (becomes the sub-directory inside ``skills/``).

        Raises:
            RuntimeError: When the underlying workspace operations fail.
        """
        ctx = request.ctx
        ws = request.workspace
        root = request.repository.path(request.skill_name)
        runtime = request.engine or request.repository.workspace_runtime
        name = request.skill_name
        digest = compute_dir_digest(root)
        md = await self.load_workspace_metadata(ctx, runtime, ws)
        dest = posixpath.join(DIR_SKILLS, name)

        skill_meta = md.skills.get(name)
        if skill_meta and skill_meta.digest == digest and skill_meta.mounted:
            if await self.skill_links_present(ctx, runtime, ws, name):
                return SkillStageResult(workspace_skill_dir=default_workspace_skill_dir(name), )

        await self.remove_workspace_path(ctx, runtime, ws, dest)

        fs = runtime.fs(ctx)
        await fs.stage_directory(ws, root, dest, WorkspaceStageOptions(), ctx)

        await self._link_workspace_dirs(ctx, runtime, ws, name)
        await self._read_only_except_symlinks(ctx, runtime, ws, dest)

        md.skills[name] = SkillMetadata(
            name=name,
            rel_path=dest,
            digest=digest,
            mounted=True,
            staged_at=datetime.now(),
        )
        await self.save_workspace_metadata(ctx, runtime, ws, md)

        return SkillStageResult(workspace_skill_dir=default_workspace_skill_dir(name), )

    async def load_workspace_metadata(
        self,
        ctx: InvocationContext,
        runtime: BaseWorkspaceRuntime,
        ws: WorkspaceInfo,
    ) -> SkillWorkspaceMetadata:
        """Read workspace metadata from the in-workspace metadata file.

        Uses the workspace FS ``collect`` API so the call works for container
        workspaces where the metadata file lives inside the sandbox.  Returns a
        freshly initialized :class:`SkillWorkspaceMetadata` when the file is
        absent or empty.
        """
        now = datetime.now()
        default_md = SkillWorkspaceMetadata(
            version=1,
            created_at=now,
            updated_at=now,
            last_access=now,
        )

        fs = runtime.fs(ctx)
        files = await fs.collect(ws, [META_FILE_NAME], ctx)
        if not files or not files[0].content.strip():
            return default_md

        try:
            data = json.loads(files[0].content)
        except (json.JSONDecodeError, ValueError):
            return default_md

        md = SkillWorkspaceMetadata.from_dict(data)
        if not md.version:
            md.version = 1
        if not md.created_at:
            md.created_at = now
        md.last_access = now
        return md

    async def save_workspace_metadata(
        self,
        ctx: InvocationContext,
        runtime: BaseWorkspaceRuntime,
        ws: WorkspaceInfo,
        md: SkillWorkspaceMetadata,
    ) -> None:
        """Persist workspace metadata into the in-workspace metadata file.

        Writes to a temporary file first, then atomically renames it via a
        ``bash mv`` command — mirrors the Go atomic-write pattern.
        """
        if not md.version:
            md.version = 1
        now = datetime.now()
        if not md.created_at:
            md.created_at = now
        md.updated_at = now
        md.last_access = now

        buf = json.dumps(md.to_dict(), indent=2, ensure_ascii=False).encode("utf-8")

        fs = runtime.fs(ctx)
        await fs.put_files(
            ws,
            [WorkspacePutFileInfo(path=TMP_FILE_NAME, content=buf, mode=0o600)],
            ctx,
        )

        cmd = (f"set -e; mv -f {shell_quote(TMP_FILE_NAME)} {shell_quote(META_FILE_NAME)}")
        runner = runtime.runner(ctx)
        await runner.run_program(
            ws,
            WorkspaceRunProgramSpec(
                cmd="bash",
                args=["-lc", cmd],
                env={},
                cwd=".",
                timeout=_DEFAULT_HELPER_TIMEOUT,
            ),
            ctx,
        )

    async def skill_links_present(
        self,
        ctx: InvocationContext,
        runtime: BaseWorkspaceRuntime,
        ws: WorkspaceInfo,
        name: str,
    ) -> bool:
        """Return ``True`` when all expected symlinks exist inside the staged skill dir.

        Checks for ``out``, ``work``, and ``inputs`` symlinks under
        ``skills/<name>/``.
        """
        name = name.strip()
        if not name:
            return False

        base = posixpath.join(DIR_SKILLS, name)
        cmd = (f"test -L {shell_quote(posixpath.join(base, DIR_OUT))}"
               f" && test -L {shell_quote(posixpath.join(base, DIR_WORK))}"
               f" && test -L {shell_quote(posixpath.join(base, _SKILL_DIR_INPUTS))}")
        runner = runtime.runner(ctx)
        ret = await runner.run_program(
            ws,
            WorkspaceRunProgramSpec(
                cmd="bash",
                args=["-lc", cmd],
                env={},
                cwd=".",
                timeout=_DEFAULT_HELPER_TIMEOUT,
            ),
            ctx,
        )
        return ret.exit_code == 0

    async def remove_workspace_path(
        self,
        ctx: InvocationContext,
        runtime: BaseWorkspaceRuntime,
        ws: WorkspaceInfo,
        rel: str,
    ) -> None:
        """Remove a workspace-relative path, making non-symlink entries writable first.

        The chmod-before-remove step lets us clean up trees that were staged
        read-only (``chmod a-w``).
        """
        target = rel.strip()
        if not target:
            return

        cmd = (f"set -e; if [ -e {shell_quote(target)} ]; then"
               f" find {shell_quote(target)} -type l -prune -o -exec chmod u+w {{}} +; fi"
               f"; rm -rf {shell_quote(target)}")
        runner = runtime.runner(ctx)
        await runner.run_program(
            ws,
            WorkspaceRunProgramSpec(
                cmd="bash",
                args=["-lc", cmd],
                env={},
                cwd=".",
                timeout=_DEFAULT_HELPER_TIMEOUT,
            ),
            ctx,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _link_workspace_dirs(
        self,
        ctx: InvocationContext,
        runtime: BaseWorkspaceRuntime,
        ws: WorkspaceInfo,
        name: str,
    ) -> None:
        """Create out/work/inputs symlinks and a .venv placeholder inside the staged skill dir.

        Removes any stale out/work/inputs/.venv entries first so the ``ln``
        calls are always idempotent.
        """
        skill_root = posixpath.join(DIR_SKILLS, name)
        to_out = posixpath.join("..", "..", DIR_OUT)
        to_work = posixpath.join("..", "..", DIR_WORK)
        to_inputs = posixpath.join("..", "..", DIR_WORK, _SKILL_DIR_INPUTS)

        cmd = (f"set -e; cd {shell_quote(skill_root)}"
               f"; rm -rf out work {_SKILL_DIR_INPUTS} {shell_quote(_SKILL_DIR_VENV)}"
               f"; mkdir -p {shell_quote(to_inputs)} {shell_quote(_SKILL_DIR_VENV)}"
               f"; ln -sfn {shell_quote(to_out)} out"
               f"; ln -sfn {shell_quote(to_work)} work"
               f"; ln -sfn {shell_quote(to_inputs)} {_SKILL_DIR_INPUTS}")
        runner = runtime.runner(ctx)
        ret = await runner.run_program(
            ws,
            WorkspaceRunProgramSpec(
                cmd="bash",
                args=["-lc", cmd],
                env={},
                cwd=".",
                timeout=_DEFAULT_HELPER_TIMEOUT,
            ),
            ctx,
        )
        if ret.exit_code != 0:
            from trpc_agent_sdk.log import logger  # noqa: PLC0415
            logger.info("Stager._link_workspace_dirs failed for %r: %s", name, ret.stderr)

    async def _read_only_except_symlinks(
        self,
        ctx: InvocationContext,
        runtime: BaseWorkspaceRuntime,
        ws: WorkspaceInfo,
        dest: str,
    ) -> None:
        """Make all files under *dest* read-only, skipping symlinks and ``.venv``.

        The ``.venv`` directory is excluded so that package-install tools can
        write into it without fighting read-only permissions.
        """
        venv = posixpath.join(dest, _SKILL_DIR_VENV)
        cmd = (f"set -e; find {shell_quote(dest)}"
               f" -path {shell_quote(venv)} -prune"
               f" -o -type l -prune"
               f" -o -exec chmod a-w {{}} +")
        runner = runtime.runner(ctx)
        ret = await runner.run_program(
            ws,
            WorkspaceRunProgramSpec(
                cmd="bash",
                args=["-lc", cmd],
                env={},
                cwd=".",
                timeout=_DEFAULT_HELPER_TIMEOUT,
            ),
            ctx,
        )
        if ret.exit_code != 0:
            from trpc_agent_sdk.log import logger  # noqa: PLC0415
            logger.info("Stager._read_only_except_symlinks failed for %r: %s", dest, ret.stderr)

    @classmethod
    def create_stager(cls) -> "Stager":
        """Create and return a new :class:`Stager` instance."""
        return cls()
