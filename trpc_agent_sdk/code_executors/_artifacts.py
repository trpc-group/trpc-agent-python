# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
Artifact helper functions for loading and saving artifacts.

This module provides helper functions to interact with the artifact service
through context, enabling artifact resolution without importing higher-level packages.
"""

from typing import Optional
from typing import Tuple

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.types import Blob
from trpc_agent_sdk.types import Part


async def load_artifact_helper(ctx: InvocationContext,
                               name: str,
                               version: Optional[int] = None) -> Optional[Tuple[bytes, int]]:
    """
    Resolve artifact name@version via callback context.

    If version is None, loads latest. Returns data, mime, actual version.

    Args:
        ctx: The context containing artifact service
        name: The artifact name
        version: Optional version number. If None, loads latest.

    Returns:
        The artifact.
    """
    if not ctx:
        raise ValueError("ctx is required")
    artifact_entry = await ctx.load_artifact(name, version)
    if artifact_entry is None:
        return None
    if version is None:
        version = artifact_entry.version.version
    return artifact_entry.data.inline_data.data, version or 0


def parse_artifact_ref(ref: str) -> Tuple[str, Optional[int]]:
    """
    Split "name@version" into name and optional version.

    Args:
        ref: The artifact reference string (e.g., "name@version" or "name")

    Returns:
        The artifact name and version.
    """
    parts = ref.split("@")
    name = parts[0]
    if not name:
        raise ValueError(f"invalid ref: {ref}")

    if len(parts) == 1:
        return name, None

    if len(parts) >= 2:
        # Try to parse version as integer
        version_str = "".join(parts[1:])
        if not version_str.isdigit():
            return name, None

        return name, int(version_str)

    raise ValueError(f"invalid ref: {ref}")


async def save_artifact_helper(ctx: InvocationContext, filename: str, data: bytes, mime: str) -> int:
    """
    Save a file as artifact using callback context.

    Args:
        ctx: The context containing artifact service
        filename: The filename of the artifact
        data: The binary data to save
        mime: The MIME type of the data

    Returns:
        The version of the artifact.
    """
    artifact = Part(inline_data=Blob(data=data, mime_type=mime), )
    return await ctx.save_artifact(filename, artifact)
