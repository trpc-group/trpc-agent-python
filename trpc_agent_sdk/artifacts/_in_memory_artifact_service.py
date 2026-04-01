# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
#
# Directly reuse the types from adk-python
# Below code are copy and modified from https://github.com/google/adk-python.git
#
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""An in-memory implementation of the artifact service."""
from typing import Any
from typing import Optional
from typing_extensions import override

from pydantic import BaseModel
from pydantic import Field

from trpc_agent_sdk.abc import ArtifactEntry
from trpc_agent_sdk.abc import ArtifactId
from trpc_agent_sdk.abc import ArtifactServiceABC
from trpc_agent_sdk.abc import ArtifactVersion
from trpc_agent_sdk.types import Part

from ._utils import artifact_path
from ._utils import create_artifact_uri
from ._utils import is_artifact_ref
from ._utils import parse_artifact_uri


class InMemoryArtifactService(ArtifactServiceABC, BaseModel):
    """An in-memory implementation of the artifact service."""

    artifacts: dict[str, list[ArtifactEntry]] = Field(default_factory=dict)

    @override
    async def save_artifact(
        self,
        *,
        artifact_id: ArtifactId,
        artifact: Part,
        metadata: Optional[dict[str, Any]] = None,
    ) -> int:
        path = artifact_path(artifact_id)
        if path not in self.artifacts:
            self.artifacts[path] = []
        version = len(self.artifacts[path])
        canonical_uri = create_artifact_uri(artifact_id, version)
        artifact_version = ArtifactVersion(
            version=version,
            canonical_uri=canonical_uri,
        )
        if metadata:
            artifact_version.custom_metadata = metadata

        if artifact.inline_data is not None:
            artifact_version.mime_type = artifact.inline_data.mime_type
        elif artifact.text is not None:
            artifact_version.mime_type = "text/plain"
        elif artifact.file_data is not None:
            if is_artifact_ref(artifact):
                if not parse_artifact_uri(artifact.file_data.file_uri):
                    raise ValueError(f"Invalid artifact reference URI: {artifact.file_data.file_uri}")
                # If it's a valid artifact URI, we store the artifact part as-is.
                # And we don't know the mime type until we load it.
            else:
                artifact_version.mime_type = artifact.file_data.mime_type
        else:
            raise ValueError("Not supported artifact type.")

        self.artifacts[path].append(ArtifactEntry(data=artifact, artifact_version=artifact_version))
        return version

    @override
    async def load_artifact(
        self,
        *,
        artifact_id: ArtifactId,
        version: Optional[int] = None,
    ) -> Optional[ArtifactEntry]:
        path = artifact_path(artifact_id)
        versions = self.artifacts.get(path)
        if not versions:
            return None
        if version is None:
            version = -1

        try:
            artifact_entry = versions[version]
        except IndexError:
            return None

        if artifact_entry is None:
            return None

        # Resolve artifact reference if needed.
        artifact_data = artifact_entry.data
        if is_artifact_ref(artifact_data):
            parsed_uri = parse_artifact_uri(artifact_data.file_data.file_uri)
            if not parsed_uri:
                raise ValueError("Invalid artifact reference URI:"
                                 f" {artifact_data.file_data.file_uri}")
            return await self.load_artifact(
                artifact_id=ArtifactId(
                    app_name=parsed_uri.app_name,
                    user_id=parsed_uri.user_id,
                    session_id=parsed_uri.session_id,
                    filename=parsed_uri.filename,
                ),
                version=parsed_uri.version,
            )

        if (artifact_data == Part() or artifact_data == Part(text="")
                or (artifact_data.inline_data and not artifact_data.inline_data.data)):
            return None
        return artifact_entry

    @override
    async def list_artifact_keys(self, *, artifact_id: ArtifactId) -> list[str]:
        user_namespace_prefix = f"{artifact_id.app_name}/{artifact_id.user_id}/user/"
        session_prefix = (f"{artifact_id.app_name}/{artifact_id.user_id}/{artifact_id.session_id}/"
                          if artifact_id.session_id else None)
        filenames = []
        for path in self.artifacts:
            if session_prefix and path.startswith(session_prefix):
                filename = path.removeprefix(session_prefix)
                filenames.append(filename)
            elif path.startswith(user_namespace_prefix):
                filename = path.removeprefix(user_namespace_prefix)
                filenames.append(filename)
        return sorted(filenames)

    @override
    async def delete_artifact(
        self,
        *,
        artifact_id: ArtifactId,
    ) -> None:
        path = artifact_path(artifact_id)
        if not self.artifacts.get(path):
            return None
        self.artifacts.pop(path, None)

    @override
    async def list_versions(
        self,
        *,
        artifact_id: ArtifactId,
    ) -> list[int]:
        path = artifact_path(artifact_id)
        versions = self.artifacts.get(path)
        if not versions:
            return []
        return list(range(len(versions)))

    @override
    async def list_artifact_versions(
        self,
        *,
        artifact_id: ArtifactId,
    ) -> list[ArtifactVersion]:
        path = artifact_path(artifact_id)
        entries = self.artifacts.get(path)
        if not entries:
            return []
        return [entry.artifact_version for entry in entries]

    @override
    async def get_artifact_version(
        self,
        *,
        artifact_id: ArtifactId,
        version: Optional[int] = None,
    ) -> Optional[ArtifactVersion]:
        path = artifact_path(artifact_id)
        entries = self.artifacts.get(path)
        if not entries:
            return None

        if version is None:
            version = -1
        try:
            return entries[version].artifact_version
        except IndexError:
            return None
