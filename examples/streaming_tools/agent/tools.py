# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tools for the agent.

Demonstrates two types of tools:
  1. StreamingFunctionTool: Supports streaming argument delivery for large content.
  2. FunctionTool: Standard synchronous tool for simple queries.
"""


def write_file(path: str, content: str) -> dict:
    """Write content to a file (streaming).

    Args:
        path: The file path to write to.
        content: The content to write.

    Returns:
        A dict containing success status, path, and content size.
    """
    print(f"\n📄 Writing to {path}...")
    print(f"Content: {content[:100]}...")
    return {"success": True, "path": path, "size": len(content)}


def get_file_info(path: str) -> dict:
    """Get file information (non-streaming).

    Args:
        path: The file path to query.

    Returns:
        A dict containing path and existence status.
    """
    return {"path": path, "exists": True}
