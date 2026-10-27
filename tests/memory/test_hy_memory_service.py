# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the Hy-Memory service adapter."""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.memory.hy_memory_service import get_hy_memory_options
from trpc_agent_sdk.memory.hy_memory_service import HyMemoryService
from trpc_agent_sdk.memory.hy_memory_service import HyMemoryServiceConfig
from trpc_agent_sdk.memory.hy_memory_service import set_hy_memory_options
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


def _config(**kwargs: Any) -> HyMemoryServiceConfig:
    return HyMemoryServiceConfig(enabled=True, **kwargs)


def _event(
    text: str,
    *,
    event_id: str,
    author: str = "user",
) -> Event:
    return Event(
        id=event_id,
        invocation_id="inv-1",
        author=author,
        content=Content(parts=[Part.from_text(text=text)]),
        timestamp=time.time(),
    )


def _tool_events() -> list[Event]:
    call_part = Part.from_function_call(
        name="apply_patch",
        args={"path": "src/search.py"},
    )
    call_part.function_call.id = "call-1"
    response_part = Part.from_function_response(
        name="apply_patch",
        response={"result": "Patch applied"},
    )
    response_part.function_response.id = "call-1"
    return [
        _event(
            "Fix the timeout.",
            event_id="event-user",
        ),
        Event(
            id="event-call",
            invocation_id="inv-1",
            author="agent",
            content=Content(role="model", parts=[call_part]),
        ),
        Event(
            id="event-response",
            invocation_id="inv-1",
            author="agent",
            content=Content(role="user", parts=[response_part]),
        ),
        _event(
            "The timeout is fixed.",
            event_id="event-answer",
            author="agent",
        ),
    ]


def _session(events: list[Event]) -> Session:
    return Session(
        id="session-1",
        app_name="coding-agent",
        user_id="user-1",
        save_key="coding-agent/user-1",
        events=events,
    )


def _context(options: dict[str, Any]) -> MagicMock:
    context = MagicMock(spec=AgentContext)
    context.get_metadata.return_value = options
    return context


class TestHyMemoryConfiguration:

    def test_default_scene_uses_chat_without_agentic_routing(self):
        config = _config()

        assert config.scene == "chat"
        assert config.search_mode == "default"

    def test_options_round_trip(self):
        context = MagicMock(spec=AgentContext)
        metadata: dict[str, Any] = {}
        context.with_metadata.side_effect = (lambda key, value: metadata.update({key: value}))
        context.get_metadata.side_effect = (lambda key, default=None: metadata.get(key, default))

        options = {"scene": "coding", "workspace_id": "repo"}
        set_hy_memory_options(context, options)
        assert get_hy_memory_options(context) == options

    def test_missing_optional_dependency_has_actionable_error(self):
        with patch.dict(sys.modules, {"hy_memory": None}):
            with pytest.raises(
                    ImportError,
                    match=r"trpc-agent-py\[hy-memory\]",
            ):
                HyMemoryService(_config())


class TestHyMemoryStore:

    def test_event_conversion_excludes_thought_content(self):
        thought = Part.from_text(text="private reasoning")
        thought.thought = True
        visible = Part.from_text(text="visible answer")
        event = Event(
            id="event-1",
            invocation_id="inv-1",
            author="agent",
            content=Content(parts=[thought, visible]),
        )

        assert HyMemoryService._events_to_messages([event]) == [{
            "role": "assistant",
            "content": "visible answer",
        }]

    async def test_store_preserves_tool_trace_and_deduplicates(self):
        client = MagicMock()
        client.async_add = AsyncMock(return_value={"success": True})
        service = HyMemoryService(_config(), client=client)
        session = _session(_tool_events())
        context = _context({
            "scene": "coding",
            "workspace_id": "github.com/example/service",
            "branch": "fix/timeout",
            "source": "cursor",
            "meta": {
                "tenant": "tenant-1"
            },
        })

        await service.store_session(session, context)
        await service.store_session(session, context)

        client.async_add.assert_awaited_once()
        _, kwargs = client.async_add.await_args
        messages = client.async_add.await_args.args[0]
        assert messages == [
            {
                "role": "user",
                "content": "Fix the timeout.",
            },
            {
                "role":
                "assistant",
                "content":
                "",
                "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "apply_patch",
                        "arguments": '{"path": "src/search.py"}',
                    },
                }],
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "name": "apply_patch",
                "content": '{"result": "Patch applied"}',
            },
            {
                "role": "assistant",
                "content": "The timeout is fixed.",
            },
        ]
        assert kwargs["user_id"] == "user-1"
        assert kwargs["agent_id"] == "coding-agent"
        assert kwargs["session_id"] == "session-1"
        assert kwargs["scene"] == "coding"
        assert kwargs["workspace_id"] == "github.com/example/service"
        assert kwargs["branch"] == "fix/timeout"
        assert kwargs["source"] == "cursor"
        assert kwargs["request_id"].startswith("trpc-agent-")
        assert kwargs["meta"]["tenant"] == "tenant-1"
        assert kwargs["meta"]["event_ids"] == [
            "event-user",
            "event-call",
            "event-response",
            "event-answer",
        ]

    async def test_failed_write_is_retried(self):
        client = MagicMock()
        client.async_add = AsyncMock(side_effect=[
            {
                "success": False,
                "error": "failed"
            },
            {
                "success": True
            },
        ], )
        service = HyMemoryService(_config(), client=client)
        session = _session([_event("hello", event_id="event-1")])

        await service.store_session(session)
        await service.store_session(session)

        assert client.async_add.await_count == 2
        assert (client.async_add.await_args_list[0].kwargs["request_id"] ==
                client.async_add.await_args_list[1].kwargs["request_id"])

    def test_shared_service_serializes_writes_across_event_loops(self):
        first_call_started = threading.Event()
        second_store_started = threading.Event()

        class BlockingClient:

            def __init__(self):
                self.calls: list[dict[str, Any]] = []
                self.active_calls = 0
                self.max_active_calls = 0
                self.state_lock = threading.Lock()

            async def async_add(self, _messages, **kwargs):
                with self.state_lock:
                    self.calls.append(kwargs)
                    call_number = len(self.calls)
                    self.active_calls += 1
                    self.max_active_calls = max(
                        self.max_active_calls,
                        self.active_calls,
                    )
                try:
                    if call_number == 1:
                        first_call_started.set()
                        second_started = await asyncio.to_thread(
                            second_store_started.wait,
                            1,
                        )
                        assert second_started
                        await asyncio.sleep(0.05)
                    return {"success": True}
                finally:
                    with self.state_lock:
                        self.active_calls -= 1

        client = BlockingClient()
        service = HyMemoryService(_config(), client=client)
        first_session = _session([_event("first", event_id="event-1")])
        second_session = _session([
            _event("first", event_id="event-1"),
            _event("second", event_id="event-2"),
        ])

        async def store_with_timeout(
            session: Session,
            started: threading.Event | None = None,
        ) -> None:
            if started is not None:
                started.set()
            await asyncio.wait_for(
                service.store_session(session),
                timeout=1,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                asyncio.run,
                store_with_timeout(first_session),
            )
            assert first_call_started.wait(timeout=1)
            second = executor.submit(
                asyncio.run,
                store_with_timeout(second_session, second_store_started),
            )
            first.result(timeout=2)
            second.result(timeout=2)

        assert client.max_active_calls == 1
        assert [call["meta"]["event_ids"] for call in client.calls] == [
            ["event-1"],
            ["event-2"],
        ]

    async def test_branch_without_workspace_is_ignored(self):
        client = MagicMock()
        client.async_add = AsyncMock(return_value={"success": True})
        service = HyMemoryService(
            _config(scene="coding", branch="main"),
            client=client,
        )

        await service.store_session(_session([_event("hello", event_id="event-1")]), )

        assert client.async_add.await_args.kwargs["branch"] is None


class TestHyMemorySearch:

    async def test_search_maps_chat_channels_and_honors_limit(self):
        client = MagicMock()
        client.async_search = AsyncMock(return_value={
            "memories": {
                "normal": [{
                    "content": "Likes science fiction",
                    "source": "cursor",
                    "memory_at": 1,
                }],
                "schema": [{
                    "memory": "Prefers concise answers"
                }],
                "proactive": [{
                    "text": "Follow up next week"
                }],
            },
        }, )
        service = HyMemoryService(_config(), client=client)

        response = await service.search_memory(
            "coding-agent/user-1",
            "What does the user prefer?",
            limit=2,
        )

        assert [entry.content.parts[0].text for entry in response.memories] == [
            "Likes science fiction",
            "Prefers concise answers",
        ]
        assert response.memories[0].author == "cursor"
        assert response.memories[0].timestamp == ("1970-01-01T00:00:01+00:00")
        client.async_search.assert_awaited_once_with(
            "What does the user prefer?",
            scene="chat",
            search_mode="default",
            user_ids=["user-1"],
            agent_ids=["coding-agent"],
            include_sources=None,
            exclude_sources=None,
            limit=2,
            min_score=0.4,
            schema_limit=10,
            schema_min_score=0.4,
            intention_limit=0,
            workspace_id=None,
            branch=None,
        )

    async def test_search_maps_productivity_layers_and_chain(self):
        client = MagicMock()
        client.async_search = AsyncMock(return_value={
            "scene":
            "productivity",
            "facts": [{
                "statement": "Timeout is 30 seconds",
                "details": "HTTP read timeout",
                "fact_chain_summary": "Changed from 10 seconds",
            }],
            "experiences": [{
                "task": "Handle transient failures",
                "solution": "Retry transient failures",
                "boundary_envs": ["HTTP client"],
            }],
            "chain_block":
            "10 seconds -> 30 seconds",
        }, )
        service = HyMemoryService(
            _config(
                scene="coding",
                workspace_id="repo",
                branch="main",
            ),
            client=client,
        )

        response = await service.search_memory(
            "coding-agent/user-1",
            "Which timeout was kept?",
            limit=5,
        )

        assert [entry.content.parts[0].text for entry in response.memories] == [
            ("Timeout is 30 seconds\nDetails: HTTP read timeout\n"
             "Evolution: Changed from 10 seconds"),
            ("Task: Handle transient failures\n"
             "Solution: Retry transient failures\n"
             'Applicable conditions: ["HTTP client"]'),
            "Code evolution context:\n10 seconds -> 30 seconds",
        ]
        assert client.async_search.await_args.kwargs["scene"] == "coding"
        assert client.async_search.await_args.kwargs["workspace_id"] == "repo"
        assert client.async_search.await_args.kwargs["branch"] == "main"

    async def test_invalid_key_is_rejected(self):
        client = MagicMock()
        service = HyMemoryService(_config(), client=client)

        with pytest.raises(ValueError, match="app_name"):
            await service.search_memory("user-only", "query")


class TestHyMemoryClose:

    async def test_injected_client_is_not_closed(self):
        client = MagicMock()
        service = HyMemoryService(_config(), client=client)

        await service.close()

        client.close.assert_not_called()

    async def test_owned_client_is_closed(self):
        client = MagicMock()
        service = HyMemoryService(_config(), client=client)
        service._owns_client = True

        await service.close()

        client.close.assert_called_once_with()
