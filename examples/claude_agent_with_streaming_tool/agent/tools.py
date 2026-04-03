# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tools for the ClaudeAgent streaming tool demo.

This module demonstrates the difference between streaming and non-streaming tools:
- write_file: Uses StreamingFunctionTool - receives streaming argument events
- get_file_info: Uses regular FunctionTool - does NOT receive streaming events

This aligns with LlmAgent behavior where only tools marked as streaming
receive real-time argument updates.
"""


def write_file(path: str, content: str) -> dict:
    """Write content to a file.

    This is a simulated file write operation that prints the content
    instead of actually writing to disk.

    This tool is wrapped with StreamingFunctionTool, so its arguments
    will be streamed in real-time as the LLM generates them.

    Args:
        path: The file path to write to
        content: The content to write to the file

    Returns:
        A dictionary containing the operation result
    """
    print(f"\n📄 [Simulated File Write]")
    print(f"   Path: {path}")
    print(f"   Content ({len(content)} chars):")
    print("-" * 40)
    print(content)
    print("-" * 40)

    return {
        "success": True,
        "path": path,
        "bytes_written": len(content),
        "message": f"Successfully wrote {len(content)} characters to {path}"
    }


def get_file_info(path: str) -> dict:
    """Get information about a file.

    This is a simulated file info operation.

    This tool is wrapped with regular FunctionTool (not streaming),
    so its arguments will NOT be streamed - they arrive only when complete.

    Args:
        path: The file path to get info for

    Returns:
        A dictionary containing simulated file information
    """
    print(f"\n📋 [Get File Info] path={path}")

    return {
        "success": True,
        "path": path,
        "exists": True,
        "size": 1024,
        "type": "text/plain",
        "message": f"File info retrieved for {path}"
    }
