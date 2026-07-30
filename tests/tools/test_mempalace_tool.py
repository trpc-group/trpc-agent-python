# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for MemPalace tools."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from trpc_agent_sdk.context import InvocationContext
import trpc_agent_sdk.tools.mempalace_tool as mempalace_tool
from trpc_agent_sdk.tools.mempalace_tool import MempalaceAddDrawerTool
from trpc_agent_sdk.tools.mempalace_tool import MempalaceDiaryReadTool
from trpc_agent_sdk.tools.mempalace_tool import MempalaceDiaryWriteTool
from trpc_agent_sdk.tools.mempalace_tool import MempalaceKGAddTool
from trpc_agent_sdk.tools.mempalace_tool import MempalaceKGInvalidateTool
from trpc_agent_sdk.tools.mempalace_tool import MempalaceKGQueryTool
from trpc_agent_sdk.tools.mempalace_tool import MempalaceKGTimelineTool
from trpc_agent_sdk.tools.mempalace_tool import MempalaceSearchTool
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Type


def _ctx() -> MagicMock:
    ctx = MagicMock(spec=InvocationContext)
    ctx.agent_name = "test_agent"
    return ctx


class FakeCollection:
    def __init__(self, get_result: dict | None = None) -> None:
        self.get_result = get_result or {"ids": []}
        self.get_calls = []
        self.upserts = []
        self.adds = []

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        return self.get_result

    def upsert(self, **kwargs):
        self.upserts.append(kwargs)

    def add(self, **kwargs):
        self.adds.append(kwargs)


class FakeConfig:
    palace_path = "/default/palace"
    collection_name = "default_collection"


class TestMempalaceBaseTool:
    def test_get_palace_path_uses_config_default(self, monkeypatch):
        monkeypatch.setattr(mempalace_tool, "MempalaceConfig", lambda: FakeConfig())

        assert MempalaceSearchTool()._get_palace_path() == "/default/palace"

    def test_get_collection_uses_config_and_create_flag(self, monkeypatch):
        calls = []
        collection = FakeCollection()

        def fake_get_collection(palace_path, collection_name, create):
            calls.append((palace_path, collection_name, create))
            return collection

        monkeypatch.setattr(mempalace_tool, "MempalaceConfig", lambda: FakeConfig())
        monkeypatch.setattr(mempalace_tool, "get_collection", fake_get_collection)

        assert MempalaceSearchTool()._get_collection(create=True) is collection
        assert calls == [("/default/palace", "default_collection", True)]

    def test_get_knowledge_graph_uses_palace_path_default(self, monkeypatch, tmp_path):
        calls = []

        def fake_knowledge_graph(db_path):
            calls.append(db_path)
            return "kg"

        monkeypatch.setattr(mempalace_tool, "KnowledgeGraph", fake_knowledge_graph)
        tool = MempalaceKGQueryTool(palace_path=str(tmp_path))

        assert tool._get_knowledge_graph() == "kg"
        assert calls == [str(tmp_path / "knowledge_graph.sqlite3")]

    async def test_run_in_thread_handles_import_error(self):
        tool = MempalaceSearchTool()

        def raise_import_error():
            raise ImportError("missing mempalace")

        result = await tool._run_in_thread(raise_import_error)

        assert result["success"] is False
        assert "MemPalace is not installed" in result["error"]

    async def test_run_in_thread_handles_generic_error(self):
        tool = MempalaceSearchTool()

        def raise_error():
            raise RuntimeError("boom")

        assert await tool._run_in_thread(raise_error) == {"success": False, "error": "boom"}


class TestMempalaceToolDeclarations:
    @pytest.mark.parametrize(
        ("tool", "name"),
        [
            (MempalaceSearchTool(), "mempalace_search"),
            (MempalaceAddDrawerTool(), "mempalace_add_drawer"),
            (MempalaceDiaryWriteTool(), "mempalace_diary_write"),
            (MempalaceDiaryReadTool(), "mempalace_diary_read"),
            (MempalaceKGQueryTool(), "mempalace_kg_query"),
            (MempalaceKGAddTool(), "mempalace_kg_add"),
            (MempalaceKGInvalidateTool(), "mempalace_kg_invalidate"),
            (MempalaceKGTimelineTool(), "mempalace_kg_timeline"),
        ],
    )
    def test_declaration(self, tool, name):
        decl = tool._get_declaration()

        assert isinstance(decl, FunctionDeclaration)
        assert decl.name == name
        assert decl.parameters.type == Type.OBJECT


class TestMempalaceSearchTool:
    async def test_run_searches_with_filters(self, monkeypatch):
        calls = []

        def fake_search_memories(**kwargs):
            calls.append(kwargs)
            return {"query": kwargs["query"], "results": []}

        monkeypatch.setattr(mempalace_tool, "search_memories", fake_search_memories)
        tool = MempalaceSearchTool(palace_path="/custom/palace")

        result = await tool._run_async_impl(
            tool_context=_ctx(),
            args={"query": "hello", "limit": "3", "wing": "  wing-a  ", "room": "  "},
        )

        assert result == {"query": "hello", "results": []}
        assert calls == [{
            "query": "hello",
            "palace_path": "/custom/palace",
            "wing": "wing_a",
            "room": None,
            "n_results": 3,
        }]


class TestMempalaceAddDrawerTool:
    async def test_add_drawer_upserts_new_drawer(self, monkeypatch):
        collection = FakeCollection(get_result={"ids": []})
        monkeypatch.setattr(mempalace_tool, "get_collection", lambda *args, **kwargs: collection)

        result = await MempalaceAddDrawerTool(palace_path="/p", added_by="tests")._run_async_impl(
            tool_context=_ctx(),
            args={
                "wing": "Personal Assistant",
                "room": "User Profile",
                "content": "User's name is Alice.",
                "source_file": "demo.txt",
            },
        )

        assert result["success"] is True
        assert result["wing"] == "personal_assistant"
        assert result["room"] == "user_profile"
        assert collection.upserts[0]["documents"] == ["User's name is Alice."]
        assert collection.upserts[0]["metadatas"][0]["added_by"] == "tests"
        assert collection.upserts[0]["metadatas"][0]["source_file"] == "demo.txt"

    async def test_add_drawer_returns_existing_drawer(self, monkeypatch):
        collection = FakeCollection(get_result={"ids": ["existing-id"]})
        monkeypatch.setattr(mempalace_tool, "get_collection", lambda *args, **kwargs: collection)

        result = await MempalaceAddDrawerTool(palace_path="/p")._run_async_impl(
            tool_context=_ctx(),
            args={"wing": "wing", "room": "room", "content": "content"},
        )

        assert result["success"] is True
        assert result["reason"] == "already_exists"
        assert collection.upserts == []


class TestMempalaceDiaryTools:
    async def test_diary_write_uses_default_agent_scope(self, monkeypatch):
        collection = FakeCollection()
        monkeypatch.setattr(mempalace_tool, "get_collection", lambda *args, **kwargs: collection)

        result = await MempalaceDiaryWriteTool(palace_path="/p")._run_async_impl(
            tool_context=_ctx(),
            args={"entry": "Finished the MemPalace example.", "topic": "daily notes"},
        )

        assert result["success"] is True
        assert result["agent"] == "test_agent"
        assert result["topic"] == "daily_notes"
        assert collection.adds[0]["documents"] == ["Finished the MemPalace example."]
        assert collection.adds[0]["metadatas"][0]["wing"] == "wing_test_agent"
        assert collection.adds[0]["metadatas"][0]["room"] == "diary"

    async def test_diary_write_uses_explicit_agent_and_wing(self, monkeypatch):
        collection = FakeCollection()
        monkeypatch.setattr(mempalace_tool, "get_collection", lambda *args, **kwargs: collection)

        result = await MempalaceDiaryWriteTool(palace_path="/p")._run_async_impl(
            tool_context=_ctx(),
            args={"agent_name": "Research Bot", "entry": "entry", "wing": "Project Wing"},
        )

        assert result["agent"] == "research_bot"
        assert collection.adds[0]["metadatas"][0]["wing"] == "project_wing"

    async def test_diary_read_returns_message_when_empty(self, monkeypatch):
        collection = FakeCollection(get_result={"ids": []})
        monkeypatch.setattr(mempalace_tool, "get_collection", lambda *args, **kwargs: collection)

        result = await MempalaceDiaryReadTool(palace_path="/p")._run_async_impl(tool_context=_ctx(), args={})

        assert result["agent"] == "test_agent"
        assert result["entries"] == []
        assert result["message"] == "No diary entries yet."

    async def test_diary_read_sorts_and_limits_entries(self, monkeypatch):
        collection = FakeCollection(get_result={
            "ids": ["old", "new"],
            "documents": ["old content", "new content"],
            "metadatas": [
                {"date": "2026-01-01", "filed_at": "2026-01-01T00:00:00", "topic": "old"},
                {"date": "2026-01-02", "filed_at": "2026-01-02T00:00:00", "topic": "new"},
            ],
        })
        monkeypatch.setattr(mempalace_tool, "get_collection", lambda *args, **kwargs: collection)

        result = await MempalaceDiaryReadTool(palace_path="/p")._run_async_impl(
            tool_context=_ctx(),
            args={"last_n": 1, "wing": "Project Wing"},
        )

        assert result["total"] == 2
        assert result["showing"] == 1
        assert result["entries"][0]["content"] == "new content"
        assert collection.get_calls[0]["where"]["$and"][0] == {"wing": "project_wing"}


class TestMempalaceKGTools:
    def test_kg_tool_explicit_path(self, monkeypatch):
        calls = []

        def fake_knowledge_graph(db_path):
            calls.append(db_path)
            return MagicMock()

        monkeypatch.setattr(mempalace_tool, "KnowledgeGraph", fake_knowledge_graph)

        MempalaceKGQueryTool(kg_path="/explicit/kg.sqlite3")._get_knowledge_graph()

        assert calls == ["/explicit/kg.sqlite3"]

    async def test_kg_query(self, monkeypatch):
        kg = MagicMock()
        kg.query_entity.return_value = [{"subject": "Alice", "predicate": "works_on", "object": "TRPC"}]
        tool = MempalaceKGQueryTool()
        monkeypatch.setattr(tool, "_get_knowledge_graph", lambda: kg)

        result = await tool._run_async_impl(
            tool_context=_ctx(),
            args={"entity": "Alice", "direction": "both"},
        )

        assert result["count"] == 1
        kg.query_entity.assert_called_once_with("Alice", as_of=None, direction="both")

    async def test_kg_query_rejects_invalid_direction(self):
        result = await MempalaceKGQueryTool()._run_async_impl(
            tool_context=_ctx(),
            args={"entity": "Alice", "direction": "sideways"},
        )

        assert result == {"error": "direction must be 'outgoing', 'incoming', or 'both'"}

    async def test_kg_add(self, monkeypatch):
        kg = MagicMock()
        kg.add_triple.return_value = "triple-1"
        tool = MempalaceKGAddTool()
        monkeypatch.setattr(tool, "_get_knowledge_graph", lambda: kg)

        result = await tool._run_async_impl(
            tool_context=_ctx(),
            args={"subject": "Alice", "predicate": "works_on", "object": "TRPC"},
        )

        assert result["success"] is True
        assert result["triple_id"] == "triple-1"

    async def test_kg_add_passes_optional_fields(self, monkeypatch):
        kg = MagicMock()
        kg.add_triple.return_value = "triple-2"
        tool = MempalaceKGAddTool()
        monkeypatch.setattr(tool, "_get_knowledge_graph", lambda: kg)

        await tool._run_async_impl(
            tool_context=_ctx(),
            args={
                "subject": "Alice",
                "predicate": "works_on",
                "object": "TRPC",
                "valid_from": "2026-01-01",
                "valid_to": "2026-12-31",
                "confidence": "0.7",
                "source_file": "source.md",
                "source_drawer_id": "drawer-1",
            },
        )

        kg.add_triple.assert_called_once_with(
            "Alice",
            "works_on",
            "TRPC",
            valid_from="2026-01-01",
            valid_to="2026-12-31",
            confidence=0.7,
            source_file="source.md",
            source_drawer_id="drawer-1",
        )

    async def test_kg_invalidate(self, monkeypatch):
        kg = MagicMock()
        tool = MempalaceKGInvalidateTool()
        monkeypatch.setattr(tool, "_get_knowledge_graph", lambda: kg)

        result = await tool._run_async_impl(
            tool_context=_ctx(),
            args={"subject": "Alice", "predicate": "works_on", "object": "OldProject", "ended": "2026-05-07"},
        )

        assert result["success"] is True
        kg.invalidate.assert_called_once_with("Alice", "works_on", "OldProject", ended="2026-05-07")

    async def test_kg_invalidate_defaults_to_today(self, monkeypatch):
        kg = MagicMock()
        tool = MempalaceKGInvalidateTool()
        monkeypatch.setattr(tool, "_get_knowledge_graph", lambda: kg)
        real_date = mempalace_tool.date

        class FakeDate:
            @classmethod
            def today(cls):
                return real_date(2026, 5, 9)

        monkeypatch.setattr(mempalace_tool, "date", FakeDate)

        result = await tool._run_async_impl(
            tool_context=_ctx(),
            args={"subject": "Alice", "predicate": "works_on", "object": "OldProject"},
        )

        assert result["ended"] == "2026-05-09"
        kg.invalidate.assert_called_once_with("Alice", "works_on", "OldProject", ended="2026-05-09")

    async def test_kg_timeline(self, monkeypatch):
        kg = MagicMock()
        kg.timeline.return_value = [{"subject": "Alice"}]
        tool = MempalaceKGTimelineTool()
        monkeypatch.setattr(tool, "_get_knowledge_graph", lambda: kg)

        result = await tool._run_async_impl(tool_context=_ctx(), args={"entity": "Alice"})

        assert result["count"] == 1
        kg.timeline.assert_called_once_with("Alice")

    async def test_kg_timeline_defaults_to_all(self, monkeypatch):
        kg = MagicMock()
        kg.timeline.return_value = []
        tool = MempalaceKGTimelineTool()
        monkeypatch.setattr(tool, "_get_knowledge_graph", lambda: kg)

        result = await tool._run_async_impl(tool_context=_ctx(), args={})

        assert result == {"entity": "all", "timeline": [], "count": 0}
        kg.timeline.assert_called_once_with(None)
