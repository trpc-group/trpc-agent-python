"""Prepare and reliably clean up isolated workspaces."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trpc_agent_sdk.code_executors import WorkspacePutFileInfo


@dataclass
class PreparedWorkspace:
    info: Any
    cleanup_id: str


class WorkspaceManager:
    def __init__(self, runtime: Any) -> None:
        self._manager = runtime.manager()
        self._fs = runtime.fs()

    async def prepare(
        self,
        execution_id: str,
        *,
        directories: dict[str, Path],
        files: dict[str, bytes],
        max_file_bytes: int = 2_000_000,
        max_total_bytes: int = 50_000_000,
    ) -> PreparedWorkspace:
        workspace = await self._manager.create_workspace(execution_id)
        try:
            staged: list[WorkspacePutFileInfo] = []
            total = 0
            for destination, source in directories.items():
                for path in source.rglob("*"):
                    relative = path.relative_to(source)
                    if (
                        not path.is_file()
                        or any(part in {".git", "__pycache__", ".venv", "node_modules"} for part in relative.parts)
                        or path.suffix == ".pyc"
                        or path.is_symlink()
                    ):
                        continue
                    size = path.stat().st_size
                    if size > max_file_bytes:
                        continue
                    total += size
                    if total > max_total_bytes:
                        raise ValueError("project snapshot exceeds the configured size limit")
                    staged.append(
                        WorkspacePutFileInfo(
                            path=f"{destination.rstrip('/')}/{relative.as_posix()}",
                            content=path.read_bytes(),
                        )
                    )
            staged.extend(
                WorkspacePutFileInfo(path=path, content=content)
                for path, content in files.items()
            )
            await self._fs.put_files(workspace, staged)
            return PreparedWorkspace(info=workspace, cleanup_id=workspace.id or execution_id)
        except Exception:
            await self._manager.cleanup(workspace.id or execution_id)
            raise

    async def cleanup(self, workspace: PreparedWorkspace) -> None:
        await self._manager.cleanup(workspace.cleanup_id)
