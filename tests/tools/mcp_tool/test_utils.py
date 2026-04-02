# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

from unittest.mock import MagicMock, patch

import pytest
from mcp import StdioServerParameters as McpStdioServerParameters

from trpc_agent_sdk.tools.mcp_tool._types import (
    DEFAULT_TIMEOUT,
    StdioConnectionParams,
    SseConnectionParams,
    StreamableHTTPConnectionParams,
)
from trpc_agent_sdk.tools.mcp_tool._utils import (
    convert_conn_params,
    patch_mcp_cancel_scope_exit_issue,
)


class TestPatchMcpCancelScopeExitIssue:
    """Tests for the patch_mcp_cancel_scope_exit_issue monkeypatch function."""

    def test_patches_request_responder_exit(self):
        """Verify __exit__ is replaced and the patched flag is set."""
        fake_responder = type("FakeRequestResponder", (), {
            "__exit__": lambda self, exc_type, exc_val, exc_tb: None,
        })

        with patch("trpc_agent_sdk.tools.mcp_tool._utils.mcp_session") as mock_session:
            mock_session.RequestResponder = fake_responder
            patch_mcp_cancel_scope_exit_issue()

            assert getattr(fake_responder, "_trpc_agent_patched_cancel_scope_exit", False) is True
            assert fake_responder.__exit__.__name__ == "_patched_exit"

    def test_double_patch_is_noop(self):
        """Calling twice should not re-patch."""
        call_count = 0
        original_exit = lambda self, et, ev, tb: None

        fake_responder = type("FakeRequestResponder", (), {
            "__exit__": original_exit,
        })

        with patch("trpc_agent_sdk.tools.mcp_tool._utils.mcp_session") as mock_session:
            mock_session.RequestResponder = fake_responder
            patch_mcp_cancel_scope_exit_issue()
            first_exit = fake_responder.__exit__

            patch_mcp_cancel_scope_exit_issue()
            assert fake_responder.__exit__ is first_exit

    def test_patched_exit_swallows_cancel_scope_runtime_error(self):
        """Cancel-scope RuntimeErrors during __exit__ should be silently caught."""

        def bad_exit(self, exc_type, exc_val, exc_tb):
            raise RuntimeError("Attempted to exit a cancel scope that isn't the current tasks's current cancel scope")

        fake_responder = type("FakeRequestResponder", (), {
            "__exit__": bad_exit,
        })

        with patch("trpc_agent_sdk.tools.mcp_tool._utils.mcp_session") as mock_session:
            mock_session.RequestResponder = fake_responder
            patch_mcp_cancel_scope_exit_issue()

            instance = fake_responder()
            result = fake_responder.__exit__(instance, None, None, None)
            assert result is None

    def test_patched_exit_reraises_other_runtime_errors(self):
        """Non-cancel-scope RuntimeErrors should propagate normally."""

        def bad_exit(self, exc_type, exc_val, exc_tb):
            raise RuntimeError("Something completely different")

        fake_responder = type("FakeRequestResponder", (), {
            "__exit__": bad_exit,
        })

        with patch("trpc_agent_sdk.tools.mcp_tool._utils.mcp_session") as mock_session:
            mock_session.RequestResponder = fake_responder
            patch_mcp_cancel_scope_exit_issue()

            instance = fake_responder()
            with pytest.raises(RuntimeError, match="Something completely different"):
                fake_responder.__exit__(instance, None, None, None)

    def test_patched_exit_passes_through_normal_return(self):
        """Normal __exit__ calls should work unchanged."""
        sentinel = object()

        def normal_exit(self, exc_type, exc_val, exc_tb):
            return sentinel

        fake_responder = type("FakeRequestResponder", (), {
            "__exit__": normal_exit,
        })

        with patch("trpc_agent_sdk.tools.mcp_tool._utils.mcp_session") as mock_session:
            mock_session.RequestResponder = fake_responder
            patch_mcp_cancel_scope_exit_issue()

            instance = fake_responder()
            result = fake_responder.__exit__(instance, None, None, None)
            assert result is sentinel

    def test_no_request_responder_returns_early(self):
        """If RequestResponder is not found, function should return without error."""
        with patch("trpc_agent_sdk.tools.mcp_tool._utils.mcp_session") as mock_session:
            mock_session.RequestResponder = None
            patch_mcp_cancel_scope_exit_issue()

    def test_no_exit_method_returns_early(self):
        """If __exit__ is not callable, function should return without error."""
        fake_responder = type("FakeRequestResponder", (), {})
        delattr(fake_responder, "__exit__") if hasattr(fake_responder, "__exit__") else None

        with patch("trpc_agent_sdk.tools.mcp_tool._utils.mcp_session") as mock_session:
            fake_responder.__exit__ = "not_callable"
            mock_session.RequestResponder = fake_responder
            patch_mcp_cancel_scope_exit_issue()
            assert not getattr(fake_responder, "_trpc_agent_patched_cancel_scope_exit", False)


class TestConvertConnParams:
    """Tests for the convert_conn_params function."""

    def test_stdio_server_params_converted_to_stdio_connection_params(self):
        """McpStdioServerParameters should be wrapped in StdioConnectionParams."""
        server_params = McpStdioServerParameters(command="npx", args=["-y", "server"])
        result = convert_conn_params(server_params)

        assert isinstance(result, StdioConnectionParams)
        assert result.server_params == server_params
        assert result.timeout == DEFAULT_TIMEOUT

    def test_stdio_connection_params_passthrough(self):
        """StdioConnectionParams should be returned as-is."""
        server_params = McpStdioServerParameters(command="npx")
        conn = StdioConnectionParams(server_params=server_params, timeout=15.0)
        result = convert_conn_params(conn)

        assert result is conn

    def test_none_passthrough(self):
        """None should be returned as-is (it's valid McpConnectionParamsType)."""
        result = convert_conn_params(None)
        assert result is None

    def test_unsupported_type_raises_value_error(self):
        """An unsupported type should raise ValueError."""
        with pytest.raises(ValueError, match="Unsupported connection parameters type"):
            convert_conn_params({"url": "http://example.com"})

    def test_unsupported_int_type_raises_value_error(self):
        with pytest.raises(ValueError, match="Unsupported connection parameters type"):
            convert_conn_params(42)
