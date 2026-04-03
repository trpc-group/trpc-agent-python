# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""A2A Agent with Cancel Example."""

from .run_server import create_a2a_service
from .run_server import serve
from .test_a2a_cancel import create_runner
from .test_a2a_cancel import main
from .test_a2a_cancel import run_remote_agent
from .test_a2a_cancel import scenario_1_cancel_during_streaming
from .test_a2a_cancel import scenario_2_cancel_during_tool_execution

__all__ = [
    "create_a2a_service",
    "serve",
    "create_runner",
    "main",
    "run_remote_agent",
    "scenario_1_cancel_during_streaming",
    "scenario_2_cancel_during_tool_execution",
]
