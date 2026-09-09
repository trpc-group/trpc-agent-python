# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Basic disk stores for long-term memory, session memory, and transcripts."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import threading
import time
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any

from ._config import AdvancedMemoryConfig
from ._formats import MemoryDocument
from ._formats import MemoryIndexEntry
from ._formats import SessionMemoryDocument
from ._paths import AdvancedMemoryPaths


def _atomic_write_text(path: Path, content: str, *, encoding: str) -> None:
    """Atomically replace a text file using a temporary sibling file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(file_descriptor, "w", encoding=encoding) as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _is_expired(path: Path, ttl: int | None) -> bool:
    if ttl is None or not path.exists():
        return False
    return time.time() - path.stat().st_mtime >= ttl


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def _expire_memory_dir(memory_dir: Path, config: AdvancedMemoryConfig) -> bool:
    """Expire the whole long-term memory group using index activity time."""
    index_path = memory_dir / config.memory_index_name
    if not _is_expired(index_path, config.memory_ttl_seconds):
        return False
    for path in memory_dir.glob("*.md"):
        path.unlink(missing_ok=True)
    return True


def _refresh_memory_dir(memory_dir: Path) -> None:
    """Refresh activity for every file in the long-term memory group."""
    for path in memory_dir.glob("*.md"):
        _touch(path)


def _session_activity_path(session_dir: Path) -> Path:
    return session_dir / ".advanced-memory-activity"


def _expire_session_dir(session_dir: Path, config: AdvancedMemoryConfig) -> bool:
    """Expire all Advanced Memory data belonging to one local session."""
    if not session_dir.exists() or config.session_ttl_seconds is None:
        return False
    activity_path = _session_activity_path(session_dir)
    if activity_path.exists():
        expired = _is_expired(activity_path, config.session_ttl_seconds)
    else:
        files = [path for path in session_dir.rglob("*") if path.is_file()]
        expired = bool(files) and time.time() - max(path.stat().st_mtime
                                                    for path in files) >= config.session_ttl_seconds
    if expired:
        if config.session_ttl_delete_transcripts:
            shutil.rmtree(session_dir, ignore_errors=True)
        else:
            transcript_path = session_dir / config.transcript_name
            for child in session_dir.iterdir():
                if child == transcript_path:
                    continue
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
    return expired


def _refresh_session_dir(session_dir: Path) -> None:
    _touch(_session_activity_path(session_dir))


class LongTermMemoryStore:
    """Manage MEMORY.md and its detail files in the same directory."""

    def __init__(self, config: AdvancedMemoryConfig, paths: AdvancedMemoryPaths | None = None) -> None:
        """Initialize long-term storage without changing legacy memory."""
        self._config = config
        self._paths = paths or AdvancedMemoryPaths(config)

    @property
    def index_path(self) -> Path:
        """Return the disk path for MEMORY.md."""
        return self._paths.memory_index_path

    async def initialize(self) -> None:
        """Create the memory directory and an empty index."""
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        """Synchronously create the memory directory and empty index."""
        self._paths.ensure_base_directories()
        if not self.index_path.exists():
            _atomic_write_text(self.index_path, "", encoding=self._config.encoding)

    async def read_index(self) -> str:
        """Read only the configured prefix of MEMORY.md."""
        return await asyncio.to_thread(self._read_index_sync)

    def _read_index_sync(self) -> str:
        """Synchronously read MEMORY.md within configured limits."""
        if _expire_memory_dir(self._paths.memory_dir, self._config) or not self.index_path.exists():
            return ""
        _refresh_memory_dir(self._paths.memory_dir)
        with self.index_path.open("r", encoding=self._config.encoding) as index_file:
            lines: list[str] = []
            used_bytes = 0
            for _ in range(self._config.memory_index_max_lines):
                line = index_file.readline()
                if not line:
                    break
                line_bytes = len(line.encode(self._config.encoding))
                if used_bytes + line_bytes > self._config.memory_index_max_bytes:
                    break
                lines.append(line)
                used_bytes += line_bytes
        return "".join(lines)

    async def write_index(self, entries: list[MemoryIndexEntry]) -> None:
        """Atomically write MEMORY.md in the standard index format."""
        content = "\n".join(entry.to_markdown() for entry in entries)
        if content:
            content += "\n"
        await asyncio.to_thread(self._write_index_sync, content)

    def _write_index_sync(self, content: str) -> None:
        """Synchronously write MEMORY.md; read_index applies prompt-size limits."""
        _atomic_write_text(self.index_path, content, encoding=self._config.encoding)
        _refresh_memory_dir(self._paths.memory_dir)

    async def read_topic(self, topic_name: str) -> str | None:
        """Read a detail memory topic, returning None if absent."""
        path = self._paths.memory_topic_path(topic_name)
        return await asyncio.to_thread(self._read_topic_sync, path)

    def _read_topic_sync(self, path: Path) -> str | None:
        if _expire_memory_dir(self._paths.memory_dir, self._config) or not path.exists():
            return None
        _refresh_memory_dir(self._paths.memory_dir)
        return path.read_text(encoding=self._config.encoding)

    async def read_topic_frontmatter(self, topic_name: str) -> str | None:
        """Read only the frontmatter of a detail memory topic."""
        path = self._paths.memory_topic_path(topic_name)
        return await asyncio.to_thread(self._read_frontmatter_sync, path)

    def _read_frontmatter_sync(self, path: Path) -> str | None:
        """Synchronously read a topic's bounded frontmatter block."""
        if _expire_memory_dir(self._paths.memory_dir, self._config) or not path.exists():
            return None
        _refresh_memory_dir(self._paths.memory_dir)
        lines: list[str] = []
        with path.open(encoding=self._config.encoding) as file:
            for line in file:
                lines.append(line)
                if len(lines) > 1 and line.rstrip("\r\n") == "---":
                    break
        return "".join(lines)

    async def write_topic(self, topic_name: str, document: MemoryDocument) -> Path:
        """Atomically write a detail memory file with frontmatter."""
        path = self._paths.memory_topic_path(topic_name)
        document = replace(document, updated_at=datetime.now(timezone.utc))
        await asyncio.to_thread(self._write_topic_sync, path, document.to_markdown())
        return path

    def _write_topic_sync(self, path: Path, content: str) -> None:
        _expire_memory_dir(self._paths.memory_dir, self._config)
        _atomic_write_text(path, content, encoding=self._config.encoding)
        _refresh_memory_dir(self._paths.memory_dir)

    async def list_topics(self) -> list[Path]:
        """List detail memory files by name, excluding MEMORY.md."""
        return await asyncio.to_thread(self._list_topics_sync)

    def _list_topics_sync(self) -> list[Path]:
        """Synchronously list all detail memory files."""
        if _expire_memory_dir(self._paths.memory_dir, self._config):
            return []
        if not self._paths.memory_dir.exists():
            return []
        _refresh_memory_dir(self._paths.memory_dir)
        return sorted(
            (path for path in self._paths.memory_dir.glob("*.md") if path.name != self._config.memory_index_name),
            key=lambda path: path.name,
        )


class SessionMemoryStore:
    """Manage an isolated structured Markdown summary per session."""

    def __init__(self, config: AdvancedMemoryConfig, paths: AdvancedMemoryPaths | None = None) -> None:
        """Initialize session memory storage."""
        self._config = config
        self._paths = paths or AdvancedMemoryPaths(config)

    async def read(self, session_id: str) -> str | None:
        """Read session memory, returning None if absent."""
        path = self._paths.session_memory_path(session_id)
        return await asyncio.to_thread(self._read_sync, session_id, path)

    def _read_sync(self, session_id: str, path: Path) -> str | None:
        """Synchronously read session memory."""
        session_dir = self._paths.session_dir(session_id)
        if _expire_session_dir(session_dir, self._config) or not path.exists():
            return None
        _refresh_session_dir(session_dir)
        return path.read_text(encoding=self._config.encoding)

    async def write(self, session_id: str, document: SessionMemoryDocument) -> Path:
        """Atomically write session memory using the fixed section template."""
        path = self._paths.session_memory_path(session_id)
        await asyncio.to_thread(
            self._write_sync,
            session_id,
            path,
            document.to_markdown(),
        )
        return path

    def _write_sync(self, session_id: str, path: Path, content: str) -> None:
        session_dir = self._paths.session_dir(session_id)
        _expire_session_dir(session_dir, self._config)
        _atomic_write_text(path, content, encoding=self._config.encoding)
        _refresh_session_dir(session_dir)


class ToolResultStore:
    """Persist complete tool results that exceed the context budget."""

    def __init__(self, config: AdvancedMemoryConfig, paths: AdvancedMemoryPaths | None = None) -> None:
        """Initialize large tool-result storage."""
        self._config = config
        self._paths = paths or AdvancedMemoryPaths(config)

    async def write(self, session_id: str, result_id: str, serialized_result: str) -> Path:
        """Atomically write a complete tool result and return its disk path."""
        path = self._paths.tool_result_path(session_id, result_id)
        await asyncio.to_thread(
            self._write_sync,
            session_id,
            path,
            serialized_result,
        )
        return path

    async def read(self, session_id: str, result_id: str) -> str | None:
        """Read a persisted complete tool result."""
        path = self._paths.tool_result_path(session_id, result_id)
        return await asyncio.to_thread(self._read_sync, session_id, path)

    def _read_sync(self, session_id: str, path: Path) -> str | None:
        """Synchronously read an optional complete tool-result file."""
        session_dir = self._paths.session_dir(session_id)
        if _expire_session_dir(session_dir, self._config) or not path.exists():
            return None
        _refresh_session_dir(session_dir)
        return path.read_text(encoding=self._config.encoding)

    def _write_sync(self, session_id: str, path: Path, content: str) -> None:
        session_dir = self._paths.session_dir(session_id)
        _expire_session_dir(session_dir, self._config)
        _atomic_write_text(path, content, encoding=self._config.encoding)
        _refresh_session_dir(session_dir)


class TranscriptStore:
    """Store complete per-session records as append-only JSONL."""

    def __init__(self, config: AdvancedMemoryConfig, paths: AdvancedMemoryPaths | None = None) -> None:
        """Initialize transcript storage and its process-local write lock."""
        self._config = config
        self._paths = paths or AdvancedMemoryPaths(config)
        self._write_lock = threading.Lock()
        self._seen_unique_values: dict[tuple[Path, str], set[str]] = {}

    async def append(self, session_id: str, record: Mapping[str, Any]) -> Path:
        """Append one JSON-serializable record to a session transcript."""
        path = self._paths.transcript_path(session_id)
        payload = dict(record)
        payload.setdefault("recorded_at", datetime.now(timezone.utc).isoformat())
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
        await asyncio.to_thread(self._append_sync, path, serialized)
        return path

    def _append_sync(self, path: Path, serialized: str) -> None:
        """Synchronously append one transcript line under the write lock."""
        _expire_session_dir(path.parent, self._config)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._write_lock:
            self._append_serialized_unlocked(path, serialized)
            _refresh_session_dir(path.parent)

    def _append_serialized_unlocked(self, path: Path, serialized: str) -> None:
        """Append one serialized line while the caller holds the lock."""
        with path.open("a", encoding=self._config.encoding) as transcript_file:
            transcript_file.write(serialized)
            transcript_file.write("\n")
            transcript_file.flush()
            if self._config.transcript_fsync:
                os.fsync(transcript_file.fileno())

    async def append_unique(
        self,
        session_id: str,
        record: Mapping[str, Any],
        *,
        unique_key: str,
    ) -> tuple[Path, bool]:
        """Append a transcript record after de-duplicating by a field."""
        path = self._paths.transcript_path(session_id)
        payload = dict(record)
        unique_value = payload.get(unique_key)
        if not isinstance(unique_value, str) or not unique_value:
            raise ValueError(f"Transcript unique key {unique_key!r} must be a non-empty string")
        payload.setdefault("recorded_at", datetime.now(timezone.utc).isoformat())
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
        appended = await asyncio.to_thread(
            self._append_unique_sync,
            path,
            serialized,
            unique_key,
            unique_value,
        )
        return path, appended

    def _append_unique_sync(
        self,
        path: Path,
        serialized: str,
        unique_key: str,
        unique_value: str,
    ) -> bool:
        """Load de-duplication state and append only new records."""
        with self._write_lock:
            if _expire_session_dir(path.parent, self._config):
                for cache_key in list(self._seen_unique_values):
                    if cache_key[0] == path:
                        self._seen_unique_values.pop(cache_key, None)
            path.parent.mkdir(parents=True, exist_ok=True)
            cache_key = (path, unique_key)
            seen_values = self._seen_unique_values.get(cache_key)
            if seen_values is None:
                seen_values = self._load_unique_values_unlocked(path, unique_key)
                self._seen_unique_values[cache_key] = seen_values
            if unique_value in seen_values:
                return False
            self._append_serialized_unlocked(path, serialized)
            seen_values.add(unique_value)
            _refresh_session_dir(path.parent)
            return True

    def _load_unique_values_unlocked(self, path: Path, unique_key: str) -> set[str]:
        """Load existing de-duplication values while holding the lock."""
        if not path.exists():
            return set()
        values: set[str] = set()
        with path.open("r", encoding=self._config.encoding) as transcript_file:
            for line in transcript_file:
                if not line.strip():
                    continue
                parsed = json.loads(line)
                if isinstance(parsed, dict) and isinstance(parsed.get(unique_key), str):
                    values.add(parsed[unique_key])
        return values

    async def read_all(self, session_id: str) -> list[dict[str, Any]]:
        """Read all transcript records for a session in write order."""
        path = self._paths.transcript_path(session_id)
        return await asyncio.to_thread(self._read_all_sync, path)

    def _read_all_sync(self, path: Path) -> list[dict[str, Any]]:
        """Parse a consistent transcript snapshot under the file lock."""
        with self._write_lock:
            expired = _expire_session_dir(path.parent, self._config)
            if expired and self._config.session_ttl_delete_transcripts:
                return []
            if not path.exists():
                return []
            _refresh_session_dir(path.parent)
            records: list[dict[str, Any]] = []
            with path.open("r", encoding=self._config.encoding) as transcript_file:
                for line_number, line in enumerate(transcript_file, start=1):
                    if not line.strip():
                        continue
                    parsed = json.loads(line)
                    if not isinstance(parsed, dict):
                        raise ValueError(f"Transcript line {line_number} is not a JSON object")
                    records.append(parsed)
            return records


class LocalAdvancedMemoryCleanup:
    """Periodically remove expired local Advanced Memory data."""

    def __init__(self, config: AdvancedMemoryConfig) -> None:
        self._config = config
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        if self._config.memory_ttl_seconds is None and self._config.session_ttl_seconds is None:
            return
        self._stop_event = asyncio.Event()
        await self.cleanup_once()
        self._task = asyncio.create_task(self._run())

    async def cleanup_once(self) -> None:
        await asyncio.to_thread(self._cleanup_sync)

    def _cleanup_sync(self) -> None:
        root = self._config.root_dir
        memory_dirs = [root / self._config.memory_dir_name]
        session_roots = [root / self._config.session_dir_name]
        tenants_root = root / "tenants"
        if tenants_root.exists():
            for app_dir in tenants_root.iterdir():
                if app_dir.is_dir():
                    for user_dir in app_dir.iterdir():
                        if user_dir.is_dir():
                            memory_dirs.append(user_dir / self._config.memory_dir_name)
                            session_roots.append(user_dir / self._config.session_dir_name)
        for memory_dir in memory_dirs:
            _expire_memory_dir(memory_dir, self._config)
        for session_root in session_roots:
            if session_root.exists():
                for session_dir in session_root.iterdir():
                    if session_dir.is_dir():
                        _expire_session_dir(session_dir, self._config)

    async def _run(self) -> None:
        if self._stop_event is None:
            return
        ttls = [
            ttl for ttl in (
                self._config.memory_ttl_seconds,
                self._config.session_ttl_seconds,
            ) if ttl is not None
        ]
        interval = min(ttls) if ttls else 60
        try:
            while not self._stop_event.is_set():
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                    break
                except asyncio.TimeoutError:
                    await self.cleanup_once()
        except asyncio.CancelledError:
            raise

    async def close(self) -> None:
        if self._task is not None:
            await self.cleanup_once()
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        self._stop_event = None
