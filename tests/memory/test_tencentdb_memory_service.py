# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the TencentDB Agent Memory V3 adapter."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.memory.tencentdb_memory_service import (
    TencentDBMemoryError,
    TencentDBMemoryService,
    TencentDBMemoryServiceConfig,
)
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


def _event(
    event_id: str,
    text: str,
    *,
    author: str = "user",
    timestamp: float = 1_700_000_000.0,
) -> Event:
    return Event(
        id=event_id,
        invocation_id=f"inv-{event_id}",
        author=author,
        content=Content(parts=[Part.from_text(text=text)]),
        timestamp=timestamp,
    )


def _session(events: list[Event]) -> Session:
    return Session(
        id="session-1",
        app_name="memory-demo",
        user_id="user-1",
        save_key="memory-demo/user-1",
        events=events,
    )


def _config() -> TencentDBMemoryServiceConfig:
    config = TencentDBMemoryServiceConfig(
        enabled=True,
        endpoint="https://memory.example.com/",
        api_key="secret",
        service_id="service-1",
        team_id="team-1",
        agent_id="agent-1",
    )
    config.clean_ttl_config()
    return config


def _json_response(data: dict[str, Any], *, code: int = 0) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "code": code,
            "message": "ok" if code == 0 else "failed",
            "request_id": "request-1",
            "data": data,
        },
    )


@pytest.mark.asyncio
async def test_store_session_uses_v3_identity_and_only_sends_new_events():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        count = len(body["messages"])
        return _json_response({
            "accepted_ids": [f"message-{index}" for index in range(count)],
            "accepted_versions": ["v1"] * count,
            "total_count": count,
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = TencentDBMemoryService(_config(), client=client)
    session = _session([
        _event("event-1", "My name is Alice."),
        _event("event-2", "Nice to meet you.", author="assistant"),
    ])

    await service.store_session(session)
    await service.store_session(session)
    session.events.append(_event("event-3", "My favorite color is blue."))
    await service.store_session(session)

    assert len(requests) == 2
    first = requests[0]
    assert first.url == "https://memory.example.com/v3/conversation/add"
    assert first.headers["authorization"] == "Bearer secret"
    assert first.headers["x-tdai-service-id"] == "service-1"

    first_body = json.loads(first.content)
    assert first_body["session_id"] == "session-1"
    assert first_body["team_id"] == "team-1"
    assert first_body["agent_id"] == "agent-1"
    assert first_body["user_id"] == "user-1"
    assert first_body["messages"] == [
        {
            "role": "user",
            "content": "My name is Alice.",
            "timestamp": "2023-11-14T22:13:20Z",
        },
        {
            "role": "assistant",
            "content": "Nice to meet you.",
            "timestamp": "2023-11-14T22:13:20Z",
        },
    ]

    second_body = json.loads(requests[1].content)
    assert [message["content"] for message in second_body["messages"]] == [
        "My favorite color is blue.",
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_store_session_retries_events_after_gateway_error():
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _json_response({}, code=5001)
        return _json_response({
            "accepted_ids": ["message-1"],
            "accepted_versions": ["v1"],
            "total_count": 1,
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = TencentDBMemoryService(_config(), client=client)
    session = _session([_event("event-1", "Remember this.")])

    await service.store_session(session)
    await service.store_session(session)

    assert calls == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_post_preserves_gateway_http_error_details():

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "code": 400,
                "message": "messages.0.timestamp: Invalid ISO datetime",
                "request_id": "request-1",
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = TencentDBMemoryService(_config(), client=client)

    with pytest.raises(
            TencentDBMemoryError,
            match="messages.0.timestamp: Invalid ISO datetime",
    ):
        await service._post("/v3/conversation/add", {})

    await client.aclose()


@pytest.mark.asyncio
async def test_store_session_retries_after_incomplete_success_response():
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _json_response({"total_count": 1})
        return _json_response({
            "accepted_ids": ["message-1"],
            "accepted_versions": ["v1"],
            "total_count": 1,
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = TencentDBMemoryService(_config(), client=client)
    session = _session([_event("event-1", "Remember this.")])

    await service.store_session(session)
    await service.store_session(session)

    assert calls == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_store_session_batches_at_v3_limit():
    batch_sizes: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        count = len(body["messages"])
        batch_sizes.append(count)
        return _json_response({
            "accepted_ids": [f"message-{index}" for index in range(count)],
            "accepted_versions": ["v1"] * count,
            "total_count": count,
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = TencentDBMemoryService(_config(), client=client)
    session = _session([_event(f"event-{index}", f"message {index}") for index in range(101)])

    await service.store_session(session)

    assert batch_sizes == [100, 1]
    await client.aclose()


@pytest.mark.asyncio
async def test_store_session_skips_empty_partial_and_error_events():
    captured_messages: list[dict[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured_messages.extend(body["messages"])
        return _json_response({
            "accepted_ids": ["message-1"],
            "accepted_versions": ["v1"],
            "total_count": 1,
        })

    partial = _event("partial", "partial")
    partial.partial = True
    error = _event("error", "error")
    error.error_code = "failed"
    empty = Event(id="empty", invocation_id="inv-empty", author="user")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = TencentDBMemoryService(_config(), client=client)
    await service.store_session(_session([
        partial,
        error,
        empty,
        _event("valid", "valid"),
    ]))

    assert [message["content"] for message in captured_messages] == ["valid"]
    await client.aclose()


@pytest.mark.asyncio
async def test_search_memory_maps_v3_atomic_items_across_sessions():
    captured_body: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
        return _json_response({
            "items": [
                {
                    "id": "atomic-1",
                    "type": "persona",
                    "content": "The user's favorite color is blue.",
                    "score": 0.97,
                    "created_at": "2026-09-24T10:00:00Z",
                    "updated_at": "2026-09-24T10:01:00Z",
                },
                {
                    "id": "atomic-2",
                    "type": "episodic",
                    "content": "",
                },
            ],
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = TencentDBMemoryService(_config(), client=client)

    result = await service.search_memory(
        key="memory-demo/user-1",
        query="favorite color",
        limit=5,
    )

    assert captured_body == {
        "query": "favorite color",
        "limit": 5,
        "team_id": "team-1",
        "agent_id": "agent-1",
        "user_id": "user-1",
    }
    assert len(result.memories) == 1
    assert result.memories[0].content.parts[0].text == ("The user's favorite color is blue.")
    assert result.memories[0].author == "persona"
    assert result.memories[0].timestamp == "2026-09-24T10:01:00Z"
    await client.aclose()


@pytest.mark.asyncio
async def test_search_memory_falls_back_to_semantic_conversation_search():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v3/atomic/search":
            return _json_response({"items": []})
        return _json_response({
            "messages": [{
                "id": "message-1",
                "role": "user",
                "content": "My favorite color is blue.",
                "timestamp": "2026-09-24T10:00:00Z",
                "score": 0.91,
            }],
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = TencentDBMemoryService(_config(), client=client)

    result = await service.search_memory(
        key="memory-demo/user-1",
        query="What color do I like?",
        limit=5,
    )

    assert [request.url.path for request in requests] == [
        "/v3/atomic/search",
        "/v3/conversation/search",
    ]
    assert json.loads(requests[1].content)["query"] == "What color do I like?"
    assert result.memories[0].content.parts[0].text == "My favorite color is blue."
    assert result.memories[0].author == "user"
    assert result.memories[0].timestamp == "2026-09-24T10:00:00Z"
    await client.aclose()


@pytest.mark.asyncio
async def test_search_memory_returns_empty_response_on_gateway_error():

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = TencentDBMemoryService(_config(), client=client)

    result = await service.search_memory(
        key="memory-demo/user-1",
        query="anything",
    )

    assert result.memories == []
    await client.aclose()


def test_truncate_utf16_does_not_split_surrogate_pair():
    text = ("a" * 8191) + "😀" + "tail"

    truncated = TencentDBMemoryService._truncate_utf16(text, 8192)

    assert truncated == "a" * 8191
    assert len(truncated.encode("utf-16-le")) // 2 <= 8192


@pytest.mark.asyncio
async def test_close_only_closes_owned_client():
    injected = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: _json_response({})))
    injected_service = TencentDBMemoryService(_config(), client=injected)

    await injected_service.close()

    assert injected.is_closed is False
    await injected.aclose()

    owned_service = TencentDBMemoryService(_config())
    owned_client = owned_service._client
    await owned_service.close()

    assert owned_client.is_closed is True
