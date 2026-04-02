# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import asyncio
import hashlib
import json
from contextlib import AsyncExitStack
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from mcp import ClientSession, StdioServerParameters as McpStdioServerParameters

from trpc_agent_sdk.tools.mcp_tool._mcp_session_manager import MCPSessionManager
from trpc_agent_sdk.tools.mcp_tool._types import (
    SseConnectionParams,
    StdioConnectionParams,
    StreamableHTTPConnectionParams,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stdio_conn(timeout=5.0):
    return StdioConnectionParams(
        server_params=McpStdioServerParameters(command="echo", args=["hello"]),
        timeout=timeout,
    )


def _sse_conn(url="http://example.com/sse", headers=None):
    return SseConnectionParams(url=url, headers=headers or {})


def _streamable_conn(url="http://example.com/stream", headers=None):
    return StreamableHTTPConnectionParams(url=url, headers=headers or {})


def _mock_session(closed=False):
    session = MagicMock(spec=ClientSession)
    read_stream = MagicMock()
    read_stream._closed = closed
    write_stream = MagicMock()
    write_stream._closed = closed
    session._read_stream = read_stream
    session._write_stream = write_stream
    return session


# ---------------------------------------------------------------------------
# Tests: __init__
# ---------------------------------------------------------------------------

class TestMCPSessionManagerInit:
    def test_init_with_stdio(self):
        mgr = MCPSessionManager(connection_params=_stdio_conn())
        assert isinstance(mgr._connection_params, StdioConnectionParams)
        assert mgr._sessions == {}

    def test_init_with_raw_stdio_server_params(self):
        server_params = McpStdioServerParameters(command="echo")
        mgr = MCPSessionManager(connection_params=server_params)
        assert isinstance(mgr._connection_params, StdioConnectionParams)

    def test_init_session_group_params_default(self):
        mgr = MCPSessionManager(connection_params=_stdio_conn())
        assert mgr._session_group_params == {}

    def test_init_session_group_params_custom(self):
        mgr = MCPSessionManager(connection_params=_stdio_conn(), session_group_params={"k": "v"})
        assert mgr._session_group_params == {"k": "v"}


# ---------------------------------------------------------------------------
# Tests: _is_cross_task_cancel_scope_error
# ---------------------------------------------------------------------------

class TestIsCrossTaskCancelScopeError:
    def test_matching_message(self):
        err = RuntimeError("Attempted to exit cancel scope in a different task than it was entered in")
        assert MCPSessionManager._is_cross_task_cancel_scope_error(err) is True

    def test_alternate_matching_message(self):
        err = RuntimeError("cancel scope in different task")
        assert MCPSessionManager._is_cross_task_cancel_scope_error(err) is True

    def test_non_matching_runtime_error(self):
        err = RuntimeError("something else entirely")
        assert MCPSessionManager._is_cross_task_cancel_scope_error(err) is False

    def test_non_runtime_error(self):
        err = ValueError("cancel scope in different task")
        assert MCPSessionManager._is_cross_task_cancel_scope_error(err) is False

    def test_base_exception(self):
        err = KeyboardInterrupt()
        assert MCPSessionManager._is_cross_task_cancel_scope_error(err) is False


# ---------------------------------------------------------------------------
# Tests: _generate_session_key
# ---------------------------------------------------------------------------

class TestGenerateSessionKey:
    def test_stdio_returns_constant(self):
        mgr = MCPSessionManager(connection_params=_stdio_conn())
        assert mgr._generate_session_key() == "stdio_session"
        assert mgr._generate_session_key({"Authorization": "Bearer token"}) == "stdio_session"

    def test_sse_with_no_headers(self):
        mgr = MCPSessionManager(connection_params=_sse_conn())
        assert mgr._generate_session_key(None) == "session_no_headers"

    def test_sse_with_headers(self):
        mgr = MCPSessionManager(connection_params=_sse_conn())
        headers = {"Authorization": "Bearer abc123"}
        key = mgr._generate_session_key(headers)
        expected_hash = hashlib.md5(json.dumps(headers, sort_keys=True).encode()).hexdigest()
        assert key == f"session_{expected_hash}"

    def test_same_headers_produce_same_key(self):
        mgr = MCPSessionManager(connection_params=_sse_conn())
        h = {"X-Key": "val", "Authorization": "token"}
        k1 = mgr._generate_session_key(h)
        k2 = mgr._generate_session_key(h)
        assert k1 == k2

    def test_different_headers_produce_different_keys(self):
        mgr = MCPSessionManager(connection_params=_sse_conn())
        k1 = mgr._generate_session_key({"X-Key": "a"})
        k2 = mgr._generate_session_key({"X-Key": "b"})
        assert k1 != k2

    def test_streamable_with_headers(self):
        mgr = MCPSessionManager(connection_params=_streamable_conn())
        headers = {"X-Api-Key": "secret"}
        key = mgr._generate_session_key(headers)
        assert key.startswith("session_")
        assert key != "session_no_headers"


# ---------------------------------------------------------------------------
# Tests: _merge_headers
# ---------------------------------------------------------------------------

class TestMergeHeaders:
    def test_stdio_returns_none(self):
        mgr = MCPSessionManager(connection_params=_stdio_conn())
        assert mgr._merge_headers({"X-Extra": "val"}) is None

    def test_raw_stdio_server_params_returns_none(self):
        mgr = MCPSessionManager(connection_params=McpStdioServerParameters(command="echo"))
        assert mgr._merge_headers() is None

    def test_sse_no_base_no_additional(self):
        mgr = MCPSessionManager(connection_params=_sse_conn())
        result = mgr._merge_headers()
        assert result == {}

    def test_sse_with_base_headers(self):
        conn = _sse_conn(headers={"Base": "val"})
        mgr = MCPSessionManager(connection_params=conn)
        result = mgr._merge_headers()
        assert result == {"Base": "val"}

    def test_sse_with_additional_headers(self):
        mgr = MCPSessionManager(connection_params=_sse_conn())
        result = mgr._merge_headers({"Additional": "extra"})
        assert result == {"Additional": "extra"}

    def test_sse_merge_base_and_additional(self):
        conn = _sse_conn(headers={"Base": "val"})
        mgr = MCPSessionManager(connection_params=conn)
        result = mgr._merge_headers({"Additional": "extra"})
        assert result == {"Base": "val", "Additional": "extra"}

    def test_additional_overrides_base(self):
        conn = _sse_conn(headers={"Key": "base"})
        mgr = MCPSessionManager(connection_params=conn)
        result = mgr._merge_headers({"Key": "override"})
        assert result == {"Key": "override"}


# ---------------------------------------------------------------------------
# Tests: _is_session_disconnected
# ---------------------------------------------------------------------------

class TestIsSessionDisconnected:
    def test_connected_session(self):
        mgr = MCPSessionManager(connection_params=_stdio_conn())
        session = _mock_session(closed=False)
        assert mgr._is_session_disconnected(session) is False

    def test_read_stream_closed(self):
        mgr = MCPSessionManager(connection_params=_stdio_conn())
        session = _mock_session(closed=False)
        session._read_stream._closed = True
        assert mgr._is_session_disconnected(session) is True

    def test_write_stream_closed(self):
        mgr = MCPSessionManager(connection_params=_stdio_conn())
        session = _mock_session(closed=False)
        session._write_stream._closed = True
        assert mgr._is_session_disconnected(session) is True

    def test_both_streams_closed(self):
        mgr = MCPSessionManager(connection_params=_stdio_conn())
        session = _mock_session(closed=True)
        assert mgr._is_session_disconnected(session) is True


# ---------------------------------------------------------------------------
# Tests: _create_client
# ---------------------------------------------------------------------------

class TestCreateClient:
    @patch("trpc_agent_sdk.tools.mcp_tool._mcp_session_manager.stdio_client")
    def test_stdio_client(self, mock_stdio):
        mgr = MCPSessionManager(connection_params=_stdio_conn())
        mgr._create_client()
        mock_stdio.assert_called_once()

    @patch("trpc_agent_sdk.tools.mcp_tool._mcp_session_manager.sse_client")
    def test_sse_client(self, mock_sse):
        mgr = MCPSessionManager(connection_params=_sse_conn())
        mgr._create_client({"Auth": "token"})
        mock_sse.assert_called_once()
        call_kwargs = mock_sse.call_args
        assert call_kwargs.kwargs["headers"] == {"Auth": "token"}

    @patch("trpc_agent_sdk.tools.mcp_tool._mcp_session_manager.streamablehttp_client")
    def test_streamable_client(self, mock_streamable):
        mgr = MCPSessionManager(connection_params=_streamable_conn())
        mgr._create_client({"Auth": "token"})
        mock_streamable.assert_called_once()

    def test_unsupported_params_raises(self):
        mgr = MCPSessionManager(connection_params=_stdio_conn())
        mgr._connection_params = "unsupported"
        with pytest.raises(ValueError, match="Unable to initialize connection"):
            mgr._create_client()


# ---------------------------------------------------------------------------
# Tests: create_session
# ---------------------------------------------------------------------------

class TestCreateSession:
    @pytest.mark.asyncio
    async def test_creates_new_session_stdio(self):
        mgr = MCPSessionManager(connection_params=_stdio_conn())

        mock_session = _mock_session(closed=False)
        mock_client = AsyncMock()
        mock_transports = (MagicMock(), MagicMock())

        with patch.object(mgr, "_create_client", return_value=mock_client), \
             patch("trpc_agent_sdk.tools.mcp_tool._mcp_session_manager.ClientSession") as mock_cs_cls:
            mock_client.__aenter__ = AsyncMock(return_value=mock_transports)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            mock_cs_instance = AsyncMock()
            mock_cs_instance.initialize = AsyncMock()
            mock_cs_cls.return_value = mock_cs_instance
            mock_cs_instance.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cs_instance.__aexit__ = AsyncMock(return_value=False)

            session = await mgr.create_session()

            assert session is mock_session
            assert len(mgr._sessions) == 1
            assert "stdio_session" in mgr._sessions

    @pytest.mark.asyncio
    async def test_returns_existing_connected_session(self):
        mgr = MCPSessionManager(connection_params=_stdio_conn())

        existing_session = _mock_session(closed=False)
        mock_exit_stack = MagicMock(spec=AsyncExitStack)
        mgr._sessions["stdio_session"] = (existing_session, mock_exit_stack)

        session = await mgr.create_session()
        assert session is existing_session

    @pytest.mark.asyncio
    async def test_replaces_disconnected_session(self):
        mgr = MCPSessionManager(connection_params=_stdio_conn())

        disconnected_session = _mock_session(closed=True)
        old_exit_stack = AsyncMock(spec=AsyncExitStack)
        old_exit_stack.aclose = AsyncMock()
        mgr._sessions["stdio_session"] = (disconnected_session, old_exit_stack)

        new_session = _mock_session(closed=False)
        mock_client = AsyncMock()
        mock_transports = (MagicMock(), MagicMock())

        with patch.object(mgr, "_create_client", return_value=mock_client), \
             patch("trpc_agent_sdk.tools.mcp_tool._mcp_session_manager.ClientSession") as mock_cs_cls:
            mock_client.__aenter__ = AsyncMock(return_value=mock_transports)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            mock_cs_instance = AsyncMock()
            mock_cs_instance.initialize = AsyncMock()
            mock_cs_cls.return_value = mock_cs_instance
            mock_cs_instance.__aenter__ = AsyncMock(return_value=new_session)
            mock_cs_instance.__aexit__ = AsyncMock(return_value=False)

            session = await mgr.create_session()

            assert session is new_session
            old_exit_stack.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cleanup_disconnected_session_swallows_cross_task_error(self):
        mgr = MCPSessionManager(connection_params=_stdio_conn())

        disconnected_session = _mock_session(closed=True)
        old_exit_stack = AsyncMock(spec=AsyncExitStack)
        old_exit_stack.aclose = AsyncMock(
            side_effect=RuntimeError("Attempted to exit cancel scope in a different task than it was entered in")
        )
        mgr._sessions["stdio_session"] = (disconnected_session, old_exit_stack)

        new_session = _mock_session(closed=False)
        mock_client = AsyncMock()
        mock_transports = (MagicMock(), MagicMock())

        with patch.object(mgr, "_create_client", return_value=mock_client), \
             patch("trpc_agent_sdk.tools.mcp_tool._mcp_session_manager.ClientSession") as mock_cs_cls:
            mock_client.__aenter__ = AsyncMock(return_value=mock_transports)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            mock_cs_instance = AsyncMock()
            mock_cs_instance.initialize = AsyncMock()
            mock_cs_cls.return_value = mock_cs_instance
            mock_cs_instance.__aenter__ = AsyncMock(return_value=new_session)
            mock_cs_instance.__aexit__ = AsyncMock(return_value=False)

            session = await mgr.create_session()
            assert session is new_session

    @pytest.mark.asyncio
    async def test_cleanup_disconnected_session_logs_other_errors(self):
        mgr = MCPSessionManager(connection_params=_stdio_conn())

        disconnected_session = _mock_session(closed=True)
        old_exit_stack = AsyncMock(spec=AsyncExitStack)
        old_exit_stack.aclose = AsyncMock(side_effect=RuntimeError("something else"))
        mgr._sessions["stdio_session"] = (disconnected_session, old_exit_stack)

        new_session = _mock_session(closed=False)
        mock_client = AsyncMock()
        mock_transports = (MagicMock(), MagicMock())

        with patch.object(mgr, "_create_client", return_value=mock_client), \
             patch("trpc_agent_sdk.tools.mcp_tool._mcp_session_manager.ClientSession") as mock_cs_cls:
            mock_client.__aenter__ = AsyncMock(return_value=mock_transports)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            mock_cs_instance = AsyncMock()
            mock_cs_instance.initialize = AsyncMock()
            mock_cs_cls.return_value = mock_cs_instance
            mock_cs_instance.__aenter__ = AsyncMock(return_value=new_session)
            mock_cs_instance.__aexit__ = AsyncMock(return_value=False)

            session = await mgr.create_session()
            assert session is new_session

    @pytest.mark.asyncio
    async def test_create_session_failure_raises_runtime_error(self):
        mgr = MCPSessionManager(connection_params=_stdio_conn())

        with patch.object(mgr, "_create_client", side_effect=Exception("client creation failed")):
            with pytest.raises(RuntimeError, match="Error creating session"):
                await mgr.create_session()

    @pytest.mark.asyncio
    async def test_create_session_with_timedelta_timeout(self):
        conn = _stdio_conn()
        conn.timeout = timedelta(seconds=10)
        mgr = MCPSessionManager(connection_params=conn)

        mock_session = _mock_session(closed=False)
        mock_client = AsyncMock()
        mock_transports = (MagicMock(), MagicMock())

        with patch.object(mgr, "_create_client", return_value=mock_client), \
             patch("trpc_agent_sdk.tools.mcp_tool._mcp_session_manager.ClientSession") as mock_cs_cls:
            mock_client.__aenter__ = AsyncMock(return_value=mock_transports)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            mock_cs_instance = AsyncMock()
            mock_cs_instance.initialize = AsyncMock()
            mock_cs_cls.return_value = mock_cs_instance
            mock_cs_instance.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cs_instance.__aexit__ = AsyncMock(return_value=False)

            session = await mgr.create_session()
            assert session is mock_session
            call_kwargs = mock_cs_cls.call_args
            assert call_kwargs.kwargs["read_timeout_seconds"] == timedelta(seconds=10)

    @pytest.mark.asyncio
    async def test_create_session_with_float_timeout(self):
        conn = _stdio_conn(timeout=15.0)
        mgr = MCPSessionManager(connection_params=conn)

        mock_session = _mock_session(closed=False)
        mock_client = AsyncMock()
        mock_transports = (MagicMock(), MagicMock())

        with patch.object(mgr, "_create_client", return_value=mock_client), \
             patch("trpc_agent_sdk.tools.mcp_tool._mcp_session_manager.ClientSession") as mock_cs_cls:
            mock_client.__aenter__ = AsyncMock(return_value=mock_transports)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            mock_cs_instance = AsyncMock()
            mock_cs_instance.initialize = AsyncMock()
            mock_cs_cls.return_value = mock_cs_instance
            mock_cs_instance.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cs_instance.__aexit__ = AsyncMock(return_value=False)

            session = await mgr.create_session()
            assert session is mock_session
            call_kwargs = mock_cs_cls.call_args
            assert call_kwargs.kwargs["read_timeout_seconds"] == timedelta(seconds=15.0)

    @pytest.mark.asyncio
    async def test_create_session_non_stdio(self):
        mgr = MCPSessionManager(connection_params=_sse_conn())

        mock_session = _mock_session(closed=False)
        mock_client = AsyncMock()
        mock_transports = (MagicMock(), MagicMock())

        with patch.object(mgr, "_create_client", return_value=mock_client), \
             patch("trpc_agent_sdk.tools.mcp_tool._mcp_session_manager.ClientSession") as mock_cs_cls:
            mock_client.__aenter__ = AsyncMock(return_value=mock_transports)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            mock_cs_instance = AsyncMock()
            mock_cs_instance.initialize = AsyncMock()
            mock_cs_cls.return_value = mock_cs_instance
            mock_cs_instance.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cs_instance.__aexit__ = AsyncMock(return_value=False)

            session = await mgr.create_session()
            assert session is mock_session

    @pytest.mark.asyncio
    async def test_create_session_with_headers(self):
        conn = _sse_conn(headers={"Base": "base_val"})
        mgr = MCPSessionManager(connection_params=conn)

        mock_session = _mock_session(closed=False)
        mock_client = AsyncMock()
        mock_transports = (MagicMock(), MagicMock())

        with patch.object(mgr, "_create_client", return_value=mock_client) as mock_create, \
             patch("trpc_agent_sdk.tools.mcp_tool._mcp_session_manager.ClientSession") as mock_cs_cls:
            mock_client.__aenter__ = AsyncMock(return_value=mock_transports)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            mock_cs_instance = AsyncMock()
            mock_cs_instance.initialize = AsyncMock()
            mock_cs_cls.return_value = mock_cs_instance
            mock_cs_instance.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cs_instance.__aexit__ = AsyncMock(return_value=False)

            session = await mgr.create_session(headers={"Extra": "extra_val"})

            assert session is mock_session
            call_args = mock_create.call_args
            merged = call_args[1].get("merged_headers") or call_args[0][0]
            assert "Base" in merged
            assert "Extra" in merged


# ---------------------------------------------------------------------------
# Tests: close
# ---------------------------------------------------------------------------

class TestClose:
    @pytest.mark.asyncio
    async def test_close_all_sessions(self):
        mgr = MCPSessionManager(connection_params=_stdio_conn())

        exit_stack1 = AsyncMock(spec=AsyncExitStack)
        exit_stack1.aclose = AsyncMock()
        exit_stack2 = AsyncMock(spec=AsyncExitStack)
        exit_stack2.aclose = AsyncMock()

        mgr._sessions = {
            "session_1": (_mock_session(), exit_stack1),
            "session_2": (_mock_session(), exit_stack2),
        }

        await mgr.close()

        exit_stack1.aclose.assert_awaited_once()
        exit_stack2.aclose.assert_awaited_once()
        assert len(mgr._sessions) == 0

    @pytest.mark.asyncio
    async def test_close_empty_sessions(self):
        mgr = MCPSessionManager(connection_params=_stdio_conn())
        await mgr.close()
        assert len(mgr._sessions) == 0

    @pytest.mark.asyncio
    async def test_close_swallows_cleanup_errors(self):
        mgr = MCPSessionManager(connection_params=_stdio_conn())

        exit_stack = AsyncMock(spec=AsyncExitStack)
        exit_stack.aclose = AsyncMock(side_effect=RuntimeError("cleanup failed"))

        mgr._sessions = {
            "session_1": (_mock_session(), exit_stack),
        }

        await mgr.close()
        assert len(mgr._sessions) == 0

    @pytest.mark.asyncio
    async def test_close_handles_multiple_errors(self):
        mgr = MCPSessionManager(connection_params=_stdio_conn())

        exit_stack1 = AsyncMock(spec=AsyncExitStack)
        exit_stack1.aclose = AsyncMock(side_effect=RuntimeError("err1"))
        exit_stack2 = AsyncMock(spec=AsyncExitStack)
        exit_stack2.aclose = AsyncMock(side_effect=RuntimeError("err2"))

        mgr._sessions = {
            "s1": (_mock_session(), exit_stack1),
            "s2": (_mock_session(), exit_stack2),
        }

        await mgr.close()
        assert len(mgr._sessions) == 0
