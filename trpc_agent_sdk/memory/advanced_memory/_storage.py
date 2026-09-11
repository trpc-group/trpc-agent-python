# Tencent is pleased to support the open source ecosystem.
#
# Copyright (C) 2026 Tencent. All rights reserved.
# Licensed under Apache-2.0.
"""Long-term memory storage owned by AdvancedMemoryService."""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
import time
from dataclasses import replace
from datetime import datetime
from datetime import timezone
from pathlib import Path

from ._formats import MemoryDocument
from ._formats import MemoryIndexEntry
from ._formats import limit_memory_index

from ._config import AdvancedMemoryServiceConfig
from ._paths import AdvancedMemoryPaths

_MEMORY_INDEX_PATTERN = re.compile(r"^- \[(?P<name>.+?)\]（(?P<filename>.+?)）:(?P<summary>.+)$")


def parse_memory_index(index: str) -> list[MemoryIndexEntry]:
    """Parse standard entries from a MEMORY.md index."""
    entries: list[MemoryIndexEntry] = []
    for line in index.splitlines():
        match = _MEMORY_INDEX_PATTERN.match(line.strip())
        if match is not None:
            entries.append(MemoryIndexEntry(**match.groupdict()))
    return entries


def prune_memory_index(index: str, valid_filenames: set[str]) -> str:
    """Remove index entries whose topic files no longer exist."""
    lines = [
        line for line in index.splitlines()
        if (match := _MEMORY_INDEX_PATTERN.match(line.strip())) is None or match.group("filename") in valid_filenames
    ]
    if lines == index.splitlines():
        return index
    return "\n".join(lines) + ("\n" if lines else "")


def _atomic_write_text(path: Path, content: str, *, encoding: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding=encoding) as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _is_expired(path: Path, ttl: int | None) -> bool:
    return ttl is not None and path.exists() and time.time() - path.stat().st_mtime >= ttl


class LongTermMemoryStore:
    """Read and write MEMORY.md and its topic files."""

    def __init__(
        self,
        config: AdvancedMemoryServiceConfig,
        paths: AdvancedMemoryPaths | None = None,
    ) -> None:
        self._config = config
        self._paths = paths or AdvancedMemoryPaths(config)

    @property
    def index_path(self) -> Path:
        return self._paths.memory_index_path

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        self._paths.ensure_base_directories()
        if not self.index_path.exists():
            _atomic_write_text(self.index_path, "", encoding=self._config.encoding)

    async def read_index(self) -> str:
        return await asyncio.to_thread(self._read_index_sync)

    def _read_index_sync(self) -> str:
        if _is_expired(self.index_path, self._config.memory_ttl_seconds):
            for path in self._paths.memory_dir.glob("*.md"):
                path.unlink(missing_ok=True)
            return ""
        if not self.index_path.exists():
            return ""
        with self.index_path.open(encoding=self._config.encoding) as source:
            index = source.read()
        valid_filenames = {
            path.name
            for path in self._paths.memory_dir.glob("*.md")
            if path.name != self._config.memory_index_name and not _is_expired(path, self._config.memory_ttl_seconds)
        }
        pruned_index = prune_memory_index(index, valid_filenames)
        if pruned_index != index:
            _atomic_write_text(
                self.index_path,
                pruned_index,
                encoding=self._config.encoding,
            )
        return limit_memory_index(
            pruned_index,
            max_lines=self._config.memory_index_max_lines,
            max_bytes=self._config.memory_index_max_bytes,
            encoding=self._config.encoding,
        )

    async def write_index(self, entries: list[MemoryIndexEntry]) -> None:
        content = "\n".join(entry.to_markdown() for entry in entries)
        await asyncio.to_thread(
            _atomic_write_text,
            self.index_path,
            f"{content}\n" if content else "",
            encoding=self._config.encoding,
        )

    async def read_topic(self, topic_name: str) -> str | None:
        path = self._paths.memory_topic_path(topic_name)
        return await asyncio.to_thread(lambda: path.read_text(encoding=self._config.encoding)
                                       if path.exists() else None)

    async def read_topic_frontmatter(self, topic_name: str) -> str | None:
        content = await self.read_topic(topic_name)
        if content is None:
            return None
        lines: list[str] = []
        for line in content.splitlines(keepends=True):
            lines.append(line)
            if len(lines) > 1 and line.rstrip("\r\n") == "---":
                break
        return "".join(lines)

    async def write_topic(self, topic_name: str, document: MemoryDocument) -> Path:
        path = self._paths.memory_topic_path(topic_name)
        updated = replace(document, updated_at=datetime.now(timezone.utc))
        await asyncio.to_thread(
            _atomic_write_text,
            path,
            updated.to_markdown(),
            encoding=self._config.encoding,
        )
        return path

    async def list_topics(self) -> list[Path]:
        return await asyncio.to_thread(lambda: sorted(path for path in self._paths.memory_dir.glob("*.md")
                                                      if path.name != self._config.memory_index_name))


class LocalAdvancedMemoryCleanup:
    """Remove expired long-term memory files for the local backend."""

    def __init__(self, config: AdvancedMemoryServiceConfig) -> None:
        self._config = config
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None

    async def start(self) -> None:
        if self._task is not None or self._config.memory_ttl_seconds is None:
            return
        self._stop_event = asyncio.Event()
        await self.cleanup_once()
        self._task = asyncio.create_task(self._run())

    async def cleanup_once(self) -> None:
        await asyncio.to_thread(self._cleanup_sync)

    def _cleanup_sync(self) -> None:
        root = self._config.root_dir
        memory_dirs = [root / self._config.memory_dir_name]
        tenants_root = root / "tenants"
        if tenants_root.exists():
            for app_dir in tenants_root.iterdir():
                if app_dir.is_dir():
                    memory_dirs.extend(user_dir / self._config.memory_dir_name for user_dir in app_dir.iterdir()
                                       if user_dir.is_dir())
        for memory_dir in memory_dirs:
            index_path = memory_dir / self._config.memory_index_name
            if _is_expired(index_path, self._config.memory_ttl_seconds):
                for path in memory_dir.glob("*.md"):
                    path.unlink(missing_ok=True)

    async def _run(self) -> None:
        if self._stop_event is None:
            return
        try:
            while not self._stop_event.is_set():
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._config.memory_ttl_seconds or 60,
                    )
                except asyncio.TimeoutError:
                    await self.cleanup_once()
        except asyncio.CancelledError:
            raise

    async def close(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        self._stop_event = None
