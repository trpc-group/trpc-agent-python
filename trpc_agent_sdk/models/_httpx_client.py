# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""HTTPX client implementation module.

This module provides the HTTPX client implementation for TRPC Agent framework.
"""

import httpx
import asyncio
import threading
import inspect
import os
from abc import ABC
from abc import abstractmethod
from typing import Callable
from typing import Optional
from typing import Any
from typing_extensions import override

_DEFAULT_HTTP_CLIENT_LIMITS = httpx.Limits(
    max_connections=1000,
    max_keepalive_connections=100,
    keepalive_expiry=30.0,
)
_DEFAULT_HTTP_CLIENT_TIMEOUT = httpx.Timeout(timeout=600.0, connect=5.0)


class BaseHttpClientProvider(ABC):
    """Provider for HTTP clients."""

    @abstractmethod
    def create_http_client(self) -> httpx.AsyncClient:
        """Create an HTTP client."""
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    async def close_http_client(self, client: Any) -> None:
        """Close an HTTP client."""
        raise NotImplementedError("Subclasses must implement this method")


class TemporaryHttpClientProvider(BaseHttpClientProvider):
    """Provider for temporary HTTP clients."""

    @override
    def create_http_client(self) -> Optional[httpx.AsyncClient]:
        """Create a temporary HTTP client."""
        return None

    @override
    async def close_http_client(self, client: Any) -> None:
        """Close a temporary HTTP client."""
        close_method = getattr(client, "close", None)
        if callable(close_method):
            result = close_method()
            if inspect.isawaitable(result):
                await result


_shared_http_clients: dict[tuple[int, int], httpx.AsyncClient] = {}
_shared_http_clients_lock: threading.RLock = threading.RLock()


def _get_loop_key() -> int:
    """Return a cache key for the current event loop, or a process-local fallback."""
    try:
        return id(asyncio.get_running_loop())
    except RuntimeError:
        return 0


def _get_client_key() -> tuple[int, int]:
    """Return a process-local and loop-local cache key for shared HTTP clients."""
    return os.getpid(), _get_loop_key()


def _reset_shared_http_clients_after_fork() -> None:
    """Drop inherited clients and recreate the lock in a forked child process."""
    global _shared_http_clients_lock
    _shared_http_clients.clear()
    _shared_http_clients_lock = threading.RLock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_shared_http_clients_after_fork)


def _create_shared_http_client() -> httpx.AsyncClient:
    """Return a loop-local shared HTTP client with bounded keep-alive reuse.

    Returns:
        A loop-local shared HTTP client with bounded keep-alive reuse.
    """
    client_key = _get_client_key()
    with _shared_http_clients_lock:
        client = _shared_http_clients.get(client_key)
        if client is None or client.is_closed:
            client = httpx.AsyncClient(
                limits=_DEFAULT_HTTP_CLIENT_LIMITS,
                timeout=_DEFAULT_HTTP_CLIENT_TIMEOUT,
                follow_redirects=True,
            )
            _shared_http_clients[client_key] = client
        return client


class SharedHttpClientProvider(BaseHttpClientProvider):
    """Provider for shared HTTP clients."""

    @override
    def create_http_client(self) -> Optional[httpx.AsyncClient]:
        """Create a shared HTTP client."""
        return _create_shared_http_client()

    @override
    async def close_http_client(self, client: Any) -> None:
        """Close a shared HTTP client."""
        return None


HttpClientProviderFactory = Callable[[], BaseHttpClientProvider]


def temporary_http_client_provider_factory() -> BaseHttpClientProvider:
    """Provider for temporary HTTP clients."""
    return TemporaryHttpClientProvider()


def shared_http_client_provider_factory() -> BaseHttpClientProvider:
    """Provider for shared HTTP clients."""
    return SharedHttpClientProvider()


async def close_shared_http_clients() -> None:
    """Close HTTP clients created by the default HTTP client factory."""
    with _shared_http_clients_lock:
        clients = list(_shared_http_clients.values())
        _shared_http_clients.clear()
        for client in clients:
            if not client.is_closed:
                await client.aclose()
