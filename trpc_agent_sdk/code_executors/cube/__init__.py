# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Cube/E2B code executor and workspace runtime.

This subpackage requires the optional ``e2b-code-interpreter`` dependency
(install with ``pip install trpc-agent-py[cube]``); importing any module
here pulls it in eagerly. Code paths that don't need the Cube backend
should import from :mod:`trpc_agent_sdk.code_executors` instead — that
package never references this subpackage and therefore stays
``[cube]``-free.
"""

from ._code_executor import CubeCodeExecutor
from ._runtime import CubeProgramRunner
from ._runtime import CubeWorkspaceFS
from ._runtime import CubeWorkspaceManager
from ._runtime import CubeWorkspaceRuntime
from ._runtime import create_cube_workspace_runtime
from ._sandbox import CubeCommandResult
from ._sandbox import CubeSandboxClient
from ._transfer import OnExisting
from ._types import CubeCodeExecutorConfig
from ._types import CubeWorkspaceRuntimeConfig

__all__ = [
    "CubeCodeExecutor",
    "CubeCodeExecutorConfig",
    "CubeCommandResult",
    "CubeProgramRunner",
    "CubeSandboxClient",
    "CubeWorkspaceFS",
    "CubeWorkspaceManager",
    "CubeWorkspaceRuntime",
    "CubeWorkspaceRuntimeConfig",
    "OnExisting",
    "create_cube_workspace_runtime",
]
