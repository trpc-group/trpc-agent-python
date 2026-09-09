# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Detect the installed a2a-sdk version.

a2a-sdk 0.3 and 1.x share the ``a2a`` import name, so only one can be
installed at a time.  Each framework package guards the extra it belongs to:

- ``trpc-agent-py[a2a]`` → a2a-sdk 0.3.x, import ``trpc_agent_sdk.server.a2a``
- ``trpc-agent-py[a2a-v1]`` → a2a-sdk 1.x, import ``trpc_agent_sdk.server.a2a_v1``
"""

from __future__ import annotations

import importlib.metadata
from typing import Optional

A2A_SDK_INSTALL_HINT = ("a2a-sdk is required. Install with pip install 'trpc-agent-py[a2a]' "
                        "(a2a-sdk 0.3) or pip install 'trpc-agent-py[a2a-v1]' (a2a-sdk 1.x). "
                        "The two extras cannot be installed together.")


def detect_a2a_sdk_version() -> Optional[str]:
    """Return the installed ``a2a-sdk`` version string, or ``None`` if missing."""
    try:
        return importlib.metadata.version("a2a-sdk")
    except importlib.metadata.PackageNotFoundError:
        return None


def detect_a2a_sdk_major() -> Optional[int]:
    """Return the installed ``a2a-sdk`` major version, or ``None`` if missing."""
    version = detect_a2a_sdk_version()
    if version is None:
        return None
    return int(version.split(".", 1)[0])


def require_a2a_sdk_major(expected_major: int) -> None:
    """Raise ``ImportError`` if the installed a2a-sdk major version is wrong.

    Args:
        expected_major: ``0`` means a2a-sdk 0.3.x (major < 1).  ``1`` means
            a2a-sdk 1.x (major >= 1).

    Raises:
        ImportError: If a2a-sdk is missing or its major version does not match.
    """
    version = detect_a2a_sdk_version()
    if version is None:
        raise ImportError(A2A_SDK_INSTALL_HINT)

    major = int(version.split(".", 1)[0])
    if expected_major < 1:
        if major >= 1:
            raise ImportError("trpc_agent_sdk.server.a2a requires a2a-sdk 0.3.x, "
                              f"but a2a-sdk {version} is installed. "
                              "Install with: pip install 'trpc-agent-py[a2a]'")
        return
    if major < 1:
        raise ImportError("trpc_agent_sdk.server.a2a_v1 requires a2a-sdk>=1.0, "
                          f"but a2a-sdk {version} is installed. "
                          "Install with: pip install 'trpc-agent-py[a2a-v1]'")
