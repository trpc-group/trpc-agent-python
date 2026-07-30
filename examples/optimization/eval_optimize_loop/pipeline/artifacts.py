"""Immutable audit directory, recursive sanitization and content manifest."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .models import ArtifactRecord
from .schema import parse_strict_json, sanitize, sanitized_text, validate_safe_component

_REPORT_FILES = {"optimization_report.json", "optimization_report.md"}
_TEXT_ARTIFACT_SUFFIXES = {".log", ".md", ".txt"}
_ATOMIC_REPLACE_LOCK = threading.Lock()
_ATOMIC_REPLACE_ATTEMPTS = 8


def load_strict_json(path: str | Path) -> dict[str, Any]:
    return parse_strict_json(Path(path).read_text(encoding="utf-8"))


class AuditSink:
    """Own safe paths and atomic persistence beneath one immutable run directory."""

    def __init__(
        self,
        artifact_root: str | Path,
        run_id: str,
        *,
        max_text_chars: int,
        publication_root: str | Path | None = None,
        max_import_files: int = 256,
        max_import_file_bytes: int = 5 * 1024 * 1024,
        max_import_total_bytes: int = 25 * 1024 * 1024,
    ) -> None:
        validate_safe_component(run_id, name="run ID")
        self.root = Path(artifact_root).resolve()
        self.publication_root = Path(publication_root).resolve() if publication_root is not None else self.root.parent
        self.run_id = run_id
        self.run_dir = self.root / run_id
        self.max_text_chars = max_text_chars
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

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
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
        clean = sanitize(payload, max_text_chars=self.max_text_chars)
        content = json.dumps(clean, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
        self._atomic_write(path, content)
        return path

    def write_text(self, relative_path: str, content: str) -> Path:
        path = self._resolve(relative_path)
        clean = sanitized_text(content, max_text_chars=self.max_text_chars)
        self._atomic_write(path, clean)
        return path

    def write_jsonl(self, relative_path: str, payloads: list[dict[str, Any]]) -> Path:
        path = self._resolve(relative_path)
        lines = [
            json.dumps(
                sanitize(payload, max_text_chars=self.max_text_chars),
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            ) for payload in payloads
        ]
        self._atomic_write(path, "\n".join(lines) + ("\n" if lines else ""))
        return path

    def import_tree(self, source: str | Path, destination: str) -> None:
        """Import known optimizer artifacts through the shared sanitization boundary."""

        source_root = Path(source).resolve()
        if not source_root.exists():
            return
        paths = sorted(item for item in source_root.rglob("*") if item.is_file())
        if len(paths) > self.max_import_files:
            raise ValueError(f"optimizer artifact count exceeds limit: {len(paths)} > {self.max_import_files}")
        total_bytes = 0
        for path in paths:
            if path.is_symlink():
                raise ValueError("optimizer artifact symlinks are not allowed")
            byte_size = path.stat().st_size
            if byte_size > self.max_import_file_bytes:
                raise ValueError(f"optimizer artifact exceeds per-file byte limit: "
                                 f"{path.name} ({byte_size} > {self.max_import_file_bytes})")
            total_bytes += byte_size
            if total_bytes > self.max_import_total_bytes:
                raise ValueError(f"optimizer artifacts exceed total byte limit: "
                                 f"{total_bytes} > {self.max_import_total_bytes}")
            relative = path.relative_to(source_root)
            target = (Path(destination) / relative).as_posix()
            suffix = path.suffix.casefold()
            if suffix == ".json":
                self.write_json(target, parse_strict_json(path.read_text(encoding="utf-8")))
            elif suffix == ".jsonl":
                payloads = [
                    parse_strict_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
                ]
                self.write_jsonl(target, payloads)
            elif suffix in _TEXT_ARTIFACT_SUFFIXES:
                self.write_text(target, path.read_text(encoding="utf-8"))
            else:
                raise ValueError(f"unsupported optimizer artifact type: {relative.as_posix()}")

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
