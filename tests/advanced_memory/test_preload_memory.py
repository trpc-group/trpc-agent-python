"""Tests for automatic Advanced Memory topic preloading."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from trpc_agent_sdk.advanced_memory import AdvancedMemoryConfig
from trpc_agent_sdk.advanced_memory import AdvancedMemoryRuntime
from trpc_agent_sdk.advanced_memory import MemoryDocument
from trpc_agent_sdk.advanced_memory import MemoryPreloader
from trpc_agent_sdk.advanced_memory import MemoryType


class _FakeSelector:
    """Return a deterministic topic selection without calling an LLM."""

    async def select(self, query, candidates, ctx, *, limit):
        """Select the first topic for the preloader test."""
        del query, ctx
        return [candidates[0].filename][:limit]


class _FailingSelector:
    """Fail deliberately to verify preload is best effort."""

    async def select(self, query, candidates, ctx, *, limit):
        """Raise a selector failure."""
        raise RuntimeError("selector failed")


async def test_preloader_injects_selected_topic_with_budget(tmp_path: Path) -> None:
    """Ensure selected topic content is rendered and bounded."""
    runtime = AdvancedMemoryRuntime.create(
        AdvancedMemoryConfig(
            enabled=True,
            root_dir=tmp_path,
            preload_memory_enabled=True,
            preload_memory_max_chars=200,
        ))
    await runtime.initialize()
    await runtime.long_term_memory.write_topic(
        "project.md",
        MemoryDocument(
            name="Project",
            description="Project details",
            memory_type=MemoryType.PROJECT,
            content="important project details",
        ),
    )

    result = await MemoryPreloader(runtime, _FakeSelector()).preload(
        "What is relevant?",
        SimpleNamespace(),
    )

    assert result is not None
    assert '<advanced-memory-preload>' in result
    assert 'filename="project.md"' in result
    assert "important pro" in result


async def test_preloader_marks_truncated_content(tmp_path: Path) -> None:
    """Tell the main model when the configured content budget truncated a topic."""
    runtime = AdvancedMemoryRuntime.create(
        AdvancedMemoryConfig(
            enabled=True,
            root_dir=tmp_path,
            preload_memory_enabled=True,
            preload_memory_max_chars=12,
        ))
    await runtime.initialize()
    await runtime.long_term_memory.write_topic(
        "project.md",
        MemoryDocument(
            name="Project",
            description="Project details",
            memory_type=MemoryType.PROJECT,
            content="important project details",
        ),
    )

    result = await MemoryPreloader(runtime, _FakeSelector()).preload(
        "What is relevant?",
        SimpleNamespace(),
    )

    assert result is not None
    assert 'filename="project.md"' in result
    assert 'truncated="true"' in result


async def test_preloader_failure_is_best_effort(tmp_path: Path) -> None:
    """Return no prompt content when relevance screening fails."""
    runtime = AdvancedMemoryRuntime.create(
        AdvancedMemoryConfig(
            enabled=True,
            root_dir=tmp_path,
            preload_memory_enabled=True,
        ))
    await runtime.initialize()
    await runtime.long_term_memory.write_topic(
        "project.md",
        MemoryDocument(
            name="Project",
            description="Project details",
            memory_type=MemoryType.PROJECT,
            content="important project details",
        ),
    )

    result = await MemoryPreloader(runtime, _FailingSelector()).preload(
        "What is relevant?",
        SimpleNamespace(),
    )

    assert result is None
