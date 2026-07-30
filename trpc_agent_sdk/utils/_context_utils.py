# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Utilities for Coroutine context management.

This module is for TrpcAgent internal use only.
Please do not rely on the implementation details.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any
from typing import AsyncGenerator


class AsyncClosingContextManager(AbstractAsyncContextManager):
    """Async context manager for safely finalizing an asynchronously cleaned-up
    resource such as an async generator, calling its ``aclose()`` method.
    Needed to correctly close contexts for OTel spans.
    """

    def __init__(self, async_generator: AsyncGenerator[Any, None]):
        self.async_generator = async_generator

    async def __aenter__(self):
        return self.async_generator

    async def __aexit__(self, *exc_info):
        await self.async_generator.aclose()
