# -*- coding: utf-8 -*-
"""Unit tests for SessionManager, _SessionWorker, _SessionRequest, and helpers."""

from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from trpc_agent_sdk.server.agents.claude._runtime import AsyncRuntime
from trpc_agent_sdk.server.agents.claude._session_config import SessionConfig
from trpc_agent_sdk.server.agents.claude._session_manager import (
    SessionManager,
    _SessionRequest,
    _SessionState,
    _SessionWorker,
    _fingerprint_options,
    _RESPONSE_SENTINEL,
)


# ---------------------------------------------------------------------------
# _SessionRequest
# ---------------------------------------------------------------------------

class TestSessionRequest:
    def test_default_init(self):
        q = queue.Queue()
        req = _SessionRequest(prompt="hello", session_id="s1", response_queue=q)
        assert req.prompt == "hello"
        assert req.session_id == "s1"
        assert req.response_queue is q
        assert not req.is_cancelled()

    def test_cancel(self):
        q = queue.Queue()
        req = _SessionRequest(prompt="hello", session_id="s1", response_queue=q)
        assert not req.is_cancelled()
        req.cancel()
        assert req.is_cancelled()

    def test_cancelled_event_provided(self):
        event = threading.Event()
        q = queue.Queue()
        req = _SessionRequest(prompt="hello", session_id="s1", response_queue=q, cancelled=event)
        assert req.cancelled is event

    def test_post_init_creates_event(self):
        q = queue.Queue()
        req = _SessionRequest(prompt="hello", session_id="s1", response_queue=q, cancelled=None)
        assert isinstance(req.cancelled, threading.Event)


# ---------------------------------------------------------------------------
# _fingerprint_options
# ---------------------------------------------------------------------------

class TestFingerprintOptions:
    def test_model_dump_json(self):
        options = MagicMock()
        options.model_dump_json.return_value = '{"model":"test"}'
        result = _fingerprint_options(options)
        assert result == '{"model":"test"}'

    def test_fallback_to_model_dump(self):
        options = MagicMock(spec=[])
        options.model_dump_json = Mock(side_effect=AttributeError)
        options.model_dump = Mock(return_value={"model": "test"})
        result = _fingerprint_options(options)
        assert json.loads(result) == {"model": "test"}

    def test_fallback_to_json(self):
        options = MagicMock(spec=[])
        options.model_dump_json = Mock(side_effect=AttributeError)
        options.model_dump = Mock(side_effect=AttributeError)
        options.json = Mock(return_value='{"model":"fallback"}')
        result = _fingerprint_options(options)
        assert result == '{"model":"fallback"}'

    def test_fallback_to_repr(self):
        options = MagicMock(spec=[])
        options.model_dump_json = Mock(side_effect=AttributeError)
        options.model_dump = Mock(side_effect=AttributeError)
        options.json = Mock(side_effect=AttributeError)
        result = _fingerprint_options(options)
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# _SessionWorker
# ---------------------------------------------------------------------------

class TestSessionWorker:
    def test_init(self):
        options = MagicMock()
        worker = _SessionWorker(session_id="s1", options=options)
        assert worker._session_id == "s1"
        assert worker._options is options
        assert not worker._closed
        assert worker.run_future is None

    def test_attach_future(self):
        options = MagicMock()
        worker = _SessionWorker(session_id="s1", options=options)
        mock_future = MagicMock()
        worker.attach_future(mock_future)
        assert worker.run_future is mock_future

    async def test_enqueue_raises_when_closed(self):
        options = MagicMock()
        worker = _SessionWorker(session_id="s1", options=options)
        worker._closed = True
        q = queue.Queue()
        req = _SessionRequest(prompt="test", session_id="s1", response_queue=q)
        with pytest.raises(RuntimeError, match="Session worker is closed"):
            await worker.enqueue(req)

    async def test_close_sends_none_sentinel(self):
        options = MagicMock()
        worker = _SessionWorker(session_id="s1", options=options)
        await worker.close()
        item = worker._request_queue.get_nowait()
        assert item is None

    async def test_close_is_idempotent(self):
        options = MagicMock()
        worker = _SessionWorker(session_id="s1", options=options)
        await worker.close()
        worker._closed = True
        await worker.close()  # Should not raise

    async def test_drain_pending_requests_with_error(self):
        options = MagicMock()
        worker = _SessionWorker(session_id="s1", options=options)
        q = queue.Queue()
        req = _SessionRequest(prompt="test", session_id="s1", response_queue=q)
        await worker._request_queue.put(req)

        error = RuntimeError("test error")
        await worker._drain_pending_requests(error)

        messages = []
        while not q.empty():
            messages.append(q.get_nowait())
        assert any(isinstance(m, RuntimeError) for m in messages)
        assert messages[-1] is _RESPONSE_SENTINEL

    async def test_drain_pending_requests_without_error(self):
        options = MagicMock()
        worker = _SessionWorker(session_id="s1", options=options)
        q = queue.Queue()
        req = _SessionRequest(prompt="test", session_id="s1", response_queue=q)
        await worker._request_queue.put(req)

        await worker._drain_pending_requests()

        messages = []
        while not q.empty():
            messages.append(q.get_nowait())
        assert messages[-1] is _RESPONSE_SENTINEL
        assert not any(isinstance(m, Exception) for m in messages)

    async def test_drain_skips_none_sentinels(self):
        options = MagicMock()
        worker = _SessionWorker(session_id="s1", options=options)
        await worker._request_queue.put(None)
        await worker._drain_pending_requests()

    async def test_handle_request_streams_messages(self):
        options = MagicMock()
        worker = _SessionWorker(session_id="s1", options=options)

        mock_client = AsyncMock()
        mock_client.query = AsyncMock()
        messages = ["msg1", "msg2"]

        async def mock_receive():
            for m in messages:
                yield m

        mock_client.receive_response = mock_receive

        q = queue.Queue()
        req = _SessionRequest(prompt="test", session_id="s1", response_queue=q)
        await worker._handle_request(mock_client, req)

        results = []
        while not q.empty():
            results.append(q.get_nowait())
        assert results[0] == "msg1"
        assert results[1] == "msg2"
        assert results[-1] is _RESPONSE_SENTINEL

    async def test_handle_request_cancelled_stops_early(self):
        options = MagicMock()
        worker = _SessionWorker(session_id="s1", options=options)

        mock_client = AsyncMock()
        mock_client.query = AsyncMock()

        async def mock_receive():
            yield "msg1"
            yield "msg2"  # Should not be received

        mock_client.receive_response = mock_receive

        q = queue.Queue()
        req = _SessionRequest(prompt="test", session_id="s1", response_queue=q)
        req.cancel()  # Cancel before processing

        await worker._handle_request(mock_client, req)

        results = []
        while not q.empty():
            results.append(q.get_nowait())
        # Should only have sentinel since cancelled before first message
        assert results[-1] is _RESPONSE_SENTINEL

    async def test_handle_request_exception(self):
        options = MagicMock()
        worker = _SessionWorker(session_id="s1", options=options)

        mock_client = AsyncMock()
        mock_client.query = AsyncMock(side_effect=RuntimeError("query failed"))

        q = queue.Queue()
        req = _SessionRequest(prompt="test", session_id="s1", response_queue=q)
        await worker._handle_request(mock_client, req)

        results = []
        while not q.empty():
            results.append(q.get_nowait())
        assert any(isinstance(m, RuntimeError) for m in results)
        assert results[-1] is _RESPONSE_SENTINEL

    async def test_run_processes_request_then_shutdown(self):
        options = MagicMock()
        worker = _SessionWorker(session_id="s1", options=options)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.query = AsyncMock()

        async def mock_receive():
            yield "response"

        mock_client.receive_response = mock_receive

        with patch("trpc_agent_sdk.server.agents.claude._session_manager.ClaudeSDKClient", return_value=mock_client):
            q = queue.Queue()
            req = _SessionRequest(prompt="hello", session_id="s1", response_queue=q)
            await worker._request_queue.put(req)
            await worker._request_queue.put(None)  # Shutdown signal

            await worker.run()

            results = []
            while not q.empty():
                results.append(q.get_nowait())
            assert "response" in results
            assert results[-1] is _RESPONSE_SENTINEL
            assert worker._closed


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------

class TestSessionManager:
    def test_init_defaults(self):
        rt = MagicMock(spec=AsyncRuntime)
        mgr = SessionManager(runtime=rt)
        assert mgr._session_ttl == 600
        assert mgr._sessions == {}
        assert not mgr._is_closed

    def test_init_custom_config(self):
        rt = MagicMock(spec=AsyncRuntime)
        config = SessionConfig(ttl=120)
        mgr = SessionManager(runtime=rt, config=config)
        assert mgr._session_ttl == 120

    def test_close_sets_closed_flag(self):
        rt = MagicMock(spec=AsyncRuntime)
        mgr = SessionManager(runtime=rt)
        mgr.close()
        assert mgr._is_closed

    def test_close_is_idempotent(self):
        rt = MagicMock(spec=AsyncRuntime)
        mgr = SessionManager(runtime=rt)
        mgr.close()
        mgr.close()
        assert mgr._is_closed

    async def test_stream_query_raises_when_closed(self):
        rt = MagicMock(spec=AsyncRuntime)
        mgr = SessionManager(runtime=rt)
        mgr._is_closed = True
        options = MagicMock()
        with pytest.raises(RuntimeError, match="SessionManager is closed"):
            async for _ in mgr.stream_query("s1", options, "hello"):
                pass

    async def test_cleanup_expired_sessions_skips_when_ttl_disabled(self):
        rt = MagicMock(spec=AsyncRuntime)
        config = SessionConfig(ttl=0)
        mgr = SessionManager(runtime=rt, config=config)
        mgr._sessions["s1"] = MagicMock()
        await mgr._cleanup_expired_sessions()
        assert "s1" in mgr._sessions

    async def test_cleanup_expired_sessions_removes_old(self):
        rt = MagicMock(spec=AsyncRuntime)
        config = SessionConfig(ttl=1)
        mgr = SessionManager(runtime=rt, config=config)

        mock_worker = MagicMock()
        mock_worker.close = AsyncMock()
        mock_worker.run_future = None

        old_state = _SessionState(
            worker=mock_worker,
            options_signature="sig",
            last_access=time.time() - 100,
        )
        mgr._sessions["expired"] = old_state

        with patch.object(mgr, "_shutdown_state", new_callable=AsyncMock):
            await mgr._cleanup_expired_sessions()
        assert "expired" not in mgr._sessions

    async def test_cleanup_expired_sessions_keeps_fresh(self):
        rt = MagicMock(spec=AsyncRuntime)
        config = SessionConfig(ttl=600)
        mgr = SessionManager(runtime=rt, config=config)

        mock_worker = MagicMock()
        fresh_state = _SessionState(
            worker=mock_worker,
            options_signature="sig",
            last_access=time.time(),
        )
        mgr._sessions["fresh"] = fresh_state

        await mgr._cleanup_expired_sessions()
        assert "fresh" in mgr._sessions

    def test_create_state(self):
        rt = MagicMock(spec=AsyncRuntime)
        mock_future = MagicMock()
        rt.submit_coroutine.return_value = mock_future

        mgr = SessionManager(runtime=rt)
        options = MagicMock()

        with patch("trpc_agent_sdk.server.agents.claude._session_manager._SessionWorker") as MockWorker:
            mock_worker = MagicMock()
            MockWorker.return_value = mock_worker

            state = mgr._create_state("s1", options, "sig123")

            assert "s1" in mgr._sessions
            assert state.options_signature == "sig123"
            mock_worker.attach_future.assert_called_once_with(mock_future)

    async def test_get_or_create_state_returns_existing(self):
        rt = MagicMock(spec=AsyncRuntime)
        mgr = SessionManager(runtime=rt)
        options = MagicMock()

        mock_worker = MagicMock()
        existing = _SessionState(worker=mock_worker, options_signature="sig", last_access=time.time())
        mgr._sessions["s1"] = existing

        with patch("trpc_agent_sdk.server.agents.claude._session_manager._fingerprint_options", return_value="sig"):
            result = await mgr._get_or_create_state("s1", options)
        assert result is existing

    async def test_get_or_create_state_replaces_on_options_change(self):
        rt = MagicMock(spec=AsyncRuntime)
        mock_future = MagicMock()
        rt.submit_coroutine.return_value = mock_future

        mgr = SessionManager(runtime=rt)
        options = MagicMock()

        mock_worker_old = MagicMock()
        mock_worker_old.close = AsyncMock()
        mock_worker_old.run_future = None
        old_state = _SessionState(worker=mock_worker_old, options_signature="old_sig", last_access=time.time())
        mgr._sessions["s1"] = old_state

        with patch("trpc_agent_sdk.server.agents.claude._session_manager._fingerprint_options",
                    return_value="new_sig"), \
             patch.object(mgr, "_shutdown_state", new_callable=AsyncMock), \
             patch("trpc_agent_sdk.server.agents.claude._session_manager._SessionWorker") as MockWorker:
            mock_worker_new = MagicMock()
            MockWorker.return_value = mock_worker_new

            result = await mgr._get_or_create_state("s1", options)
            assert result.options_signature == "new_sig"

    async def test_shutdown_state(self):
        rt = MagicMock(spec=AsyncRuntime)
        mgr = SessionManager(runtime=rt)

        close_future = asyncio.get_event_loop().create_future()
        close_future.set_result(None)
        run_future = asyncio.get_event_loop().create_future()
        run_future.set_result(None)

        mock_worker = MagicMock()
        mock_worker.close = AsyncMock()
        mock_worker.run_future = run_future
        rt.submit_coroutine.return_value = close_future

        state = _SessionState(worker=mock_worker, options_signature="sig", last_access=time.time())
        await mgr._shutdown_state("s1", state)

    async def test_shutdown_state_handles_close_error(self):
        rt = MagicMock(spec=AsyncRuntime)
        mgr = SessionManager(runtime=rt)

        close_future = asyncio.get_event_loop().create_future()
        close_future.set_exception(RuntimeError("close failed"))

        mock_worker = MagicMock()
        mock_worker.run_future = None
        rt.submit_coroutine.return_value = close_future

        state = _SessionState(worker=mock_worker, options_signature="sig", last_access=time.time())
        # Should not raise
        await mgr._shutdown_state("s1", state)

    def test_close_shuts_down_all_sessions(self):
        rt = MagicMock(spec=AsyncRuntime)
        mgr = SessionManager(runtime=rt)

        close_future = MagicMock()
        close_future.result.return_value = None
        run_future = MagicMock()
        run_future.result.return_value = None

        mock_worker = MagicMock()
        mock_worker.run_future = run_future
        rt.submit_coroutine.return_value = close_future

        state = _SessionState(worker=mock_worker, options_signature="sig", last_access=time.time())
        mgr._sessions["s1"] = state

        mgr.close()

        assert mgr._is_closed
        assert len(mgr._sessions) == 0
        rt.submit_coroutine.assert_called()

    def test_close_handles_session_error(self):
        rt = MagicMock(spec=AsyncRuntime)
        mgr = SessionManager(runtime=rt)

        close_future = MagicMock()
        close_future.result.side_effect = RuntimeError("close error")

        mock_worker = MagicMock()
        mock_worker.run_future = None
        rt.submit_coroutine.return_value = close_future

        state = _SessionState(worker=mock_worker, options_signature="sig", last_access=time.time())
        mgr._sessions["s1"] = state

        mgr.close()
        assert mgr._is_closed

    def test_close_handles_run_future_error(self):
        rt = MagicMock(spec=AsyncRuntime)
        mgr = SessionManager(runtime=rt)

        close_future = MagicMock()
        close_future.result.return_value = None

        run_future = MagicMock()
        run_future.result.side_effect = RuntimeError("run error")

        mock_worker = MagicMock()
        mock_worker.run_future = run_future
        rt.submit_coroutine.return_value = close_future

        state = _SessionState(worker=mock_worker, options_signature="sig", last_access=time.time())
        mgr._sessions["s1"] = state

        mgr.close()
        assert mgr._is_closed

    async def test_get_or_create_state_creates_new(self):
        rt = MagicMock(spec=AsyncRuntime)
        mock_future = MagicMock()
        rt.submit_coroutine.return_value = mock_future

        mgr = SessionManager(runtime=rt)
        options = MagicMock()

        with patch("trpc_agent_sdk.server.agents.claude._session_manager._fingerprint_options",
                    return_value="new_sig"), \
             patch("trpc_agent_sdk.server.agents.claude._session_manager._SessionWorker") as MockWorker:
            mock_worker = MagicMock()
            MockWorker.return_value = mock_worker

            result = await mgr._get_or_create_state("s1", options)
            assert result is not None
            assert "s1" in mgr._sessions


# ---------------------------------------------------------------------------
# _SessionState
# ---------------------------------------------------------------------------

class TestSessionState:
    def test_init(self):
        mock_worker = MagicMock()
        state = _SessionState(worker=mock_worker, options_signature="sig", last_access=100.0)
        assert state.worker is mock_worker
        assert state.options_signature == "sig"
        assert state.last_access == 100.0


    async def test_shutdown_state_handles_run_future_error(self):
        rt = MagicMock(spec=AsyncRuntime)
        mgr = SessionManager(runtime=rt)

        close_future = asyncio.get_event_loop().create_future()
        close_future.set_result(None)

        run_future = asyncio.get_event_loop().create_future()
        run_future.set_exception(RuntimeError("run error"))

        mock_worker = MagicMock()
        mock_worker.run_future = run_future
        rt.submit_coroutine.return_value = close_future

        state = _SessionState(worker=mock_worker, options_signature="sig", last_access=time.time())
        # Should not raise
        await mgr._shutdown_state("s1", state)

    async def test_cleanup_expired_with_negative_ttl(self):
        rt = MagicMock(spec=AsyncRuntime)
        config = SessionConfig(ttl=-1)
        mgr = SessionManager(runtime=rt, config=config)
        mgr._sessions["s1"] = MagicMock()
        await mgr._cleanup_expired_sessions()
        assert "s1" in mgr._sessions
