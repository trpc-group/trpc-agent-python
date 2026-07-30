# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""MemPalace tools with MCP-compatible names."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from datetime import date
from datetime import datetime
from typing import Any
from typing import Optional
from typing_extensions import override

from mempalace.config import MempalaceConfig  # type: ignore[import-not-found]
from mempalace.config import sanitize_content  # type: ignore[import-not-found]
from mempalace.config import sanitize_name  # type: ignore[import-not-found]
from mempalace.knowledge_graph import DEFAULT_KG_PATH  # type: ignore[import-not-found]
from mempalace.knowledge_graph import KnowledgeGraph  # type: ignore[import-not-found]
from mempalace.palace import get_collection  # type: ignore[import-not-found]
from mempalace.searcher import search_memories  # type: ignore[import-not-found]

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

from ._base_tool import BaseTool

__all__ = [
    "MempalaceSearchTool",
    "MempalaceAddDrawerTool",
    "MempalaceDiaryWriteTool",
    "MempalaceDiaryReadTool",
    "MempalaceKGQueryTool",
    "MempalaceKGAddTool",
    "MempalaceKGInvalidateTool",
    "MempalaceKGTimelineTool",
]


def _string_schema(description: str) -> Schema:
    return Schema(type=Type.STRING, description=description)


def _integer_schema(description: str) -> Schema:
    return Schema(type=Type.INTEGER, description=description)


def _number_schema(description: str) -> Schema:
    return Schema(type=Type.NUMBER, description=description)


def _optional_str(args: dict[str, Any], key: str) -> Optional[str]:
    value = args.get(key)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _scope_name(value: str, field_name: str) -> str:
    """Normalize MemPalace scope-like names to avoid duplicate logical scopes."""
    value = sanitize_name(value, field_name)
    return sanitize_name(re.sub(r"[\s-]+", "_", value.lower()), field_name)


class _MempalaceBaseTool(BaseTool):
    """Base class for local MemPalace-backed tools."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        palace_path: Optional[str] = None,
        kg_path: Optional[str] = None,
        filters_name: Optional[list[str]] = None,
        filters: Optional[list[Any]] = None,
    ) -> None:
        super().__init__(name=name, description=description, filters_name=filters_name, filters=filters)
        self._palace_path = palace_path
        self._kg_path = kg_path

    def _get_palace_path(self) -> str:
        return self._palace_path or MempalaceConfig().palace_path

    def _get_collection(self, *, create: bool = False):
        config = MempalaceConfig()
        return get_collection(
            self._palace_path or config.palace_path,
            collection_name=config.collection_name,
            create=create,
        )

    def _get_knowledge_graph(self):
        kg_path = self._kg_path
        if kg_path is None and self._palace_path:
            kg_path = os.path.join(self._palace_path, "knowledge_graph.sqlite3")
        return KnowledgeGraph(db_path=kg_path or DEFAULT_KG_PATH)

    async def _run_in_thread(self, fn, *args, **kwargs):
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except ImportError as exc:
            return {
                "success": False,
                "error": f"MemPalace is not installed: {exc}. Install with `pip install mempalace`.",
            }
        except Exception as exc:  # pylint: disable=broad-except
            return {"success": False, "error": str(exc)}


class MempalaceSearchTool(_MempalaceBaseTool):
    """Search MemPalace semantic memory."""

    def __init__(self, palace_path: Optional[str] = None, **kwargs: Any) -> None:
        super().__init__(
            name="mempalace_search",
            description="Search MemPalace semantic memory. Returns verbatim drawer content with metadata.",
            palace_path=palace_path,
            filters_name=kwargs.pop("filters_name", None),
            filters=kwargs.pop("filters", None),
        )

    @override
    def _get_declaration(self) -> FunctionDeclaration | None:
        return FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "query": _string_schema("What to search for."),
                    "limit": _integer_schema("Maximum number of results. Default: 5."),
                    "wing": _string_schema("Optional wing/project/user scope filter."),
                    "room": _string_schema("Optional room/topic filter."),
                },
                required=["query"],
            ),
        )

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> dict:

        def _search():
            return search_memories(
                query=args["query"],
                palace_path=self._get_palace_path(),
                wing=_scope_name(wing, "wing") if (wing := _optional_str(args, "wing")) else None,
                room=_scope_name(room, "room") if (room := _optional_str(args, "room")) else None,
                n_results=int(args.get("limit") or 5),
            )

        return await self._run_in_thread(_search)


class MempalaceAddDrawerTool(_MempalaceBaseTool):
    """Add a verbatim drawer to MemPalace."""

    def __init__(self, palace_path: Optional[str] = None, added_by: str = "trpc-agent", **kwargs: Any) -> None:
        super().__init__(
            name="mempalace_add_drawer",
            description="File verbatim content into MemPalace under a wing and room.",
            palace_path=palace_path,
            filters_name=kwargs.pop("filters_name", None),
            filters=kwargs.pop("filters", None),
        )
        self._added_by = added_by

    @override
    def _get_declaration(self) -> FunctionDeclaration | None:
        return FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "wing": _string_schema("Wing/project/user scope to store under."),
                    "room": _string_schema("Room/topic to store under."),
                    "content": _string_schema("Verbatim content to store."),
                    "source_file": _string_schema("Optional source identifier."),
                },
                required=["wing", "room", "content"],
            ),
        )

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> dict:

        def _add_drawer():
            wing = _scope_name(args["wing"], "wing")
            room = _scope_name(args["room"], "room")
            content = sanitize_content(args["content"])
            source_file = _optional_str(args, "source_file") or ""
            col = self._get_collection(create=True)
            drawer_id = f"drawer_{wing}_{room}_{hashlib.sha256((wing + room + content).encode()).hexdigest()[:24]}"
            existing = col.get(ids=[drawer_id])
            if existing and existing.get("ids"):
                return {"success": True, "reason": "already_exists", "drawer_id": drawer_id}
            col.upsert(
                ids=[drawer_id],
                documents=[content],
                metadatas=[{
                    "wing": wing,
                    "room": room,
                    "source_file": source_file,
                    "chunk_index": 0,
                    "added_by": self._added_by,
                    "filed_at": datetime.now().isoformat(),
                }],
            )
            return {"success": True, "drawer_id": drawer_id, "wing": wing, "room": room}

        return await self._run_in_thread(_add_drawer)


class MempalaceDiaryWriteTool(_MempalaceBaseTool):
    """Write an agent diary entry."""

    def __init__(self, palace_path: Optional[str] = None, **kwargs: Any) -> None:
        super().__init__(
            name="mempalace_diary_write",
            description="Write an agent diary entry into MemPalace.",
            palace_path=palace_path,
            filters_name=kwargs.pop("filters_name", None),
            filters=kwargs.pop("filters", None),
        )

    @override
    def _get_declaration(self) -> FunctionDeclaration | None:
        return FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "agent_name": _string_schema("Agent name. Defaults to the current agent when omitted."),
                    "entry": _string_schema("Diary entry content."),
                    "topic": _string_schema("Topic tag. Default: general."),
                    "wing": _string_schema("Optional wing. Default: wing_<agent_name>."),
                },
                required=["entry"],
            ),
        )

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> dict:

        def _write():
            agent_name = _scope_name(_optional_str(args, "agent_name") or tool_context.agent_name, "agent_name")
            entry = sanitize_content(args["entry"])
            topic = _scope_name(_optional_str(args, "topic") or "general", "topic")
            wing = _optional_str(args, "wing")
            wing = _scope_name(wing, "wing") if wing else f"wing_{agent_name}"
            now = datetime.now()
            entry_id = (f"diary_{wing}_{now.strftime('%Y%m%d_%H%M%S%f')}_"
                        f"{hashlib.sha256(entry.encode()).hexdigest()[:12]}")
            col = self._get_collection(create=True)
            col.add(
                ids=[entry_id],
                documents=[entry],
                metadatas=[{
                    "wing": wing,
                    "room": "diary",
                    "hall": "hall_diary",
                    "topic": topic,
                    "type": "diary_entry",
                    "agent": agent_name,
                    "filed_at": now.isoformat(),
                    "date": now.strftime("%Y-%m-%d"),
                }],
            )
            return {"success": True, "entry_id": entry_id, "agent": agent_name, "topic": topic}

        return await self._run_in_thread(_write)


class MempalaceDiaryReadTool(_MempalaceBaseTool):
    """Read recent agent diary entries."""

    def __init__(self, palace_path: Optional[str] = None, **kwargs: Any) -> None:
        super().__init__(
            name="mempalace_diary_read",
            description="Read recent MemPalace diary entries for an agent.",
            palace_path=palace_path,
            filters_name=kwargs.pop("filters_name", None),
            filters=kwargs.pop("filters", None),
        )

    @override
    def _get_declaration(self) -> FunctionDeclaration | None:
        return FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "agent_name": _string_schema("Agent name. Defaults to the current agent when omitted."),
                    "last_n": _integer_schema("Number of recent entries. Default: 10."),
                    "wing": _string_schema("Optional wing filter."),
                },
            ),
        )

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> dict:

        def _read():
            agent_name = _scope_name(_optional_str(args, "agent_name") or tool_context.agent_name, "agent_name")
            wing = _optional_str(args, "wing")
            if wing:
                wing = _scope_name(wing, "wing")
            last_n = max(1, min(int(args.get("last_n") or 10), 100))
            col = self._get_collection(create=False)
            conditions = [{"room": "diary"}, {"agent": agent_name}]
            if wing:
                conditions.insert(0, {"wing": wing})
            results = col.get(where={"$and": conditions}, include=["documents", "metadatas"], limit=10000)
            if not results.get("ids"):
                return {"agent": agent_name, "entries": [], "message": "No diary entries yet."}
            entries = []
            for doc, meta in zip(results.get("documents", []), results.get("metadatas", [])):
                meta = meta or {}
                entries.append({
                    "date": meta.get("date", ""),
                    "timestamp": meta.get("filed_at", ""),
                    "topic": meta.get("topic", ""),
                    "content": doc,
                })
            entries.sort(key=lambda item: item["timestamp"], reverse=True)
            entries = entries[:last_n]
            return {"agent": agent_name, "entries": entries, "total": len(results["ids"]), "showing": len(entries)}

        return await self._run_in_thread(_read)


class MempalaceKGQueryTool(_MempalaceBaseTool):
    """Query MemPalace knowledge graph relationships."""

    def __init__(self, palace_path: Optional[str] = None, kg_path: Optional[str] = None, **kwargs: Any) -> None:
        super().__init__(
            name="mempalace_kg_query",
            description="Query the MemPalace knowledge graph for an entity's relationships.",
            palace_path=palace_path,
            kg_path=kg_path,
            filters_name=kwargs.pop("filters_name", None),
            filters=kwargs.pop("filters", None),
        )

    @override
    def _get_declaration(self) -> FunctionDeclaration | None:
        return FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "entity": _string_schema("Entity to query."),
                    "as_of": _string_schema("Optional date filter in YYYY-MM-DD."),
                    "direction": _string_schema("outgoing, incoming, or both. Default: both."),
                },
                required=["entity"],
            ),
        )

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> dict:

        def _query():
            direction = _optional_str(args, "direction") or "both"
            if direction not in ("outgoing", "incoming", "both"):
                return {"error": "direction must be 'outgoing', 'incoming', or 'both'"}
            kg = self._get_knowledge_graph()
            entity = args["entity"]
            as_of = _optional_str(args, "as_of")
            facts = kg.query_entity(entity, as_of=as_of, direction=direction)
            return {"entity": entity, "as_of": as_of, "facts": facts, "count": len(facts)}

        return await self._run_in_thread(_query)


class MempalaceKGAddTool(_MempalaceBaseTool):
    """Add a fact to the MemPalace knowledge graph."""

    def __init__(self, palace_path: Optional[str] = None, kg_path: Optional[str] = None, **kwargs: Any) -> None:
        super().__init__(
            name="mempalace_kg_add",
            description="Add a relationship fact to the MemPalace knowledge graph.",
            palace_path=palace_path,
            kg_path=kg_path,
            filters_name=kwargs.pop("filters_name", None),
            filters=kwargs.pop("filters", None),
        )

    @override
    def _get_declaration(self) -> FunctionDeclaration | None:
        return FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "subject": _string_schema("Subject entity."),
                    "predicate": _string_schema("Relationship type."),
                    "object": _string_schema("Object entity."),
                    "valid_from": _string_schema("Optional start date in YYYY-MM-DD."),
                    "valid_to": _string_schema("Optional end date in YYYY-MM-DD."),
                    "confidence": _number_schema("Optional confidence score 0.0-1.0."),
                    "source_file": _string_schema("Optional provenance source file."),
                    "source_drawer_id": _string_schema("Optional provenance drawer ID."),
                },
                required=["subject", "predicate", "object"],
            ),
        )

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> dict:

        def _add():
            kg = self._get_knowledge_graph()
            triple_id = kg.add_triple(
                args["subject"],
                args["predicate"],
                args["object"],
                valid_from=_optional_str(args, "valid_from"),
                valid_to=_optional_str(args, "valid_to"),
                confidence=float(args.get("confidence") or 1.0),
                source_file=_optional_str(args, "source_file"),
                source_drawer_id=_optional_str(args, "source_drawer_id"),
            )
            return {
                "success": True,
                "triple_id": triple_id,
                "fact": f"{args['subject']} -> {args['predicate']} -> {args['object']}",
            }

        return await self._run_in_thread(_add)


class MempalaceKGInvalidateTool(_MempalaceBaseTool):
    """Invalidate a current MemPalace knowledge graph fact."""

    def __init__(self, palace_path: Optional[str] = None, kg_path: Optional[str] = None, **kwargs: Any) -> None:
        super().__init__(
            name="mempalace_kg_invalidate",
            description="Mark a MemPalace knowledge graph fact as no longer current.",
            palace_path=palace_path,
            kg_path=kg_path,
            filters_name=kwargs.pop("filters_name", None),
            filters=kwargs.pop("filters", None),
        )

    @override
    def _get_declaration(self) -> FunctionDeclaration | None:
        return FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "subject": _string_schema("Subject entity."),
                    "predicate": _string_schema("Relationship type."),
                    "object": _string_schema("Object entity."),
                    "ended": _string_schema("Optional end date in YYYY-MM-DD. Default: today."),
                },
                required=["subject", "predicate", "object"],
            ),
        )

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> dict:

        def _invalidate():
            ended = _optional_str(args, "ended") or date.today().isoformat()
            kg = self._get_knowledge_graph()
            kg.invalidate(args["subject"], args["predicate"], args["object"], ended=ended)
            return {
                "success": True,
                "fact": f"{args['subject']} -> {args['predicate']} -> {args['object']}",
                "ended": ended,
            }

        return await self._run_in_thread(_invalidate)


class MempalaceKGTimelineTool(_MempalaceBaseTool):
    """Read the MemPalace knowledge graph timeline."""

    def __init__(self, palace_path: Optional[str] = None, kg_path: Optional[str] = None, **kwargs: Any) -> None:
        super().__init__(
            name="mempalace_kg_timeline",
            description="Get chronological MemPalace knowledge graph facts, optionally for one entity.",
            palace_path=palace_path,
            kg_path=kg_path,
            filters_name=kwargs.pop("filters_name", None),
            filters=kwargs.pop("filters", None),
        )

    @override
    def _get_declaration(self) -> FunctionDeclaration | None:
        return FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "entity": _string_schema("Optional entity to filter timeline for."),
                },
            ),
        )

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> dict:

        def _timeline():
            entity = _optional_str(args, "entity")
            kg = self._get_knowledge_graph()
            timeline = kg.timeline(entity)
            return {"entity": entity or "all", "timeline": timeline, "count": len(timeline)}

        return await self._run_in_thread(_timeline)
