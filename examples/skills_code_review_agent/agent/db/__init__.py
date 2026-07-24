# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Code Review Agent — persistence foundation (Phase 0).

Exposes the :class:`ReviewStore` protocol and the default
:class:`SQLiteStore` implementation. Downstream phases (P1–P6) interact
with persisted review data exclusively through this package so that the
storage backend can be swapped without touching the orchestration layer.
"""

from .storage import ReviewStore
from .storage import SQLiteStore

__all__ = ["ReviewStore", "SQLiteStore"]
