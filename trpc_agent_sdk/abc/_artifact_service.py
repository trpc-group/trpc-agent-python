# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
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
"""Base artifact service interface and implementations."""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from typing import Dict
from typing import Optional

from google.genai.types import Part
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import alias_generators


class ArtifactVersion(BaseModel):
    """Metadata describing a specific version of an artifact."""

    model_config = ConfigDict(
        alias_generator=alias_generators.to_camel,
        populate_by_name=True,
    )

    version: int = Field(description=("Monotonically increasing identifier for the artifact version."))
    canonical_uri: str = Field(description="Canonical URI referencing the persisted artifact payload.")
    custom_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional user-supplied metadata stored with the artifact.",
    )
    create_time: float = Field(
        default_factory=lambda: datetime.now().timestamp(),
        description=("Unix timestamp (seconds) when the version record was created."),
    )
    mime_type: Optional[str] = Field(
        default=None,
        description=("MIME type when the artifact payload is stored as binary data."),
    )


class ArtifactId(BaseModel):
    """Identifier for an artifact."""

    model_config = ConfigDict(
        alias_generator=alias_generators.to_camel,
        populate_by_name=True,
    )
    app_name: str = Field(description="The name of the application.")
    user_id: str = Field(description="The ID of the user.")
    session_id: Optional[str] = Field(default=None,
                                      description="The ID of the session. If None, the artifact is user-scoped.")
    filename: str = Field(default="", description="The filename of the artifact.")


@dataclass
class ArtifactEntry:
    """Represents a single version of an artifact stored in memory.

    Attributes:
      data: The actual data of the artifact.
      artifact_version: Metadata about this specific version of the artifact.
    """

    data: Part
    version: ArtifactVersion


class ArtifactServiceABC(ABC):
    """Abstract base class for artifact management services."""

    @abstractmethod
    async def save_artifact(self, artifact_id: ArtifactId, artifact: Part, metadata: Dict[str, Any]) -> int:
        """Save an artifact and return its version.

        Args:
            artifact_id: The identifier for the artifact.
            artifact: The artifact to save.
            metadata: The metadata for the artifact.

        Returns:
            The version of the artifact.
        """
        pass

    @abstractmethod
    async def load_artifact(
        self,
        *,
        artifact_id: ArtifactId,
        version: Optional[int] = None,
    ) -> Optional[ArtifactEntry]:
        """Gets an artifact from the artifact service storage.

        The artifact is a file identified by the app name, user ID, session ID, and
        filename.

        Args:
          artifact_id: The identifier for the artifact.
          version: The version of the artifact. If None, the latest version will be
            returned.

        Returns:
          The artifact or None if not found.
        """

    @abstractmethod
    async def list_artifact_keys(self, *, artifact_id: ArtifactId) -> list[str]:
        """Lists all the versions of an artifact.

        Args:
            artifact_id: The identifier for the artifact.

        Returns:
            A list of all versions of the artifact.
        """

    @abstractmethod
    async def delete_artifact(
        self,
        *,
        artifact_id: ArtifactId,
    ) -> None:
        """Deletes an artifact.

        Args:
            artifact_id: The identifier for the artifact.
        """

    @abstractmethod
    async def list_versions(
        self,
        *,
        artifact_id: ArtifactId,
    ) -> list[int]:
        """Lists all versions of an artifact.

        Args:
            app_name: The name of the application.
            user_id: The ID of the user.
            filename: The name of the artifact file.
            session_id: The ID of the session. If `None`, only list the user-scoped
              artifacts versions.

        Returns:
            A list of all available versions of the artifact.
        """

    @abstractmethod
    async def list_artifact_versions(
        self,
        *,
        artifact_id: ArtifactId,
    ) -> list[ArtifactVersion]:
        """Lists all versions and their metadata for a specific artifact.

        Args:
          artifact_id: The identifier for the artifact.

        Returns:
          A list of ArtifactVersion objects, each representing a version of the
          artifact and its associated metadata.
        """

    @abstractmethod
    async def get_artifact_version(self,
                                   artifact_id: ArtifactId,
                                   version: Optional[int] = None) -> Optional[ArtifactVersion]:
        """Retrieve a specific version of an artifact.

        Args:
            artifact_id: The identifier for the artifact.
            version: The version of the artifact. If None, the latest version will be
              returned.

        Returns:
            The artifact version or None if not found.
        """
