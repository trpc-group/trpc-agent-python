# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Multi-field prompt registry with atomic write_all for AgentOptimizer."""

from __future__ import annotations

import inspect
import os
from pathlib import Path
from typing import Awaitable
from typing import Callable
from typing import Optional

AsyncRead = Callable[[], Awaitable[str]]
AsyncWrite = Callable[[str], Awaitable[None]]


class _RollbackError(RuntimeError):
    """Aggregate error raised when one or more path-field rollbacks fail.

    Carries ``(field_name, error)`` pairs for every field whose rollback
    raised. The original ``write_all`` failure is preserved as
    ``__context__`` (via ``raise ... from primary_err``) so chained
    tracebacks surface both the root cause and every rollback failure.

    Private (underscore-prefixed) — users only observe it through the
    formatted message in tracebacks; never declared in the public API.
    """

    def __init__(self, failures: list[tuple[str, BaseException]]) -> None:
        self.failures = failures
        details = "; ".join(f"{name}: {type(err).__name__}: {err}" for name, err in failures)
        super().__init__(f"TargetPrompt.write_all rollback failed for "
                         f"{len(failures)} field(s): {details}")


class _Source:
    """Base for a single registered prompt source."""


class _PathSource(_Source):
    """File-backed prompt source: read/write a UTF-8 text file at a fixed path."""

    __slots__ = ("path", )

    def __init__(self, path: str) -> None:
        self.path = path


class _CallbackSource(_Source):
    """Callback-backed prompt source: caller-provided async read/write functions."""

    __slots__ = ("read_fn", "write_fn")

    def __init__(self, read_fn: AsyncRead, write_fn: AsyncWrite) -> None:
        self.read_fn = read_fn
        self.write_fn = write_fn


class TargetPrompt:
    """Registry of prompt fields to be optimized by AgentOptimizer.

    Each field is registered with a unique name and one of two source forms:
    - add_path(name, path): file-backed source; framework reads/writes the file
    - add_callback(name, read=, write=): caller-backed source with async functions

    Typical use:
        target = (
            TargetPrompt()
            .add_path("system_prompt", "my_pkg/system.md")
            .add_callback("retriever", read=load_fn, write=save_fn)
        )

    read_all / write_all operate on every registered field. write_all is atomic
    for path-backed fields (tmp file + os.replace, rollback on partial failure);
    callback-backed atomicity is the caller's responsibility.
    """

    def __init__(self) -> None:
        self._sources: dict[str, _Source] = {}

    def add_path(self, name: str, path: str) -> "TargetPrompt":
        """Register a file-backed prompt field. name must be unique."""
        self._reject_duplicate(name)
        self._sources[name] = _PathSource(path)
        return self

    def add_callback(
        self,
        name: str,
        *,
        read: AsyncRead,
        write: AsyncWrite,
    ) -> "TargetPrompt":
        """Register a callback-backed prompt field with async read / write functions."""
        self._reject_duplicate(name)
        if not inspect.iscoroutinefunction(read):
            raise TypeError(f"add_callback {name!r}: read must be an async function")
        if not inspect.iscoroutinefunction(write):
            raise TypeError(f"add_callback {name!r}: write must be an async function")
        self._sources[name] = _CallbackSource(read, write)
        return self

    def names(self) -> list[str]:
        """Return registered field names in insertion order."""
        return list(self._sources.keys())

    def describe_source(self, name: str) -> str:
        """Human-readable source label for a field.

        Path-backed fields return the file path verbatim; callback-backed
        fields return the literal ``"<callback>"``. Raises KeyError if name
        is unknown. Used by the optimizer reporter header.
        """
        src = self._sources[name]
        if isinstance(src, _PathSource):
            return src.path
        return "<callback>"

    async def read(self, name: str) -> str:
        """Read the value of a single registered field. Raises KeyError if name is unknown."""
        src = self._sources[name]
        return await self._read_one(src)

    async def read_all(self) -> dict[str, str]:
        """Read every registered field. Propagates underlying errors (FileNotFoundError / callback exceptions)."""
        out: dict[str, str] = {}
        for name, src in self._sources.items():
            out[name] = await self._read_one(src)
        return out

    async def write_all(self, prompts: dict[str, str]) -> None:
        """Atomically write all registered fields. Keys must exactly match registered names.

        Atomicity contract:
        - Path fields: write to {path}.tmp, then os.replace (single-file POSIX-atomic rename).
        - On any path write failure: already-renamed paths are rolled back to pre-call content,
          residual .tmp files are removed, and the original exception propagates. Rollback uses
          the same tmp + os.replace primitive, so an interrupted rollback cannot leave a path
          field half-written.
        - If rollback of any field also fails, the original exception is preserved on
          ``__context__`` and a single ``_RollbackError`` listing every per-field rollback
          failure propagates. Rollback is best-effort: a failure on one field does not skip
          the remaining fields.
        - Callback fields: invoked sequentially after every path write succeeds. A callback
          failure rolls back path fields to the pre-call snapshot before propagating; callback
          fields themselves are not rolled back (caller-owned idempotency).
        """
        if set(prompts.keys()) != set(self._sources.keys()):
            raise ValueError(f"TargetPrompt.write_all: prompts keys mismatch; "
                             f"expected {sorted(self._sources.keys())}, got {sorted(prompts.keys())}")

        path_backups = self._snapshot_path_contents()
        written_paths: list[str] = []
        try:
            for name, src in self._sources.items():
                if isinstance(src, _PathSource):
                    self._atomic_write_path(src.path, prompts[name])
                    written_paths.append(name)
            for name, src in self._sources.items():
                if isinstance(src, _CallbackSource):
                    await src.write_fn(prompts[name])
        except BaseException as primary_err:
            rollback_failures = self._rollback_paths(written_paths, path_backups)
            self._cleanup_tmp_files()
            if rollback_failures:
                raise _RollbackError(rollback_failures) from primary_err
            raise

    def _reject_duplicate(self, name: str) -> None:
        if name in self._sources:
            raise ValueError(f"TargetPrompt: name {name!r} already registered")

    def _snapshot_path_contents(self) -> dict[str, Optional[str]]:
        """Capture pre-call content of every path-backed field (None if source did not exist)."""
        snapshot: dict[str, Optional[str]] = {}
        for name, src in self._sources.items():
            if isinstance(src, _PathSource):
                try:
                    snapshot[name] = self._read_path(src.path)
                except FileNotFoundError:
                    snapshot[name] = None
        return snapshot

    def _rollback_paths(
        self,
        written: list[str],
        backups: dict[str, Optional[str]],
    ) -> list[tuple[str, BaseException]]:
        """Best-effort atomic rollback of every successfully written path field.

        For each field in ``written`` whose source did not exist before
        write_all (``backups[name] is None``) the file is unlinked; for
        fields that had pre-call content the content is restored via
        ``_atomic_write_path`` (tmp + os.replace), so an interrupted
        rollback cannot leave a path field half-written.

        Failures are collected and returned rather than raised, so a
        single field's failure does not skip subsequent fields. The
        caller wraps the collected failures into ``_RollbackError``.
        """
        failures: list[tuple[str, BaseException]] = []
        for name in written:
            src = self._sources[name]
            if not isinstance(src, _PathSource):
                continue
            backup = backups.get(name)
            try:
                if backup is None:
                    try:
                        os.unlink(src.path)
                    except FileNotFoundError:
                        pass
                else:
                    self._atomic_write_path(src.path, backup)
            except BaseException as err:
                failures.append((name, err))
        return failures

    def _cleanup_tmp_files(self) -> None:
        for src in self._sources.values():
            if isinstance(src, _PathSource):
                tmp = src.path + ".tmp"
                try:
                    os.unlink(tmp)
                except FileNotFoundError:
                    pass

    @staticmethod
    def _atomic_write_path(path: str, content: str) -> None:
        tmp = path + ".tmp"
        with Path(tmp).open("w", encoding="utf-8", newline="") as prompt_file:
            prompt_file.write(content)
        os.replace(tmp, path)

    @staticmethod
    def _read_path(path: str) -> str:
        with Path(path).open("r", encoding="utf-8", newline="") as prompt_file:
            return prompt_file.read()

    async def _read_one(self, src: _Source) -> str:
        if isinstance(src, _PathSource):
            return self._read_path(src.path)
        if isinstance(src, _CallbackSource):
            return await src.read_fn()
        raise TypeError(f"unknown source type: {type(src).__name__}")
