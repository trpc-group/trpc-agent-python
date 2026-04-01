# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
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
