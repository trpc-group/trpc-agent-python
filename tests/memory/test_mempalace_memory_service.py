# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.memory.mempalace_memory_service."""

from __future__ import annotations

import time
from typing import Optional

from trpc_agent_sdk.abc import MemoryServiceConfig
from trpc_agent_sdk.context import new_agent_context
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.memory.mempalace_memory_service import MempalaceMemoryService
from trpc_agent_sdk.memory.mempalace_memory_service import get_mempalace_filters
from trpc_agent_sdk.memory.mempalace_memory_service import set_mempalace_filters
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.types import SearchMemoryResponse


def _make_config() -> MemoryServiceConfig:
    cfg = MemoryServiceConfig(enabled=True)
    cfg.clean_ttl_config()
    return cfg


def _make_event(text: str = "hello world", author: str = "user", event_id: str = "") -> Event:
    return Event(
        id=event_id or Event.new_id(),
        invocation_id="inv-1",
        author=author,
        content=Content(parts=[Part.from_text(text=text)]),
        timestamp=time.time(),
    )


def _make_session(
    events: Optional[list[Event]] = None,
    save_key: str = "app/user1",
    session_id: str = "session-1",
) -> Session:
    return Session(
        id=session_id,
        app_name="app",
        user_id="user1",
        save_key=save_key,
        events=events or [],
    )


class TestMempalaceMetadata:
    def test_set_and_get_filters(self):
        ctx = new_agent_context()
        set_mempalace_filters(ctx, {"wing": "my_app", "room": "decisions"})

        assert get_mempalace_filters(ctx) == {"wing": "my_app", "room": "decisions"}

    def test_scope_names_are_normalized(self):
        svc = MempalaceMemoryService(memory_service_config=_make_config(), wing="My-App Name", room="User Room")

        assert svc._resolve_wing("fallback/user", {}) == "my_app_name"
        assert svc._resolve_room({}) == "user_room"
        assert svc._resolve_wing("fallback/user", {"wing": "Project-Wing"}) == "project_wing"
        assert svc._resolve_room({"room": "Long Term"}) == "long_term"


class TestMempalaceStoreSession:
    async def test_store_session_maps_session_to_wing_and_room(self, monkeypatch):
        calls = []

        def fake_store(session, events_to_store, wing, room):
            calls.append((session, events_to_store, wing, room))
            return {drawer_id for _, _, drawer_id in events_to_store}

        svc = MempalaceMemoryService(memory_service_config=_make_config(), wing="My App", room="Decisions")
        monkeypatch.setattr(svc, "_store_events", fake_store)
        session = _make_session(events=[_make_event("remember this", event_id="e1")])

        await svc.store_session(session)
        await svc.close()

        assert len(calls) == 1
        _, events_to_store, wing, room = calls[0]
        assert wing == "my_app"
        assert room == "decisions"
        assert events_to_store[0][0].id == "e1"
        assert "remember this" in events_to_store[0][1]
        assert events_to_store[0][2] in svc._stored_drawer_ids

    async def test_store_session_skips_invisible_events(self, monkeypatch):
        calls = []

        def fake_store(session, events_to_store, wing, room):
            calls.append(events_to_store)
            return {drawer_id for _, _, drawer_id in events_to_store}

        visible_event = _make_event("visible")
        invisible_event = _make_event("hidden")
        invisible_event.set_model_visible(False)
        svc = MempalaceMemoryService(memory_service_config=_make_config())
        monkeypatch.setattr(svc, "_store_events", fake_store)

        await svc.store_session(_make_session(events=[visible_event, invisible_event]))
        await svc.close()

        assert len(calls) == 1
        assert len(calls[0]) == 1
        assert "visible" in calls[0][0][1]

    async def test_store_session_is_incremental(self, monkeypatch):
        calls = []

        def fake_store(session, events_to_store, wing, room):
            calls.append(events_to_store)
            return {drawer_id for _, _, drawer_id in events_to_store}

        event1 = _make_event("first", event_id="e1")
        event2 = _make_event("second", event_id="e2")
        svc = MempalaceMemoryService(memory_service_config=_make_config())
        monkeypatch.setattr(svc, "_store_events", fake_store)

        session = _make_session(events=[event1])
        await svc.store_session(session)
        await svc.close()

        session.events.append(event2)
        await svc.store_session(session)
        await svc.close()

        assert len(calls) == 2
        assert [event.id for event, _, _ in calls[0]] == ["e1"]
        assert [event.id for event, _, _ in calls[1]] == ["e2"]


class TestMempalaceSearchMemory:
    async def test_search_memory_converts_results(self, monkeypatch):
        async def fake_search(query, wing, room, limit):
            return {
                "results": [{
                    "text": "stored memory",
                    "metadata": {
                        "author": "assistant",
                        "timestamp": "2026-01-01T00:00:00",
                    },
                }]
            }

        svc = MempalaceMemoryService(memory_service_config=_make_config(), wing="my_app")
        monkeypatch.setattr(svc, "_search", fake_search)

        result = await svc.search_memory("app/user1", "memory", limit=1)

        assert isinstance(result, SearchMemoryResponse)
        assert len(result.memories) == 1
        assert result.memories[0].content.parts[0].text == "stored memory"
        assert result.memories[0].content.role == "user"
        assert result.memories[0].author == "assistant"
