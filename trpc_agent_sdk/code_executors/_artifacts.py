# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""
Artifact helper functions for loading and saving artifacts.

This module provides helper functions to interact with the artifact service
through context, enabling artifact resolution without importing higher-level packages.
"""

from typing import Any
from typing import Optional
from typing import Tuple

from trpc_agent_sdk.abc import ArtifactServiceABC
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

    if len(parts) == 1:
        return parts[0], None

    if len(parts) == 2:
        # Try to parse version as integer
        version_str = parts[1]
        if not version_str.isdigit():
            raise ValueError(f"invalid version: {version_str}")

        return parts[0], int(version_str)

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


def with_artifact_service(ctx: InvocationContext, svc: ArtifactServiceABC) -> InvocationContext:
    """
    Store an artifact.Service in the context.

    Callers retrieve it in lower layers to load/save artifacts
    without importing higher-level packages.

    Args:
        ctx: The context to store the service in
        svc: The artifact service

    Returns:
        Updated context with artifact service
    """
    ctx.artifact_service = svc
    return ctx


def artifact_service_from_context(ctx: InvocationContext) -> Optional[ArtifactServiceABC]:
    """
    Fetch the artifact.Service previously stored by with_artifact_service.

    Args:
        ctx: The context to retrieve the service from

    Returns:
        Tuple of (service, ok) where ok indicates presence
    """
    return ctx.artifact_service


def with_artifact_session(ctx: InvocationContext, info: Any) -> InvocationContext:
    assert False, "Not implemented"


def artifact_session_from_context(ctx: InvocationContext) -> Any:
    """
    Retrieve artifact session info from context.

    Args:
        ctx: The context to retrieve the session info from

    Returns:
        SessionInfo object (empty if not found)
    """
    assert False, "Not implemented"
