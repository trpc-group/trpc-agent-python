# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Provide long-term memory read/write tools for standalone Advanced Memory."""

from __future__ import annotations

import asyncio
from typing import Any

from trpc_agent_sdk.abc import ToolABC
from trpc_agent_sdk.abc import ToolSetABC
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools import FunctionTool
from ._file_storage import parse_memory_index
from ._formats import AdvancedMemoryDocument
from ._formats import AdvancedMemoryIndexEntry
from ._formats import AdvancedMemoryType
from ._formats import memory_freshness
from ._formats import parse_memory_updated_at
from ._runtime import AdvancedMemoryRuntime


async def save_memory(
    self: "AdvancedMemoryToolSet",
    filename: str,
    name: str,
    description: str,
    memory_type: str,
    summary: str,
    content: str,
    tool_context: Any | None = None,
) -> dict:
    """Save or overwrite a long-term memory file and update MEMORY.md."""
    try:
        resolved_type = AdvancedMemoryType(memory_type)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in AdvancedMemoryType)
        raise ValueError(f"memory_type must be one of: {allowed}") from exc
    document = AdvancedMemoryDocument(
        name=name,
        description=description,
        memory_type=resolved_type,
        content=content,
    )
    runtime = self._runtime_for_context(tool_context)
    async with self._index_lock(runtime):
        path = await runtime.long_term_memory.write_topic(
            filename,
            document,
        )
        entries = parse_memory_index(await runtime.long_term_memory.read_index())
        new_entry = AdvancedMemoryIndexEntry(
            name=name,
            filename=path.name,
            summary=summary,
        )
        entries = [entry for entry in entries if entry.filename != new_entry.filename]
        entries.insert(0, new_entry)
        await runtime.long_term_memory.write_index(entries)
    updated_at = parse_memory_updated_at(await runtime.long_term_memory.read_topic(filename) or "")
    return {
        "saved": True,
        "filename": path.name,
        "path": runtime.paths.storage_reference("memory_topic", topic_name=path.name),
        "memory_type": resolved_type.value,
        "updated_at": updated_at.isoformat() if updated_at is not None else None,
    }


async def read_memory(
    self: "AdvancedMemoryToolSet",
    filename: str,
    tool_context: Any | None = None,
) -> dict:
    """Read a complete long-term memory by its filename in MEMORY.md."""
    runtime = self._runtime_for_context(tool_context)
    content = await runtime.long_term_memory.read_topic(filename)
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


async def list_memory_index(
    self: "AdvancedMemoryToolSet",
    tool_context: Any | None = None,
) -> dict:
    """Return the current long-term memory index and its storage reference."""
    runtime = self._runtime_for_context(tool_context)
    return {
        "index_path": runtime.paths.storage_reference("memory_index"),
        "index": await runtime.long_term_memory.read_index(),
    }


class AdvancedMemoryToolSet(ToolSetABC):
    """Expose the official long-term memory tools as a framework toolset."""

    save_memory = save_memory
    read_memory = read_memory
    list_memory_index = list_memory_index

    def __init__(self, runtime: AdvancedMemoryRuntime) -> None:
        """Store the runtime and create the Agent-callable tools."""
        super().__init__(name="advanced_memory")
        self._runtime = runtime
        self._index_locks: dict[str, asyncio.Lock] = {}
        self._tools = (
            FunctionTool(self.save_memory),
            FunctionTool(self.read_memory),
            FunctionTool(self.list_memory_index),
        )

    async def get_tools(
        self,
        invocation_context: "InvocationContext | None" = None,
    ) -> list[ToolABC]:
        """Return the memory tools selected for the current context."""
        return [tool for tool in self._tools if self._is_tool_selected(tool, invocation_context)]

    def _runtime_for_context(self, tool_context: Any | None) -> Any:
        """Resolve storage from the authenticated session, never tool arguments."""
        if tool_context is None:
            return self._runtime
        session = getattr(tool_context, "session", None)
        return self._runtime.for_session(session)

    def _index_lock(self, runtime: Any) -> asyncio.Lock:
        """Return a lock for one long-term-memory tenant index."""
        scope = getattr(runtime, "scope", None)
        key = scope.storage_key if scope is not None else str(runtime.paths.root_dir)
        lock = self._index_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._index_locks[key] = lock
        return lock


def create_advanced_memory_toolset(runtime: AdvancedMemoryRuntime) -> AdvancedMemoryToolSet:
    """Create the Advanced Memory toolset bound to the given runtime."""
    return AdvancedMemoryToolSet(runtime)
