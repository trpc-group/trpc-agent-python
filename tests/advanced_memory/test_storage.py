"""Unit tests for the independent Advanced Memory stores."""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime
from datetime import timezone
from pathlib import Path

import pytest

from trpc_agent_sdk.advanced_memory import AdvancedMemoryConfig
from trpc_agent_sdk.advanced_memory import AdvancedMemoryPaths
from trpc_agent_sdk.advanced_memory import AdvancedMemoryRuntime
from trpc_agent_sdk.advanced_memory import MemoryDocument
from trpc_agent_sdk.advanced_memory import MemoryIndexEntry
from trpc_agent_sdk.advanced_memory import MemoryType
from trpc_agent_sdk.advanced_memory import SESSION_MEMORY_SECTIONS
from trpc_agent_sdk.advanced_memory import SessionMemoryDocument
from trpc_agent_sdk.advanced_memory import memory_freshness
from trpc_agent_sdk.advanced_memory import parse_memory_updated_at


def _enabled_config(tmp_path: Path, **overrides: object) -> AdvancedMemoryConfig:
    """Create an enabled configuration rooted at the test directory."""
    return AdvancedMemoryConfig(enabled=True, root_dir=tmp_path, **overrides)


def test_config_reads_context_window_from_environment(monkeypatch: pytest.MonkeyPatch, ) -> None:
    """Use both model limits from the environment when not provided explicitly."""
    monkeypatch.setenv("TRPC_AGENT_MODEL_CONTEXT_WINDOW_TOKENS", "128000")
    monkeypatch.setenv("TRPC_AGENT_MAX_OUTPUT_TOKENS", "8192")

    config = AdvancedMemoryConfig()

    assert config.model_context_window_tokens == 128_000
    assert config.max_output_tokens == 8_192


def test_config_rejects_invalid_context_window_environment(monkeypatch: pytest.MonkeyPatch, ) -> None:
    """Reject invalid environment values with a clear configuration error."""
    monkeypatch.setenv("TRPC_AGENT_MODEL_CONTEXT_WINDOW_TOKENS", "not-a-number")

    with pytest.raises(ValueError, match="TRPC_AGENT_MODEL_CONTEXT_WINDOW_TOKENS"):
        AdvancedMemoryConfig()


def test_config_rejects_invalid_max_output_tokens_environment(monkeypatch: pytest.MonkeyPatch, ) -> None:
    """Reject invalid maximum output-token environment values."""
    monkeypatch.setenv("TRPC_AGENT_MAX_OUTPUT_TOKENS", "-1")

    with pytest.raises(ValueError, match="TRPC_AGENT_MAX_OUTPUT_TOKENS"):
        AdvancedMemoryConfig()


async def test_disabled_runtime_does_not_create_directories(tmp_path: Path) -> None:
    """Ensure disabled runtime initialization creates no directories."""
    runtime = AdvancedMemoryRuntime.create(AdvancedMemoryConfig(enabled=False, root_dir=tmp_path))

    initialized = await runtime.initialize()

    assert initialized is False
    assert not (tmp_path / "MEMORY").exists()
    assert not (tmp_path / "SESSION").exists()


async def test_enabled_runtime_creates_expected_layout(tmp_path: Path) -> None:
    """Ensure enabled initialization creates the expected empty layout."""
    runtime = AdvancedMemoryRuntime.create(_enabled_config(tmp_path))

    initialized = await runtime.initialize()

    assert initialized is True
    assert (tmp_path / "MEMORY" / "MEMORY.md").read_text() == ""
    assert (tmp_path / "SESSION").is_dir()


async def test_long_term_memory_writes_index_and_topics(tmp_path: Path) -> None:
    """Ensure the index and detail files share the MEMORY directory."""
    runtime = AdvancedMemoryRuntime.create(_enabled_config(tmp_path))
    await runtime.initialize()

    await runtime.long_term_memory.write_index([
        MemoryIndexEntry(
            name="认证方案",
            filename="auth.md",
            summary="记录项目采用的认证方案",
        ),
    ])
    topic_path = await runtime.long_term_memory.write_topic(
        "auth",
        MemoryDocument(
            name="认证方案",
            description="记录项目采用的认证方案",
            memory_type=MemoryType.PROJECT,
            content="# Authentication\n\nUse OAuth.",
        ),
    )

    assert await runtime.long_term_memory.read_index() == "- [认证方案]（auth.md）:记录项目采用的认证方案\n"
    topic_content = await runtime.long_term_memory.read_topic("auth")
    assert topic_content is not None
    assert topic_content.startswith("---\n"
                                    "name: 认证方案\n"
                                    "description: 记录项目采用的认证方案\n"
                                    "type: project\n"
                                    "updated_at: ")
    assert topic_content.endswith("---\n# Authentication\n\nUse OAuth.\n")
    assert parse_memory_updated_at(topic_content) is not None
    assert topic_path == tmp_path / "MEMORY" / "auth.md"
    assert await runtime.long_term_memory.list_topics() == [topic_path]


async def test_memory_index_is_truncated_when_read_over_line_limit(tmp_path: Path) -> None:
    """Ensure prompt reads respect the configured line limit without rejecting writes."""
    runtime = AdvancedMemoryRuntime.create(_enabled_config(tmp_path, memory_index_max_lines=2), )
    await runtime.initialize()

    await runtime.long_term_memory.write_index([
        MemoryIndexEntry(name="one", filename="one.md", summary="one"),
        MemoryIndexEntry(name="two", filename="two.md", summary="two"),
        MemoryIndexEntry(name="three", filename="three.md", summary="three"),
    ])

    assert (await runtime.long_term_memory.read_index()).splitlines() == [
        "- [one]（one.md）:one",
        "- [two]（two.md）:two",
    ]


async def test_session_memory_is_isolated_by_session_id(tmp_path: Path) -> None:
    """Ensure structured summaries for different sessions do not overlap."""
    runtime = AdvancedMemoryRuntime.create(_enabled_config(tmp_path))
    await runtime.initialize()

    first_document = SessionMemoryDocument(session_title="会话 A", current_state="A")
    second_document = SessionMemoryDocument(session_title="会话 B", current_state="B")
    first_path = await runtime.session_memory.write("session-a", first_document)
    second_path = await runtime.session_memory.write("session-b", second_document)

    assert first_path == tmp_path / "SESSION" / "session-a" / "session_memory.md"
    assert second_path == tmp_path / "SESSION" / "session-b" / "session_memory.md"
    first_content = await runtime.session_memory.read("session-a")
    second_content = await runtime.session_memory.read("session-b")
    assert first_content == first_document.to_markdown()
    assert second_content == second_document.to_markdown()
    assert first_content is not None
    assert all(f"# {section}" in first_content for section in SESSION_MEMORY_SECTIONS)


async def test_transcript_appends_jsonl_in_order(tmp_path: Path) -> None:
    """Ensure transcripts preserve order and payloads as JSONL."""
    runtime = AdvancedMemoryRuntime.create(_enabled_config(tmp_path))
    await runtime.initialize()

    transcript_path = await runtime.transcripts.append(
        "session-a",
        {
            "kind": "user",
            "payload": {
                "text": "你好"
            }
        },
    )
    await runtime.transcripts.append(
        "session-a",
        {
            "kind": "assistant",
            "payload": {
                "text": "你好"
            }
        },
    )

    records = await runtime.transcripts.read_all("session-a")
    raw_lines = transcript_path.read_text().splitlines()
    assert [record["kind"] for record in records] == ["user", "assistant"]
    assert records[0]["payload"] == {"text": "你好"}
    assert all("recorded_at" in record for record in records)
    assert len(raw_lines) == 2
    assert all(isinstance(json.loads(line), dict) for line in raw_lines)


async def test_transcript_append_unique_uses_persisted_ids(tmp_path: Path) -> None:
    """Ensure transcript de-duplication recognizes persisted event IDs."""
    first_runtime = AdvancedMemoryRuntime.create(_enabled_config(tmp_path))
    await first_runtime.initialize()
    await first_runtime.transcripts.append_unique(
        "session-a",
        {
            "kind": "event",
            "event_id": "event-1"
        },
        unique_key="event_id",
    )

    second_runtime = AdvancedMemoryRuntime.create(_enabled_config(tmp_path))
    _, appended = await second_runtime.transcripts.append_unique(
        "session-a",
        {
            "kind": "event",
            "event_id": "event-1"
        },
        unique_key="event_id",
    )

    assert appended is False
    assert len(await second_runtime.transcripts.read_all("session-a")) == 1


async def test_transcript_read_waits_for_in_progress_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure reads do not observe a partially written JSONL record."""
    runtime = AdvancedMemoryRuntime.create(_enabled_config(tmp_path))
    started = threading.Event()
    release = threading.Event()

    def slow_append(path: Path, serialized: str) -> None:
        """Pause under the write lock to simulate a partial write."""
        midpoint = len(serialized) // 2
        with path.open("a", encoding="utf-8") as transcript_file:
            transcript_file.write(serialized[:midpoint])
            transcript_file.flush()
            started.set()
            release.wait(timeout=2)
            transcript_file.write(serialized[midpoint:] + "\n")
            transcript_file.flush()

    monkeypatch.setattr(
        runtime.transcripts,
        "_append_serialized_unlocked",
        slow_append,
    )
    append_task = asyncio.create_task(runtime.transcripts.append("session-a", {"kind": "event"}))
    assert await asyncio.to_thread(started.wait, 2)
    read_task = asyncio.create_task(runtime.transcripts.read_all("session-a"))
    await asyncio.sleep(0.05)

    assert read_task.done() is False
    release.set()
    await append_task
    records = await read_task
    assert len(records) == 1
    assert records[0]["kind"] == "event"
    assert "recorded_at" in records[0]


async def test_memory_index_is_truncated_when_read_over_byte_budget(tmp_path: Path) -> None:
    """Ensure prompt reads respect the configured byte limit without rejecting writes."""
    config = AdvancedMemoryConfig(
        enabled=True,
        root_dir=tmp_path,
        memory_index_max_bytes=80,
    )
    runtime = AdvancedMemoryRuntime.create(config)
    entries = [MemoryIndexEntry(
        name="较长中文记忆名称",
        filename="memory.md",
        summary="这是一段会按 UTF-8 字节计数的较长中文概述",
    )]

    await runtime.long_term_memory.write_index(entries)

    assert await runtime.long_term_memory.read_index() == ""


def test_paths_sanitize_external_identifiers(tmp_path: Path) -> None:
    """Ensure session and topic identifiers cannot escape the root directory."""
    paths = AdvancedMemoryPaths(_enabled_config(tmp_path))

    session_path = paths.session_dir("../../session")
    topic_path = paths.memory_topic_path("../auth notes")
    assert session_path.parent == tmp_path / "SESSION"
    assert session_path.name.startswith("session-")
    assert topic_path.parent == tmp_path / "MEMORY"
    assert topic_path.name.startswith("auth_notes-")
    assert topic_path.suffix == ".md"
    assert paths.session_dir("session") != session_path
    assert paths.memory_topic_path("auth_notes") != topic_path


def test_config_rejects_nested_path_components(tmp_path: Path) -> None:
    """Ensure directory and file settings accept only safe path components."""
    with pytest.raises(ValueError, match="Invalid memory path component"):
        AdvancedMemoryConfig(root_dir=tmp_path, memory_dir_name="../MEMORY")


def test_memory_freshness_uses_expected_buckets() -> None:
    now = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)

    assert memory_freshness(now, now=now) == "today"
    assert memory_freshness(
        datetime(2026, 8, 17, 13, tzinfo=timezone.utc),
        now=now,
    ) == "today"
    assert memory_freshness(
        datetime(2026, 8, 17, 0, tzinfo=timezone.utc),
        now=now,
    ) == "yesterday"
    assert memory_freshness(
        datetime(2026, 8, 12, 12, tzinfo=timezone.utc),
        now=now,
    ) == "within 7 days"
    assert memory_freshness(
        datetime(2026, 7, 25, 12, tzinfo=timezone.utc),
        now=now,
    ) == "within 30 days"
    assert memory_freshness(
        datetime(2026, 7, 1, 12, tzinfo=timezone.utc),
        now=now,
    ) == "over 30 days"
    assert memory_freshness(None, now=now) == "unknown"


def test_parse_memory_updated_at_only_reads_frontmatter() -> None:
    content = ("---\n"
               "name: Example\n"
               "description: Example memory\n"
               "type: project\n"
               "updated_at: 2026-08-18T10:00:00+00:00\n"
               "---\n"
               "The body mentions updated_at: 1999-01-01T00:00:00+00:00.\n")

    assert parse_memory_updated_at(content) == datetime(
        2026,
        8,
        18,
        10,
        tzinfo=timezone.utc,
    )
