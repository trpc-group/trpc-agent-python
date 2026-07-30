# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Black-box CLI agent: subprocess 调 trpc-claudecode 真实 CLI。"""

from .call_agent import call_agent

__all__ = ["call_agent"]
