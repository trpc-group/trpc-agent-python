# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Tests for trpc_agent_sdk.types._memory.

Covers:
    - MemoryEntry: construction, optional fields
    - SearchMemoryResponse: default factory, population, serialisation
"""

from __future__ import annotations

from google.genai.types import Content, Part

from trpc_agent_sdk.types._memory import MemoryEntry, SearchMemoryResponse


# ---------------------------------------------------------------------------
# MemoryEntry
# ---------------------------------------------------------------------------
class TestMemoryEntry:
    """Tests for the MemoryEntry Pydantic model."""

    def _make_content(self, text: str = "hello") -> Content:
        return Content(parts=[Part(text=text)])

    def test_required_content(self):
        c = self._make_content()
        entry = MemoryEntry(content=c)
        assert entry.content.parts[0].text == "hello"

    def test_optional_author_default(self):
        entry = MemoryEntry(content=self._make_content())
        assert entry.author is None

    def test_optional_author_set(self):
        entry = MemoryEntry(content=self._make_content(), author="user_1")
        assert entry.author == "user_1"

    def test_optional_timestamp_default(self):
        entry = MemoryEntry(content=self._make_content())
        assert entry.timestamp is None

    def test_optional_timestamp_set(self):
        entry = MemoryEntry(
            content=self._make_content(),
            timestamp="2026-01-01T00:00:00Z",
        )
        assert entry.timestamp == "2026-01-01T00:00:00Z"

    def test_full_construction(self):
        entry = MemoryEntry(
            content=self._make_content("data"),
            author="agent",
            timestamp="2026-06-15T12:00:00Z",
        )
        assert entry.content.parts[0].text == "data"
        assert entry.author == "agent"
        assert entry.timestamp == "2026-06-15T12:00:00Z"

    def test_json_roundtrip(self):
        entry = MemoryEntry(
            content=self._make_content("round"),
            author="bot",
            timestamp="2026-03-01T00:00:00Z",
        )
        json_str = entry.model_dump_json()
        restored = MemoryEntry.model_validate_json(json_str)
        assert restored.author == "bot"
        assert restored.timestamp == "2026-03-01T00:00:00Z"


# ---------------------------------------------------------------------------
# SearchMemoryResponse
# ---------------------------------------------------------------------------
class TestSearchMemoryResponse:
    """Tests for the SearchMemoryResponse Pydantic model."""

    def _make_entry(self, text: str = "mem") -> MemoryEntry:
        return MemoryEntry(content=Content(parts=[Part(text=text)]))

    def test_default_empty(self):
        resp = SearchMemoryResponse()
        assert resp.memories == []

    def test_default_is_independent(self):
        r1 = SearchMemoryResponse()
        r2 = SearchMemoryResponse()
        r1.memories.append(self._make_entry())
        assert len(r2.memories) == 0

    def test_with_memories(self):
        entries = [self._make_entry("a"), self._make_entry("b")]
        resp = SearchMemoryResponse(memories=entries)
        assert len(resp.memories) == 2
        assert resp.memories[0].content.parts[0].text == "a"

    def test_json_roundtrip(self):
        entries = [self._make_entry("x")]
        resp = SearchMemoryResponse(memories=entries)
        json_str = resp.model_dump_json()
        restored = SearchMemoryResponse.model_validate_json(json_str)
        assert len(restored.memories) == 1
        assert restored.memories[0].content.parts[0].text == "x"
