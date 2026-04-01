# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""
Tools for comprehensive streaming tool testing.

This module demonstrates various ways to create streaming tools:
1. Sync function -> StreamingFunctionTool
2. Async function -> StreamingFunctionTool
3. FunctionTool -> StreamingFunctionTool
4. Tools inside ToolSet
5. Custom BaseTool with is_streaming=True
"""

from typing import Any
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.tools import BaseToolSet
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.tools import StreamingFunctionTool
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type


# =============================================================================
# Test 1: Sync function -> StreamingFunctionTool
# =============================================================================
def write_file(path: str, content: str) -> dict:
    """Write content to a file (sync version).

    Args:
        path: The file path to write to
        content: The content to write to the file

    Returns:
        A dictionary containing the operation result
    """
    print(f"\n📄 [Sync Write File]")
    print(f"   Path: {path}")
    print(f"   Content length: {len(content)} chars")

    return {
        "success": True,
        "path": path,
        "bytes_written": len(content),
        "tool_type": "sync_function",
    }


# =============================================================================
# Test 2: Async function -> StreamingFunctionTool
# =============================================================================
async def async_write_file(path: str, content: str) -> dict:
    """Write content to a file (async version).

    Args:
        path: The file path to write to
        content: The content to write to the file

    Returns:
        A dictionary containing the operation result
    """
    import asyncio
    await asyncio.sleep(0.1)  # Simulate async operation

    print(f"\n📄 [Async Write File]")
    print(f"   Path: {path}")
    print(f"   Content length: {len(content)} chars")

    return {
        "success": True,
        "path": path,
        "bytes_written": len(content),
        "tool_type": "async_function",
    }


# =============================================================================
# Test 3: FunctionTool -> StreamingFunctionTool
# =============================================================================
def append_file(path: str, content: str) -> dict:
    """Append content to a file.

    Args:
        path: The file path to append to
        content: The content to append

    Returns:
        A dictionary containing the operation result
    """
    print(f"\n📄 [Append File]")
    print(f"   Path: {path}")
    print(f"   Content length: {len(content)} chars")

    return {
        "success": True,
        "path": path,
        "bytes_appended": len(content),
        "tool_type": "function_tool_converted",
    }


# Create FunctionTool first, then convert to StreamingFunctionTool
append_file_function_tool = FunctionTool(append_file)
append_file_streaming_tool = StreamingFunctionTool(append_file_function_tool)


# =============================================================================
# Test 4: Custom BaseTool with is_streaming=True
# =============================================================================
class CustomStreamingWriteTool(BaseTool):
    """A custom tool that inherits from BaseTool with streaming support.

    This demonstrates that custom tools can also support streaming by
    overriding the is_streaming property.
    """

    def __init__(self):
        super().__init__(
            name="custom_write",
            description="Custom streaming write tool that demonstrates BaseTool with is_streaming=True",
        )

    @property
    @override
    def is_streaming(self) -> bool:
        """Enable streaming for this custom tool."""
        return True

    @override
    def _get_declaration(self) -> Optional[FunctionDeclaration]:
        return FunctionDeclaration(
            name="custom_write",
            description="Write content using custom streaming tool",
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "filename": Schema(
                        type=Type.STRING,
                        description="The filename to write to",
                    ),
                    "data": Schema(
                        type=Type.STRING,
                        description="The data to write",
                    ),
                },
                required=["filename", "data"],
            ),
        )

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        filename = args.get("filename", "unknown")
        data = args.get("data", "")

        print(f"\n📄 [Custom Streaming Write]")
        print(f"   Filename: {filename}")
        print(f"   Data length: {len(data)} chars")

        return {
            "success": True,
            "filename": filename,
            "bytes_written": len(data),
            "tool_type": "custom_base_tool",
        }


# =============================================================================
# Test 5: ToolSet containing streaming tools
# =============================================================================
class StreamingFileToolSet(BaseToolSet):
    """A ToolSet containing streaming tools.

    This demonstrates that streaming tools inside a ToolSet are properly
    detected at runtime via the is_streaming property.
    """

    def __init__(self):
        super().__init__(name="streaming_file_toolset")
        self._tools = []
        self._init_tools()

    def _init_tools(self):
        # Add a streaming tool for creating files
        self._tools.append(StreamingFunctionTool(self._create_file))

        # Add a non-streaming tool for reading files (for comparison)
        self._tools.append(FunctionTool(self._read_file))

    def _create_file(self, path: str, content: str) -> dict:
        """Create a new file with content (inside ToolSet).

        Args:
            path: The file path
            content: The file content
        """
        print(f"\n📄 [ToolSet Create File]")
        print(f"   Path: {path}")
        print(f"   Content length: {len(content)} chars")

        return {
            "success": True,
            "path": path,
            "bytes_written": len(content),
            "tool_type": "toolset_streaming",
        }

    def _read_file(self, path: str) -> dict:
        """Read a file (non-streaming, for comparison).

        Args:
            path: The file path to read
        """
        print(f"\n📖 [ToolSet Read File] {path}")

        return {
            "success": True,
            "path": path,
            "content": f"[Simulated content of {path}]",
            "tool_type": "toolset_non_streaming",
        }

    @override
    async def get_tools(self, invocation_context: Optional[InvocationContext] = None):
        return self._tools


# =============================================================================
# Test 6: StreamingFunctionTool wrapping a plain function
# =============================================================================
def _save_document(title: str, body: str) -> dict:
    """Save a document.

    Args:
        title: The document title
        body: The document body content

    Returns:
        A dictionary containing the operation result
    """
    print(f"\n📄 [Save Document via StreamingFunctionTool]")
    print(f"   Title: {title}")
    print(f"   Body length: {len(body)} chars")

    return {
        "success": True,
        "title": title,
        "body_length": len(body),
        "tool_type": "streaming_function_tool",
    }


save_document = StreamingFunctionTool(_save_document)


# =============================================================================
# Non-streaming tool for comparison
# =============================================================================
def get_file_info(path: str) -> dict:
    """Get file information (non-streaming tool for comparison).

    Args:
        path: The file path

    Returns:
        File information
    """
    print(f"\n📊 [Get File Info] {path}")

    return {
        "path": path,
        "exists": True,
        "size": 1024,
        "tool_type": "non_streaming",
    }


# =============================================================================
# Export all tools and toolsets
# =============================================================================
__all__ = [
    # Sync function
    "write_file",
    # Async function
    "async_write_file",
    # FunctionTool -> StreamingFunctionTool
    "append_file_streaming_tool",
    # Custom BaseTool
    "CustomStreamingWriteTool",
    # ToolSet
    "StreamingFileToolSet",
    # @register_tool decorated
    "save_document",
    # Non-streaming for comparison
    "get_file_info",
]
