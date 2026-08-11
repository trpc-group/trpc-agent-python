"""为独立 Advanced Memory 提供长期记忆读写工具。"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from trpc_agent_sdk.advanced_memory.formats import MemoryDocument
from trpc_agent_sdk.advanced_memory.formats import MemoryIndexEntry
from trpc_agent_sdk.advanced_memory.formats import MemoryType
from trpc_agent_sdk.advanced_memory.formats import memory_freshness
from trpc_agent_sdk.advanced_memory.formats import parse_memory_updated_at
from trpc_agent_sdk.advanced_memory.runtime import AdvancedMemoryRuntime

from ._function_tool import FunctionTool

ADVANCED_MEMORY_TOOL_NAMES = frozenset({
    "save_memory",
    "read_memory",
    "list_memory_index",
})
_INDEX_PATTERN = re.compile(r"^- \[(?P<name>.+?)\]（(?P<filename>.+?)）:(?P<summary>.+)$")


def _parse_index(index: str) -> list[MemoryIndexEntry]:
    """解析 Advanced Memory 的标准 MEMORY.md 索引行。"""
    entries: list[MemoryIndexEntry] = []
    for line in index.splitlines():
        match = _INDEX_PATTERN.match(line.strip())
        if match is None:
            continue
        entries.append(MemoryIndexEntry(**match.groupdict()))
    return entries


class AdvancedMemoryTools:
    """把长期记忆存储包装为 Agent 可调用的三个正式工具。"""

    def __init__(self, runtime: AdvancedMemoryRuntime) -> None:
        """保存运行时并创建索引更新锁。"""
        self._runtime = runtime
        self._index_lock = asyncio.Lock()
        self._tools = (
            FunctionTool(self.save_memory),
            FunctionTool(self.read_memory),
            FunctionTool(self.list_memory_index),
        )

    @property
    def runtime(self) -> AdvancedMemoryRuntime:
        """返回工具绑定的新记忆运行时。"""
        return self._runtime

    def as_tools(self) -> list[FunctionTool]:
        """返回可直接追加到 LlmAgent.tools 的工具列表。"""
        return list(self._tools)

    def owns_tool(self, tool: Any) -> bool:
        """判断给定 FunctionTool 是否由当前工具容器创建。"""
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
        """保存或覆盖长期记忆详情文件，并同步更新 MEMORY.md 索引。"""
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
        """按 MEMORY.md 中的文件名读取一条长期记忆全文。"""
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
        """返回当前长期记忆索引及其磁盘路径。"""
        return {
            "index_path": str(self._runtime.paths.memory_index_path),
            "index": await self._runtime.long_term_memory.read_index(),
        }


def create_advanced_memory_tools(runtime: AdvancedMemoryRuntime, ) -> list[FunctionTool]:
    """创建绑定指定运行时的正式 Advanced Memory 工具列表。"""
    return AdvancedMemoryTools(runtime).as_tools()
