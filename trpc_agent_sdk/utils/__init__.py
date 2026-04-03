# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Utilities package for TRPC Agent framework.

This module provides utility functions and helper classes used across
the TRPC Agent system.
"""

from ._context_utils import AsyncClosingContextManager
from ._execute_cmd import CommandExecResult
from ._execute_cmd import async_execute_command
from ._hash_key import user_key
from ._registry_factory import BaseRegistryFactory
from ._singleton import SingletonBase
from ._singleton import SingletonMeta
from ._singleton import singleton

__all__ = [
    "AsyncClosingContextManager",
    "CommandExecResult",
    "async_execute_command",
    "user_key",
    "BaseRegistryFactory",
    "SingletonBase",
    "SingletonMeta",
    "singleton",
]
