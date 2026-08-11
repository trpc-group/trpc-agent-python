"""Tests for the SDK-provided Advanced Memory tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from trpc_agent_sdk.advanced_memory import AdvancedMemoryConfig
from trpc_agent_sdk.advanced_memory import AdvancedMemoryRuntime
from trpc_agent_sdk.tools import AdvancedMemoryTools
from trpc_agent_sdk.tools import create_advanced_memory_tools


def _runtime(tmp_path: Path) -> AdvancedMemoryRuntime:
    """Create a test runtime with long-term memory enabled."""
    return AdvancedMemoryRuntime.create(AdvancedMemoryConfig(
        enabled=True,
        root_dir=tmp_path,
    ))


async def test_save_read_and_update_memory_index(tmp_path: Path) -> None:
    """Ensure the tools save, read, and update one index entry."""
    runtime = _runtime(tmp_path)
    tools = AdvancedMemoryTools(runtime)

    await tools.save_memory(
        filename="preferences.md",
        name="编码偏好",
        description="保存用户编码偏好",
        memory_type="user",
        summary="用户使用 Python 3.12",
        content="用户使用 Python 3.12。",
    )
    await tools.save_memory(
        filename="preferences.md",
        name="编码偏好",
        description="保存用户编码偏好",
        memory_type="user",
        summary="公共函数需要中文 docstring",
        content="公共函数需要中文 docstring。",
    )

    result = await tools.read_memory("preferences.md")
    index = await tools.list_memory_index()
    assert result["found"] is True
    assert "公共函数需要中文 docstring" in result["content"]
    assert result["freshness"] == "today"
    assert result["updated_at"] is not None
    assert "may no longer reflect the current state" in result["freshness_notice"]
    assert index["index"].count("preferences.md") == 1
    assert "公共函数需要中文 docstring" in index["index"]


async def test_save_memory_rejects_unknown_type(tmp_path: Path) -> None:
    """Ensure the tools reject unsupported memory types."""
    tools = AdvancedMemoryTools(_runtime(tmp_path))

    with pytest.raises(ValueError, match="memory_type"):
        await tools.save_memory(
            filename="invalid.md",
            name="无效类型",
            description="测试无效类型",
            memory_type="other",
            summary="无效",
            content="无效",
        )


def test_factory_returns_three_named_tools(tmp_path: Path) -> None:
    """Ensure the factory returns the three installable tools."""
    tools = create_advanced_memory_tools(_runtime(tmp_path))

    tool_names = {tool.name for tool in tools}
    assert tool_names == {
        "save_memory",
        "read_memory",
        "list_memory_index",
    }
