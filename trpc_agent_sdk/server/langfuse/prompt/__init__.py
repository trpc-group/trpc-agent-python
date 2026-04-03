# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Langfuse prompt module for TRPC Agent framework."""

from ._manager import Instruction
from ._manager import RemoteInstructionManager

__all__ = [
    "Instruction",
    "RemoteInstructionManager",
]
