"""Tests for the SDK-provided Advanced Memory tools."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from trpc_agent_sdk.tools.advanced_memory import AdvancedMemoryPaths
from trpc_agent_sdk.tools.advanced_memory import AdvancedMemoryRuntime
from trpc_agent_sdk.tools.advanced_memory import AdvancedMemoryConfig
from trpc_agent_sdk.tools import AdvancedMemoryToolSet
from trpc_agent_sdk.tools import create_advanced_memory_toolset


def _runtime(tmp_path: Path) -> AdvancedMemoryRuntime:
    """Create a test runtime with long-term memory enabled."""
    return AdvancedMemoryRuntime.create(AdvancedMemoryConfig(
        enabled=True,
        root_dir=tmp_path,
    )).for_scope("demo-app", "demo-user")


async def test_save_read_and_update_memory_index(tmp_path: Path) -> None:
    """Ensure the tools save, read, and update one index entry."""
    runtime = _runtime(tmp_path)
    tools = await AdvancedMemoryToolSet(runtime).get_tools()
    save_memory = tools[0].func
    read_memory = tools[1].func
    list_memory_index = tools[2].func

    await save_memory(
        filename="preferences.md",
        name="编码偏好",
        description="保存用户编码偏好",
        memory_type="user",
        summary="用户使用 Python 3.12",
        content="用户使用 Python 3.12。",
    )
    await save_memory(
        filename="preferences.md",
        name="编码偏好",
        description="保存用户编码偏好",
        memory_type="user",
        summary="公共函数需要中文 docstring",
        content="公共函数需要中文 docstring。",
    )

    result = await read_memory("preferences.md")
    index = await list_memory_index()
    assert result["found"] is True
    assert "公共函数需要中文 docstring" in result["content"]
    assert result["freshness"] == "today"
    assert result["updated_at"] is not None
    assert "may no longer reflect the current state" in result["freshness_notice"]
    assert index["index"].count("preferences.md") == 1
    assert "公共函数需要中文 docstring" in index["index"]


async def test_save_memory_rejects_unknown_type(tmp_path: Path) -> None:
    """Ensure the tools reject unsupported memory types."""
    tools = await AdvancedMemoryToolSet(_runtime(tmp_path)).get_tools()

    with pytest.raises(ValueError, match="memory_type"):
        await tools[0].func(
            filename="invalid.md",
            name="无效类型",
            description="测试无效类型",
            memory_type="other",
            summary="无效",
            content="无效",
        )


@pytest.mark.parametrize(
    ("storage_backend", "expected_prefix"),
    (("redis", "advanced-memory://redis/"), ("sql", "advanced-memory://sql/")),
)
async def test_list_memory_index_reports_backend_storage_reference(
    storage_backend: str,
    expected_prefix: str,
) -> None:
    """Avoid exposing a local filesystem path for external memory stores."""
    config = AdvancedMemoryConfig(
        storage_backend=storage_backend,
        redis_url="redis://localhost:6379/0" if storage_backend == "redis" else None,
        sql_url="sqlite:///advanced-memory.db" if storage_backend == "sql" else None,
    )
    paths = AdvancedMemoryPaths(config).for_scope("demo-app", "demo-user")
    runtime = SimpleNamespace(
        config=config,
        paths=paths,
        scope=paths.scope,
        long_term_memory=SimpleNamespace(read_index=AsyncMock(return_value="")),
    )

    tools = await AdvancedMemoryToolSet(runtime).get_tools()
    result = await tools[2].func()

    assert result["index_path"].startswith(expected_prefix)
    assert str(paths.memory_index_path) not in result["index_path"]


async def test_factory_returns_three_named_tools(tmp_path: Path) -> None:
    """Ensure the factory returns a toolset with three installable tools."""
    toolset = create_advanced_memory_toolset(_runtime(tmp_path))
    tools = await toolset.get_tools()

    tool_names = {tool.name for tool in tools}
    assert tool_names == {
        "save_memory",
        "read_memory",
        "list_memory_index",
    }
