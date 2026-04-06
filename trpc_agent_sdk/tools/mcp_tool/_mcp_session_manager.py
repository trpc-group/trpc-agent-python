# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
# Below code are copy and modified from https://github.com/google/adk-python.git
#
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""MCP session manager for TRPC Agent framework."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Dict
from typing import Optional
from typing import Union

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from trpc_agent_sdk.log import logger

from ._types import McpConnectionParamsType
from ._types import McpStdioServerParameters
from ._types import SseConnectionParams
from ._types import StdioConnectionParams
from ._types import StreamableHTTPConnectionParams
from ._utils import convert_conn_params


class MCPSessionManager:
    """Manages MCP client sessions.

    This class provides methods for creating and initializing MCP client sessions,
    handling different connection parameters (Stdio and SSE) and supporting
    session pooling based on authentication headers.
    """

    def __init__(
        self,
        connection_params: Union[McpConnectionParamsType, McpStdioServerParameters],
        session_group_params: Optional[dict] = None,
    ):
        """Initializes the MCP session manager.

        Args:
            connection_params: Parameters for the MCP connection (Stdio, SSE or
              Streamable HTTP). Stdio by default also has a 5s read timeout as other
              parameters but it's not configurable for now.
        """
        self._connection_params = convert_conn_params(connection_params)
        self._session_group_params = session_group_params or {}

        # Session pool: maps session keys to (session, exit_stack) tuples
        self._sessions: Dict[str, tuple[ClientSession, AsyncExitStack]] = {}

        # Lock to prevent race conditions in session creation
        self._session_lock = asyncio.Lock()

    @staticmethod
    def _is_cross_task_cancel_scope_error(error: BaseException) -> bool:
        """Whether the error is AnyIO cancel-scope cross-task close noise."""
        if not isinstance(error, RuntimeError):
            return False
        message = str(error).lower()
        return ("attempted to exit cancel scope in a different task than it was entered in" in message
                or ("cancel scope" in message and "different task" in message))

    def _generate_session_key(self, merged_headers: Optional[Dict[str, str]] = None) -> str:
        """Generates a session key based on connection params and merged headers.

        For StdioConnectionParams, returns a constant key since headers are not
        supported. For SSE and StreamableHTTP connections, generates a key based
        on the provided merged headers.

        Args:
            merged_headers: Already merged headers (base + additional).

        Returns:
            A unique session key string.
        """
        if isinstance(self._connection_params, StdioConnectionParams):
            # For stdio connections, headers are not supported, so use constant key
            return 'stdio_session'

        # For SSE and StreamableHTTP connections, use merged headers
        if merged_headers:
            headers_json = json.dumps(merged_headers, sort_keys=True)
            headers_hash = hashlib.md5(headers_json.encode()).hexdigest()
            return f'session_{headers_hash}'
        else:
            return 'session_no_headers'

    def _merge_headers(self, additional_headers: Optional[Dict[str, str]] = None) -> Optional[Dict[str, str]]:
        """Merges base connection headers with additional headers.

        Args:
            additional_headers: Optional headers to merge with connection headers.

        Returns:
            Merged headers dictionary, or None if no headers are provided.
        """
        if isinstance(self._connection_params, (StdioConnectionParams, McpStdioServerParameters)):
            # Stdio connections don't support headers, so return None
            return None

        base_headers = {}
        if (hasattr(self._connection_params, 'headers') and self._connection_params.headers):
            base_headers = self._connection_params.headers.copy()

        if additional_headers:
            base_headers.update(additional_headers)

        return base_headers

    def _is_session_disconnected(self, session: ClientSession) -> bool:
        """Checks if a session is disconnected or closed.

        Args:
            session: The ClientSession to check.

        Returns:
            True if the session is disconnected, False otherwise.
        """
        return session._read_stream._closed or session._write_stream._closed

    def _create_client(self, merged_headers: Optional[Dict[str, str]] = None):
        """Creates an MCP client based on the connection parameters.

        Args:
            merged_headers: Optional headers to include in the connection.
                           Only applicable for SSE and StreamableHTTP connections.

        Returns:
            The appropriate MCP client instance.

        Raises:
            ValueError: If the connection parameters are not supported.
        """
        if isinstance(self._connection_params, StdioConnectionParams):
            client = stdio_client(
                server=self._connection_params.server_params,
                errlog=sys.stderr,
            )
        elif isinstance(self._connection_params, SseConnectionParams):
            client = sse_client(
                url=self._connection_params.url,
                headers=merged_headers,
                timeout=self._connection_params.timeout,
                sse_read_timeout=self._connection_params.sse_read_timeout,
            )
        elif isinstance(self._connection_params, StreamableHTTPConnectionParams):
            client = streamablehttp_client(
                url=self._connection_params.url,
                headers=merged_headers,
                timeout=self._connection_params.timeout,
                sse_read_timeout=self._connection_params.sse_read_timeout,
                terminate_on_close=self._connection_params.terminate_on_close,
            )
        else:
            raise ValueError('Unable to initialize connection. Connection should be'
                             ' StdioConnectionParams or SseConnectionParams or StreamableHTTPConnectionParams, but got'
                             f' {type(self._connection_params)}')
        return client

    async def create_session(self, headers: Optional[Dict[str, str]] = None) -> ClientSession | None:
        """Creates and initializes an MCP client session.

        This method will check if an existing session for the given headers
        is still connected. If it's disconnected, it will be cleaned up and
        a new session will be created.

        Args:
            headers: Optional headers to include in the session. These will be
                    merged with any existing connection headers. Only applicable
                    for SSE and StreamableHTTP connections.

        Returns:
            ClientSession: The initialized MCP client session.
        """
        # Merge headers once at the beginning
        merged_headers = self._merge_headers(headers)

        # Generate session key using merged headers
        session_key = self._generate_session_key(merged_headers)

        # Use async lock to prevent race conditions
        async with self._session_lock:
            # Check if we have an existing session
            if session_key in self._sessions:
                session, exit_stack = self._sessions[session_key]

                # Check if the existing session is still connected
                if not self._is_session_disconnected(session):
                    # Session is still good, return it
                    return session
                else:
                    # Session is disconnected, clean it up
                    logger.info('Cleaning up disconnected session: %s', session_key)
                    try:
                        await exit_stack.aclose()
                    except Exception as e:  # pylint: disable=broad-except
                        if self._is_cross_task_cancel_scope_error(e):
                            logger.debug('Ignore cross-task cancel-scope cleanup noise for %s', session_key)
                        else:
                            logger.warning('Error during disconnected session cleanup: %s', e)
                    finally:
                        del self._sessions[session_key]

            # Create a new session (either first time or replacing disconnected one)
            exit_stack = AsyncExitStack()

            try:
                client = self._create_client(merged_headers)

                transports = await exit_stack.enter_async_context(client)
                # The streamable http client returns a GetSessionCallback in addition to
                # the read/write MemoryObjectStreams needed to build the ClientSession,
                # we limit then to the two first values to be compatible with all clients.
                if isinstance(self._connection_params, StdioConnectionParams):
                    if isinstance(self._connection_params.timeout, timedelta):
                        timeout = self._connection_params.timeout
                    else:
                        timeout = timedelta(seconds=self._connection_params.timeout)
                    session = await exit_stack.enter_async_context(
                        ClientSession(
                            *transports[:2],
                            read_timeout_seconds=timeout,
                            **self._session_group_params,
                        ))
                else:
                    session = await exit_stack.enter_async_context(
                        ClientSession(*transports[:2], **self._session_group_params))
                await session.initialize()

                # Store session and exit stack in the pool
                self._sessions[session_key] = (session, exit_stack)
                logger.debug('Created new session: %s', session_key)
                return session
            except BaseException as ex:  # pylint: disable=broad-except
                # If session creation fails, clean up the exit stack
                error_msg = f'Error creating session: {session_key} mcp_info: {self._connection_params} error: {ex}'
                logger.error(error_msg)
                if exit_stack:
                    await exit_stack.aclose()
                logger.error("Error creating session: %s mcp_info: %s error: %s", session_key, self._connection_params,
                             ex)
                logger.error("Error creating session: %s mcp_info: %s error: %s", session_key, self._connection_params,
                             ex)
                raise RuntimeError(error_msg) from ex

    async def close(self):
        """Closes all sessions and cleans up resources."""
        async with self._session_lock:
            for session_key in list(self._sessions.keys()):
                _, exit_stack = self._sessions[session_key]
                try:
                    await exit_stack.aclose()
                except Exception as ex:  # pylint: disable=broad-except
                    # Log the error but don't re-raise to avoid blocking shutdown
                    logger.warning("Warning: Error during MCP session cleanup for %s: %s", session_key, ex)
                finally:
                    del self._sessions[session_key]
