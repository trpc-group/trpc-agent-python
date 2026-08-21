# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Do not collect this tree unless a2a-sdk 1.x is installed."""

from __future__ import annotations

from trpc_agent_sdk.server._a2a_detect import detect_a2a_sdk_major


def pytest_ignore_collect(collection_path, config):
    major = detect_a2a_sdk_major()
    if major is not None and major >= 1:
        return None
    return True
