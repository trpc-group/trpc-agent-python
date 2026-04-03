# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Metrics module for trpc-claw."""

from ._metrics import register_metrics
from ._metrics import setup_metrics

__all__ = [
    "register_metrics",
    "setup_metrics",
]
