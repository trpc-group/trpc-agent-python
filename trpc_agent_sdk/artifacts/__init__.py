# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Artifact management module.

This module provides artifact management functionality including:
- Abstract artifact service interfaces
- In-memory artifact service implementation
"""

from trpc_agent_sdk.abc import ArtifactServiceABC as BaseArtifactService

from ._in_memory_artifact_service import InMemoryArtifactService
from ._utils import ParsedArtifactUri
from ._utils import artifact_path
from ._utils import create_artifact_uri
from ._utils import file_has_user_namespace
from ._utils import get_artifact_uri
from ._utils import is_artifact_ref
from ._utils import parse_artifact_uri

__all__ = [
    "BaseArtifactService",
    "InMemoryArtifactService",
    "ParsedArtifactUri",
    "artifact_path",
    "create_artifact_uri",
    "file_has_user_namespace",
    "get_artifact_uri",
    "is_artifact_ref",
    "parse_artifact_uri",
]
