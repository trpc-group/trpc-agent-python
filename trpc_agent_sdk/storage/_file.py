# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Small asynchronous file storage primitives."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path


class FileStorage:
    """Read and atomically write files below one configured root directory."""

    def __init__(self, root: str | Path, *, encoding: str = "utf-8") -> None:
        self.root = Path(root)
        self.encoding = encoding

    async def read_text(self, key: str | Path) -> str | None:
        """Read one relative file, returning ``None`` when it does not exist."""
        path = self._resolve(key)

        def _read() -> str | None:
            if not path.exists():
                return None
            return path.read_text(encoding=self.encoding)

        return await asyncio.to_thread(_read)

    async def write_text(
        self,
        key: str | Path,
        content: str,
        *,
        overwrite: bool = True,
    ) -> bool:
        """Atomically write one relative file.

        Returns ``False`` without changing the file when ``overwrite`` is
        disabled and the target already exists.
        """
        path = self._resolve(key)
        return await asyncio.to_thread(
            self._write_text_sync,
            path,
            content,
            overwrite,
        )

    async def list_files(self, key: str | Path, pattern: str) -> list[Path]:
        """List relative files matching a glob below one relative directory."""
        directory = self._resolve(key)

        def _list() -> list[Path]:
            if not directory.exists():
                return []
            return sorted(path.relative_to(self.root.resolve()) for path in directory.glob(pattern) if path.is_file())

        return await asyncio.to_thread(_list)

    async def close(self) -> None:
        """Release resources. File storage does not retain open handles."""

    def _resolve(self, key: str | Path) -> Path:
        root = self.root.resolve()
        path = (root / key).resolve()
        if path != root and root not in path.parents:
            raise ValueError(f"File storage key escapes root: {key!s}")
        return path

    def _write_text_sync(
        self,
        path: Path,
        content: str,
        overwrite: bool,
    ) -> bool:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            dir=path.parent,
        )
        try:
            with os.fdopen(descriptor, "w", encoding=self.encoding) as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            if overwrite:
                os.replace(temporary_name, path)
            else:
                try:
                    os.link(temporary_name, path)
                except FileExistsError:
                    return False
            return True
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
