# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for trpc_agent_sdk.memory.mem0_memory_service.

Covers:
- Mem0Kwargs: dataclass basics
- set_mem0_filters / get_mem0_filters: metadata round-trip
- Mem0MemoryService: store_session, search_memory, close, _retry_transport
- Static helpers: _event_to_text, _event_to_role, _parse_event_timestamp, _extract_memories_from_result
- Cleanup task lifecycle: _start_cleanup_task, _stop_cleanup_task, _cleanup_expired_memories
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# mem0 may not be installed; provide stubs so the module under test can be imported.
if "mem0" not in sys.modules:
    _mem0_stub = MagicMock()
    sys.modules["mem0"] = _mem0_stub

from trpc_agent_sdk.abc import MemoryServiceConfig
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.memory.mem0_memory_service import (
    Mem0Kwargs,
    Mem0MemoryService,
    get_mem0_filters,
    set_mem0_filters,
)
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.types import Content, Part, SearchMemoryResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(text: str = "hello world", author: str = "user") -> Event:
    return Event(
        id=Event.new_id(),
        invocation_id="inv-1",
        author=author,
        content=Content(parts=[Part.from_text(text=text)]),
        timestamp=time.time(),
    )


def _make_event_no_content() -> Event:
    return Event(id=Event.new_id(), invocation_id="inv-1", author="user")


def _make_session(
    events: Optional[list[Event]] = None,
    save_key: str = "agent1/user1",
    session_id: str = "session-1",
) -> Session:
    return Session(
        id=session_id,
        app_name="app",
        user_id="user1",
        save_key=save_key,
        events=events or [],
    )


def _make_config_no_ttl() -> MemoryServiceConfig:
    cfg = MemoryServiceConfig(enabled=True)
    cfg.clean_ttl_config()
    return cfg


def _make_config_with_ttl(ttl_seconds: int = 3600, cleanup_interval: float = 0.0) -> MemoryServiceConfig:
    ttl = MemoryServiceConfig.create_ttl_config(
        enable=True, ttl_seconds=ttl_seconds, cleanup_interval_seconds=cleanup_interval
    )
    return MemoryServiceConfig(enabled=True, ttl=ttl)


def _make_svc(
    is_remote: bool = False,
    infer: bool = True,
    config: Optional[MemoryServiceConfig] = None,
) -> Mem0MemoryService:
    """Create a Mem0MemoryService with a mocked mem0 client."""
    svc = Mem0MemoryService.__new__(Mem0MemoryService)
    svc._memory_service_config = config or _make_config_no_ttl()
    svc._mem0 = AsyncMock()
    svc._infer = infer
    svc._async_mode = False
    svc._known_user_ids = set()
    svc._is_remote_mem0 = is_remote
    svc._Mem0MemoryService__cleanup_task = None
    svc._Mem0MemoryService__cleanup_stop_event = None
    return svc


# ---------------------------------------------------------------------------
# Mem0Kwargs
# ---------------------------------------------------------------------------


class TestMem0Kwargs:
    def test_defaults(self):
        kw = Mem0Kwargs(user_id="u1")
        assert kw.user_id == "u1"
        assert kw.agent_id is None

    def test_full_init(self):
        kw = Mem0Kwargs(user_id="u1", agent_id="a1", run_id="r1", session_id="s1", filters={"k": "v"})
        assert kw.user_id == "u1"
        assert kw.agent_id == "a1"


# ---------------------------------------------------------------------------
# set_mem0_filters / get_mem0_filters
# ---------------------------------------------------------------------------


class TestMem0Filters:
    def test_set_and_get_roundtrip(self):
        ctx = MagicMock(spec=AgentContext)
        metadata_store: dict[str, Any] = {}
        ctx.with_metadata = lambda k, v: metadata_store.update({k: v})
        ctx.get_metadata = lambda k, default=None: metadata_store.get(k, default)

        set_mem0_filters(ctx, {"key": "value"})
        result = get_mem0_filters(ctx)
        assert result == {"key": "value"}

    def test_get_filters_none_context(self):
        result = get_mem0_filters(None)
        assert result == {}

    def test_set_filters_none_context(self):
        set_mem0_filters(None, {"key": "value"})  # should not raise


# ---------------------------------------------------------------------------
# parse_mem0_kwargs
# ---------------------------------------------------------------------------


class TestParseMem0Kwargs:
    def test_save_key_with_slash(self):
        svc = _make_svc(is_remote=False)
        kw = svc.parse_mem0_kwargs({"session_id": "s1"}, "agent1/user1")
        assert kw.agent_id == "agent1"
        assert kw.user_id == "user1"
        assert kw.run_id == "s1"
        assert kw.session_id == "s1"

    def test_save_key_without_slash(self):
        svc = _make_svc(is_remote=False)
        kw = svc.parse_mem0_kwargs({}, "singlekey")
        assert kw.agent_id is None
        assert kw.user_id == "singlekey"

    def test_remote_mem0_kwargs(self):
        svc = _make_svc(is_remote=True)
        kw = svc.parse_mem0_kwargs({"session_id": "s1"}, "agent1/user1")
        assert kw.run_id == "s1"

    def test_save_key_multiple_slashes(self):
        svc = _make_svc(is_remote=False)
        kw = svc.parse_mem0_kwargs({}, "a/b/c")
        assert kw.agent_id == "a"
        assert kw.user_id == "bc"


# ---------------------------------------------------------------------------
# Static helpers
# ---------------------------------------------------------------------------


class TestStaticHelpers:
    def test_event_to_text_basic(self):
        event = _make_event("hello world")
        assert Mem0MemoryService._event_to_text(event) == "hello world"

    def test_event_to_text_no_content(self):
        event = _make_event_no_content()
        assert Mem0MemoryService._event_to_text(event) == ""

    def test_event_to_text_no_parts(self):
        event = Event(id="e1", invocation_id="inv-1", author="user", content=Content(parts=[]))
        assert Mem0MemoryService._event_to_text(event) == ""

    def test_event_to_text_multiple_parts(self):
        event = Event(
            id="e1", invocation_id="inv-1", author="user",
            content=Content(parts=[Part.from_text(text="hello"), Part.from_text(text="world")]),
        )
        assert Mem0MemoryService._event_to_text(event) == "hello world"

    def test_event_to_role_user(self):
        event = _make_event(author="user")
        assert Mem0MemoryService._event_to_role(event) == "user"

    def test_event_to_role_assistant(self):
        event = _make_event(author="my_agent")
        assert Mem0MemoryService._event_to_role(event) == "assistant"

    def test_parse_event_timestamp_valid(self):
        now = datetime.now()
        ts = Mem0MemoryService._parse_event_timestamp(now.isoformat())
        assert ts is not None
        assert abs(ts - now.timestamp()) < 1

    def test_parse_event_timestamp_invalid(self):
        assert Mem0MemoryService._parse_event_timestamp("invalid") is None

    def test_parse_event_timestamp_none(self):
        assert Mem0MemoryService._parse_event_timestamp(None) is None

    def test_extract_memories_from_result_list(self):
        data = [{"id": "1"}, {"id": "2"}]
        assert Mem0MemoryService._extract_memories_from_result(data) == data

    def test_extract_memories_from_result_dict(self):
        data = {"results": [{"id": "1"}]}
        assert Mem0MemoryService._extract_memories_from_result(data) == [{"id": "1"}]

    def test_extract_memories_from_result_dict_no_results_key(self):
        data = {"other": "value"}
        assert Mem0MemoryService._extract_memories_from_result(data) == []

    def test_extract_memories_from_result_unknown_type(self):
        assert Mem0MemoryService._extract_memories_from_result("string") == []

    def test_extract_memories_from_result_none(self):
        assert Mem0MemoryService._extract_memories_from_result(None) == []


# ---------------------------------------------------------------------------
# store_session
# ---------------------------------------------------------------------------


class TestMem0StoreSession:
    async def test_store_basic_local(self):
        svc = _make_svc(is_remote=False, infer=True)
        session = _make_session(events=[_make_event("hello", author="user")])
        await svc.store_session(session)
        assert svc._mem0.add.called
        assert len(svc._known_user_ids) == 1

    async def test_store_basic_remote(self):
        svc = _make_svc(is_remote=True, infer=True)
        session = _make_session(events=[_make_event("hello", author="user")])
        await svc.store_session(session)
        assert svc._mem0.add.called

    async def test_store_skips_empty_events(self):
        svc = _make_svc(is_remote=False)
        session = _make_session(events=[_make_event_no_content()])
        await svc.store_session(session)
        svc._mem0.add.assert_not_called()

    async def test_store_separates_user_and_assistant(self):
        svc = _make_svc(is_remote=False, infer=True)
        events = [_make_event("hello", author="user"), _make_event("world", author="assistant")]
        session = _make_session(events=events)
        await svc.store_session(session)
        assert svc._mem0.add.call_count == 2

    async def test_store_infer_false_deletes_first(self):
        svc = _make_svc(is_remote=False, infer=False)
        session = _make_session(events=[_make_event("hello", author="user")])
        await svc.store_session(session)
        svc._mem0.delete_all.assert_called()

    async def test_store_handles_exception(self):
        svc = _make_svc(is_remote=False, infer=True)
        svc._mem0.add = AsyncMock(side_effect=Exception("mem0 error"))
        session = _make_session(events=[_make_event("hello", author="user")])
        await svc.store_session(session)  # should not raise

    async def test_store_remote_passes_async_mode(self):
        svc = _make_svc(is_remote=True, infer=True)
        svc._async_mode = True
        session = _make_session(events=[_make_event("hello", author="user")])
        await svc.store_session(session)
        call_kwargs = svc._mem0.add.call_args
        assert call_kwargs.kwargs.get("async_mode") is True

    async def test_store_infer_true_local_clears_run_id(self):
        svc = _make_svc(is_remote=False, infer=True)
        session = _make_session(events=[_make_event("hello", author="user")])
        await svc.store_session(session)
        call_kwargs = svc._mem0.add.call_args
        assert call_kwargs.kwargs.get("run_id") is None


# ---------------------------------------------------------------------------
# search_memory
# ---------------------------------------------------------------------------


class TestMem0SearchMemory:
    async def test_search_basic_local(self):
        svc = _make_svc(is_remote=False, infer=True)
        svc._mem0.search = AsyncMock(return_value={
            "results": [{"memory": "hello world", "created_at": datetime.now().isoformat(), "role": "user"}]
        })
        result = await svc.search_memory("agent1/user1", "hello")
        assert len(result.memories) == 1

    async def test_search_basic_remote(self):
        svc = _make_svc(is_remote=True, infer=True)
        svc._mem0.search = AsyncMock(return_value={
            "results": [{
                "memory": "hello world",
                "created_at": datetime.now().isoformat(),
                "metadata": {"real_role": "user"},
            }]
        })
        result = await svc.search_memory("agent1/user1", "hello")
        assert len(result.memories) == 1

    async def test_search_empty_results(self):
        svc = _make_svc(is_remote=False)
        svc._mem0.search = AsyncMock(return_value={"results": []})
        result = await svc.search_memory("agent1/user1", "hello")
        assert result.memories == []

    async def test_search_skips_empty_memory(self):
        svc = _make_svc(is_remote=False)
        svc._mem0.search = AsyncMock(return_value={
            "results": [{"memory": "", "created_at": datetime.now().isoformat()}]
        })
        result = await svc.search_memory("agent1/user1", "hello")
        assert result.memories == []

    async def test_search_uses_updated_at_as_timestamp(self):
        svc = _make_svc(is_remote=False)
        now = datetime.now().isoformat()
        svc._mem0.search = AsyncMock(return_value={
            "results": [{"memory": "hello", "created_at": "2024-01-01T00:00:00", "updated_at": now, "role": "user"}]
        })
        result = await svc.search_memory("agent1/user1", "hello")
        assert result.memories[0].timestamp == now

    async def test_search_handles_exception(self):
        svc = _make_svc(is_remote=False)
        svc._mem0.search = AsyncMock(side_effect=Exception("search error"))
        result = await svc.search_memory("agent1/user1", "hello")
        assert result.memories == []

    async def test_search_handles_http_error(self):
        svc = _make_svc(is_remote=False)
        response = MagicMock()
        response.text = "error body"
        response.status_code = 500
        request = MagicMock()
        svc._mem0.search = AsyncMock(
            side_effect=httpx.HTTPStatusError("500 error", request=request, response=response)
        )
        result = await svc.search_memory("agent1/user1", "hello")
        assert result.memories == []

    async def test_search_remote_with_agent_id_filters(self):
        svc = _make_svc(is_remote=True, infer=True)
        svc._mem0.search = AsyncMock(return_value={"results": []})
        await svc.search_memory("agent1/user1", "hello")
        svc._mem0.search.assert_called_once()

    async def test_search_local_infer_true_clears_agent_id(self):
        svc = _make_svc(is_remote=False, infer=True)
        svc._mem0.search = AsyncMock(return_value={"results": []})
        await svc.search_memory("agent1/user1", "hello")
        call_kwargs = svc._mem0.search.call_args
        assert call_kwargs.kwargs.get("agent_id") is None


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


class TestMem0Close:
    async def test_close_stops_cleanup(self):
        svc = _make_svc()
        await svc.close()  # should not raise

    async def test_close_with_async_client(self):
        svc = _make_svc()
        svc._mem0.async_client = AsyncMock()
        svc._mem0.async_client.aclose = AsyncMock()
        await svc.close()
        svc._mem0.async_client.aclose.assert_called_once()

    async def test_close_handles_async_client_error(self):
        svc = _make_svc()
        svc._mem0.async_client = MagicMock()
        svc._mem0.async_client.aclose = AsyncMock(side_effect=Exception("close error"))
        await svc.close()  # should not raise


# ---------------------------------------------------------------------------
# _retry_transport
# ---------------------------------------------------------------------------


class TestRetryTransport:
    async def test_succeeds_first_try(self):
        svc = _make_svc()
        result = await svc._retry_transport("test_op", AsyncMock(return_value="ok"))
        assert result == "ok"

    async def test_retries_on_transport_error(self):
        svc = _make_svc()
        call = AsyncMock(side_effect=[httpx.TransportError("fail"), "ok"])
        result = await svc._retry_transport("test_op", call, max_attempts=3)
        assert result == "ok"
        assert call.call_count == 2

    async def test_exhausts_retries(self):
        svc = _make_svc()
        call = AsyncMock(side_effect=httpx.TransportError("always fail"))
        result = await svc._retry_transport("test_op", call, max_attempts=2)
        assert result is None
        assert call.call_count == 2

    async def test_non_transport_error_not_retried(self):
        svc = _make_svc()
        call = AsyncMock(side_effect=ValueError("bad value"))
        with pytest.raises(ValueError):
            await svc._retry_transport("test_op", call, max_attempts=3)


# ---------------------------------------------------------------------------
# Cleanup task lifecycle
# ---------------------------------------------------------------------------


class TestMem0CleanupTask:
    async def test_start_no_ttl(self):
        svc = _make_svc(config=_make_config_no_ttl())
        svc._start_cleanup_task()
        assert svc._Mem0MemoryService__cleanup_task is None

    async def test_start_with_ttl(self):
        svc = _make_svc(config=_make_config_with_ttl(ttl_seconds=3600, cleanup_interval=3600.0))
        svc._start_cleanup_task()
        assert svc._Mem0MemoryService__cleanup_task is not None
        svc._stop_cleanup_task()

    async def test_start_idempotent(self):
        svc = _make_svc(config=_make_config_with_ttl(ttl_seconds=3600, cleanup_interval=3600.0))
        svc._start_cleanup_task()
        task = svc._Mem0MemoryService__cleanup_task
        svc._start_cleanup_task()
        assert svc._Mem0MemoryService__cleanup_task is task
        svc._stop_cleanup_task()

    async def test_stop_when_no_task(self):
        svc = _make_svc()
        svc._stop_cleanup_task()  # should not raise

    async def test_stop_sets_none(self):
        svc = _make_svc(config=_make_config_with_ttl(ttl_seconds=3600, cleanup_interval=3600.0))
        svc._start_cleanup_task()
        svc._stop_cleanup_task()
        assert svc._Mem0MemoryService__cleanup_task is None
        assert svc._Mem0MemoryService__cleanup_stop_event is None


# ---------------------------------------------------------------------------
# _cleanup_expired_memories
# ---------------------------------------------------------------------------


class TestMem0CleanupExpired:
    async def test_no_known_users_noop(self):
        svc = _make_svc()
        svc._known_user_ids = set()
        await svc._cleanup_expired_memories()
        svc._mem0.get_all.assert_not_called()

    async def test_local_deletes_expired(self):
        svc = _make_svc(is_remote=False, infer=True,
                        config=_make_config_with_ttl(ttl_seconds=10))
        svc._known_user_ids = {("agent1", "user1")}
        old_time = datetime(2020, 1, 1).isoformat()
        svc._mem0.get_all = AsyncMock(return_value={
            "results": [{"id": "m1", "created_at": old_time, "updated_at": old_time}]
        })
        svc._mem0.delete = AsyncMock()

        await svc._cleanup_expired_memories()
        svc._mem0.delete.assert_called_once_with(memory_id="m1")

    async def test_local_keeps_fresh(self):
        svc = _make_svc(is_remote=False, infer=True,
                        config=_make_config_with_ttl(ttl_seconds=999999))
        svc._known_user_ids = {("agent1", "user1")}
        now = datetime.now().isoformat()
        svc._mem0.get_all = AsyncMock(return_value={
            "results": [{"id": "m1", "created_at": now, "updated_at": now}]
        })
        svc._mem0.delete = AsyncMock()

        await svc._cleanup_expired_memories()
        svc._mem0.delete.assert_not_called()

    async def test_remote_deletes_expired(self):
        svc = _make_svc(is_remote=True,
                        config=_make_config_with_ttl(ttl_seconds=10))
        svc._known_user_ids = {("agent1", "user1")}
        old_time = datetime(2020, 1, 1).isoformat()
        svc._mem0.get_all = AsyncMock(return_value={
            "results": [{"id": "m1", "created_at": old_time, "updated_at": old_time}]
        })
        svc._mem0.delete = AsyncMock()

        await svc._cleanup_expired_memories()
        svc._mem0.delete.assert_called_once_with(memory_id="m1")

    async def test_cleanup_handles_exception(self):
        svc = _make_svc(is_remote=False, infer=True,
                        config=_make_config_with_ttl(ttl_seconds=10))
        svc._known_user_ids = {("agent1", "user1")}
        svc._mem0.get_all = AsyncMock(side_effect=Exception("get_all error"))

        await svc._cleanup_expired_memories()  # should not raise

    async def test_cleanup_handles_http_status_error(self):
        svc = _make_svc(is_remote=False, infer=True,
                        config=_make_config_with_ttl(ttl_seconds=10))
        svc._known_user_ids = {("agent1", "user1")}
        response = MagicMock()
        response.text = "error"
        response.status_code = 500
        request = MagicMock()
        svc._mem0.get_all = AsyncMock(
            side_effect=httpx.HTTPStatusError("500", request=request, response=response)
        )

        await svc._cleanup_expired_memories()  # should not raise

    async def test_cleanup_skips_unparseable_timestamp(self):
        svc = _make_svc(is_remote=False, infer=True,
                        config=_make_config_with_ttl(ttl_seconds=10))
        svc._known_user_ids = {("agent1", "user1")}
        svc._mem0.get_all = AsyncMock(return_value={
            "results": [{"id": "m1", "created_at": "invalid-ts", "updated_at": None}]
        })
        svc._mem0.delete = AsyncMock()

        await svc._cleanup_expired_memories()
        svc._mem0.delete.assert_not_called()

    async def test_cleanup_skips_memory_without_id(self):
        svc = _make_svc(is_remote=False, infer=True,
                        config=_make_config_with_ttl(ttl_seconds=10))
        svc._known_user_ids = {("agent1", "user1")}
        old_time = datetime(2020, 1, 1).isoformat()
        svc._mem0.get_all = AsyncMock(return_value={
            "results": [{"created_at": old_time, "updated_at": old_time}]
        })
        svc._mem0.delete = AsyncMock()

        await svc._cleanup_expired_memories()
        svc._mem0.delete.assert_not_called()

    async def test_local_infer_true_clears_agent_id(self):
        svc = _make_svc(is_remote=False, infer=True,
                        config=_make_config_with_ttl(ttl_seconds=10))
        svc._known_user_ids = {("agent1", "user1")}
        svc._mem0.get_all = AsyncMock(return_value={"results": []})

        await svc._cleanup_expired_memories()
        call_kwargs = svc._mem0.get_all.call_args
        assert call_kwargs.kwargs.get("agent_id") is None

    async def test_local_infer_false_keeps_agent_id(self):
        svc = _make_svc(is_remote=False, infer=False,
                        config=_make_config_with_ttl(ttl_seconds=10))
        svc._known_user_ids = {("agent1", "user1")}
        svc._mem0.get_all = AsyncMock(return_value={"results": []})

        await svc._cleanup_expired_memories()
        call_kwargs = svc._mem0.get_all.call_args
        assert call_kwargs.kwargs.get("agent_id") == "agent1"


# ---------------------------------------------------------------------------
# Mem0MemoryService — _cleanup_loop
# ---------------------------------------------------------------------------


class TestMem0CleanupLoop:
    async def test_cleanup_loop_runs_and_stops(self):
        svc = _make_svc(config=_make_config_with_ttl(ttl_seconds=3600, cleanup_interval=0.05))
        svc._start_cleanup_task()
        await asyncio.sleep(0.1)
        svc._stop_cleanup_task()

    async def test_cleanup_loop_handles_error(self):
        svc = _make_svc(config=_make_config_with_ttl(ttl_seconds=3600, cleanup_interval=0.05))
        with patch.object(Mem0MemoryService, "_cleanup_expired_memories", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            svc._start_cleanup_task()
            await asyncio.sleep(0.15)
            svc._stop_cleanup_task()


# ---------------------------------------------------------------------------
# Mem0MemoryService — __init__
# ---------------------------------------------------------------------------


class TestMem0Init:
    @patch("trpc_agent_sdk.memory.mem0_memory_service.AsyncMemoryClient", new=type("FakeClient", (), {}))
    def test_init_local_client(self):
        mock_client = MagicMock()
        cfg = _make_config_no_ttl()
        svc = Mem0MemoryService(memory_service_config=cfg, mem0_client=mock_client)
        assert svc._infer is True
        assert svc._async_mode is False
        assert svc._known_user_ids == set()
        assert svc._is_remote_mem0 is False

    @patch("trpc_agent_sdk.memory.mem0_memory_service.AsyncMemoryClient", new=type("FakeClient", (), {}))
    def test_init_with_infer_false(self):
        mock_client = MagicMock()
        cfg = _make_config_no_ttl()
        svc = Mem0MemoryService(memory_service_config=cfg, mem0_client=mock_client, infer=False)
        assert svc._infer is False

    def test_init_remote_client(self):
        FakeRemote = type("FakeRemote", (), {})
        mock_client = FakeRemote()
        with patch("trpc_agent_sdk.memory.mem0_memory_service.AsyncMemoryClient", FakeRemote):
            cfg = _make_config_no_ttl()
            svc = Mem0MemoryService(memory_service_config=cfg, mem0_client=mock_client)
            assert svc._is_remote_mem0 is True


# ---------------------------------------------------------------------------
# Additional edge cases for higher coverage
# ---------------------------------------------------------------------------


class TestMem0SearchEdgeCases:
    async def test_search_remote_with_metadata_filters(self):
        svc = _make_svc(is_remote=True, infer=True)
        svc._mem0.search = AsyncMock(return_value={
            "results": [{"memory": "data", "created_at": datetime.now().isoformat(),
                         "metadata": {"real_role": "assistant"}}]
        })
        ctx = MagicMock(spec=AgentContext)
        metadata_store: dict[str, Any] = {"metadata": {"session_id": "s1"}}
        ctx.get_metadata = lambda k, default=None: metadata_store.get(k, default)
        result = await svc.search_memory("agent1/user1", "data", agent_context=ctx)
        assert len(result.memories) == 1
        assert result.memories[0].author == "assistant"

    async def test_search_local_no_agent_id(self):
        svc = _make_svc(is_remote=False, infer=True)
        svc._mem0.search = AsyncMock(return_value={"results": []})
        await svc.search_memory("singlekey", "hello")
        call_kwargs = svc._mem0.search.call_args
        assert call_kwargs.kwargs.get("agent_id") is None

    async def test_search_local_infer_false_passes_filters(self):
        svc = _make_svc(is_remote=False, infer=False)
        svc._mem0.search = AsyncMock(return_value={"results": []})
        ctx = MagicMock(spec=AgentContext)
        ctx.get_metadata = lambda k, default=None: {"session_id": "s1", "extra": "val"} if k == "metadata" else default
        await svc.search_memory("agent1/user1", "hello", agent_context=ctx)
        call_kwargs = svc._mem0.search.call_args
        assert call_kwargs.kwargs.get("filters") is not None

    async def test_store_event_with_empty_text_skipped(self):
        svc = _make_svc(is_remote=False, infer=True)
        event = Event(
            id=Event.new_id(), invocation_id="inv-1", author="user",
            content=Content(parts=[Part()]),
        )
        session = _make_session(events=[event])
        await svc.store_session(session)
        svc._mem0.add.assert_not_called()
