# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Tools for the streaming tool demo agent."""


def write_file(path: str, content: str) -> dict:
    """Write content to a file.

    This is a simulated file write operation that prints the content
    instead of actually writing to disk.

    Args:
        path: The file path to write to
        content: The content to write to the file

    Returns:
        A dictionary containing the operation result
    """
    # Simulate file write by printing
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
