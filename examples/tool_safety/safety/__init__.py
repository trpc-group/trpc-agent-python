# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0
"""Compatibility re-export of the SDK Tool Script Safety Guard.

The implementation lives in ``trpc_agent_sdk.safety``. This package keeps
existing example imports working::

    from examples.tool_safety.safety import SafetyScanner
"""
from __future__ import annotations

from trpc_agent_sdk.safety import *  # noqa: F401,F403
from trpc_agent_sdk.safety import _SDK_AVAILABLE  # noqa: F401
from trpc_agent_sdk.safety import SCANNER_VERSION  # noqa: F401
