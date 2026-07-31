"""Immutable audit directory, recursive sanitization and content manifest."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import ArtifactRecord
from .schema import parse_strict_json, sanitize, validate_safe_component

_REPORT_FILES = {"optimization_report.json", "optimization_report.md"}
_TEXT_ARTIFACT_SUFFIXES = {".log", ".md", ".txt"}
_ATOMIC_REPLACE_LOCK = threading.Lock()
_ATOMIC_REPLACE_ATTEMPTS = 8


class AuditPersistenceError(RuntimeError):
    """Raised when a terminal audit report cannot be durably persisted."""


def load_strict_json(path: str | Path) -> dict[str, Any]:
    return parse_strict_json(Path(path).read_text(encoding="utf-8"))


@dataclass(frozen=True)
class _ArtifactSnapshot:
    relative_path: str
    content: str


_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_NO_FOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _stat_is_link_or_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def _path_is_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction()) if callable(is_junction) else False


def _require_safe_root(path: Path) -> os.stat_result:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise FileNotFoundError(path) from None
    if _stat_is_link_or_reparse(info) or _path_is_junction(path):
        raise ValueError("optimizer artifact symlinks, hard links and reparse points are not allowed")
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("optimizer artifact source must be a directory")
    return info


def _same_file_identity(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
    ) == (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
    )


class AuditSink:
    """Own safe paths and atomic persistence beneath one immutable run directory."""

    def __init__(
        self,
        artifact_root: str | Path,
        run_id: str,
        *,
        publication_root: str | Path | None = None,
        max_file_bytes: int = 25 * 1024 * 1024,
        max_import_files: int = 256,
        max_import_file_bytes: int = 5 * 1024 * 1024,
        max_import_total_bytes: int = 25 * 1024 * 1024,
    ) -> None:
        validate_safe_component(run_id, name="run ID")
        self.root = Path(artifact_root).resolve()
        self.publication_root = Path(publication_root).resolve() if publication_root is not None else self.root.parent
        self.run_id = run_id
        self.run_dir = self.root / run_id
        if min(max_file_bytes, max_import_files, max_import_file_bytes, max_import_total_bytes) < 1:
            raise ValueError("audit and import limits must be positive")
        if max_import_total_bytes < max_import_file_bytes:
            raise ValueError("total import byte limit must be at least the per-file limit")
        self.max_file_bytes = max_file_bytes
        self.max_import_files = max_import_files
        self.max_import_file_bytes = max_import_file_bytes
        self.max_import_total_bytes = max_import_total_bytes

    def create(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        self.run_dir.mkdir(exist_ok=False)
        return self.run_dir

    def phase_dir(self, name: str) -> Path:
        validate_safe_component(name, name="stage name")
        path = self.run_dir / name
        path.mkdir(exist_ok=False)
        return path

    def _resolve(self, relative_path: str) -> Path:
        raw = Path(relative_path)
        if raw.is_absolute() or not raw.parts:
            raise ValueError("artifact path must be relative")
        for part in raw.parts:
            validate_safe_component(part, name="artifact path component")
        resolved = (self.run_dir / raw).resolve()
        if self.run_dir not in resolved.parents:
            raise ValueError("artifact path escapes the run directory")
        return resolved

    def _atomic_write(self, path: Path, content: str) -> None:
        byte_size = len(content.encode("utf-8"))
        if byte_size > self.max_file_bytes:
            raise ValueError(f"audit file exceeds byte limit: {path.name} ({byte_size} > {self.max_file_bytes})")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(content, encoding="utf-8", newline="\n")
            with _ATOMIC_REPLACE_LOCK:
                for attempt in range(_ATOMIC_REPLACE_ATTEMPTS):
                    try:
                        os.replace(temporary, path)
                        break
                    except PermissionError:
                        if attempt + 1 == _ATOMIC_REPLACE_ATTEMPTS:
                            raise
                        time.sleep(0.005 * (2**attempt))
        finally:
            temporary.unlink(missing_ok=True)

    def write_json(self, relative_path: str, payload: Any) -> Path:
        path = self._resolve(relative_path)
        clean = sanitize(payload, max_text_chars=None)
        content = json.dumps(clean, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
        self._atomic_write(path, content)
        return path

    def write_text(self, relative_path: str, content: str) -> Path:
        path = self._resolve(relative_path)
        clean = sanitize(content, max_text_chars=None)
        if not isinstance(clean, str):
            raise TypeError("text audit content must remain text after sanitization")
        self._atomic_write(path, clean)
        return path

    def write_jsonl(self, relative_path: str, payloads: list[dict[str, Any]]) -> Path:
        path = self._resolve(relative_path)
        lines = [
            json.dumps(
                sanitize(payload, max_text_chars=None),
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            ) for payload in payloads
        ]
        self._atomic_write(path, "\n".join(lines) + ("\n" if lines else ""))
        return path

    def _read_verified_file(self, path: Path, before: os.stat_result) -> bytes:
        if before.st_nlink != 1:
            raise ValueError(f"optimizer artifact hard links are not allowed: {path.name}")
        flags = os.O_RDONLY | _NO_FOLLOW
        try:
            descriptor = os.open(path, flags)
        except OSError as error:
            raise ValueError(f"optimizer artifact could not be opened safely: {path.name}") from error
        try:
            opened = os.fstat(descriptor)
            if _stat_is_link_or_reparse(opened) or not stat.S_ISREG(opened.st_mode):
                raise ValueError(f"optimizer artifact is not a regular file: {path.name}")
            if opened.st_nlink != 1 or not _same_file_identity(before, opened):
                raise ValueError(f"optimizer artifact changed during validation: {path.name}")
            chunks: list[bytes] = []
            remaining = self.max_import_file_bytes + 1
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            after = os.fstat(descriptor)
            if not _same_file_identity(opened, after):
                raise ValueError(f"optimizer artifact changed while reading: {path.name}")
            content = b"".join(chunks)
            if len(content) > self.max_import_file_bytes:
                raise ValueError(f"optimizer artifact exceeds per-file byte limit: "
                                 f"{path.name} ({len(content)} > {self.max_import_file_bytes})")
            return content
        finally:
            os.close(descriptor)

    @staticmethod
    def _render_json(payload: Any) -> str:
        return json.dumps(
            sanitize(payload, max_text_chars=None),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ) + "\n"

    @staticmethod
    def _render_jsonl(payload: Any) -> str:
        return json.dumps(
            sanitize(payload, max_text_chars=None),
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )

    def _snapshot_file(self, path: Path, relative_path: str, before: os.stat_result) -> _ArtifactSnapshot:
        suffix = path.suffix.casefold()
        if suffix not in {".json", ".jsonl", *_TEXT_ARTIFACT_SUFFIXES}:
            raise ValueError(f"unsupported optimizer artifact type: {relative_path}")
        if before.st_size > self.max_import_file_bytes:
            raise ValueError(f"optimizer artifact exceeds per-file byte limit: "
                             f"{relative_path} ({before.st_size} > {self.max_import_file_bytes})")
        raw = self._read_verified_file(path, before)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(f"optimizer text artifact is not UTF-8: {relative_path}") from error
        if suffix == ".json":
            content = self._render_json(parse_strict_json(text))
        elif suffix == ".jsonl":
            payloads = [parse_strict_json(line) for line in text.splitlines() if line.strip()]
            content = "\n".join(self._render_jsonl(payload) for payload in payloads)
            content += "\n" if payloads else ""
        else:
            clean = sanitize(text, max_text_chars=None)
            if not isinstance(clean, str):
                raise TypeError("text audit content must remain text after sanitization")
            content = clean
        if len(content.encode("utf-8")) > self.max_file_bytes:
            raise ValueError(f"audit file exceeds byte limit: {relative_path}")
        return _ArtifactSnapshot(relative_path=relative_path, content=content)

    def _snapshot_tree(self, source: str | Path) -> tuple[_ArtifactSnapshot, ...]:
        source_root = Path(source)
        if _path_is_junction(source_root):
            raise ValueError("optimizer artifact symlinks, hard links and reparse points are not allowed")
        try:
            _require_safe_root(source_root)
        except FileNotFoundError:
            return ()
        pending: list[tuple[Path, str]] = [(source_root, "")]
        snapshots: list[_ArtifactSnapshot] = []
        total_bytes = 0
        while pending:
            directory, prefix = pending.pop()
            try:
                entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
            except OSError as error:
                raise ValueError(f"optimizer artifact directory cannot be read: {directory}") from error
            for entry in entries:
                relative = f"{prefix}/{entry.name}" if prefix else entry.name
                for component in Path(relative).parts:
                    validate_safe_component(component, name="optimizer artifact component")
                try:
                    info = os.lstat(entry.path)
                except OSError as error:
                    raise ValueError(f"optimizer artifact cannot be inspected: {relative}") from error
                if _stat_is_link_or_reparse(info) or _path_is_junction(Path(entry.path)):
                    raise ValueError(f"optimizer artifact symlinks, hard links and reparse points are not allowed: "
                                     f"{relative}")
                if stat.S_ISDIR(info.st_mode):
                    pending.append((Path(entry.path), relative))
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError(f"optimizer artifact must be a regular file: {relative}")
                if len(snapshots) >= self.max_import_files:
                    raise ValueError(f"optimizer artifact count exceeds limit: "
                                     f"{len(snapshots) + 1} > {self.max_import_files}")
                total_bytes += info.st_size
                if total_bytes > self.max_import_total_bytes:
                    raise ValueError(f"optimizer artifacts exceed total byte limit: "
                                     f"{total_bytes} > {self.max_import_total_bytes}")
                snapshots.append(self._snapshot_file(Path(entry.path), relative, info))
        return tuple(snapshots)

    def import_tree(self, source: str | Path, destination: str) -> None:
        """Validate and sanitize the complete optimizer tree before publishing it."""

        snapshots = self._snapshot_tree(source)
        for snapshot in snapshots:
            target = (Path(destination) / snapshot.relative_path).as_posix()
            self._atomic_write(self._resolve(target), snapshot.content)

    def records(
        self,
        *,
        include_manifest: bool = False,
        include_report_files: bool = True,
    ) -> tuple[ArtifactRecord, ...]:
        records: list[ArtifactRecord] = []
        for path in sorted(item for item in self.run_dir.rglob("*") if item.is_file()):
            relative = path.relative_to(self.run_dir).as_posix()
            if not include_manifest and relative == "manifest.json":
                continue
            if not include_report_files and relative in _REPORT_FILES:
                continue
            content = path.read_bytes()
            records.append(
                ArtifactRecord(
                    path=relative,
                    sha256=hashlib.sha256(content).hexdigest(),
                    byte_size=len(content),
                ))
        return tuple(records)

    def write_manifest(self) -> tuple[ArtifactRecord, ...]:
        records = self.records()
        self.write_json(
            "manifest.json",
            {
                "schemaVersion": "v1",
                "files": [record.model_dump(by_alias=True) for record in records]
            },
        )
        return records

    def publish_latest_snapshot(self, source_name: str, destination_name: str) -> Path:
        source = self._resolve(source_name)
        validate_safe_component(destination_name, name="latest report name")
        destination = self.publication_root / destination_name
        self._atomic_write(destination, source.read_text(encoding="utf-8"))
        return destination
