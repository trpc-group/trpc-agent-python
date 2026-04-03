# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
Common utilities for TRPC Agent.
"""

from ._compatible import OSDetector
from ._compatible import OS_DETECTOR
from ._compatible import checkenum

__all__ = [
    "OSDetector",
    "OS_DETECTOR",
    "checkenum",
]
