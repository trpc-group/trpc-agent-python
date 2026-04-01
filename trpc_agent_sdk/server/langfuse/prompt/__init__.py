# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Langfuse prompt module for TRPC Agent framework."""

from ._manager import Instruction
from ._manager import RemoteInstructionManager

__all__ = [
    "Instruction",
    "RemoteInstructionManager",
]
