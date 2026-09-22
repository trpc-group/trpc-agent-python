# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Artifact tools exposed to the example agent."""

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.types import Part


async def save_report(
    filename: str,
    content: str,
    tool_context: InvocationContext,
) -> dict[str, object]:
    """Save a Markdown report as a versioned artifact.

    Args:
        filename: Artifact filename ending in ``.md``.
        content: Complete Markdown report content.
    """
    if not filename.endswith(".md"):
        return {
            "error": "filename must end with .md",
            "success": False,
        }

    version = await tool_context.save_artifact(
        filename=filename,
        artifact=Part.from_text(text=content),
    )
    return {
        "filename": filename,
        "success": True,
        "version": version,
    }


async def list_reports(tool_context: InvocationContext) -> dict[str, object]:
    """List artifacts visible in the current session."""
    filenames = await tool_context.list_artifacts()
    return {
        "artifacts": filenames,
        "success": True,
    }


async def load_report(
    filename: str,
    tool_context: InvocationContext,
) -> dict[str, object]:
    """Load the latest version of one report artifact.

    Args:
        filename: Artifact filename to load.
    """
    artifact = await tool_context.load_artifact(filename=filename)
    if artifact is None:
        return {
            "error": f"artifact not found: {filename}",
            "success": False,
        }
    return {
        "content": artifact.data.text,
        "filename": filename,
        "success": True,
        "version": artifact.version.version,
    }
