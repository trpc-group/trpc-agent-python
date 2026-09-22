# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Filesystem-backed artifact storage."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote
from urllib.parse import unquote
from typing import Any
from typing import Optional
from typing_extensions import override

from google.genai.types import Part

from trpc_agent_sdk.abc import ArtifactEntry
from trpc_agent_sdk.abc import ArtifactId
from trpc_agent_sdk.abc import ArtifactServiceABC
from trpc_agent_sdk.abc import ArtifactVersion

from ._utils import file_has_user_namespace
from ._utils import get_artifact_uri
from ._utils import is_artifact_ref
from ._utils import parse_artifact_uri


class FileArtifactService(ArtifactServiceABC):
    """Persist versioned artifacts below a local filesystem directory.

    Each artifact version is stored in one atomically replaced JSON file.
    Namespace components are percent-encoded and validated before use so an
    artifact identifier cannot escape ``root_dir``.
    """

    _RECORD_SUFFIX = ".json"
    _RESERVATION_SUFFIX = ".pending"
    _SCHEMA_VERSION = 1

    def __init__(self, root_dir: os.PathLike[str] | str) -> None:
        """Initialize the service and create the storage root if necessary."""
        root = Path(root_dir).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink() or not root.is_dir():
            raise ValueError("root_dir must be a real directory")
        self._root_dir = root.resolve()

    @property
    def root_dir(self) -> Path:
        """Return the resolved storage root."""
        return self._root_dir

    @override
    async def save_artifact(
        self,
        *,
        artifact_id: ArtifactId,
        artifact: Part,
        metadata: Optional[dict[str, Any]] = None,
    ) -> int:
        """Save an artifact as a new version and return its version number."""
        return await asyncio.to_thread(self._save_artifact, artifact_id, artifact, metadata)

    @override
    async def load_artifact(
        self,
        *,
        artifact_id: ArtifactId,
        version: Optional[int] = None,
    ) -> Optional[ArtifactEntry]:
        """Load a specific version, or the latest version when omitted."""
        return await asyncio.to_thread(self._load_artifact, artifact_id, version, set())

    @override
    async def list_artifact_keys(self, *, artifact_id: ArtifactId) -> list[str]:
        """List session artifacts plus user-scoped artifacts visible to a user."""
        return await asyncio.to_thread(self._list_artifact_keys, artifact_id)

    @override
    async def delete_artifact(self, *, artifact_id: ArtifactId) -> None:
        """Delete every stored version of an artifact."""
        await asyncio.to_thread(self._delete_artifact, artifact_id)

    @override
    async def list_versions(self, *, artifact_id: ArtifactId) -> list[int]:
        """List all complete versions in ascending order."""
        return await asyncio.to_thread(self._list_versions, artifact_id)

    @override
    async def list_artifact_versions(self, *, artifact_id: ArtifactId) -> list[ArtifactVersion]:
        """List metadata for all complete versions in ascending order."""
        return await asyncio.to_thread(self._list_artifact_versions, artifact_id)

    @override
    async def get_artifact_version(
        self,
        *,
        artifact_id: ArtifactId,
        version: Optional[int] = None,
    ) -> Optional[ArtifactVersion]:
        """Return metadata for a specific or latest artifact version."""
        return await asyncio.to_thread(self._get_artifact_version, artifact_id, version)

    def _save_artifact(
        self,
        artifact_id: ArtifactId,
        artifact: Part,
        metadata: Optional[dict[str, Any]],
    ) -> int:
        mime_type = self._artifact_mime_type(artifact)
        versions_dir = self._versions_directory(artifact_id)
        versions_dir.mkdir(parents=True, exist_ok=True)
        self._assert_safe_path(versions_dir)

        version, reservation = self._reserve_version(versions_dir)
        artifact_version = ArtifactVersion(
            canonical_uri=get_artifact_uri(artifact_id, version),
            custom_metadata=metadata or {},
            mime_type=mime_type,
            version=version,
        )
        record = {
            "artifact": artifact.model_dump(
                by_alias=True,
                exclude_none=True,
                mode="json",
            ),
            "artifact_version": artifact_version.model_dump(
                by_alias=True,
                mode="json",
            ),
            "schema_version": self._SCHEMA_VERSION,
        }
        destination = versions_dir / f"{version}{self._RECORD_SUFFIX}"
        temporary_path: Optional[Path] = None
        try:
            file_descriptor, temporary_name = tempfile.mkstemp(
                dir=versions_dir,
                prefix=f".{version}.",
                suffix=".tmp",
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as file:
                json.dump(record, file, ensure_ascii=False, separators=(",", ":"))
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_path, destination)
            self._fsync_directory(versions_dir)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            reservation.unlink(missing_ok=True)
        return version

    def _load_artifact(
        self,
        artifact_id: ArtifactId,
        version: Optional[int],
        visited: set[str],
    ) -> Optional[ArtifactEntry]:
        record = self._read_record(artifact_id, version)
        if record is None:
            return None
        artifact_data = Part.model_validate(record["artifact"])
        artifact_version = ArtifactVersion.model_validate(record["artifact_version"])

        if is_artifact_ref(artifact_data):
            uri = artifact_data.file_data.file_uri
            parsed_uri = parse_artifact_uri(uri)
            if parsed_uri is None:
                raise ValueError(f"Invalid artifact reference URI: {uri}")
            if uri in visited:
                raise ValueError(f"Cyclic artifact reference: {uri}")
            visited.add(uri)
            return self._load_artifact(
                ArtifactId(
                    app_name=parsed_uri.app_name,
                    filename=parsed_uri.filename,
                    session_id=parsed_uri.session_id,
                    user_id=parsed_uri.user_id,
                ),
                parsed_uri.version,
                visited,
            )

        is_empty_part = artifact_data == Part()
        is_empty_text_part = artifact_data == Part(text="")
        has_empty_inline_data = bool(artifact_data.inline_data and not artifact_data.inline_data.data)
        if is_empty_part or is_empty_text_part or has_empty_inline_data:
            return None
        if artifact_version.mime_type is None:
            artifact_version.mime_type = "application/octet-stream"
        return ArtifactEntry(data=artifact_data, version=artifact_version)

    def _list_artifact_keys(self, artifact_id: ArtifactId) -> list[str]:
        self._validate_component("app_name", artifact_id.app_name)
        self._validate_component("user_id", artifact_id.user_id)
        keys = set(self._keys_in_scope(self._user_artifacts_directory(artifact_id)))
        if artifact_id.session_id:
            self._validate_component("session_id", artifact_id.session_id)
            keys.update(self._keys_in_scope(self._session_artifacts_directory(artifact_id)))
        return sorted(keys)

    def _delete_artifact(self, artifact_id: ArtifactId) -> None:
        artifact_dir = self._artifact_directory(artifact_id)
        if artifact_dir.exists():
            self._assert_safe_path(artifact_dir)
            if artifact_dir.is_symlink():
                raise ValueError("Artifact storage path cannot contain symbolic links")
            shutil.rmtree(artifact_dir)

    def _list_versions(self, artifact_id: ArtifactId) -> list[int]:
        versions_dir = self._versions_directory(artifact_id)
        if not versions_dir.is_dir():
            return []
        versions = []
        for path in versions_dir.iterdir():
            if path.is_file() and path.name.endswith(self._RECORD_SUFFIX):
                stem = path.name[:-len(self._RECORD_SUFFIX)]
                if stem.isdigit():
                    versions.append(int(stem))
        return sorted(versions)

    def _list_artifact_versions(self, artifact_id: ArtifactId) -> list[ArtifactVersion]:
        versions = []
        for version in self._list_versions(artifact_id):
            artifact_version = self._get_artifact_version(artifact_id, version)
            if artifact_version is not None:
                versions.append(artifact_version)
        return versions

    def _get_artifact_version(
        self,
        artifact_id: ArtifactId,
        version: Optional[int],
    ) -> Optional[ArtifactVersion]:
        record = self._read_record(artifact_id, version)
        if record is None:
            return None
        return ArtifactVersion.model_validate(record["artifact_version"])

    def _read_record(
        self,
        artifact_id: ArtifactId,
        version: Optional[int],
    ) -> Optional[dict[str, Any]]:
        versions_dir = self._versions_directory(artifact_id)
        if version is None:
            versions = self._list_versions(artifact_id)
            if not versions:
                return None
            version = versions[-1]
        if version < 0:
            return None
        path = versions_dir / f"{version}{self._RECORD_SUFFIX}"
        self._assert_safe_path(path)
        try:
            with path.open(encoding="utf-8") as file:
                record = json.load(file)
        except FileNotFoundError:
            return None
        if record.get("schema_version") != self._SCHEMA_VERSION:
            raise ValueError(f"Unsupported artifact record schema in {path}")
        return record

    def _reserve_version(self, versions_dir: Path) -> tuple[int, Path]:
        candidates = []
        for path in versions_dir.iterdir():
            for suffix in (self._RECORD_SUFFIX, self._RESERVATION_SUFFIX):
                if path.name.endswith(suffix):
                    stem = path.name[:-len(suffix)]
                    if stem.isdigit():
                        candidates.append(int(stem))
                    break
        version = max(candidates, default=-1) + 1
        while True:
            reservation = versions_dir / f"{version}{self._RESERVATION_SUFFIX}"
            record = versions_dir / f"{version}{self._RECORD_SUFFIX}"
            if record.exists():
                version += 1
                continue
            try:
                file_descriptor = os.open(
                    reservation,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                version += 1
                continue
            os.close(file_descriptor)
            if record.exists():
                reservation.unlink(missing_ok=True)
                version += 1
                continue
            return version, reservation

    def _artifact_mime_type(self, artifact: Part) -> Optional[str]:
        if artifact.inline_data is not None:
            return artifact.inline_data.mime_type
        if artifact.text is not None:
            return "text/plain"
        if artifact.file_data is not None:
            if is_artifact_ref(artifact):
                uri = artifact.file_data.file_uri
                if parse_artifact_uri(uri) is None:
                    raise ValueError(f"Invalid artifact reference URI: {uri}")
                return None
            return artifact.file_data.mime_type
        raise ValueError("Not supported artifact type.")

    def _artifact_directory(self, artifact_id: ArtifactId) -> Path:
        self._validate_artifact_id(artifact_id)
        if file_has_user_namespace(artifact_id.filename):
            parent = self._user_artifacts_directory(artifact_id)
        else:
            parent = self._session_artifacts_directory(artifact_id)
        return self._safe_path(parent, self._encode(artifact_id.filename))

    def _versions_directory(self, artifact_id: ArtifactId) -> Path:
        return self._safe_path(self._artifact_directory(artifact_id), "versions")

    def _user_artifacts_directory(self, artifact_id: ArtifactId) -> Path:
        return self._safe_path(
            self._root_dir,
            "apps",
            self._encode(artifact_id.app_name),
            "users",
            self._encode(artifact_id.user_id),
            "user",
            "artifacts",
        )

    def _session_artifacts_directory(self, artifact_id: ArtifactId) -> Path:
        session_id = artifact_id.session_id if artifact_id.session_id is not None else "__none__"
        return self._safe_path(
            self._root_dir,
            "apps",
            self._encode(artifact_id.app_name),
            "users",
            self._encode(artifact_id.user_id),
            "sessions",
            self._encode(session_id),
            "artifacts",
        )

    def _keys_in_scope(self, artifacts_dir: Path) -> list[str]:
        if not artifacts_dir.is_dir():
            return []
        keys = []
        for artifact_dir in artifacts_dir.iterdir():
            self._assert_safe_path(artifact_dir)
            if artifact_dir.is_dir() and self._list_record_files(artifact_dir / "versions"):
                keys.append(unquote(artifact_dir.name))
        return keys

    def _list_record_files(self, versions_dir: Path) -> list[Path]:
        if not versions_dir.is_dir():
            return []
        return [path for path in versions_dir.iterdir() if path.is_file() and path.name.endswith(self._RECORD_SUFFIX)]

    def _validate_artifact_id(self, artifact_id: ArtifactId) -> None:
        self._validate_component("app_name", artifact_id.app_name)
        self._validate_component("user_id", artifact_id.user_id)
        self._validate_component("filename", artifact_id.filename)
        if not file_has_user_namespace(artifact_id.filename) and artifact_id.session_id is not None:
            self._validate_component("session_id", artifact_id.session_id)

    @staticmethod
    def _validate_component(name: str, value: str) -> None:
        if not value or value in {".", ".."}:
            raise ValueError(f"{name} must be a non-empty path component")
        if "\x00" in value or "/" in value or "\\" in value:
            raise ValueError(f"{name} cannot contain path separators")

    @staticmethod
    def _encode(value: str) -> str:
        return quote(value, safe="")

    def _safe_path(self, base: Path, *components: str) -> Path:
        path = base.joinpath(*components)
        self._assert_safe_path(path)
        return path

    def _assert_safe_path(self, path: Path) -> None:
        try:
            relative_path = path.relative_to(self._root_dir)
            path.resolve(strict=False).relative_to(self._root_dir)
        except ValueError as error:
            raise ValueError("Artifact storage path escapes root_dir") from error
        current_path = self._root_dir
        for component in relative_path.parts:
            current_path /= component
            if current_path.is_symlink():
                raise ValueError("Artifact storage path cannot contain symbolic links")

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        try:
            file_descriptor = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(file_descriptor)
        finally:
            os.close(file_descriptor)
