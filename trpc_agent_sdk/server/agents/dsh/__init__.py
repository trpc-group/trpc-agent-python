# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""DeepSeek Harness (DSH) agent integration."""

from ._agent import DSHAgent
from ._config import DSHConfig
from ._config import DSHModel
from ._protocol import DSH_SESSION_STATE_KEY
from ._protocol import DSHModelSpec
from ._protocol import DSHRunInput

__all__ = [
    "DSHAgent",
    "DSHConfig",
    "DSHModel",
    "DSHModelSpec",
    "DSHRunInput",
    "DSH_SESSION_STATE_KEY",
]

DSHAgent.model_rebuild()
