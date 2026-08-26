# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Provide long-term memory read/write tools for standalone Advanced Memory."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from trpc_agent_sdk.advanced_memory._formats import MemoryDocument
from trpc_agent_sdk.advanced_memory._formats import MemoryIndexEntry
from trpc_agent_sdk.advanced_memory._formats import MemoryType
from trpc_agent_sdk.advanced_memory._formats import memory_freshness
from trpc_agent_sdk.advanced_memory._formats import parse_memory_updated_at
from trpc_agent_sdk.advanced_memory._runtime import AdvancedMemoryRuntime

from ._function_tool import FunctionTool

ADVANCED_MEMORY_TOOL_NAMES = frozenset({
    "save_memory",
    "read_memory",
    "list_memory_index",
})
_INDEX_PATTERN = re.compile(r"^- \[(?P<name>.+?)\]（(?P<filename>.+?)）:(?P<summary>.+)$")


def _parse_index(index: str) -> list[MemoryIndexEntry]:
    """Parse standard Advanced Memory index entries from MEMORY.md."""
    entries: list[MemoryIndexEntry] = []
    for line in index.splitlines():
        match = _INDEX_PATTERN.match(line.strip())
        if match is None:
            continue
        entries.append(MemoryIndexEntry(**match.groupdict()))
    return entries


class AdvancedMemoryTools:
    """Wrap long-term memory storage as three official Agent-callable tools."""

    def __init__(self, runtime: AdvancedMemoryRuntime) -> None:
        """Store the runtime and create the index update lock."""
        self._runtime = runtime
        self._index_lock = asyncio.Lock()
        self._tools = (
            FunctionTool(self.save_memory),
            FunctionTool(self.read_memory),
            FunctionTool(self.list_memory_index),
        )

    @property
    def runtime(self) -> AdvancedMemoryRuntime:
        """Return the Advanced Memory runtime bound to these tools."""
        return self._runtime

    def as_tools(self) -> list[FunctionTool]:
        """Return tools that can be appended directly to LlmAgent.tools."""
        return list(self._tools)

    def owns_tool(self, tool: Any) -> bool:
        """Return whether this container created the given FunctionTool."""
        function = getattr(tool, "func", None)
        return getattr(function, "__self__", None) is self

    async def save_memory(
        self,
        filename: str,
        name: str,
        description: str,
        memory_type: str,
        summary: str,
        content: str,
    ) -> dict:
        """Save or overwrite a long-term memory file and update MEMORY.md."""
        try:
            resolved_type = MemoryType(memory_type)
        except ValueError as exc:
            allowed = ", ".join(item.value for item in MemoryType)
            raise ValueError(f"memory_type must be one of: {allowed}") from exc
        document = MemoryDocument(
            name=name,
            description=description,
            memory_type=resolved_type,
            content=content,
        )
        async with self._index_lock:
            path = await self._runtime.long_term_memory.write_topic(
                filename,
                document,
            )
            entries = _parse_index(await self._runtime.long_term_memory.read_index())
            new_entry = MemoryIndexEntry(
                name=name,
                filename=path.name,
                summary=summary,
            )
            entries = [entry for entry in entries if entry.filename != new_entry.filename]
            entries.insert(0, new_entry)
            await self._runtime.long_term_memory.write_index(entries)
        updated_at = parse_memory_updated_at(await self._runtime.long_term_memory.read_topic(filename) or "")
        return {
            "saved": True,
            "filename": path.name,
            "path": str(path),
            "memory_type": resolved_type.value,
            "updated_at": updated_at.isoformat() if updated_at is not None else None,
        }

    async def read_memory(self, filename: str) -> dict:
        """Read a complete long-term memory by its filename in MEMORY.md."""
        content = await self._runtime.long_term_memory.read_topic(filename)
        if content is None:
            return {"found": False, "filename": filename}
        updated_at = parse_memory_updated_at(content)
        freshness = memory_freshness(updated_at)
        return {
            "found":
            True,
            "filename":
            filename,
            "content":
            content,
            "updated_at":
            updated_at.isoformat() if updated_at is not None else None,
            "freshness":
            freshness,
            "freshness_notice": (f"This memory was last updated {freshness}. It is a point-in-time observation "
                                 "and may no longer reflect the current state. Verify it when necessary, and "
                                 "update this memory if it is outdated or incorrect."),
        }

    async def list_memory_index(self) -> dict:
        """Return the current long-term memory index and its disk path."""
        return {
            "index_path": str(self._runtime.paths.memory_index_path),
            "index": await self._runtime.long_term_memory.read_index(),
        }


def create_advanced_memory_tools(runtime: AdvancedMemoryRuntime, ) -> list[FunctionTool]:
    """Create the official Advanced Memory tools bound to the given runtime."""
    return AdvancedMemoryTools(runtime).as_tools()
