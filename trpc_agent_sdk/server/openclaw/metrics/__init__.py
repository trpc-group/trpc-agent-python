# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Metrics module for trpc_claw."""

from ._metrics import register_metrics
from ._metrics import setup_metrics

__all__ = [
    "register_metrics",
    "setup_metrics",
]
