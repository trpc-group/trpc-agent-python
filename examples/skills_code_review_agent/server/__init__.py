# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Server module for the code review agent — Phase 3: Learning path enhancement."""

from .a2a_server import create_a2a_service, serve

__all__ = ["create_a2a_service", "serve"]