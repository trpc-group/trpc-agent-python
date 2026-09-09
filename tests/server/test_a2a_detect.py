# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for a2a-sdk version detection."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.server._a2a_detect import A2A_SDK_INSTALL_HINT
from trpc_agent_sdk.server._a2a_detect import detect_a2a_sdk_major
from trpc_agent_sdk.server._a2a_detect import detect_a2a_sdk_version
from trpc_agent_sdk.server._a2a_detect import require_a2a_sdk_major


def test_detect_major_matches_installed_version():
    version = detect_a2a_sdk_version()
    if version is None:
        pytest.skip("a2a-sdk is not installed")
    assert detect_a2a_sdk_major() == int(version.split(".", 1)[0])


def test_require_raises_when_sdk_missing(monkeypatch):
    monkeypatch.setattr(
        "trpc_agent_sdk.server._a2a_detect.detect_a2a_sdk_version",
        lambda: None,
    )
    with pytest.raises(ImportError, match="trpc-agent-py\\[a2a\\]"):
        require_a2a_sdk_major(0)
    assert "a2a-v1" in A2A_SDK_INSTALL_HINT


def test_require_v0_3_rejects_v1(monkeypatch):
    monkeypatch.setattr(
        "trpc_agent_sdk.server._a2a_detect.detect_a2a_sdk_version",
        lambda: "1.0.0",
    )
    with pytest.raises(ImportError, match="a2a requires a2a-sdk 0.3"):
        require_a2a_sdk_major(0)


def test_require_v1_rejects_v0_3(monkeypatch):
    monkeypatch.setattr(
        "trpc_agent_sdk.server._a2a_detect.detect_a2a_sdk_version",
        lambda: "0.3.22",
    )
    with pytest.raises(ImportError, match="a2a_v1 requires a2a-sdk>=1.0"):
        require_a2a_sdk_major(1)


def test_require_accepts_matching_major(monkeypatch):
    monkeypatch.setattr(
        "trpc_agent_sdk.server._a2a_detect.detect_a2a_sdk_version",
        lambda: "0.3.22",
    )
    require_a2a_sdk_major(0)
    monkeypatch.setattr(
        "trpc_agent_sdk.server._a2a_detect.detect_a2a_sdk_version",
        lambda: "1.2.3",
    )
    require_a2a_sdk_major(1)
