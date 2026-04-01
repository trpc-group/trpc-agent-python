# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""MCP Tool utils.
"""

from __future__ import annotations

from mcp.shared import session as mcp_session

from ._types import DEFAULT_TIMEOUT
from ._types import McpConnectionParamsType
from ._types import McpStdioServerParameters
from ._types import StdioConnectionParams


def patch_mcp_cancel_scope_exit_issue() -> None:
    """Work around AnyIO cancel-scope RuntimeError inside mcp's RequestResponder.

    Symptom (key error in logs):
      RuntimeError: Attempted to exit a cancel scope that isn't the current tasks's current cancel scope

    This exception originates from RequestResponder.__exit__ in mcp/shared/session.py, and is treated
    as an unhandled exception by anyio.TaskGroup, causing the StreamableHTTP session manager to exit
    and trigger a full ASGI application shutdown.

    This implements a minimal-intrusion monkeypatch: only catch and ignore this type of cancel-scope
    RuntimeError during the __exit__ phase (typically occurs during connection/request cancellation cleanup).

    Note: This is a temporary compatibility workaround. It is recommended to upgrade/downgrade mcp + anyio
    versions to resolve this issue permanently.
    """

    RequestResponder = getattr(mcp_session, "RequestResponder", None)  # pylint: disable=invalid-name
    if RequestResponder is None:
        return

    original_exit = getattr(RequestResponder, "__exit__", None)
    if not callable(original_exit):
        return

    # Avoid double-patching
    if getattr(RequestResponder, "_trpc_agent_patched_cancel_scope_exit", False):
        return

    def _patched_exit(self, exc_type, exc_val, exc_tb):  # type: ignore[no-untyped-def]
        try:
            return original_exit(self, exc_type, exc_val, exc_tb)
        except RuntimeError as ex:
            msg = str(ex).lower()
            if "cancel scope" in msg and "exit" in msg:
                # Cleanup phase compatibility: prevent task group from crashing due to this exception
                return None
            raise

    setattr(RequestResponder, "__exit__", _patched_exit)
    setattr(RequestResponder, "_trpc_agent_patched_cancel_scope_exit", True)


def convert_conn_params(
        connection_params: McpConnectionParamsType | McpStdioServerParameters) -> McpConnectionParamsType:
    """Convert the connection parameters to the correct type.

    Args:
        connection_params: The connection parameters to convert.

    Returns:
        The converted connection parameters.
    """
    if isinstance(connection_params, McpConnectionParamsType):
        return connection_params

    if isinstance(connection_params, McpStdioServerParameters):
        connection_params = StdioConnectionParams(
            server_params=connection_params,
            timeout=DEFAULT_TIMEOUT,
        )
    else:
        raise ValueError(f"Unsupported connection parameters type: {type(connection_params)}")

    return connection_params
