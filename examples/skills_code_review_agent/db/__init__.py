# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Database module for backward compatibility.

Re-exports from the storage package for use by test files and
other modules that import from 'db'.
"""

from .init_db import init_db
from .storage import SqliteStorage

__all__ = ["init_db", "SqliteStorage"]