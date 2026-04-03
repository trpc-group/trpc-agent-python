# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""File storage implementation.

This backend provides a lightweight key-value style storage on local filesystem,
while still following the same ``BaseStorage`` contract used by SQL/Redis
backends.
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Optional
from typing_extensions import override
from urllib.parse import quote
from urllib.parse import unquote

import aiofiles
from aiofiles import os as aio_os
from aiofiles import ospath as aio_ospath
from nanobot.utils.helpers import safe_filename
from trpc_agent_sdk.storage import BaseStorage
from trpc_agent_sdk.storage import DEFAULT_MAX_KEY_LENGTH

from ..config import FileStorageConfig
from ._constants import HISTORY_FILENAME
from ._constants import MEMORY_FILENAME


@dataclass
class FileCondition:
    """Query condition for file storage."""

    limit: int = -1
    """Maximum number of matched records to return. ``-1`` means no limit."""


@dataclass
class FileData:
    """Data payload for file storage add operation."""

    key: str
    value: Any


@dataclass
class FileSession:
    """Lightweight file storage session."""

    base_dir: Path


class FileAsyncContextManager:
    """Async context manager for file storage sessions."""

    def __init__(self, storage: "AioFileStorage") -> None:
        self._storage = storage
        self._file_session: Optional[FileSession] = None

    async def __aenter__(self) -> FileSession:
        self._file_session = await self._storage.create_file_session()
        return self._file_session

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self._file_session = None


class AioFileStorage(BaseStorage):
    """Local filesystem based storage backend."""

    def __init__(self, config: FileStorageConfig) -> None:
        super().__init__()
        self._base_dir = Path(config.base_dir).expanduser().resolve()
        self._max_key_length = config.max_key_length
        self._closed = False

    async def create_file_session(self) -> FileSession:
        await aio_os.makedirs(self._base_dir, mode=0o755, exist_ok=True)
        return FileSession(base_dir=self._base_dir)

    @override
    def create_db_session(self) -> FileAsyncContextManager:
        return FileAsyncContextManager(self)

    @override
    async def add(self, db: FileSession, data: FileData | dict[str, Any]) -> None:
        if isinstance(data, dict):
            key = str(data.get("key", "")).strip()
            value = data.get("value")
        else:
            key = data.key.strip()
            value = data.value
        self._validate_key(key)
        file_path = await self._resolve_key_path(db.base_dir, key)
        await aio_os.makedirs(file_path.parent, mode=0o755, exist_ok=True)
        if self._is_text_file_key(key) and isinstance(value, str):
            payload = value
        else:
            payload = json.dumps(value, ensure_ascii=False, default=str)
        if key.startswith("history:"):
            mode = "a"
        else:
            mode = "w"
        async with aiofiles.open(file_path, mode=mode, encoding="utf-8") as fp:
            await fp.write(payload)

    @override
    async def delete(self, db: FileSession, key: str, conditions: Optional[FileCondition] = None) -> None:
        file_path = await self._resolve_key_path(db.base_dir, key)
        if not await aio_ospath.exists(file_path):
            return
        await aio_os.remove(file_path)

    @override
    async def query(self,
                    db: FileSession,
                    key: str,
                    conditions: Optional[FileCondition] = None) -> list[tuple[str, Any]]:
        cond = conditions or FileCondition()
        pattern = key or "*"
        names = await aio_os.listdir(db.base_dir)
        paths = [db.base_dir / name for name in names if name.endswith(".json")]
        results: list[tuple[str, Any]] = []
        for file_path in sorted(paths):
            decoded_key = self._path_to_key(file_path)
            if not fnmatch.fnmatch(decoded_key, pattern):
                continue
            value = await self._read_value(file_path)
            results.append((decoded_key, value))
            if cond.limit > 0 and len(results) >= cond.limit:
                break
        return results

    @override
    async def get(self, db: FileSession, key: str) -> Any:
        file_path = await self._resolve_key_path(db.base_dir, key)
        if not await aio_ospath.exists(file_path):
            return None
        if self._is_text_file_key(key):
            async with aiofiles.open(file_path, mode="r", encoding="utf-8") as fp:
                return await fp.read()
        return await self._read_value(file_path)

    @override
    async def commit(self, db: FileSession) -> None:
        pass

    @override
    async def refresh(self, db: FileSession, data: FileData) -> None:
        pass

    @override
    async def close(self) -> None:
        self._closed = True

    async def _read_value(self, file_path: Path) -> Any:
        async with aiofiles.open(file_path, mode="r", encoding="utf-8") as fp:
            raw = await fp.read()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    @staticmethod
    def _key_to_path(base_dir: Path, key: str) -> Path:
        encoded = quote(key, safe="")
        return base_dir / f"{encoded}.json"

    @staticmethod
    def _path_to_key(path: Path) -> str:
        return unquote(path.stem)

    @staticmethod
    def _validate_key(key: str) -> None:
        if not key:
            raise ValueError("AioFileStorage key cannot be empty")
        if len(key) > DEFAULT_MAX_KEY_LENGTH:
            raise ValueError(f"AioFileStorage key too long: {len(key)} > {DEFAULT_MAX_KEY_LENGTH}")
        if "/" in key or "\\" in key:
            # Key is logical identifier, not filesystem path.
            raise ValueError("AioFileStorage key must not contain path separators")

    async def _session_dir(self, base_dir: Path, memory_key: str) -> Path:
        """Return (creating if necessary) the per-session directory path.

        Args:
            memory_key: The memory key.

        Returns:
            Path: The per-session directory path.
        """
        if memory_key:
            safe_key = safe_filename(memory_key.replace(":", "_"))
            directory = base_dir / safe_key
        else:
            directory = base_dir
        await aio_os.makedirs(directory, exist_ok=True)
        return directory

    async def _resolve_key_path(self, base_dir: Path, key: str) -> Path:
        if key.startswith("memory:"):
            return (await self._session_dir(base_dir, "")) / MEMORY_FILENAME
        if key.startswith("history:"):
            return (await self._session_dir(base_dir, "")) / HISTORY_FILENAME
        if key.startswith("session:"):
            return Path(unquote(key[len("session:"):]))
        return self._key_to_path(base_dir, key)

    @staticmethod
    def _is_text_file_key(key: str) -> bool:
        return key.startswith(("memory:", "history:", "session:"))
