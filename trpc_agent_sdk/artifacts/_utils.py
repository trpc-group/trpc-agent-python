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
"""Utility functions for handling artifact URIs."""

from __future__ import annotations

import re
from typing import NamedTuple
from typing import Optional

from google.genai import types

from trpc_agent_sdk.abc import ArtifactId


class ParsedArtifactUri(NamedTuple):
    """The result of parsing an artifact URI."""

    app_name: str
    user_id: str
    session_id: Optional[str]
    filename: str
    version: int


_SESSION_SCOPED_ARTIFACT_URI_RE = re.compile(
    r"artifact://apps/([^/]+)/users/([^/]+)/sessions/([^/]+)/artifacts/([^/]+)/versions/(\d+)")
_USER_SCOPED_ARTIFACT_URI_RE = re.compile(r"artifact://apps/([^/]+)/users/([^/]+)/artifacts/([^/]+)/versions/(\d+)")


def parse_artifact_uri(uri: str) -> Optional[ParsedArtifactUri]:
    """Parses an artifact URI.

    Args:
        uri: The artifact URI to parse.

    Returns:
        A ParsedArtifactUri if parsing is successful, None otherwise.
    """
    if not uri or not uri.startswith("artifact://"):
        return None

    match = _SESSION_SCOPED_ARTIFACT_URI_RE.match(uri)
    if match:
        return ParsedArtifactUri(
            app_name=match.group(1),
            user_id=match.group(2),
            session_id=match.group(3),
            filename=match.group(4),
            version=int(match.group(5)),
        )

    match = _USER_SCOPED_ARTIFACT_URI_RE.match(uri)
    if match:
        return ParsedArtifactUri(
            app_name=match.group(1),
            user_id=match.group(2),
            session_id=None,
            filename=match.group(3),
            version=int(match.group(4)),
        )

    return None


def get_artifact_uri(artifact_id: ArtifactId, version: int) -> str:
    """Constructs an artifact URI.

    Args:
        app_name: The name of the application.
        user_id: The ID of the user.
        filename: The name of the artifact file.
        version: The version of the artifact.
        session_id: The ID of the session.

    Returns:
        The constructed artifact URI.
    """
    prefix = f"artifact://apps/{artifact_id.app_name}/users/{artifact_id.user_id}"
    if artifact_id.session_id:
        return f"{prefix}/sessions/{artifact_id.session_id}/artifacts/{artifact_id.filename}/versions/{version}"
    return f"{prefix}/artifacts/{artifact_id.filename}/versions/{version}"


def is_artifact_ref(artifact: types.Part) -> bool:
    """Checks if an artifact part is an artifact reference.

    Args:
        artifact: The artifact part to check.

    Returns:
        True if the artifact part is an artifact reference, False otherwise.
    """
    return bool(artifact.file_data and artifact.file_data.file_uri
                and artifact.file_data.file_uri.startswith("artifact://"))


def file_has_user_namespace(filename: str) -> bool:
    """Checks if the filename has a user namespace.

    Args:
        filename: The filename to check.

    Returns:
        True if the filename has a user namespace (starts with "user:"),
        False otherwise.
    """
    return filename.startswith("user:")


def artifact_path(artifact_id: ArtifactId) -> str:
    """Constructs the artifact path.

Args:
    artifact_id: The identifier for the artifact.

Returns:
    The constructed artifact path.
"""
    if file_has_user_namespace(artifact_id.filename):
        return f"{artifact_id.app_name}/{artifact_id.user_id}/user/{artifact_id.filename}"
    return f"{artifact_id.app_name}/{artifact_id.user_id}/{artifact_id.session_id}/{artifact_id.filename}"


def create_artifact_uri(artifact_id: ArtifactId, version: int) -> str:
    """Creates an artifact URI.

    Args:
        app_name: The name of the application.
        user_id: The ID of the user.
        filename: The name of the artifact file.
        version: The version of the artifact.
        session_id: The ID of the session.

    Returns:
        The constructed artifact URI.
    """
    prefix = f"memory://apps/{artifact_id.app_name}/users/{artifact_id.user_id}"
    if file_has_user_namespace(artifact_id.filename):
        return f"{prefix}/artifacts/{artifact_id.filename}/versions/{version}"
    return f"{prefix}/sessions/{artifact_id.session_id}/artifacts/{artifact_id.filename}/versions/{version}"
