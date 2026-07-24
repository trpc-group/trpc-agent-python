# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Official Tool Script Safety Guard entry under ``trpc_agent_sdk.tools.safety``.

This module re-exports ``trpc_agent_sdk.safety`` without importing the rest of
the tools package, so safety can be used without heavy optional model deps.

Preferred imports::

    from trpc_agent_sdk.tools.safety import SafetyScanner, ToolSafetyFilter
    # equivalent:
    from trpc_agent_sdk.safety import SafetyScanner, ToolSafetyFilter
"""
from __future__ import annotations

from trpc_agent_sdk.safety import *  # noqa: F401,F403
from trpc_agent_sdk.safety import _SDK_AVAILABLE  # noqa: F401
from trpc_agent_sdk.safety import SCANNER_VERSION  # noqa: F401
