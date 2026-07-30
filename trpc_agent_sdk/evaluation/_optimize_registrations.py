# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Central registration of optimizer algorithms.

Each algorithm is registered under ``try/except ImportError`` so optional
third-party deps that are missing simply omit the algorithm rather than
breaking package import.
"""

from __future__ import annotations

from ._optimize_registry import OPTIMIZER_REGISTRY

try:
    from ._optimize_gepa_reflective import GepaReflectiveOptimizer
except ImportError:
    pass
else:
    OPTIMIZER_REGISTRY.register("gepa_reflective", GepaReflectiveOptimizer)
